# -*- coding: utf-8 -*-
"""
Collaudi automatici dello scanner: l'operatore c'e' il meno possibile.

    python collaudo_auto.py senza      [--ponte ...] [--scanner ...]
        SENZA pellicola, nessuna domanda. Stato e informazioni della macchina,
        azzeramento, calibrazione, letture a finestra vuota (canali,
        esposizioni, linearita', tetto d'esposizione, lettura unica, IR,
        rumore, buio), pressore, fuoco, piccoli movimenti del trasporto,
        comandi che senza pellicola devono rifiutare (DX, fotogramma
        successivo), tempi del ponte. Alla fine lo zip dei registri.

    python collaudo_auto.py pellicola  [--ponte ...] [--scanner ...]
        CON pellicola: chiede solo di inserire una striscia (non di un
        cliente) e la riconosce da solo. Poi codice DX (prima e dopo che la
        pellicola si muove), letture, esposizione, pressore misurato sulla
        nitidezza, autofocus, avanzamenti e ritorno, sonde capture e Digital
        ICE, espulsione. Alla fine lo zip dei registri.

Come si comporta quando qualcosa va storto (31/10/2026, richiesta di
Alessandro: "l'operatore deve essere meno presente possibile"):

- ogni passo ha un tempo massimo; se fallisce, si fa un passo indietro
  (recupero: pressore su, attesa dello scanner, mezzo millimetro indietro
  per i movimenti) e lo si ripete una volta;
- se l'agente non risponde (ping senza risposta in 8 s) o si e' chiuso, si
  aspettano 8 s, lo si riavvia (MASTERFLEX.bat agente: chiude prima i
  programmi originali rimasti) e si riprende dal passo dopo;
- un passo fallito non ferma il collaudo: lo si annota e si va avanti.
  Si ferma solo lo STOP EMERGENZA.

I rapporti vanno in software/log/collaudi/, con quelli manuali.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
import traceback

import numpy as np

import collaudo_scanner as cs
from filmscan import registri
from filmscan.hal import Emergenza, PonteSdkBackend

QUI = os.path.dirname(os.path.abspath(__file__))
ATTESA_MORTO = 8.0          # ping senza risposta: l'agente e' considerato morto
ATTESA_PRONTO = 240.0       # dopo un riavvio l'SDK ci mette 40-150 s


def nitidezza(img: np.ndarray) -> float:
    """Varianza del laplaciano su un ritaglio centrale: piu' alta, piu' a
    fuoco. Serve a confrontare letture dello stesso punto, non in assoluto."""
    x = img.astype(np.float64)
    h, w = x.shape[:2]
    c = x[h // 4:3 * h // 4, w // 4:3 * w // 4]
    lap = (c[1:-1, :-2] + c[1:-1, 2:] + c[:-2, 1:-1] + c[2:, 1:-1]
           - 4 * c[1:-1, 1:-1])
    return float(lap.var() / max(c.mean(), 1.0))


class Banco(object):
    """Il collaudo: passi, recuperi, riavvii, rapporto."""

    def __init__(self, a, tipo: str):
        self.a = a
        self.r = cs.Rapporto(tipo)
        self.r.dati["passi"] = []
        self.b: PonteSdkBackend = None
        self.riavvii = 0
        self.falliti = 0

    # --- l'agente ---------------------------------------------------------
    def _pid(self):
        try:
            with open(os.path.join(self.a.ponte, "agente.pid")) as f:
                return int(f.read().strip())
        except (IOError, OSError, ValueError):
            return None

    def vivo(self) -> bool:
        if os.name == "nt":
            import manutenzione
            return manutenzione.agente_attivo(self.a.ponte)
        pid = self._pid()
        if not pid:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        try:                        # un processo morto ma non raccolto
            with open("/proc/%d/status" % pid) as f:
                return "\nState:\tZ" not in f.read()
        except (IOError, OSError):
            return True

    def avvia_agente(self) -> None:
        self.r("  avvio dell'agente...")
        if os.name == "nt" and not self.a.simula:
            # la stessa strada del menu: chiude i programmi originali rimasti
            # (il GateControl dell'agente morto) e apre la finestra
            subprocess.call(["cmd", "/c", "call", os.path.join(QUI, "MASTERFLEX.bat"),
                             "agente"], cwd=QUI)
            return
        cmd = [sys.executable, os.path.join(QUI, "ponte", "agente.py"),
               "--condivisa", self.a.ponte, "--scanner", self.a.scanner]
        if self.a.simula:
            cmd.append("--simula")
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = 0x00000010            # CREATE_NEW_CONSOLE
        subprocess.Popen(cmd, cwd=QUI, **kw)

    def aspetta_pid(self, secondi: float = 60.0) -> None:
        """Dopo l'avvio: il pid c'e' appena l'agente parte. Finche' non c'e'
        non si avvia nient'altro (due agenti sulla stessa cartella si
        rubano i file)."""
        fine = time.time() + secondi
        while time.time() < fine and not self.vivo():
            time.sleep(1.0)

    def ferma_agente(self) -> None:
        pid = self._pid()
        if not pid:
            return
        self.r("  l'agente non risponde: lo chiudo (pid %d)" % pid)
        try:
            if os.name == "nt":
                subprocess.call(["taskkill", "/PID", str(pid), "/T", "/F"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.kill(pid, 9)
        except OSError:
            pass

    def risponde(self) -> bool:
        try:
            self.b._send_una("ping", attesa=ATTESA_MORTO)
            return True
        except Emergenza:
            raise
        except Exception:                               # noqa: BLE001
            return False

    def collega(self, avvia: bool = True) -> bool:
        """Collegamento, con avvio dell'agente se non gira. True se pronto."""
        if self.b is None:
            self.b = PonteSdkBackend(self.a.ponte, risoluzione=0, ir=True)
        if avvia and not self.vivo():
            self.avvia_agente()
            self.aspetta_pid()
        fine = time.time() + ATTESA_PRONTO
        while time.time() < fine:
            try:
                info = json.loads(self.b._send_una("stato", attesa=ATTESA_MORTO))
                if info.get("pronto"):
                    self.b._info = info
                    self.b._connesso = True
                    return True
            except Emergenza:
                raise
            except Exception:                           # noqa: BLE001
                pass
            time.sleep(3)
        return False

    def ripristina(self, motivo: str) -> bool:
        """L'agente e' morto o bloccato: 8 s, riavvio, collegamento."""
        self.riavvii += 1
        self.r("  RIPRISTINO %d (%s)" % (self.riavvii, motivo))
        if self.vivo() and not self.risponde():
            self.ferma_agente()
        time.sleep(ATTESA_MORTO)
        if not self.vivo():
            self.avvia_agente()
            self.aspetta_pid()
        ok = self.collega(avvia=False)
        self.r("  agente %s" % ("di nuovo pronto" if ok else "NON pronto dopo il riavvio"))
        return ok

    # --- i passi ----------------------------------------------------------
    def passo(self, nome: str, fn, recupero=None, tentativi: int = 2,
              informativo: bool = False):
        """Esegue fn(); se fallisce: recupero (o ripristino dell'agente) e
        un altro tentativo. Restituisce il valore di fn o None.
        'informativo': il rifiuto e' una risposta che si vuole conoscere
        (tetto d'esposizione, comandi senza pellicola), non un guasto."""
        for k in range(tentativi):
            t0 = time.time()
            try:
                valore = fn()
                dt = time.time() - t0
                breve = valore if isinstance(valore, (int, float, str, bool)) or valore is None \
                    else json.dumps(valore, default=str)[:300]
                self.r("  [ok] %-44s %5.1f s  %s" % (nome, dt, "" if breve is None else breve))
                self.r.dati["passi"].append({"passo": nome, "esito": "ok", "secondi": round(dt, 2),
                                             "tentativo": k + 1, "valore": valore})
                return valore
            except Emergenza:
                raise
            except Exception as exc:                    # noqa: BLE001
                dt = time.time() - t0
                testo = str(exc).replace("\n", " ")
                self.r("  [!!] %-44s %5.1f s  %s" % (nome, dt, testo[:400]))
                self.r.dati["passi"].append({"passo": nome, "esito": "errore", "secondi": round(dt, 2),
                                             "tentativo": k + 1, "errore": testo,
                                             "traccia": traceback.format_exc(limit=3)})
                if k + 1 >= tentativi:
                    break
                if not self.vivo() or not self.risponde():
                    if not self.ripristina("dopo '%s'" % nome):
                        break
                elif recupero is not None:
                    try:
                        recupero()
                        self.r("       recupero fatto, riprovo")
                    except Exception as e2:             # noqa: BLE001
                        self.r("       recupero non riuscito: %s" % e2)
                else:
                    time.sleep(2.0)
        if informativo:
            self.r("       (rifiuto annotato: e' una delle risposte che si cercano)")
            return None
        self.falliti += 1
        self.r.avvisi.append(nome)
        return None

    def cmd(self, comando: str, attesa: float = 30.0):
        risposta = self.b._send(comando, attesa=attesa)
        try:
            return json.loads(risposta)
        except ValueError:
            return risposta

    def leggi(self, canale: str, esposizione: float = 1.0, buio: bool = False):
        nome = self.b._send("grab_%s_0_%.3f%s" % (canale, esposizione, "_buio" if buio else ""),
                            attesa=self.b.ATTESA_ACQUISIZIONE)
        return self.b._leggi(nome)

    def stato_sdk(self):
        try:
            return json.loads(self.b._send_una("stato", attesa=ATTESA_MORTO)).get("stato_sdk")
        except Exception:                               # noqa: BLE001
            return None

    def pressore_su(self):
        self.b._send("pressore_su", attesa=30.0)

    def aspetta_pronto(self):
        fine = time.time() + 120
        while time.time() < fine:
            try:
                if json.loads(self.b._send_una("stato", attesa=ATTESA_MORTO)).get("pronto"):
                    return
            except Exception:                           # noqa: BLE001
                pass
            time.sleep(2)

    def salva(self, nome: str, img, alto=None):
        try:
            cs.salva_png(cs.a8bit(img.astype(np.float64), alto or max(float(np.percentile(img, 99.5)), 1.0)),
                         os.path.join(self.r.cartella, nome + ".png"))
        except Exception:                               # noqa: BLE001
            pass

    def titolo(self, testo: str):
        self.r("")
        self.r("== %s" % testo)

    def chiudi(self, sonde: bool = False):
        if sonde:
            sorgente = os.path.join(self.a.ponte, "raw")
            for n in sorted(os.listdir(sorgente)) if os.path.isdir(sorgente) else []:
                if n.startswith("sonda_"):
                    try:
                        shutil.copy2(os.path.join(sorgente, n), os.path.join(self.r.cartella, n))
                    except (IOError, OSError):
                        pass
        self.r("")
        self.r("Passi falliti: %d. Riavvii dell'agente: %d." % (self.falliti, self.riavvii))
        self.r.dati["riavvii"] = self.riavvii
        self.r.chiudi()
        try:
            pacco = registri.raccogli(7, self.a.ponte, self.a.scanner,
                                      os.path.join(QUI, "impostazioni.json"))
            print("")
            print("Zip da mandare (registri e questo collaudo):")
            print("  %s" % pacco)
        except Exception as exc:                        # noqa: BLE001
            print("Zip dei registri non preparato: %s" % exc)
        # con il collegamento remoto parte da solo: niente chiavetta
        try:
            import remoto
            cfg = remoto.carica()
            if cfg is not None:
                print("Invio dei registri con il collegamento remoto...")
                print("  inviato: %s" % remoto.invia(cfg, 1, self.r.dati.get("tipo", "collaudo")))
        except Exception as exc:                        # noqa: BLE001
            print("Invio remoto non riuscito: %s" % exc)


# ---------------------------------------------------------------------------
# senza pellicola
# ---------------------------------------------------------------------------

def senza_pellicola(a) -> int:
    k = Banco(a, "auto-senza")
    r = k.r
    r("COLLAUDO AUTOMATICO SENZA PELLICOLA - %s" % datetime.datetime.now())
    print("Il percorso della pellicola deve essere VUOTO. Si parte fra 10 s;")
    print("Ctrl+C per fermare. Da qui in poi non serve nessuno.")
    time.sleep(10)
    if not k.collega():
        r.avviso("l'agente non e' pronto: collaudo non fatto")
        k.chiudi()
        return 1

    k.titolo("Stato della macchina")
    k.passo("stato", lambda: k.cmd("stato", 15))
    k.passo("temperatura", lambda: k.cmd("temperatura", 15))
    k.passo("tensioni", lambda: k.cmd("tensioni", 15))
    k.passo("sensori", lambda: k.cmd("sensori", 15))
    k.passo("offset del formato 135", lambda: k.cmd("offsetxy_0", 15))
    k.passo("pellicola secondo l'SDK", lambda: k.b.pellicola_sdk())
    k.passo("ultimo caricamento", lambda: k.b.azzerato_alle())

    k.titolo("Ponte: tempi di risposta")

    def giri():
        tempi = []
        for _ in range(20):
            t0 = time.time()
            k.b._send_una("ping", attesa=ATTESA_MORTO)
            tempi.append(time.time() - t0)
        return {"medio_ms": round(1000 * float(np.mean(tempi)), 1),
                "massimo_ms": round(1000 * float(np.max(tempi)), 1)}
    k.passo("20 ping", giri)

    k.titolo("Azzeramento e calibrazione della macchina")

    def azzera():
        t0 = time.time()
        k.cmd("azzera", 180)
        k.aspetta_pronto()
        return {"secondi_fino_a_pronto": round(time.time() - t0, 1),
                "stato": k.stato_sdk()}
    k.passo("azzeramento (Gretag Reset) fino a pronto", azzera, recupero=k.aspetta_pronto)
    k.passo("esposizione: tempi calibrati", lambda: k.cmd("esposizione_auto", 15))
    k.passo("calibrazione della macchina (calibrate_all)", lambda: k.cmd("calibrasdk", 600),
            recupero=k.aspetta_pronto)

    k.titolo("Letture a finestra vuota")
    medie = {}
    for ch in ("R", "G", "B"):
        for e in (0.125, 0.25, 0.5, 1.0, 2.0):
            def leggi(ch=ch, e=e):
                img = k.leggi(ch, e)
                m = cs.misura(img)
                medie[(ch, e)] = m
                if e == 0.25:
                    k.salva("vuota_%s_025" % ch, img, 4095)
                    np.save(os.path.join(r.cartella, "profilo_colonne_%s_025.npy" % ch),
                            img.astype(np.float32).mean(axis=0))
                return {k2: round(v, 2) if isinstance(v, float) else v for k2, v in m.items()
                        if k2 in ("media", "p1", "p50", "p99", "saturati_pct", "rumore",
                                  "colonne_var_pct", "righe_var_pct")}
            k.passo("%s a %.3gx" % (ch, e), leggi)
    for ch in ("R", "G", "B"):
        punti = [(e, medie[(ch, e)]["media"]) for e in (0.125, 0.25, 0.5, 1.0, 2.0)
                 if (ch, e) in medie and medie[(ch, e)]["saturati_pct"] < 1]
        if len(punti) >= 2:
            (e0, m0), (e1, m1) = punti[0], punti[-1]
            r("  linearita' %s: %.3gx -> %.3gx, medie %.0f -> %.0f (rapporto %.2f, atteso %.2f)"
              % (ch, e0, e1, m0, m1, m1 / max(m0, 1), e1 / e0))
    r.dati["finestra_vuota"] = dict(("%s@%g" % kv[0], kv[1]) for kv in medie.items())

    k.titolo("Buio, rumore, infrarosso")
    k.passo("G al buio (bit di buio)", lambda: cs.misura(k.leggi("G", 1.0, buio=True))["media"],
            tentativi=1, informativo=True)

    def rumore():
        imgs = [k.leggi("G", 0.25).astype(np.float64) for _ in range(4)]
        d = np.std(imgs[1] - imgs[0]) / np.sqrt(2)
        return {"rumore_temporale": round(float(d), 2),
                "deriva_media": round(float(np.mean(imgs[-1]) - np.mean(imgs[0])), 2)}
    k.passo("4 letture uguali di G (rumore nel tempo)", rumore)

    def ir():
        img = k.leggi("IR", 1.0)
        k.salva("vuota_IR_1", img, 4095)
        return {kk: round(v, 2) for kk, v in cs.misura(img).items()
                if kk in ("media", "p99", "saturati_pct")}
    k.passo("IR (canale 3) a 1x", ir, tentativi=1, informativo=True)

    k.titolo("Tetto d'esposizione")
    for e in (4.0, 6.0, 8.0, 12.0):
        k.passo("G a %gx" % e, lambda e=e: round(cs.misura(k.leggi("G", e))["media"], 1),
                tentativi=1, informativo=True)

    k.titolo("Lettura unica (R, G, B in una scan_raw)")

    def unica():
        k.b._lettura_unica = None
        piani = k.b.capture_frame(["R", "G", "B"], {"R": 0.25, "G": 0.25, "B": 0.25})
        if piani is None:
            return "non disponibile"
        return dict((c, round(float(p.mean()), 1)) for c, p in piani.items())
    k.passo("grabm R G B a 0.25x", unica)
    k.passo("grabm R G B IR (canale 5)",
            lambda: k.cmd("grabm_0_0.250_0.250_0.250_0.250", 60), tentativi=1,
            informativo=True)

    k.titolo("Pressore senza pellicola")
    k.passo("su, gia' su", lambda: k.cmd("pressore_su", 30), tentativi=1, informativo=True)
    k.passo("giu'", lambda: k.cmd("pressore_giu", 30), tentativi=1)
    k.passo("G con il pressore giu'", lambda: round(cs.misura(k.leggi("G", 0.25))["media"], 1),
            tentativi=1)
    k.passo("su dopo la lettura", lambda: k.cmd("pressore_su", 30), recupero=lambda: time.sleep(2))
    k.passo("G con il pressore su", lambda: round(cs.misura(k.leggi("G", 0.25))["media"], 1),
            tentativi=1)
    k.passo("giu' e subito su", lambda: (k.cmd("pressore_giu", 30), k.cmd("pressore_su", 30)),
            recupero=k.pressore_su)
    k.passo("stato dopo il pressore", lambda: k.stato_sdk())

    k.titolo("Fuoco")
    k.passo("fuoco nominale", lambda: k.cmd("fuoco_nominale", 120), tentativi=1)
    k.passo("autofocus rapido (senza pellicola puo' fallire)",
            lambda: k.cmd("fuoco_rapido", 180), tentativi=1, informativo=True)
    k.passo("fuoco nominale di nuovo", lambda: k.cmd("fuoco_nominale", 120))

    k.titolo("Movimenti e rifiuti senza pellicola")
    k.passo("formato 135", lambda: k.cmd("formato_0", 60))
    k.passo("regolazione fine +100 passi (1 mm)", lambda: k.cmd("regola_100", 60), tentativi=1)
    k.passo("regolazione fine -100 passi", lambda: k.cmd("regola_-100", 60), tentativi=1)
    k.passo("codice DX senza pellicola (atteso: errore 12)", lambda: k.cmd("dx", 30),
            tentativi=1, informativo=True)
    k.passo("stato dopo il DX", lambda: k.stato_sdk())
    k.passo("G dopo il DX", lambda: round(cs.misura(k.leggi("G", 0.25))["media"], 1), tentativi=1)
    k.passo("fotogramma successivo senza pellicola (atteso: errore)",
            lambda: k.cmd("prossimo", 60), tentativi=1, informativo=True)
    k.passo("carrello in attesa della pellicola", lambda: k.cmd("carica_1", 60))
    k.passo("pellicola secondo l'SDK", lambda: k.b.pellicola_sdk())
    k.passo("video del programma originale", lambda: k.cmd("video", 60), tentativi=1)

    k.titolo("Fine")
    k.passo("stato finale", lambda: k.cmd("stato", 15))
    k.chiudi()
    return 0 if not k.falliti else 2


# ---------------------------------------------------------------------------
# con pellicola
# ---------------------------------------------------------------------------

def con_pellicola(a) -> int:
    k = Banco(a, "auto-pellicola")
    r = k.r
    r("COLLAUDO AUTOMATICO CON PELLICOLA - %s" % datetime.datetime.now())
    if not k.collega():
        r.avviso("l'agente non e' pronto: collaudo non fatto")
        k.chiudi()
        return 1
    k.passo("pressore su (partenza)", k.pressore_su, tentativi=1)
    k.passo("formato 135", lambda: k.cmd("formato_0", 60))
    if not k.b.pellicola_sdk():
        k.passo("carrello in attesa della pellicola", lambda: k.cmd("carica_1", 60))
        print("")
        print("  >>> Inserire una striscia di negativo (non di un cliente). <<<")
        print("  Il collaudo parte da solo appena il carrello la prende.")
        fine = time.time() + 600
        while time.time() < fine and not k.b.pellicola_sdk():
            time.sleep(0.5)
    if not k.b.pellicola_sdk():
        r.avviso("nessuna pellicola in 10 minuti: collaudo non fatto")
        k.chiudi()
        return 1
    r("Pellicola nel carrello: da qui fa tutto da solo.")

    def mezzo_indietro():
        k.pressore_su()
        k.cmd("regola_-50", 60)

    k.titolo("Codice DX appena inserita")
    k.passo("DX all'inserimento", lambda: k.cmd("dx", 30), tentativi=1)
    k.passo("stato dopo il DX", lambda: k.stato_sdk())
    k.passo("G subito dopo il DX", lambda: round(cs.misura(k.leggi("G", 1.0))["media"], 1),
            recupero=k.aspetta_pronto)

    k.titolo("Prima posizione (di solito la coda)")
    livelli = {}
    for ch in ("R", "G", "B"):
        def l(ch=ch):
            img = k.leggi(ch, 1.0)
            livelli[ch] = float(np.percentile(img, 99))
            k.salva("pos0_%s" % ch, img, 4095)
            return round(float(img.mean()), 1)
        k.passo("%s a 1x" % ch, l, recupero=k.aspetta_pronto)

    k.titolo("Fotogrammi")
    guadagni = {"R": 1.0, "G": 1.0, "B": 1.0}
    for n in range(1, 5):
        k.passo("fotogramma successivo (%d)" % n, lambda: k.cmd("prossimo", 90),
                recupero=mezzo_indietro)
        k.passo("DX dopo il movimento (%d)" % n, lambda: k.cmd("dx", 30), tentativi=1)
        if n == 1:
            def misura_esp():
                for ch in ("R", "G", "B"):
                    p99 = float(np.percentile(k.leggi(ch, 1.0), 99))
                    guadagni[ch] = float(np.clip(0.8 * 4095 / max(p99, 1.0), 0.25, 4.0))
                return dict((c, round(v, 2)) for c, v in guadagni.items())
            k.passo("esposizione per canale (80% della scala)", misura_esp,
                    recupero=k.aspetta_pronto)

        def rgb(n=n):
            piani = k.b.capture_frame(["R", "G", "B"], dict(guadagni))
            if piani is None:
                piani = dict((c, k.leggi(c, guadagni[c])) for c in ("R", "G", "B"))
                modo = "un canale per volta"
            else:
                modo = "lettura unica"
            for c, p in piani.items():
                k.salva("fot%d_%s" % (n, c), p, 4095)
            return {"modo": modo, "medie": dict((c, round(float(p.mean()), 1))
                                                for c, p in piani.items()),
                    "nitidezza_G": round(nitidezza(piani["G"]), 4)}
        k.passo("R G B del fotogramma %d" % n, rgb, recupero=k.aspetta_pronto)

    k.titolo("Pressore sulla pellicola (nitidezza)")

    def confronto():
        su = [nitidezza(k.leggi("G", guadagni["G"])) for _ in range(2)]
        k.cmd("pressore_giu", 30)
        try:
            giu = [nitidezza(k.leggi("G", guadagni["G"])) for _ in range(2)]
        finally:
            k.cmd("pressore_su", 30)
        return {"su": [round(x, 4) for x in su], "giu": [round(x, 4) for x in giu]}
    k.passo("nitidezza G pressore su e giu'", confronto, recupero=k.pressore_su)
    k.passo("su con il pressore gia' su", lambda: k.cmd("pressore_su", 30), tentativi=1,
            informativo=True)

    k.titolo("Fuoco sulla pellicola")
    for metodo in ("nominale", "rapido", "gi"):
        def fuoco(metodo=metodo):
            k.cmd("fuoco_%s" % metodo, 240)
            return round(nitidezza(k.leggi("G", guadagni["G"])), 4)
        k.passo("fuoco %s, poi nitidezza" % metodo, fuoco, recupero=k.aspetta_pronto)

    k.titolo("Tetto d'esposizione sulla pellicola")
    for e in (2.0, 4.0, 8.0, 12.0):
        k.passo("G a %gx" % e, lambda e=e: round(float(k.leggi("G", e).mean()), 1),
                tentativi=1, informativo=True)

    k.titolo("Ritorno e regolazione fine")
    k.passo("fotogramma precedente", lambda: k.cmd("precedente", 90), recupero=mezzo_indietro)
    k.passo("DX dopo il ritorno", lambda: k.cmd("dx", 30), tentativi=1)
    k.passo("regolazione +100 passi", lambda: k.cmd("regola_100", 60), tentativi=1)
    k.passo("regolazione -100 passi", lambda: k.cmd("regola_-100", 60), tentativi=1)

    k.titolo("Sonde del percorso capture e del Digital ICE (muovono la pellicola)")
    k.passo("capture senza ICE", lambda: k.cmd("sonda_capture_0_0", 180), tentativi=1)
    k.passo("capture con ICE (ASF)", lambda: k.cmd("sonda_capture_0_1", 180), tentativi=1)
    k.passo("Digital ICE diretto", lambda: k.cmd("sonda_ice_0", 240), tentativi=1)

    k.titolo("Fine")
    k.passo("pressore su", k.pressore_su, tentativi=1)
    k.passo("espulsione dal davanti", lambda: k.cmd("espelli", 120), recupero=k.pressore_su)
    k.passo("stato finale", lambda: k.cmd("stato", 15))
    k.chiudi(sonde=True)
    return 0 if not k.falliti else 2


def main(argv=None):
    ap = argparse.ArgumentParser(description="Collaudi automatici dello scanner")
    ap.add_argument("prova", choices=("senza", "pellicola"))
    ap.add_argument("--ponte", default=cs.PONTE)
    ap.add_argument("--scanner", default=r"C:\MasterflexD\Scanner")
    ap.add_argument("--simula", action="store_true",
                    help="l'agente, se va avviato, parte simulato")
    a = ap.parse_args(argv)
    try:
        return {"senza": senza_pellicola, "pellicola": con_pellicola}[a.prova](a)
    except KeyboardInterrupt:
        print("\nfermato dall'operatore")
        if cs.Rapporto.aperto is not None:
            cs.Rapporto.aperto.avviso("fermato dall'operatore")
            cs.Rapporto.aperto.chiudi()
        return 1
    except Emergenza as exc:
        print("\nSTOP EMERGENZA: %s" % exc)
        if cs.Rapporto.aperto is not None:
            cs.Rapporto.aperto.avviso("STOP EMERGENZA: %s" % exc)
            cs.Rapporto.aperto.chiudi()
        return 1


if __name__ == "__main__":
    sys.exit(main())
