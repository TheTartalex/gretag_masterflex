# -*- coding: utf-8 -*-
"""
L'agente: il processo che tiene in mano lo scanner e obbedisce a noi.

L'idea del ponte in una frase: della macchina originale teniamo i driver e i
comandi, che funzionano e non si rifanno; dell'interfaccia non teniamo niente.

Tutto gira sullo stesso PC - quello del 2009 con Windows 7 a 32 bit, con la
scheda dentro - ma in due processi separati:

    processo 1: questo agente          processo 2: filmscan
      ctypes -> scn_dll.dll              interfaccia web sulla rete
      EKframe.sys -> scheda PCI          ordini, pipeline, export
             \\                          /
              C:\\MasterflexD\\ponte  (cmd / ack / raw)

Separati di proposito. L'SDK e' C++ del 2001 chiamato con trampolini fatti a
mano, e comanda motori: se inciampa si porta dietro il processo che lo ospita.
Meglio che sia questo e non quello che tiene l'interfaccia in mano
all'operatore, con un rullo a meta'.

DIPENDENZE: nessuna. Solo la libreria standard di Python, di proposito.
Installare numpy su una macchina del 2001 e' una serata persa; qui non serve.

PROTOCOLLO
----------
    <condivisa>/cmd/<token>     noi scriviamo un comando (il contenuto e' il comando)
    <condivisa>/ack/<token>     l'agente risponde: "ok ..." oppure "errore ..."
    <condivisa>/raw/<nome>      l'agente deposita i dati grezzi acquisiti

Il token e' il nome del file e lega comando e risposta. Ogni file grezzo ha
accanto un .json con canale, dimensioni, esposizione e ora.

COMANDI
-------
    ping                        verifica che l'agente sia vivo
    stato                       tipo di scanner, gate, matricola
    sensori                     stato degli ingressi digitali
    formato_<n>                 sceglie il formato (0=135, 1=APS, 4=110)
    alimenta_on | alimenta_off  trascinamento
    avanza | avanza_mezzo       avanza di un fotogramma o mezzo
    prossimo                    va al fotogramma successivo
    regola_<passi>              sposta di N passi motore (0,01 mm l'uno),
                                al massimo --max-passi per comando
    fuoco                       autofocus
    grab_<canale>_<ris>_<exp>   acquisisce un canale (canale R|G|B|IR)
    flash_backup                salva il Parameter Flash in raw/
    offsetxy_<formato>          legge gli offset XY dal flash

SICUREZZA DEI MOTORI (ottobre 2026)
-----------------------------------
Sulla riserva, all'avvio, i motori delle guide sono andati a fine corsa e
hanno continuato a spingere. Da qui in poi l'agente:

  - rifiuta le regolazioni oltre --max-passi (di serie 300 = 3 mm): prima
    filmscan chiedeva "regola_3800" per passare al fotogramma successivo,
    cioe' 38 mm con un comando di correzione fine;
  - con --senza-motori rifiuta ogni comando che muove la meccanica e lascia
    passare solo letture e acquisizioni;
  - spegne da solo il trascinamento acceso da piu' di --max-alimentazione
    secondi;
  - scrive in <condivisa>/log/ ogni comando, quanto e' durato, e gli eventi e
    gli errori asincroni che l'SDK segnala (prima non li ascoltava nessuno);
  - con --apertura leggera apre l'SDK con l'altra combinazione di flag, per
    capire se e' l'apertura completa a lanciare l'inizializzazione delle guide.

Avvio sul PC della macchina:

    python agente.py --condivisa C:\\MasterflexD\\ponte
    python agente.py --condivisa C:\\MasterflexD\\ponte --senza-motori
    python agente.py --condivisa C:\\ponte-prova --simula   (senza macchina)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


CANALI = {"R": 0, "G": 1, "B": 2, "IR": 3}

# Comandi che muovono la meccanica. "fuoco" muove la lente.
MOVIMENTI = ("formato", "alimenta", "avanza", "prossimo", "regola", "fuoco")

MAX_PASSI = 300               # 3 mm: oltre non e' piu' una regolazione fine
MAX_ALIMENTAZIONE = 15.0      # secondi di trascinamento acceso senza comandi
COMANDO_LENTO = 30.0          # oltre, il comando finisce nel registro come lento


class Registro(object):
    """Scrive a schermo e in <condivisa>/log/agente-AAAAMMGG.log.

    Il file serve a chi deve capire dopo cosa e' successo: la finestra
    dell'agente si chiude, il file resta e si allega alla segnalazione.
    """

    def __init__(self, condivisa):
        self.cartella = os.path.join(condivisa, "log")
        try:
            os.makedirs(self.cartella)
        except OSError:
            pass

    def __call__(self, testo):
        riga = "%s  %s" % (time.strftime("%H:%M:%S"), testo)
        print(riga)
        try:
            nome = "agente-%s.log" % time.strftime("%Y%m%d")
            with open(os.path.join(self.cartella, nome), "a") as f:
                f.write(riga + "\n")
        except (IOError, OSError):
            pass


class AgenteSimulato(object):
    """Sostituto dello scanner per provare il ponte senza la macchina.

    Non finge che tutto vada bene: genera dati plausibili e risponde ai
    comandi, cosi' il lato nostro si collauda davvero prima di accendere
    un motore del 1995.
    """

    def tipo_scanner(self): return 3
    def id_scanner(self): return 60210
    def tipo_gate(self): return 0
    def scegli_formato(self, f): pass
    def alimentazione(self, accesa=True): pass
    def avanza_fotogramma(self, mezzo=False): pass
    def fotogramma_successivo(self): pass
    def fuoco(self, tipo=1): pass
    def regola_fine(self, passi): pass
    def offset_xy(self, formato=0): return (47, 76)
    def sensori(self, numeri=range(0, 16)):
        return dict((n, False) for n in numeri)
    def salva_parametri_flash(self, percorso):
        with open(percorso, "wb") as f:
            f.write(b"\x00" * 1024)
        return percorso

    def scansiona_canale(self, canale=1, esposizione=1.0, risoluzione=0,
                         x0=0, y0=0, larghezza=0, altezza=0):
        import random
        dim = {0: (2100, 1400), 1: (1052, 700), 2: (524, 350),
               3: (264, 175), 4: (132, 88)}[risoluzione]
        w, h = larghezza or dim[0], altezza or dim[1]
        rnd = random.Random(canale * 7 + risoluzione)
        # il segnale segue l'esposizione fino alla saturazione, come un CCD
        base = [min(65535.0, esposizione * (12000 + 5000 * (x / float(w))
                                            + rnd.randint(-300, 300)))
                for x in range(w)]
        # Bande orizzontali che cambiano a ogni lettura: senza un po' di
        # "immagine" il rilevamento di fine pellicola vedeva luce uniforme e
        # chiudeva il rullo simulato dopo il primo fotogramma.
        self._letture = getattr(self, "_letture", 0) + 1
        var = random.Random(self._letture)
        varianti = []
        for _ in range(8):
            k = var.uniform(0.75, 1.0)
            riga = bytearray()
            for v in base:
                v = int(v * k)
                riga += bytes((v & 0xFF, (v >> 8) & 0xFF))
            varianti.append(bytes(riga))
        banda = max(1, h // 16)
        dati = b"".join(varianti[(y // banda) % 8] for y in range(h))
        return dati, w, h


class Agente(object):

    def __init__(self, condivisa, scanner, log=print, motori=True,
                 max_passi=MAX_PASSI, max_alimentazione=MAX_ALIMENTAZIONE,
                 apertura="completa"):
        self.condivisa = condivisa
        self.s = scanner
        self.log = log
        self.motori = motori
        self.max_passi = max_passi
        self.max_alimentazione = max_alimentazione
        self.apertura = apertura
        self.alimentazione_dal = None
        for sub in ("cmd", "ack", "raw"):
            d = os.path.join(condivisa, sub)
            if not os.path.isdir(d):
                os.makedirs(d)

    def controlla_alimentazione(self):
        """Il trascinamento acceso non resta acceso per sempre.

        gate_feed_on fa girare il motore finche' qualcuno non chiama
        gate_feed_off. Se filmscan si blocca o si chiude in mezzo, nessuno lo
        chiama piu'. Si controlla qui, nel thread dell'agente, fra un comando
        e l'altro: mai due chiamate all'SDK insieme.
        """
        if self.alimentazione_dal is None:
            return
        if time.time() - self.alimentazione_dal < self.max_alimentazione:
            return
        self.log("trascinamento acceso da oltre %.0f s: lo spengo"
                 % self.max_alimentazione)
        try:
            self.s.alimentazione(False)
        except Exception as e:
            self.log("spegnimento del trascinamento fallito: %s" % e)
        self.alimentazione_dal = None

    # -- protocollo --------------------------------------------------------

    def gira(self, pausa=0.05):
        self.log("agente in ascolto su %s (motori %s, max %d passi)"
                 % (self.condivisa, "attivi" if self.motori else "BLOCCATI",
                    self.max_passi))
        while True:
            fatti = self.giro()
            self.controlla_alimentazione()
            if not fatti:
                time.sleep(pausa)

    def giro(self):
        """Esegue i comandi in attesa.

        I file arrivano completi: filmscan li scrive con un nome .tmp e poi li
        rinomina. Prima li scriveva direttamente, e capitava di leggere un
        comando ancora vuoto ("comando sconosciuto: ") a meta' calibrazione.
        """
        cartella = os.path.join(self.condivisa, "cmd")
        try:
            token_list = sorted(os.listdir(cartella))
        except OSError:
            return 0
        n = 0
        for token in token_list:
            percorso = os.path.join(cartella, token)
            if token.endswith(".tmp") or not os.path.isfile(percorso):
                continue
            try:
                with open(percorso) as f:
                    comando = f.read().strip()
            except IOError:
                continue
            self.log("  <- %s" % comando)
            t0 = time.time()
            try:
                risposta = self.esegui(comando)
                esito = "ok %s" % (risposta if risposta is not None else "")
            except ValueError as e:
                # comando che non conosciamo: una riga basta e avanza.
                # Il traceback qui non aggiunge niente e sommerge quelli veri.
                esito = "errore %s" % e
            except Exception as e:
                esito = "errore %s" % e
                self.log(traceback.format_exc())
            ack = os.path.join(self.condivisa, "ack", token)
            with open(ack + ".tmp", "w") as f:
                f.write(esito.strip())
            os.replace(ack + ".tmp", ack)
            try:
                os.remove(percorso)
            except OSError:
                pass
            durata = time.time() - t0
            self.log("  -> %s  (%.1f s)" % (esito.strip()[:120], durata))
            if durata > COMANDO_LENTO:
                self.log("  ATTENZIONE: '%s' ha impiegato %.0f s" % (comando, durata))
            n += 1
        return n

    # -- comandi -----------------------------------------------------------

    def esegui(self, comando):
        parti = comando.split("_")
        testa = parti[0]

        if comando == "ping":
            return "vivo"

        if comando == "stato":
            return json.dumps({
                "tipo_scanner": self.s.tipo_scanner(),
                "id": self.s.id_scanner(),
                "gate": self.s.tipo_gate(),
                "simulato": isinstance(self.s, AgenteSimulato),
                "motori": self.motori,
                "max_passi": self.max_passi,
                "apertura": self.apertura,
            })

        if testa in MOVIMENTI and not self.motori:
            raise ValueError("motori bloccati (--senza-motori): '%s' rifiutato"
                             % comando)

        if comando == "sensori":
            return json.dumps(self.s.sensori())

        if testa == "formato":
            self.s.scegli_formato(int(parti[1]))
            return "formato %s" % parti[1]

        if testa == "alimenta":
            accesa = parti[1] == "on"
            self.s.alimentazione(accesa)
            self.alimentazione_dal = time.time() if accesa else None
            return "alimentazione %s" % parti[1]

        if comando == "avanza":
            self.s.avanza_fotogramma()
            return "avanzato"

        if comando == "avanza_mezzo":
            self.s.avanza_fotogramma(mezzo=True)
            return "avanzato mezzo"

        if comando == "prossimo":
            self.s.fotogramma_successivo()
            return "fotogramma successivo"

        if testa == "regola":
            passi = int(parti[1])
            if abs(passi) > self.max_passi:
                raise ValueError(
                    "regolazione di %d passi rifiutata: il massimo e' %d "
                    "(%.1f mm). Per passare al fotogramma successivo si usa "
                    "'avanza'." % (passi, self.max_passi, self.max_passi / 100.0))
            self.s.regola_fine(passi)
            return "regolato di %d passi" % passi

        if comando == "fuoco":
            self.s.fuoco()
            return "fuoco fatto"

        if testa == "offsetxy":
            x, y = self.s.offset_xy(int(parti[1]))
            return json.dumps({"x": x, "y": y})

        if comando == "flash_backup":
            nome = "parameter_flash_%d.bin" % int(time.time())
            self.s.salva_parametri_flash(os.path.join(self.condivisa, "raw", nome))
            return nome

        if testa == "grab":
            return self.acquisisci(parti[1], int(parti[2]), float(parti[3]))

        raise ValueError("comando sconosciuto: %s" % comando)

    def acquisisci(self, canale, risoluzione, esposizione):
        if canale not in CANALI:
            raise ValueError("canale sconosciuto: %s" % canale)
        t0 = time.time()
        dati, w, h = self.s.scansiona_canale(CANALI[canale], esposizione, risoluzione)
        nome = "%d_%s_%dx%d.raw" % (int(t0 * 1000), canale, w, h)
        percorso = os.path.join(self.condivisa, "raw", nome)
        with open(percorso, "wb") as f:
            f.write(dati)
        with open(percorso[:-4] + ".json", "w") as f:
            json.dump({"canale": canale, "larghezza": w, "altezza": h,
                       "esposizione": esposizione, "risoluzione": risoluzione,
                       "dtype": "uint16", "ordine": "little",
                       "secondi": round(time.time() - t0, 2)}, f)
        return nome


def prova_motori(scanner, log, max_passi):
    """Prova guidata dei motori, un movimento piccolo alla volta.

    Serve a rispondere a due domande che finora erano "da misurare" (piano
    dei lavori 3.13): in che verso va la regolazione fine, e quanto si muove.
    Ogni movimento si fa solo dopo che l'operatore ha confermato, con la mano
    sull'interruttore, e il risultato finisce nel registro.
    """
    passi = min(20, max_passi)
    prove = [("regolazione fine +%d passi (%.2f mm)" % (passi, passi / 100.0),
              lambda: scanner.regola_fine(passi)),
             ("regolazione fine -%d passi (ritorno)" % passi,
              lambda: scanner.regola_fine(-passi)),
             ("avanzamento di mezzo fotogramma",
              lambda: scanner.avanza_fotogramma(mezzo=True))]
    print("")
    print("  PROVA GUIDATA DEI MOTORI")
    print("  Percorso pellicola con una striscia di prova, NON un rullo di un")
    print("  cliente. Mano sull'interruttore: se qualcosa spinge a fine corsa,")
    print("  si spegne la macchina.")
    for nome, azione in prove:
        print("")
        r = input("  Prossimo: %s. Invio = esegui, n = salta, q = esci: " % nome)
        if r.strip().lower() == "q":
            break
        if r.strip().lower() == "n":
            continue
        t0 = time.time()
        try:
            azione()
            esito = "eseguito in %.1f s" % (time.time() - t0)
        except Exception as e:
            esito = "errore: %s" % e
        visto = input("  %s. Cosa si e' visto (verso, quanto, rumori)? " % esito)
        log("prova motori - %s: %s - osservato: %s" % (nome, esito, visto))
    print("")
    print("  Fatto. Le risposte sono nel registro dell'agente (cartella log).")
    return 0


def principale(argv=None):
    p = argparse.ArgumentParser(description="Agente ponte per lo scanner Masterflex D1008")
    p.add_argument("--condivisa", required=True,
                   help="cartella di scambio con filmscan")
    p.add_argument("--scanner", default=r"C:\MasterflexD\Scanner",
                   help="cartella dello scanner del software originale")
    p.add_argument("--simula", action="store_true",
                   help="senza macchina: risponde ai comandi e genera dati finti")
    p.add_argument("--simula-se-manca", action="store_true",
                   help="se lo scanner non si apre, continua in simulazione "
                        "invece di fermarsi (dicendolo a chiare lettere)")
    p.add_argument("--senza-motori", action="store_true",
                   help="rifiuta ogni comando che muove la meccanica")
    p.add_argument("--max-passi", type=int, default=MAX_PASSI,
                   help="regolazione fine massima per comando (passi da 0,01 mm)")
    p.add_argument("--max-alimentazione", type=float, default=MAX_ALIMENTAZIONE,
                   help="secondi dopo cui il trascinamento acceso si spegne da solo")
    p.add_argument("--apertura", choices=("completa", "leggera"), default="completa",
                   help="flag di apertura dell'SDK (vedi sdk.APERTURA)")
    p.add_argument("--prova-motori", action="store_true",
                   help="prova guidata dei motori, poi esce")
    p.add_argument("--una-volta", action="store_true",
                   help="esegue un solo giro e termina (per collaudo)")
    a = p.parse_args(argv)

    if not os.path.isdir(a.condivisa):
        os.makedirs(a.condivisa)
    log = Registro(a.condivisa)

    def banner_simulazione(motivo=""):
        print("")
        print("  " + "=" * 60)
        print("   MODALITA' SIMULATA")
        print("   Nessuna macchina viene toccata: i fotogrammi sono finti.")
        if motivo:
            print("")
            print("   Perche': %s" % motivo)
        print("  " + "=" * 60)
        print("")

    if a.simula:
        scanner = AgenteSimulato()
        banner_simulazione()
    else:
        try:
            from sdk import Scanner
            scanner = Scanner(a.scanner, apertura=a.apertura, log=log)
            log("scanner aperto: tipo %s, matricola %s"
                % (scanner.tipo_scanner(), scanner.id_scanner()))
        except Exception as exc:
            # Qui ci si arriva su un PC senza scheda, con il driver fermo, o
            # con un Python a 64 bit. Prima l'agente moriva e basta: la
            # finestra spariva e dall'interfaccia si vedevano solo comandi in
            # timeout senza sapere perche'. Adesso lo dice.
            log("apertura dello scanner fallita: %s" % exc)
            print("")
            print("  Non sono riuscito ad aprire lo scanner:")
            print("    %s" % exc)
            print("")
            print("  Cause tipiche, in ordine di probabilita':")
            print("    - il driver della scheda non e' avviato (servizio EKFrame):")
            print("      sulla macchina di riserva serve EKframePnP")
            print("    - un altro programma ha la scheda (ScanDiag, ektest,")
            print("      il programma originale): chiuderlo e riprovare")
            print("    - Python non e' a 32 bit e non puo' caricare l'SDK")
            print("    - manca %s\\Bin\\scn_dll.dll" % a.scanner)
            print("")
            print("  Dettagli nel log dell'SDK:")
            print("    %s\\SDKCacheDir\\log_0000.txt" % a.scanner)
            if not a.simula_se_manca:
                print("")
                print("  L'agente si ferma qui. Per provare l'interfaccia senza")
                print("  macchina: MASTERFLEX.bat, voce \"simulazione\".")
                return 2
            banner_simulazione("lo scanner non si e' aperto (vedi sopra)")
            scanner = AgenteSimulato()

    if a.prova_motori:
        return prova_motori(scanner, log, a.max_passi)

    agente = Agente(a.condivisa, scanner, log=log, motori=not a.senza_motori,
                    max_passi=a.max_passi, max_alimentazione=a.max_alimentazione,
                    apertura=a.apertura)

    # Il file pid dice agli altri strumenti (diagnosi, ektest) che la scheda
    # e' in mano all'agente: con EKframePnP, come con l'originale, chiudere
    # l'handle di un secondo programma spegne la scheda anche per questo.
    pid = os.path.join(a.condivisa, "agente.pid")
    try:
        with open(pid, "w") as f:
            f.write(str(os.getpid()))
    except (IOError, OSError):
        pass
    try:
        if a.una_volta:
            agente.giro()
        else:
            agente.gira()
    except KeyboardInterrupt:
        print("\nfermato")
    finally:
        if agente.alimentazione_dal is not None:
            try:
                scanner.alimentazione(False)
            except Exception:
                pass
        try:
            os.remove(pid)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(principale())
