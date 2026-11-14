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

    python collaudo_auto.py ir         [--ponte ...] [--scanner ...]
        Il canale INFRAROSSO, prima di cambiare come lo legge il rullo
        (7/11/2026: nel rullo delle 19:01 l'IR era saturo su tutti i
        fotogrammi e il Digital ICE non ha pulito niente). Finestra vuota,
        poi una striscia: IR a x1, x0,5, x0,25, x0,125 (saturazione, se
        porta immagine), la maschera nostra, il Digital ICE della Kodak, e
        l'ipotesi da verificare: il codice DX (get_media_info) cambia i
        tempi dell'SDK; poi lo stesso fotogramma con tutti i motori Digital
        ICE affiancati (confronto_ice.png). Niente cambia nelle impostazioni.

    python collaudo_auto.py dx         [--ponte ...] [--scanner ...]
        Il CODICE DX: chiede che pellicola e' la striscia (marca, nome,
        sensibilita'), legge il DX all'ingresso e dopo tre avanzamenti (ogni
        volta seguito da una capture senza ICE, che rimette l'SDK nello
        stato 0), salva tutta la risposta dell'SDK e ci cerca i numeri DX
        attesi dall'elenco I3A. Serve a sapere dove sta il DX su questa
        macchina (dal programma originale si aspettava a MediaInfo +0x10).

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
            t0 = time.monotonic()
            try:
                valore = fn()
                dt = time.monotonic() - t0
                breve = valore if isinstance(valore, (int, float, str, bool)) or valore is None \
                    else json.dumps(valore, default=str)[:300]
                self.r("  [ok] %-44s %5.1f s  %s" % (nome, dt, "" if breve is None else breve))
                self.r.dati["passi"].append({"passo": nome, "esito": "ok", "secondi": round(dt, 2),
                                             "tentativo": k + 1, "valore": valore})
                return valore
            except Emergenza:
                raise
            except Exception as exc:                    # noqa: BLE001
                dt = time.monotonic() - t0
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

# ---------------------------------------------------------------------------
# sensore intero (8/11/2026)
# ---------------------------------------------------------------------------
# La finestra del formato e' 2100x1400 dall'offset XY tarato (47, 76): come il
# programma originale, che sul 135 taglia ~1 mm per lato in larghezza e
# ~0,65 mm in altezza (16,19 um di pellicola a pixel). Il sensore, con pixel
# da 6,8 um, e' con ogni probabilita' un Kodak KAF-3200 (2184x1472 attivi):
# letto tutto darebbe ~35,4x23,8 mm. Qui si misura davvero: rettangoli
# crescenti da (0, 0), quali accetta il firmware e dove finisce la luce.

RETTANGOLI_SENSORE = ((0, 0, 2184, 1472), (0, 0, 2200, 1500),
                      (0, 0, 2268, 1512), (0, 0, 2304, 1536))
FILE_SENSORE = os.path.join(QUI, "sensore.json")


def leggi_rettangolo(k, canale: str, esposizione: float, rett) -> np.ndarray:
    nome = k.b._send("grab_%s_0_%.3f_f%d.%d.%d.%d" % ((canale, esposizione) + tuple(rett)),
                     attesa=k.b.ATTESA_ACQUISIZIONE)
    return k.b._leggi(nome)


def limiti_luce(img: np.ndarray, soglia: float = 0.15):
    """Prima e ultima colonna, prima e ultima riga con luce (oltre il 15%
    del livello al centro): fuori ci sono i pixel coperti del sensore."""
    a = np.asarray(img, dtype=np.float64)
    h, w = a.shape
    centro = float(np.median(a[h // 3:2 * h // 3, w // 3:2 * w // 3]))
    col = a.mean(axis=0)
    riga = a.mean(axis=1)
    cs_ = np.flatnonzero(col > soglia * centro)
    rs_ = np.flatnonzero(riga > soglia * centro)
    if not cs_.size or not rs_.size:
        return None
    return int(cs_[0]), int(cs_[-1]), int(rs_[0]), int(rs_[-1]), round(centro, 1)


def prova_sensore(k, pellicola: bool) -> None:
    """Senza pellicola misura il rettangolo illuminato e lo scrive in
    software/sensore.json (lo usa il programma: Macchina, Area del
    sensore). Con la pellicola legge il rettangolo misurato su un
    fotogramma: nelle immagini si vede se compaiono tutti e quattro i
    bordi del fotogramma."""
    r = k.r
    dati = {}
    r.dati["sensore"] = dati
    e = 1.0 if pellicola else 0.25
    if pellicola:
        try:
            with open(FILE_SENSORE, encoding="utf-8") as f:
                misurato = json.load(f).get("rettangolo")
        except (IOError, OSError, ValueError):
            misurato = None
        prove = [tuple(misurato)] if misurato else list(RETTANGOLI_SENSORE)
        dati["formato"] = k.passo("G a %gx, finestra del formato" % e,
                                  lambda: (k.salva("sensore_formato_G", k.leggi("G", e), 4095),
                                           "salvata")[1])
    else:
        prove = list(RETTANGOLI_SENSORE)
        dati["formato"] = k.passo("G a %gx, finestra del formato (2100x1400)" % e,
                                  lambda: limiti_luce(k.leggi("G", e)))
    accettati = []
    for rett in prove:
        def l(rett=rett):
            img = leggi_rettangolo(k, "G", e, rett)
            k.salva("sensore_%dx%d_G" % (rett[2], rett[3]), img, 4095)
            lim = limiti_luce(img)
            accettati.append((rett, img.shape, lim))
            return {"forma": list(img.shape), "luce": lim}
        dati["%dx%d" % rett[2:]] = k.passo("G a %gx, sensore da (%d,%d) %dx%d" % ((e,) + tuple(rett)),
                                         l, tentativi=1, informativo=True)
    if pellicola or not accettati:
        if not accettati:
            r("  -> nessun rettangolo piu' grande accettato: resta la finestra del formato")
        return
    rett, forma, lim = max(accettati, key=lambda t: t[0][2] * t[0][3])
    if lim is None:
        r("  -> il rettangolo piu' grande non ha luce misurabile: non si scrive niente")
        return
    c0, c1, r0, r1, centro = lim
    # due pixel di margine dalla luce, lati pari (le risoluzioni ridotte)
    x0, y0 = rett[0] + c0 + 2, rett[1] + r0 + 2
    w = (c1 - c0 + 1 - 4) // 2 * 2
    h = (r1 - r0 + 1 - 4) // 2 * 2
    bordo = []
    if c1 >= forma[1] - 2:
        bordo.append("a destra")
    if r1 >= forma[0] - 2:
        bordo.append("in basso")
    misura = {"rettangolo": [x0, y0, w, h], "data": datetime.date.today().isoformat(),
              "collaudo": os.path.basename(r.cartella), "letto": list(rett),
              "luce_al_centro": centro,
              "nota": ("la luce arriva fino al bordo %s del rettangolo letto: il "
                       "sensore potrebbe continuare oltre" % " e ".join(bordo)) if bordo else ""}
    dati["misura"] = misura
    mm = 6.8 / 0.420026 / 1000.0
    r("  -> sensore illuminato: da colonna %d, riga %d, %dx%d pixel = %.1f x %.1f mm "
      "sul 135 (la finestra del formato: 2100x1400 = %.1f x %.1f mm)"
      % (x0, y0, w, h, w * mm, h * mm, 2100 * mm, 1400 * mm))
    if bordo:
        r("     " + misura["nota"])
    try:
        with open(FILE_SENSORE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(misura, f, indent=1)
        os.replace(FILE_SENSORE + ".tmp", FILE_SENSORE)
        r("     scritto in %s: si sceglie in Macchina, Area del sensore" % FILE_SENSORE)
    except (IOError, OSError) as exc:
        r("     %s non scritto: %s" % (FILE_SENSORE, exc))


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

    k.titolo("Sensore intero (oltre la finestra 2100x1400 del programma originale)")
    prova_sensore(k, pellicola=False)

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
    # senza pellicola l'SDK rifiuta ogni fuoco con l'errore 12 (riserva,
    # 7/11/2026): si annota, il fuoco vero si prova con la pellicola
    k.passo("fuoco nominale (senza pellicola: errore 12)",
            lambda: k.cmd("fuoco_nominale", 120), tentativi=1, informativo=True)

    k.titolo("Movimenti e rifiuti senza pellicola")
    k.passo("formato 135", lambda: k.cmd("formato_0", 60))
    k.passo("regolazione fine +100 passi (senza pellicola: errore 3)",
            lambda: k.cmd("regola_100", 60), tentativi=1, informativo=True)
    k.passo("codice DX senza pellicola (atteso: errore 12)", lambda: k.cmd("dx", 30),
            tentativi=1, informativo=True)
    k.passo("stato dopo il DX", lambda: k.stato_sdk())
    k.passo("G dopo il DX", lambda: round(cs.misura(k.leggi("G", 0.25))["media"], 1), tentativi=1)
    # Niente piu' "fotogramma successivo senza pellicola" (14/11/2026): il
    # trasporto gira a vuoto 15 s, risponde ERR_MOVE, e alla riapertura
    # successiva dell'SDK lo scanner non finiva piu' l'inizializzazione
    # (stato 10, evento 37509) fino a spegnere e riaccendere la Masterflex.
    k.passo("carrello in attesa della pellicola", lambda: k.cmd("carica_1", 60))
    k.passo("pellicola secondo l'SDK", lambda: k.b.pellicola_sdk())
    k.passo("video del programma originale", lambda: k.cmd("video", 60), tentativi=1)

    # La lettura unica (scan_raw canale 4) non si prova piu': sulla riserva
    # (7/11/2026) blocca l'SDK per 60 s e poi la testa rifiuta ogni lettura.
    # Al suo posto, con la pellicola, il percorso capture dell'originale.

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

    # Il DX (get_media_info) porta l'SDK nello stato 1, dove scan_raw e'
    # rifiutato dal firmware (46/47/42, riserva 7/11/2026) finche' una
    # capture non lo riporta a 0. Percio' qui niente DX prima delle letture:
    # lo si legge in fondo, prima delle sonde capture.

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

    k.titolo("Sensore intero sulla pellicola")
    prova_sensore(k, pellicola=True)

    k.titolo("Digital ICE della Kodak (libreria dell'SDK)")
    # 7/11/2026 (16:34): la libreria chiamata direttamente ha trovato 17679
    # pixel difettosi con i pixel intercalati RGBI. Qui la strada vera:
    # ice_kodak.pulisci, come nell'elaborazione, sul fotogramma in finestra.

    def dice():
        from filmscan import ice_kodak
        from filmscan.acquire import RawScan, ScanMetadata
        if not ice_kodak.disponibile():
            raise RuntimeError("DMLDICELib.dll non trovata")
        piani = dict((c, k.leggi(c, guadagni.get(c, 1.0))) for c in ("R", "G", "B"))
        piani["IR"] = k.leggi("IR", 1.0)
        r = RawScan(ScanMetadata(time.time(), "135-full", "collaudo", 1569, 16,
                                 ["R", "G", "B", "IR"], [1.0], 1, 0, 0.0, 0.0))
        for c, p in piani.items():
            r.add(c, np.asarray(p, dtype=np.float32) / 4095.0)
        nuovo, esito = ice_kodak.pulisci(r)
        for c in ("R", "G", "B"):
            dopo = nuovo.planes[c][0] * 4095.0
            k.salva("dice_prima_%s" % c, piani[c], 4095)
            k.salva("dice_dopo_%s" % c, dopo, 4095)
            k.salva("dice_differenza_%s" % c, np.abs(dopo - piani[c]), 200)
        k.salva("dice_IR", piani["IR"], 4095)
        return esito
    k.passo("Digital ICE Kodak sul fotogramma", dice, tentativi=1, recupero=k.aspetta_pronto)

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
    # il 7/11/2026 la regolazione fine e' stata rifiutata (codice 3) dopo
    # "precedente": si annota senza ripetere
    k.passo("regolazione +100 passi", lambda: k.cmd("regola_100", 60), tentativi=1,
            informativo=True)
    k.passo("regolazione -100 passi", lambda: k.cmd("regola_-100", 60), tentativi=1,
            informativo=True)

    k.titolo("Capture del programma originale (DX + capture)")
    # Riserva, 7/11/2026: la capture funziona solo con il codice DX letto
    # subito prima (stato 1): alle 14:54 un fotogramma RGB in 4,9 s; alle
    # 16:08, con il DX rifiutato (ERR_MOVE dopo "precedente"), tre attese di
    # 60 s e il collaudo sembrava fermo. Adesso il comando "cattura" legge
    # il DX da se' e alla prima risposta "stato non valido" si ferma. Prima
    # di ogni capture un fotogramma avanti: e' il caso del rullo.
    for n in range(1, 4):
        k.passo("fotogramma successivo (capture %d)" % n, lambda: k.cmd("prossimo", 90),
                recupero=mezzo_indietro)

        def cattura(n=n, asf=False):
            piani = k.b.cattura_originale(asf)
            for c, p in piani.items():
                k.salva("capture%d_%s" % (n, c), p, 4095)
            return {"medie": dict((c, round(float(p.mean()), 1)) for c, p in piani.items()),
                    "massimi": dict((c, int(p.max())) for c, p in piani.items()),
                    "nitidezza_G": round(nitidezza(piani["G"]), 4)}
        k.passo("capture %d (DX + capture)" % n, cattura, tentativi=2,
                recupero=lambda: k.cmd("prossimo", 90))
        k.passo("stato dopo la capture %d" % n, lambda: k.stato_sdk())
    k.passo("G con scan_raw dopo la capture", lambda: round(float(k.leggi("G", 1.0).mean()), 1),
            tentativi=1, informativo=True)

    k.titolo("Digital ICE dell'SDK (capture con ASF)")
    k.passo("fotogramma successivo (ICE)", lambda: k.cmd("prossimo", 90), recupero=mezzo_indietro)

    def con_ice():
        piani = k.b.cattura_originale(True)
        for c, p in piani.items():
            k.salva("ice_%s" % c, p, 4095)
        return {"medie": dict((c, round(float(p.mean()), 1)) for c, p in piani.items()),
                "nitidezza_G": round(nitidezza(piani["G"]), 4)}
    k.passo("capture con Digital ICE", con_ice, tentativi=2,
            recupero=lambda: k.cmd("prossimo", 90))
    k.passo("stesso fotogramma senza ICE", lambda: (k.cmd("precedente", 90), k.cmd("prossimo", 90),
                                                    cattura(9, False))[2],
            tentativi=1, informativo=True)

    k.titolo("Fine")
    k.passo("pressore su", k.pressore_su, tentativi=1)
    k.passo("espulsione dal davanti", lambda: k.cmd("espelli", 120), recupero=k.pressore_su)
    k.passo("stato finale", lambda: k.cmd("stato", 15))
    k.chiudi()     # le sonde le mette nello zip registri.raccogli (sonde/)
    return 0 if not k.falliti else 2


# ---------------------------------------------------------------------------
# canale infrarosso (Digital ICE)
# ---------------------------------------------------------------------------

FONDO = 4095.0              # 12 bit sulla macchina (il simulatore ne da' 16)


def fondo_di(img) -> float:
    return FONDO if float(np.max(img)) <= FONDO + 0.5 else 65535.0


def numeri_ir(img) -> dict:
    """I numeri che dicono se un piano IR e' usabile: saturazione, livelli,
    e se porta immagine (deviazione lungo le colonne, in conteggi: un piano
    saturo ha un solo valore per colonna, 0)."""
    a = np.asarray(img, dtype=np.float64)
    c = a[::4, ::8]
    fondo = fondo_di(a)
    return {"fondo": fondo, "media": round(float(a.mean()), 1),
            "p1": round(float(np.percentile(a, 1)), 1),
            "p50": round(float(np.percentile(a, 50)), 1),
            "p99": round(float(np.percentile(a, 99)), 1),
            "saturi_pct": round(float((a >= fondo - 5).mean()) * 100, 2),
            "dev_colonne": round(float(np.median(c.std(axis=0))), 2)}


def usabile(n: dict) -> bool:
    return n["saturi_pct"] < 0.5 and n["p99"] < 0.95 * n["fondo"] and n["dev_colonne"] > 1.0


def prova_ir(a) -> int:
    k = Banco(a, "auto-ir")
    r = k.r
    r("COLLAUDO DEL CANALE INFRAROSSO - %s" % datetime.datetime.now())
    r("Non cambia impostazioni: legge, misura e salva le immagini.")
    if not k.collega():
        r.avviso("l'agente non e' pronto: collaudo non fatto")
        k.chiudi()
        return 1
    ir_dati = {}
    r.dati["ir"] = ir_dati

    def stretto(nome, img):
        """PNG con i livelli tirati fra p0,5 e p99,5: la polvere si vede."""
        try:
            x = np.asarray(img, dtype=np.float64)
            lo, hi = np.percentile(x, [0.5, 99.5])
            cs.salva_png(cs.a8bit(x - lo, max(hi - lo, 1.0)),
                         os.path.join(r.cartella, nome + ".png"))
        except Exception:                               # noqa: BLE001
            pass

    k.passo("pressore su (partenza)", k.pressore_su, tentativi=1)
    k.passo("formato 135", lambda: k.cmd("formato_0", 60))
    # come il rullo: tempi calibrati dell'SDK
    k.passo("tempi calibrati dell'SDK (come il rullo)", lambda: k.cmd("esposizione_auto", 15))

    vuota = {}
    if not k.b.pellicola_sdk():
        k.titolo("Finestra vuota (il riferimento)")
        for e in (1.0, 0.5):
            def l(e=e):
                img = k.leggi("IR", e)
                vuota[e] = np.asarray(img, dtype=np.float64)
                stretto("vuota_IR_%gx" % e, img)
                return numeri_ir(img)
            ir_dati["vuota_IR_%gx" % e] = k.passo("IR a %gx, finestra vuota" % e, l,
                                                 recupero=k.aspetta_pronto)
        ir_dati["vuota_G_1x"] = k.passo("G a 1x, finestra vuota",
                                        lambda: numeri_ir(k.leggi("G", 1.0)),
                                        recupero=k.aspetta_pronto)

        def fabbrica():
            k.cmd("esposizione_fabbrica", 15)
            try:
                return numeri_ir(k.leggi("IR", 1.0))
            finally:
                k.cmd("esposizione_auto", 15)
        ir_dati["vuota_IR_1x_fabbrica"] = k.passo("IR a 1x con i tempi di fabbrica", fabbrica,
                                                  tentativi=1, informativo=True)

        k.passo("carrello in attesa della pellicola", lambda: k.cmd("carica_1", 60))
        print("")
        print("  >>> Inserire una striscia di negativo a colori (non di un cliente). <<<")
        print("  Meglio se ha un po' di polvere o un graffio: e' quello che si cerca.")
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

    # due fotogrammi avanti: la prima posizione e' quasi sempre la coda
    for n in (1, 2):
        k.passo("fotogramma successivo (%d)" % n, lambda: k.cmd("prossimo", 90),
                recupero=mezzo_indietro)

    k.titolo("IR sulla pellicola, PRIMA del codice DX")
    letti = {}
    for e in (1.0, 0.5, 0.25, 0.125):
        def l(e=e):
            img = k.leggi("IR", e)
            letti[e] = np.asarray(img, dtype=np.float64)
            stretto("pellicola_IR_%gx" % e, img)
            return numeri_ir(img)
        ir_dati["pellicola_IR_%gx" % e] = k.passo("IR a %gx" % e, l, recupero=k.aspetta_pronto)
    for ch in ("R", "G", "B"):
        ir_dati["pellicola_%s_1x" % ch] = k.passo(
            "%s a 1x (per confronto)" % ch, lambda ch=ch: numeri_ir(k.leggi(ch, 1.0)),
            recupero=k.aspetta_pronto)
    migliore = None
    for e in (1.0, 0.5, 0.25, 0.125):
        n = ir_dati.get("pellicola_IR_%gx" % e)
        if n and usabile(n):
            migliore = e
            break
    ir_dati["esposizione_ir_migliore"] = migliore
    r("  -> esposizione IR piu' alta senza saturare: %s"
      % ("x%g" % migliore if migliore else "NESSUNA (tutte sature o senza immagine)"))

    k.titolo("Maschera difetti nostra (irclean) sull'IR buono")

    def maschera():
        from filmscan import irclean
        if migliore is None:
            raise RuntimeError("nessuna lettura IR usabile")
        ir = letti[migliore]
        if 1.0 in vuota:
            norm = ir / np.clip(vuota[1.0] * migliore, 1.0, None)
            rif = "diviso la finestra vuota"
        else:
            norm = ir / max(float(np.percentile(ir, 90)), 1.0)
            rif = "senza finestra vuota (pellicola gia' dentro)"
        m = irclean.defect_mask(norm.astype(np.float32))
        cs.salva_png((m * 255).astype(np.uint8), os.path.join(r.cartella, "maschera_ir.png"))
        stretto("ir_normalizzato", norm)
        return {"copertura_pct": round(irclean.mask_coverage(m), 3), "riferimento": rif,
                "senza_immagine_x1": bool(irclean.ir_senza_immagine(letti[1.0] / fondo_di(letti[1.0])))
                if 1.0 in letti else None}
    ir_dati["maschera"] = k.passo("maschera dei difetti", maschera, tentativi=1)

    k.titolo("Digital ICE della Kodak (libreria dell'SDK)")
    guadagni = {}

    def misura_esp():
        for ch in ("R", "G", "B"):
            img = k.leggi(ch, 1.0)
            p99 = float(np.percentile(img, 99))
            guadagni[ch] = float(np.clip(0.8 * fondo_di(img) / max(p99, 1.0), 0.25, 4.0))
        return dict((c, round(v, 2)) for c, v in guadagni.items())
    k.passo("esposizione R G B (80% della scala)", misura_esp, recupero=k.aspetta_pronto)

    def dice(e_ir, etichetta):
        from filmscan import ice_kodak
        from filmscan.acquire import RawScan, ScanMetadata
        if not ice_kodak.disponibile():
            raise RuntimeError("DMLDICELib.dll non trovata")
        piani = dict((c, k.leggi(c, guadagni.get(c, 1.0))) for c in ("R", "G", "B"))
        piani["IR"] = letti[e_ir] if e_ir in letti else k.leggi("IR", e_ir)
        raw = RawScan(ScanMetadata(time.time(), "135-full", "collaudo", 1569, 16,
                                   ["R", "G", "B", "IR"], [1.0], 1, 0, 0.0, 0.0))
        fondo = max(fondo_di(p) for p in piani.values())
        for c, p in piani.items():
            raw.add(c, np.asarray(p, dtype=np.float32) / fondo)
        t0 = time.monotonic()
        nuovo, esito = ice_kodak.pulisci(raw)
        esito["secondi_totali"] = round(time.monotonic() - t0, 1)
        h, w = piani["G"].shape
        y0, x0 = h // 2 - 300, w // 2 - 300
        for c in ("R", "G", "B"):
            prima = np.asarray(piani[c], dtype=np.float64)
            dopo = nuovo.planes[c][0] * fondo
            k.salva("dice_%s_prima_%s" % (etichetta, c), prima[y0:y0 + 600, x0:x0 + 600], fondo)
            k.salva("dice_%s_dopo_%s" % (etichetta, c), dopo[y0:y0 + 600, x0:x0 + 600], fondo)
            k.salva("dice_%s_differenza_%s" % (etichetta, c), np.abs(dopo - prima), 200)
        return esito

    def confronto_motori():
        """Lo stesso fotogramma con tutti i motori Digital ICE (8/11/2026):
        il grezzo resta nella cartella del collaudo (fotogramma_rgbi.npz,
        striscia di prova, non di un cliente) e le immagini affiancate in
        confronto_ice.png."""
        from filmscan import confronto_ice, registration
        if migliore is None:
            raise RuntimeError("nessuna lettura IR usabile")
        if not guadagni:
            misura_esp()
        piani = dict((c, np.asarray(k.leggi(c, guadagni.get(c, 1.0)), dtype=np.float32))
                     for c in ("R", "G", "B"))
        piani["IR"] = letti[migliore].astype(np.float32)
        fondo = max(fondo_di(p) for p in piani.values())
        np.savez_compressed(os.path.join(r.cartella, "fotogramma_rgbi.npz"),
                            dpi=1569, **dict((c, p.astype(np.uint16)) for c, p in piani.items()))
        norm = dict((c, p / fondo) for c, p in piani.items())
        allineati, sp = registration.register_channels(norm)
        esiti = confronto_ice.confronta(*[allineati[c] for c in ("R", "G", "B", "IR")],
                                        dpi=1569, cartella=r.cartella)
        esiti["spostamenti"] = dict((c, [round(v[0], 2), round(v[1], 2)]) for c, v in sp.items())
        return esiti
    ir_dati["confronto_motori"] = k.passo("confronto dei motori Digital ICE", confronto_motori,
                                          tentativi=1, recupero=k.aspetta_pronto)

    if migliore is not None:
        ir_dati["dice_ir_buono"] = k.passo(
            "Digital ICE Kodak con IR a x%g" % migliore,
            lambda: dice(migliore, "buono"), tentativi=1, recupero=k.aspetta_pronto)
    if migliore != 1.0:
        # quello che succedeva nel rullo: deve essere scartato, non pulire
        ir_dati["dice_ir_1x"] = k.passo(
            "Digital ICE Kodak con IR a x1 (come nel rullo)",
            lambda: dice(1.0, "x1"), tentativi=1, informativo=True)

    k.titolo("Ipotesi: il codice DX cambia i tempi dell'SDK")
    # Nel rullo delle 19:01 ogni fotogramma cominciava con una capture, che
    # legge il DX (get_media_info, stato 0 -> 1). Si confronta G e IR a x1
    # prima e dopo; poi una capture, che riporta lo stato a 0.
    ir_dati["dx"] = k.passo("codice DX", lambda: k.cmd("dx", 60), tentativi=1, informativo=True)
    ir_dati["stato_dopo_dx"] = k.passo("stato SDK dopo il DX", lambda: k.stato_sdk(), tentativi=1)
    ir_dati["dopo_dx_G_1x"] = k.passo("G a 1x dopo il DX", lambda: numeri_ir(k.leggi("G", 1.0)),
                                      tentativi=1, informativo=True)
    ir_dati["dopo_dx_IR_1x"] = k.passo("IR a 1x dopo il DX", lambda: numeri_ir(k.leggi("IR", 1.0)),
                                       tentativi=1, informativo=True)

    def cattura_senza():
        piani = k.b.cattura_originale(False)
        return dict((c, numeri_ir(p)) for c, p in piani.items())
    ir_dati["capture"] = k.passo("capture senza ICE (riporta lo stato a 0)", cattura_senza,
                                 tentativi=1, informativo=True)
    ir_dati["stato_dopo_capture"] = k.passo("stato SDK dopo la capture", lambda: k.stato_sdk(),
                                            tentativi=1)
    ir_dati["dopo_capture_G_1x"] = k.passo("G a 1x dopo la capture",
                                           lambda: numeri_ir(k.leggi("G", 1.0)),
                                           tentativi=1, informativo=True)
    ir_dati["dopo_capture_IR_1x"] = k.passo("IR a 1x dopo la capture",
                                            lambda: numeri_ir(k.leggi("IR", 1.0)),
                                            tentativi=1, informativo=True)

    k.titolo("Capture con il Digital ICE dell'SDK (nel rullo: codice 5, parametro)")
    for ris in (0, 1):
        ir_dati["capture_ice_ris%d" % ris] = k.passo(
            "capture con ICE, risoluzione %d" % ris,
            lambda ris=ris: k.cmd("cattura_%d_1" % ris, 120), tentativi=1, informativo=True)

    k.titolo("Riassunto")
    prima = ir_dati.get("pellicola_IR_1x") or {}
    dopo = ir_dati.get("dopo_dx_IR_1x") or {}
    vu = ir_dati.get("vuota_IR_1x") or {}
    if vu:
        r("  IR a x1, finestra vuota: media %s, saturi %s%%" % (vu.get("media"), vu.get("saturi_pct")))
    if prima:
        r("  IR a x1, pellicola, prima del DX: media %s, saturi %s%%, immagine %s"
          % (prima.get("media"), prima.get("saturi_pct"),
             "si'" if prima.get("dev_colonne", 0) > 1 else "NO"))
    if dopo:
        r("  IR a x1, pellicola, dopo il DX:   media %s, saturi %s%%, immagine %s"
          % (dopo.get("media"), dopo.get("saturi_pct"),
             "si'" if dopo.get("dev_colonne", 0) > 1 else "NO"))
    gp, gd = ir_dati.get("pellicola_G_1x") or {}, ir_dati.get("dopo_dx_G_1x") or {}
    if gp and gd and gp.get("media"):
        r("  G a x1 dopo il DX / prima: %.2f (1,00 = il DX non cambia i tempi)"
          % (float(gd["media"]) / float(gp["media"])))
    r("  IR migliore: %s" % ("x%g" % migliore if migliore else "nessuno"))

    k.titolo("Fine")
    k.passo("pressore su", k.pressore_su, tentativi=1)
    k.passo("espulsione dal davanti", lambda: k.cmd("espelli", 120), recupero=k.pressore_su)
    k.chiudi()
    return 0 if not k.falliti else 2


# ---------------------------------------------------------------------------
# codice DX (8/11/2026)
# ---------------------------------------------------------------------------
# La lettura del DX riesce (get_media_info, stato SDK 1), ma il numero non lo
# troviamo: dove lo legge il programma originale (MediaInfo +0x10 -> byte 4 e
# 5) sulla riserva c'e' 0xffff. Qui si legge il DX di una pellicola NOTA piu'
# volte, si salva tutto e si cercano i suoi numeri DX (elenco I3A,
# pellicole_dx.py). Dopo ogni DX una capture senza ICE, che rimette l'SDK in
# grado di leggere i canali: e' la sequenza che userebbe il rullo.

def _byte(h: str) -> bytes:
    try:
        return bytes.fromhex(h or "")
    except ValueError:
        return b""


def candidati_dx(testo: str):
    """Le voci dell'elenco DX che corrispondono a quello che l'operatore ha
    scritto (tutte le parole nel nome; un numero e' la sensibilita')."""
    from filmscan import pellicole_dx
    parole = [w for w in testo.upper().replace(",", " ").split() if w]
    iso = [int(w) for w in parole if w.isdigit()]
    nomi = [w for w in parole if not w.isdigit()]
    trovati = []
    for (d1, d2), voce in pellicole_dx.TABELLA.items():
        nome, tipo, sens = voce[0].upper(), voce[1], voce[2]
        if nomi and not all(w in nome for w in nomi):
            continue
        if iso and sens not in iso:
            continue
        trovati.append(((d1, d2), voce[0], sens))
    return trovati


def cerca_dx(letture, attesi):
    """Dove compaiono i numeri DX attesi nelle letture salvate: come due
    byte di fila (dx1, dx2), come numero a 16 bit (dx1*16 + dx2, il valore
    del programma originale) e, a parte, quali coppie di byte vicine sono
    voci dell'elenco DX in tutte le letture dopo che la pellicola si e'
    mossa (per quando la pellicola non e' nota)."""
    from filmscan import pellicole_dx
    risultati = []
    for (d1, d2), nome, _ in attesi:
        aghi = {"byte": bytes([d1 & 0xff, d2 & 0xff]),
                "u16": int((d1 << 4) + d2).to_bytes(2, "little")}
        for k, l in enumerate(letture):
            for dove, h in [("media_info", l.get("media_info"))] + [
                    ("puntatore +%s" % off, v.get("dati")) for off, v in (l.get("puntatori") or {}).items()]:
                b = _byte(h)
                for tipo, ago in aghi.items():
                    i = b.find(ago)
                    while i >= 0:
                        risultati.append({"pellicola": nome, "dx": [d1, d2], "lettura": k,
                                          "dove": dove, "offset": "%02x" % i, "come": tipo})
                        i = b.find(ago, i + 1)
    # senza pellicola nota: coppie (b[i], b[i+1]) che sono voci dell'elenco,
    # stabili in tutte le letture dalla seconda in poi
    stabili = None
    for l in letture[1:]:
        b = _byte(l.get("media_info"))
        qui = set()
        for i in range(len(b) - 1):
            if (b[i], b[i + 1]) in pellicole_dx.TABELLA:
                qui.add((i, b[i], b[i + 1]))
        stabili = qui if stabili is None else (stabili & qui)
    ipotesi = [{"offset": "%02x" % i, "dx": [d1, d2], "pellicola": pellicole_dx.TABELLA[(d1, d2)][0]}
               for i, d1, d2 in sorted(stabili or [])]
    return risultati, ipotesi


def prova_dx(a) -> int:
    k = Banco(a, "auto-dx")
    r = k.r
    r("COLLAUDO DEL CODICE DX - %s" % datetime.datetime.now())
    print("")
    print("  Che pellicola e' la striscia di prova? Marca, nome e sensibilita',")
    print("  come sul rullino (es. Kodak Gold 200, Fujicolor Superia 400).")
    try:
        testo = input("  Pellicola (Invio se non la sai): ").strip()
    except EOFError:
        testo = ""
    attesi = candidati_dx(testo) if testo else []
    r("Pellicola dichiarata: %s" % (testo or "(non detta)"))
    for (d1, d2), nome, sens in attesi[:12]:
        r("  nell'elenco DX: %d-%d  %s (%d ISO)" % (d1, d2, nome, sens))
    if testo and not attesi:
        r("  nessuna voce dell'elenco DX corrisponde: si salvano comunque le letture")
    dati = {"pellicola": testo, "attesi": [[list(c[0]), c[1]] for c in attesi[:40]]}
    r.dati["dx"] = dati
    if not k.collega():
        r.avviso("l'agente non e' pronto: collaudo non fatto")
        k.chiudi()
        return 1
    k.passo("pressore su (partenza)", k.pressore_su, tentativi=1)
    k.passo("formato 135", lambda: k.cmd("formato_0", 60))
    if not k.b.pellicola_sdk():
        k.passo("carrello in attesa della pellicola", lambda: k.cmd("carica_1", 60))
        print("")
        print("  >>> Inserire la striscia (non di un cliente). Parte da solo. <<<")
        fine = time.time() + 600
        while time.time() < fine and not k.b.pellicola_sdk():
            time.sleep(0.5)
    if not k.b.pellicola_sdk():
        r.avviso("nessuna pellicola in 10 minuti: collaudo non fatto")
        k.chiudi()
        return 1

    def mezzo_indietro():
        k.pressore_su()
        k.cmd("regola_-50", 60)

    letture = []

    def leggi_dx(etichetta):
        d = k.cmd("dx", 60)
        if isinstance(d, dict):
            d = dict(d, momento=etichetta, stato_dopo=k.stato_sdk())
            letture.append(d)
            return {"tipo": d.get("tipo"), "dx": d.get("dx"), "stato_dopo": d.get("stato_dopo"),
                    "puntatori": len(d.get("puntatori") or {})}
        return d

    k.titolo("DX all'ingresso della striscia")
    k.passo("codice DX (subito)", lambda: leggi_dx("ingresso"), tentativi=1, informativo=True)
    k.passo("capture senza ICE (rimette l'SDK nello stato 0)",
            lambda: sorted(k.b.cattura_originale(False)), tentativi=1, informativo=True)
    k.passo("stato SDK", lambda: k.stato_sdk(), tentativi=1)
    k.passo("G con scan_raw dopo DX e capture", lambda: round(float(k.leggi("G", 1.0).mean()), 1),
            tentativi=1, informativo=True)

    k.titolo("DX dopo che la pellicola si e' mossa")
    for n in range(1, 4):
        k.passo("fotogramma successivo (%d)" % n, lambda: k.cmd("prossimo", 90),
                recupero=mezzo_indietro)
        k.passo("codice DX (%d)" % n, lambda n=n: leggi_dx("dopo %d fotogrammi" % n),
                tentativi=1, informativo=True)
        k.passo("capture senza ICE (%d)" % n, lambda: sorted(k.b.cattura_originale(False)),
                tentativi=1, informativo=True)

    k.titolo("Dove sta il DX")
    dati["letture"] = letture
    trovati, ipotesi = cerca_dx(letture, attesi)
    dati["trovati"], dati["ipotesi"] = trovati, ipotesi
    if trovati:
        posti = {}
        for t in trovati:
            chiave = (t["dove"], t["offset"], t["come"], tuple(t["dx"]))
            posti.setdefault(chiave, set()).add(t["lettura"])
        for (dove, off, come, dx), quali in sorted(posti.items(), key=lambda kv: -len(kv[1])):
            r("  DX %d-%d trovato in %s, offset 0x%s (%s), in %d letture su %d"
              % (dx[0], dx[1], dove, off, come, len(quali), len(letture)))
    elif attesi:
        r("  i numeri DX della pellicola dichiarata non compaiono nelle letture")
    if ipotesi:
        r("  coppie di byte stabili che sono voci dell'elenco DX (pellicola non nota):")
        for h in ipotesi[:10]:
            r("    offset 0x%s: %d-%d %s" % (h["offset"], h["dx"][0], h["dx"][1], h["pellicola"]))
    for l in letture:
        r("  %-20s tipo %s, media_info %s..." % (l.get("momento"), l.get("tipo"),
                                               (l.get("media_info") or "")[:64]))

    k.titolo("Fine")
    k.passo("pressore su", k.pressore_su, tentativi=1)
    k.passo("espulsione dal davanti", lambda: k.cmd("espelli", 120), recupero=k.pressore_su)
    k.chiudi()
    return 0 if not k.falliti else 2


def main(argv=None):
    ap = argparse.ArgumentParser(description="Collaudi automatici dello scanner")
    ap.add_argument("prova", choices=("senza", "pellicola", "ir", "dx"))
    ap.add_argument("--ponte", default=cs.PONTE)
    ap.add_argument("--scanner", default=r"C:\MasterflexD\Scanner")
    ap.add_argument("--simula", action="store_true",
                    help="l'agente, se va avviato, parte simulato")
    a = ap.parse_args(argv)
    try:
        return {"senza": senza_pellicola, "pellicola": con_pellicola,
                "ir": prova_ir, "dx": prova_dx}[a.prova](a)
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
