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
    carica[_<modo>]             carrello in attesa della pellicola nel formato
                                scelto (set_scanner_input_mode, modo 1 di serie)
    regola_<passi>              sposta di N passi motore (0,01 mm l'uno),
                                al massimo --max-passi per comando
    fuoco                       autofocus
    grab_<canale>_<ris>_<exp>   acquisisce un canale (canale R|G|B|IR)
    grabm_<ris>_<eR>_<eG>_<eB>[_<eIR>]
                                R, G, B (e IR) in una lettura sola, un tempo
                                per LED
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
import faulthandler
import json
import os
import shutil
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


CANALI = {"R": 0, "G": 1, "B": 2, "IR": 3}

# Comandi che muovono la meccanica. "fuoco" muove la lente.
MOVIMENTI = ("formato", "carica", "alimenta", "avanza", "prossimo", "precedente",
             "espelli", "espelli_retro", "regola", "fuoco", "azzera",
             "pressore", "calibrasdk",
             # il percorso capture dell'SDK puo' muovere la pellicola da solo
             "sonda")

MAX_PASSI = 300               # 3 mm: oltre non e' piu' una regolazione fine
MAX_ALIMENTAZIONE = 15.0      # secondi di trascinamento acceso senza comandi
COMANDO_LENTO = 30.0          # oltre, il comando finisce nel registro come lento


# I registri stanno nella cartella del software (software/log), con quelli
# di filmscan, dei collaudi e le copie dell'SDK: vedi filmscan/registri.py.
# Prima l'agente scriveva in <ponte>/log, dentro C:\MasterflexD.
CARTELLA_LOG = os.environ.get("MASTERFLEX_LOG") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "log")


def copia_log_sdk(scanner, log=None):
    """All'avvio, prima che l'SDK lo riscriva: copia di SDKCacheDir/log_*.txt
    in software/log/sdk, con data e ora del file."""
    sorgente = os.path.join(scanner, "SDKCacheDir")
    dest = os.path.join(CARTELLA_LOG, "sdk")
    try:
        if not os.path.isdir(sorgente):
            return
        if not os.path.isdir(dest):
            os.makedirs(dest)
        for n in sorted(os.listdir(sorgente)):
            if not (n.lower().startswith("log") and n.lower().endswith(".txt")):
                continue
            p = os.path.join(sorgente, n)
            quando = time.strftime("%Y%m%d-%H%M%S", time.localtime(os.path.getmtime(p)))
            nome = os.path.join(dest, "%s-%s" % (quando, n))
            if not os.path.exists(nome):
                shutil.copy2(p, nome)
    except (IOError, OSError) as exc:
        if log:
            log("copia del registro dell'SDK non riuscita: %s" % exc)


class Registro(object):
    """Scrive a schermo e in software/log/agente/agente-AAAAMMGG.log.

    Il file serve a chi deve capire dopo cosa e' successo: la finestra
    dell'agente si chiude, il file resta e si allega alla segnalazione.
    """

    def __init__(self, condivisa=None):
        self.cartella = os.path.join(CARTELLA_LOG, "agente")
        self.avvisato = False
        try:
            os.makedirs(self.cartella)
        except OSError:
            pass

    def __call__(self, testo, schermo=None):
        """'schermo': la versione corta per la finestra; nel file va sempre
        'testo' intero (prima si troncava anche li', e la risposta del DX
        arrivava a meta': riserva, 31/10/2026)."""
        riga = "%s  %s" % (time.strftime("%H:%M:%S"), testo)
        print("%s  %s" % (riga[:8], schermo) if schermo is not None else riga)
        try:
            nome = "agente-%s.log" % time.strftime("%Y%m%d")
            with open(os.path.join(self.cartella, nome), "a") as f:
                f.write(riga + "\n")
        except (IOError, OSError) as exc:
            # Una volta sola, ma a schermo: se qui non si scrive, di solito
            # non si scrivono nemmeno le risposte del ponte, e l'interfaccia
            # vede solo comandi senza risposta.
            if not self.avvisato:
                self.avvisato = True
                print("  ATTENZIONE: non riesco a scrivere il registro in %s (%s)."
                      % (self.cartella, exc))
                print("  Controllare i permessi della cartella del ponte.")


    def registra_crash(self, tieni=10):
        """Se l'agente muore dentro l'SDK (una violazione d'accesso in
        scn_dll.dll non e' un'eccezione Python: la finestra si chiude e
        basta), faulthandler scrive lo stack di tutti i thread in
        log/crash-AAAAMMGG-HHMMSS.txt. Un file per avvio, perche' quello del
        crash non lo sovrascriva il riavvio; si tengono gli ultimi 'tieni'.
        Su Windows annota anche le eccezioni C++ che l'SDK poi gestisce da
        solo: in quel file conta l'ultima voce prima della fine."""
        try:
            vecchi = sorted(n for n in os.listdir(self.cartella)
                            if n.startswith("crash-") and n.endswith(".txt"))
            for n in vecchi[:max(0, len(vecchi) - tieni + 1)]:
                os.remove(os.path.join(self.cartella, n))
            nome = os.path.join(self.cartella, "crash-%s.txt"
                                % time.strftime("%Y%m%d-%H%M%S"))
            # il file resta aperto per tutta la vita del processo: e' li'
            # che faulthandler scrive quando non c'e' piu' niente di vivo
            self._crash = open(nome, "w")
            self._crash.write("Agente avviato %s. Se l'agente si e' chiuso "
                              "da solo, qui sotto c'e' dove.\n\n"
                              % time.strftime("%Y-%m-%d %H:%M:%S"))
            self._crash.flush()
            faulthandler.enable(file=self._crash, all_threads=True)
        except (IOError, OSError, ValueError) as exc:
            self("registro dei crash non attivo: %s" % exc)


class ErroreSimulato(RuntimeError):
    """Come sdk.ErroreScanner: il messaggio e i codici dell'SDK."""

    def __init__(self, messaggio, codici):
        RuntimeError.__init__(self, messaggio)
        self.codici = codici


class AgenteSimulato(object):
    """Sostituto dello scanner per provare il ponte senza la macchina.

    Non finge che tutto vada bene: genera dati plausibili e risponde ai
    comandi, cosi' il lato nostro si collauda davvero prima di accendere
    un motore del 1995.
    """

    # Come l'SDK vero, all'inizio non e' pronto: sulla riserva ci mette una
    # quarantina di secondi (stato 10 -> 0). Qui pochi secondi, per provare
    # l'attesa del ponte senza aspettare davvero.
    RITARDO_PRONTO = 3.0
    _pronto_da = None

    def pronto(self):
        if self._pronto_da is None:
            self._pronto_da = time.time() + self.RITARDO_PRONTO
        return time.time() >= self._pronto_da

    @property
    def stato_sdk(self):
        return 0 if self.pronto() else 10

    def tipo_scanner(self): return 3
    def id_scanner(self): return 60210
    def tipo_gate(self): return 0
    def scegli_formato(self, f): pass
    pellicola_inserita = False
    # Come la riserva il 26/10/2026: oltre un certo tempo di esposizione il
    # firmware rifiuta (46/47/42), e dopo l'ultimo fotogramma il carrello
    # risponde ERR_MOVE. Il limite vero non si sa: qui x4. La striscia e'
    # di 8 fotogrammi.
    MAX_ESPOSIZIONE = 4.0
    FOTOGRAMMI_STRISCIA = 8
    # in mezzo alle strisce capitano fotogrammi non esposti: solo base
    FOTOGRAMMI_VUOTI = (3, 4)
    # un rullo tagliato in tre strisce: dalla seconda la prima posizione e'
    # la coda, e dopo la terza non se ne inseriscono altre
    STRISCE = 3
    _fotogramma = 0
    _strisce = 0

    def arma_carrello(self, modo=1):
        self._strisce += 1
        self.pellicola_inserita = self._strisce <= self.STRISCE
        self._fotogramma = 0

    def alimentazione(self, accesa=True): pass
    def avanza_fotogramma(self, mezzo=False): pass

    def _pressore_libero(self):
        # col pressore giu' la pellicola non deve muoversi: qui lo si
        # rifiuta, cosi' le prove scoprono chi si dimentica di alzarlo
        if self.pressore_giu:
            raise ErroreSimulato("movimento con il pressore abbassato",
                                 [(3, 0, 0)])

    # Con FILMSCAN_SIM_NON_TROVATO=5 nella prima striscia il carrello "non
    # trova" una volta il fotogramma dopo il 5, come con un negativo
    # sbiadito: si apre il posizionamento a mano. Di serie spento, perche'
    # poi il rullo aspetta qualcuno ai tasti.
    NON_TROVATO_DOPO = (int(os.environ["FILMSCAN_SIM_NON_TROVATO"])
                        if os.environ.get("FILMSCAN_SIM_NON_TROVATO") else None)
    _non_trovato_fatto = False

    def fotogramma_successivo(self):
        self._pressore_libero()
        if (self._strisce == 1 and self._fotogramma == self.NON_TROVATO_DOPO
                and not self._non_trovato_fatto):
            self._non_trovato_fatto = True
            raise ErroreSimulato(
                "fotogramma successivo: l'SDK segnala 1 errore: codice 39 "
                "(0, 0) ERR_FILM_UNKNOWN", [(39, 0, 0)])
        if self._fotogramma + 1 >= self.FOTOGRAMMI_STRISCIA:
            raise ErroreSimulato(
                "fotogramma successivo: l'SDK segnala 1 errore: codice 31 "
                "(0, 0) ERR_MOVE", [(31, 0, 0)])
        self._fotogramma += 1
    def fotogramma_precedente(self): pass

    def reset(self):
        self._fotogramma = 0

    tempi_calibrati = False
    pressore_giu = False

    def calibra_sdk(self): time.sleep(0.5)

    def pressore(self, giu): self.pressore_giu = bool(giu)

    def temperatura(self): return 34.5, 29.0

    def tensioni(self): return [5.02, 12.1, -11.95, 3.31, 24.2, 5.0, 15.1, -15.0]

    def fuoco_gi(self, completo=True): time.sleep(0.3)

    def video(self):
        dati, w, h = self.scansiona_canale(1, 1.0, 3)
        return dati, w, h, w * h

    def finestra_gretag(self, numero, mostra=True): pass

    def codice_dx(self):
        # una Kodak Gold 200 qualunque: serve a provare la memoria per pellicola
        return {"dx1": 115, "dx2": 3, "dx": (115 << 4) + 3, "simulato": True}

    def sonda_capture(self, risoluzione, asf, cartella, attesa=60.0):
        return {"simulato": True, "risoluzione": risoluzione, "asf": bool(asf)}

    def sonda_ice(self, piani, larghezza, altezza, cartella):
        return {"simulato": True, "larghezza": larghezza, "altezza": altezza}

    def modo_asf(self, acceso): pass
    def espelli(self): self.pellicola_inserita = False
    def espelli_retro(self):
        fatti = self.FOTOGRAMMI_STRISCIA - 1 - self._fotogramma
        self._fotogramma = 0
        self.pellicola_inserita = False
        return fatti, "fotogramma successivo: codice 31 (0, 0) ERR_MOVE"
    def fuoco(self, tipo=1): pass
    _passi = 0

    def regola_fine(self, passi):
        # a mano si va avanti a passi: ogni 38 mm (3800 passi da 0,01 mm)
        # in finestra c'e' il fotogramma dopo
        self._pressore_libero()
        self._passi += int(passi)
        while self._passi >= 3800:
            self._passi -= 3800
            self._fotogramma = min(self._fotogramma + 1,
                                   self.FOTOGRAMMI_STRISCIA - 1)
        while self._passi <= -3800:
            self._passi += 3800
            self._fotogramma = max(self._fotogramma - 1, 0)
    def offset_xy(self, formato=0): return (47, 76)
    def sensori(self, numeri=range(0, 16)):
        return dict((n, False) for n in numeri)
    def salva_parametri_flash(self, percorso):
        with open(percorso, "wb") as f:
            f.write(b"\x00" * 1024)
        return percorso

    def scansiona_canale(self, canale=1, esposizione=1.0, risoluzione=0,
                         x0=0, y0=0, larghezza=0, altezza=0, buio=False,
                         per_led=None):
        import random
        if canale in (4, 5):
            # lettura unica: i piani uno dopo l'altro, come si suppone li
            # metta la macchina (chi legge lo verifica comunque)
            piani = []
            fattori = list(per_led or [1.0] * 4)[:3 if canale == 4 else 4]
            for k, f in enumerate(fattori):
                d, w, h = self.scansiona_canale(k, esposizione * f, risoluzione,
                                                x0, y0, larghezza, altezza, buio)
                piani.append(d)
            return b"".join(piani), w, h
        if esposizione > self.MAX_ESPOSIZIONE:
            raise ErroreSimulato(
                "scansione canale %d: l'SDK segnala 3 errori: codice 46 (0, 0) "
                "ERR_SW_SCANNER_IFC, codice 47 (1, 0) ERR_FW_ERR_NO, codice 42 "
                "(1, 0) ERR_GENERAL_FW" % canale, [(46, 0, 0), (47, 1, 0), (42, 1, 0)])
        # come sdk.DIMENSIONI_GREZZE
        dim = {0: (2100, 1400), 1: (1050, 700), 2: (525, 350),
               3: (262, 175), 4: (131, 87)}[risoluzione]
        w, h = larghezza or dim[0], altezza or dim[1]
        rnd = random.Random(canale * 7 + risoluzione)
        # il segnale segue l'esposizione fino alla saturazione, come un CCD
        base = [min(65535.0, esposizione * (12000 + 5000 * (x / float(w))
                                            + rnd.randint(-300, 300)))
                for x in range(w)]
        if buio:
            # come la riserva il 26/10/2026 con il bit del buio acceso
            base = [19.0 + rnd.randint(-2, 2) for x in range(w)]
        # Cosa c'e' in finestra. Senza pellicola luce piena e uniforme (la
        # calibrazione). Con la pellicola, bande orizzontali che cambiano a
        # ogni lettura, con mezza densita' abbondante di escursione come una
        # fotografia; nei fotogrammi non esposti solo la base, uniforme.
        self._letture = getattr(self, "_letture", 0) + 1
        var = random.Random(self._letture)
        if not self.pellicola_inserita:
            intervallo = (1.0, 1.0)
        elif self._fotogramma == 0 and self._strisce > 1:
            intervallo = (0.01, 0.01)       # la coda della striscia, annerita
        elif self._fotogramma in self.FOTOGRAMMI_VUOTI:
            intervallo = (0.45, 0.45)
        else:
            intervallo = (0.3, 1.0)
        varianti = []
        for _ in range(8):
            k = var.uniform(*intervallo)
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

    def gira(self, pausa=0.01):
        self.log("agente in ascolto su %s (motori %s, max %d passi)"
                 % (self.condivisa, "attivi" if self.motori else "BLOCCATI",
                    self.max_passi))
        self.annuncia_pronto(self.s.pronto())
        while True:
            fatti = self.giro()
            if self.esci:
                # "esci" (riavvio dalla interfaccia): si chiude da solo, e
                # il finally di principale() toglie il pid
                self.log("chiuso su richiesta (riavvio con la versione nuova)")
                break
            self.controlla_alimentazione()
            self.pubblica_stato()
            if self.pronto_annunciato is not True and time.time() >= self._prossimo_controllo:
                self._prossimo_controllo = time.time() + 2.0
                self.annuncia_pronto(self.s.pronto())
            if not fatti:
                time.sleep(pausa)

    pronto_annunciato = None
    esci = False
    _prossimo_controllo = 0.0
    _stato_pubblicato = None

    def pubblica_stato(self):
        """Scrive <condivisa>/stato.json quando cambia qualcosa che arriva
        dagli eventi dell'SDK (pronto, pellicola inserita). L'interfaccia lo
        legge ogni secondo senza disturbare l'agente ne' la macchina."""
        stato = {"pronto": self.pronto_annunciato,
                 "stato_sdk": getattr(self.s, "stato_sdk", None),
                 "pellicola_inserita": getattr(self.s, "pellicola_inserita", None),
                 "caricato": self.caricato_alle}
        if stato == self._stato_pubblicato:
            return
        percorso = os.path.join(self.condivisa, "stato.json")
        try:
            with open(percorso + ".tmp", "w") as f:
                json.dump(stato, f)
            os.replace(percorso + ".tmp", percorso)
            self._stato_pubblicato = stato
        except (IOError, OSError):
            pass
    _inizio_attesa = None
    # Quando lo scanner e' diventato pronto l'ultima volta. Ogni caricamento
    # (apertura dell'SDK, o il Gretag Reset dopo "azzera") fa ripartire il
    # GateControl, che azzera i motori del carrello: filmscan lo usa per non
    # rifare l'azzeramento in Calibra subito dopo il collegamento.
    caricato_alle = None

    def annuncia_pronto(self, pronto):
        """Una riga nel registro quando lo scanner diventa pronto, o smette."""
        if pronto == self.pronto_annunciato:
            return
        if pronto:
            attesa = ("dopo %.0f s" % (time.time() - self._inizio_attesa)
                      if self._inizio_attesa else "")
            self.log("scanner pronto %s" % attesa)
            self.caricato_alle = time.time()
            self._inizio_attesa = None
        else:
            self._inizio_attesa = self._inizio_attesa or time.time()
            self.log("attendo che l'SDK colleghi lo scanner (stato %s): sulla "
                     "riserva servono circa 40 s" % getattr(self.s, "stato_sdk", "?"))
        self.pronto_annunciato = pronto

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
                # Gli errori dell'SDK (ErroreScanner, con i codici) sono gia'
                # completi nella riga di esito: il traceback serve solo per
                # gli errori nostri.
                if not hasattr(e, "codici"):
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
            self.log("  -> %s  (%.1f s)" % (esito.strip()[:4000], durata),
                     schermo="  -> %s  (%.1f s)" % (esito.strip()[:120], durata))
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

        if comando == "esci":
            self.esci = True
            return "esco"

        # STOP EMERGENZA: filmscan ha scritto il file di blocco. Passano solo
        # le domande; ogni comando che muove o acquisisce si rifiuta finche'
        # il tecnico non da' l'ok dall'interfaccia, che toglie il file.
        # Alzare il pressore libera la pellicola senza muoverla: e' proprio
        # quello che serve al tecnico, e resta permesso.
        if comando not in ("stato", "pressore_su") and os.path.exists(
                os.path.join(self.condivisa, "EMERGENZA.json")):
            if not getattr(self, "_emergenza_annunciata", False):
                self._emergenza_annunciata = True
                self.log("STOP EMERGENZA: comandi bloccati finche' il tecnico "
                         "non da' l'ok dall'interfaccia")
            raise ValueError("STOP EMERGENZA attivo: macchina bloccata, "
                             "comando '%s' rifiutato" % comando)
        self._emergenza_annunciata = False

        # Finche' l'SDK non ha finito di collegare lo scanner rifiuta quasi
        # tutto con ERR_NO_SCANNER (stato 10, vedi sdk.py): meglio dirlo
        # chiaro che far fallire il comando con codici incomprensibili.
        pronto = self.s.pronto()
        if not pronto:
            self.annuncia_pronto(False)

        if comando == "stato":
            # Ogni voce per conto suo: sulla riserva get_gate_type risponde
            # con due errori (codici 4 e 3, 26/10/2026), e prima questo
            # faceva fallire tutto lo stato, anche le voci che funzionano.
            stato = {
                "pronto": pronto,
                "stato_sdk": getattr(self.s, "stato_sdk", None),
                "simulato": isinstance(self.s, AgenteSimulato),
                "motori": self.motori,
                "max_passi": self.max_passi,
                "apertura": self.apertura,
                "pellicola_inserita": getattr(self.s, "pellicola_inserita", None),
            }
            errori = {}
            letture = (("tipo_scanner", self.s.tipo_scanner),
                       ("id", self.s.id_scanner),
                       ("gate", self.s.tipo_gate))
            for voce, leggi in (letture if pronto else ()):
                try:
                    stato[voce] = leggi()
                except Exception as e:
                    stato[voce] = None
                    errori[voce] = str(e)
            if errori:
                stato["errori"] = errori
            return json.dumps(stato)

        if not pronto:
            raise ValueError(
                "scanner non ancora pronto: l'SDK lo sta ancora collegando "
                "(stato %s). Riprovare tra qualche secondo."
                % getattr(self.s, "stato_sdk", "?"))

        if testa in MOVIMENTI and not self.motori:
            raise ValueError("motori bloccati (--senza-motori): '%s' rifiutato"
                             % comando)

        if comando == "sensori":
            return json.dumps(self.s.sensori())

        if testa == "formato":
            self.s.scegli_formato(int(parti[1]))
            return "formato %s" % parti[1]

        if testa == "carica":
            modo = int(parti[1]) if len(parti) > 1 else 1
            self.s.arma_carrello(modo)
            return "carrello in attesa della pellicola (modo %d)" % modo

        if testa == "alimenta":
            accesa = parti[1] == "on"
            self.s.alimentazione(accesa)
            self.alimentazione_dal = time.time() if accesa else None
            return "alimentazione %s" % parti[1]

        if testa == "esposizione":
            # esposizione_auto | esposizione_fabbrica
            self.s.tempi_calibrati = parti[1] == "auto"
            return "esposizione: %s" % ("tempi calibrati dell'SDK"
                                         if self.s.tempi_calibrati else "fabbrica")

        if comando == "calibrasdk":
            # calibrate_all, a percorso vuoto: puo' muovere lente e CCD
            self.s.calibra_sdk()
            return "calibrazione dell'SDK fatta"

        if testa == "pressore":
            giu = parti[1] == "giu"
            self.s.pressore(giu)
            return "pressore %s" % ("giu'" if giu else "su")

        if comando == "temperatura":
            a, b = self.s.temperatura()
            return json.dumps({"t1": round(a, 2), "t2": round(b, 2)})

        if testa == "fuoco" and len(parti) > 1:
            # fuoco_gi | fuoco_rapido | fuoco_nominale
            if parti[1] == "gi":
                self.s.fuoco_gi(True)
            elif parti[1] == "rapido":
                self.s.fuoco_gi(False)
            elif parti[1] == "nominale":
                self.s.fuoco(0)
            else:
                raise ValueError("autofocus sconosciuto: %s" % comando)
            return "autofocus %s fatto" % parti[1]

        if comando == "video":
            dati, a, b, n = self.s.video()
            nome = "%d_video_%dx%d.raw" % (int(time.time() * 1000), a, b)
            percorso = os.path.join(self.condivisa, "raw", nome)
            with open(percorso, "wb") as f:
                f.write(dati)
            with open(percorso[:-4] + ".json", "w") as f:
                json.dump({"canale": "video", "larghezza": a, "altezza": b,
                           "campioni": n, "dtype": "uint16",
                           "ordine": "little"}, f)
            return nome

        if testa == "finestra":
            # finestra_<numero>_on | finestra_<numero>_off
            self.s.finestra_gretag(int(parti[1]), parti[2] == "on")
            return "finestra %s del carrello %s" % (
                parti[1], "aperta" if parti[2] == "on" else "chiusa")

        if comando == "tensioni":
            return json.dumps([round(v, 3) for v in self.s.tensioni()])

        if testa == "ice":
            self.s.modo_asf(parti[1] == "on")
            return "Digital ICE (ASF) %s" % parti[1]

        if comando == "azzera":
            # il carrello porta a zero i suoi motori (Gretag Reset)
            self.s.reset()
            return "motori azzerati"

        if comando == "precedente":
            self.s.fotogramma_precedente()
            return "fotogramma precedente"

        if comando == "espelli":
            self.s.espelli()
            return "pellicola espulsa dal davanti"

        if comando == "espelli_retro":
            fatti, motivo = self.s.espelli_retro()
            return ("pellicola espulsa dal retro: %d fotogramm%s, poi il "
                    "carrello si e' fermato (%s)"
                    % (fatti, "o" if fatti == 1 else "i", motivo))

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

        if comando == "dx":
            return json.dumps(self.s.codice_dx())

        if testa == "sonda" and len(parti) >= 3:
            # sonda_capture_<ris>_<0|1> | sonda_ice_<ris>: una volta, per
            # imparare i formati del percorso capture e del Digital ICE
            cartella = os.path.join(self.condivisa, "raw")
            if parti[1] == "capture":
                asf = len(parti) > 3 and parti[3] == "1"
                return json.dumps(self.s.sonda_capture(int(parti[2]), asf, cartella))
            if parti[1] == "ice":
                ris = int(parti[2])
                piani, w, h = [], 0, 0
                for k in range(4):
                    dati, w, h = self.s.scansiona_canale(k, 1.0, ris)
                    piani.append(dati)
                return json.dumps(self.s.sonda_ice(piani, w, h, cartella))
            raise ValueError("sonda sconosciuta: %s" % comando)

        if testa == "grabm":
            # grabm_<risoluzione>_<eR>_<eG>_<eB>[_<eIR>]
            return self.acquisisci_insieme(int(parti[1]),
                                           [float(x) for x in parti[2:6]])

        if testa == "grab":
            # grab_<canale>_<risoluzione>_<esposizione>[_buio]
            buio = len(parti) > 4 and parti[4] == "buio"
            return self.acquisisci(parti[1], int(parti[2]), float(parti[3]), buio)

        raise ValueError("comando sconosciuto: %s" % comando)

    def acquisisci_insieme(self, risoluzione, esposizioni):
        """R, G, B (e IR se c'e' il quarto tempo) in una lettura sola, ogni
        LED col suo tempo: tre chiamate a scan_raw diventano una."""
        canale = 5 if len(esposizioni) >= 4 else 4
        piani = 4 if canale == 5 else 3
        t0 = time.time()
        dati, w, h = self.s.scansiona_canale(canale, 1.0, risoluzione,
                                             per_led=list(esposizioni)[:piani])
        t1 = time.time()
        nome = "%d_RGB%s_%dx%d.raw" % (int(t0 * 1000), "I" if piani == 4 else "",
                                       w, h)
        percorso = os.path.join(self.condivisa, "raw", nome)
        with open(percorso, "wb") as f:
            f.write(dati)
        with open(percorso[:-4] + ".json", "w") as f:
            json.dump({"canale": "RGBI"[:piani] if piani == 4 else "RGB",
                       "larghezza": w, "altezza": h, "piani": piani,
                       "campioni": len(dati) // 2,
                       "esposizioni": list(esposizioni)[:piani],
                       "risoluzione": risoluzione,
                       "dtype": "uint16", "ordine": "little",
                       "secondi_sdk": round(t1 - t0, 2),
                       "secondi": round(time.time() - t0, 2)}, f)
        self.log("lettura unica %d piani: SDK %.2f s, file %.2f s"
                 % (piani, t1 - t0, time.time() - t1))
        return nome

    def acquisisci(self, canale, risoluzione, esposizione, buio=False):
        if canale not in CANALI:
            raise ValueError("canale sconosciuto: %s" % canale)
        t0 = time.time()
        if buio:
            dati, w, h = self.s.scansiona_canale(CANALI[canale], esposizione,
                                                 risoluzione, buio=True)
        else:
            dati, w, h = self.s.scansiona_canale(CANALI[canale], esposizione,
                                                 risoluzione)
        nome = "%d_%s_%dx%d.raw" % (int(t0 * 1000), canale, w, h)
        percorso = os.path.join(self.condivisa, "raw", nome)
        with open(percorso, "wb") as f:
            f.write(dati)
        with open(percorso[:-4] + ".json", "w") as f:
            json.dump({"canale": canale, "larghezza": w, "altezza": h,
                       "esposizione": esposizione, "risoluzione": risoluzione,
                       "buio": buio,
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
    log.registra_crash()
    # Il pid subito, prima di aprire l'SDK (che puo' metterci decine di
    # secondi): altrimenti in quel tempo l'agente sembra spento, e il
    # collaudo automatico ne avviava un secondo sulla stessa cartella. Due
    # agenti si rubano i file del ponte (31/10/2026, in simulazione).
    pid = os.path.join(a.condivisa, "agente.pid")
    try:
        with open(pid, "w") as f:
            f.write(str(os.getpid()))
    except (IOError, OSError):
        pass

    def via_pid():
        try:
            os.remove(pid)
        except OSError:
            pass

    if not a.simula:
        copia_log_sdk(a.scanner, log)

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
                via_pid()
                return 2
            banner_simulazione("lo scanner non si e' aperto (vedi sopra)")
            scanner = AgenteSimulato()

    if a.prova_motori:
        via_pid()
        return prova_motori(scanner, log, a.max_passi)

    agente = Agente(a.condivisa, scanner, log=log, motori=not a.senza_motori,
                    max_passi=a.max_passi, max_alimentazione=a.max_alimentazione,
                    apertura=a.apertura)

    # Il file pid dice agli altri strumenti (diagnosi, ektest) che la scheda
    # e' in mano all'agente: con EKframePnP, come con l'originale, chiudere
    # l'handle di un secondo programma spegne la scheda anche per questo.
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
