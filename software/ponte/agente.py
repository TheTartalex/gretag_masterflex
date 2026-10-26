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
             \                          /
              C:\MasterflexD\ponte  (cmd / ack / raw)

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
    regola_<passi>              sposta di N passi motore (0,01 mm l'uno)
    fuoco                       autofocus
    grab_<canale>_<ris>_<exp>   acquisisce un canale (canale R|G|B|IR)
    flash_backup                salva il Parameter Flash in raw/
    offsetxy_<formato>          legge gli offset XY dal flash

Avvio sul PC originale:

    python agente.py --condivisa \\\\server\\ponte
    python agente.py --condivisa C:\\ponte --simula      (prova senza macchina)
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
        riga = bytearray()
        for x in range(w):
            v = int(20000 + 8000 * (x / float(w)) + rnd.randint(-400, 400))
            riga += bytes((v & 0xFF, (v >> 8) & 0xFF))
        return bytes(riga) * h, w, h


class Agente(object):

    def __init__(self, condivisa, scanner, log=print):
        self.condivisa = condivisa
        self.s = scanner
        self.log = log
        for sub in ("cmd", "ack", "raw"):
            d = os.path.join(condivisa, sub)
            if not os.path.isdir(d):
                os.makedirs(d)

    # -- protocollo --------------------------------------------------------

    def gira(self, pausa=0.05):
        self.log("agente in ascolto su %s" % self.condivisa)
        while True:
            fatti = self.giro()
            if not fatti:
                time.sleep(pausa)

    def giro(self):
        cartella = os.path.join(self.condivisa, "cmd")
        try:
            token_list = sorted(os.listdir(cartella))
        except OSError:
            return 0
        n = 0
        for token in token_list:
            percorso = os.path.join(cartella, token)
            if not os.path.isfile(percorso):
                continue
            try:
                with open(percorso) as f:
                    comando = f.read().strip()
            except IOError:
                continue
            self.log("  <- %s" % comando)
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
            with open(os.path.join(self.condivisa, "ack", token), "w") as f:
                f.write(esito.strip())
            try:
                os.remove(percorso)
            except OSError:
                pass
            self.log("  -> %s" % esito.strip()[:120])
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
            })

        if comando == "sensori":
            return json.dumps(self.s.sensori())

        if testa == "formato":
            self.s.scegli_formato(int(parti[1]))
            return "formato %s" % parti[1]

        if testa == "alimenta":
            self.s.alimentazione(parti[1] == "on")
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
            self.s.regola_fine(int(parti[1]))
            return "regolato di %s passi" % parti[1]

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


def principale(argv=None):
    p = argparse.ArgumentParser(description="Agente ponte per lo scanner Masterflex D1008")
    p.add_argument("--condivisa", required=True,
                   help="cartella condivisa fra il PC originale e noi")
    p.add_argument("--scanner", default=r"C:\MasterflexD\Scanner",
                   help="cartella dello scanner sul PC originale")
    p.add_argument("--simula", action="store_true",
                   help="senza macchina: risponde ai comandi e genera dati finti")
    p.add_argument("--simula-se-manca", action="store_true",
                   help="se lo scanner non si apre, continua in simulazione "
                        "invece di fermarsi (dicendolo a chiare lettere)")
    p.add_argument("--una-volta", action="store_true",
                   help="esegue un solo giro e termina (per collaudo)")
    a = p.parse_args(argv)

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
            scanner = Scanner(a.scanner)
            print("scanner aperto: tipo %s, matricola %s"
                  % (scanner.tipo_scanner(), scanner.id_scanner()))
        except Exception as exc:
            # Qui ci si arriva su un PC senza scheda - una macchina virtuale,
            # o il portatile di prova. Prima l'agente moriva e basta: la
            # finestra spariva, e dall'interfaccia si vedevano solo comandi in
            # timeout senza sapere perche'. Adesso lo dice.
            print("")
            print("  Non sono riuscito ad aprire lo scanner:")
            print("    %s" % exc)
            print("")
            print("  Cause tipiche, in ordine di probabilita':")
            print("    - la scheda non c'e' (macchina virtuale o PC di prova)")
            print("    - il driver EKFrame non e' avviato")
            print("    - Python non e' a 32 bit e non puo' caricare l'SDK")
            print("    - manca %s\\Bin\\scn_dll.dll" % a.scanner)
            print("")
            print("  Dettagli nel log dell'SDK:")
            print("    %s\\SDKCacheDir\\log_0000.txt" % a.scanner)
            if not a.simula_se_manca:
                print("")
                print("  L'agente si ferma qui. Per provare l'interfaccia senza")
                print("  macchina: avvia-simulazione.bat")
                return 2
            banner_simulazione("lo scanner non si e' aperto (vedi sopra)")
            scanner = AgenteSimulato()

    agente = Agente(a.condivisa, scanner)
    if a.una_volta:
        agente.giro()
    else:
        try:
            agente.gira()
        except KeyboardInterrupt:
            print("\nfermato")
    return 0


if __name__ == "__main__":
    sys.exit(principale())
