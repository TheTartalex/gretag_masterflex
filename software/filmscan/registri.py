# -*- coding: utf-8 -*-
"""
La cartella dei registri: tutto quello che serve per capire un problema, in
un posto solo.

    <cartella del software>\\log\\     (es. Desktop\\software\\log)
      LEGGIMI.txt
      programma\\   filmscan-AAAAMMGG.log   diario, avvisi, errori con traceback,
                                            comunicazione col ponte che non va
                    crash-AAAAMMGG-HHMMSS.txt  se Python muore (faulthandler)
      agente\\      agente-AAAAMMGG.log     ogni comando e risposta dell'agente
                    crash-*.txt             se l'agente muore dentro l'SDK
      sdk\\         AAAAMMGG-HHMMSS-log_0000.txt  copie del registro dell'SDK
                                            (lui lo riscrive a ogni avvio)
      collaudi\\    <tipo>-<data>\\           rapporti e file dei collaudi
      diagnosi\\    diagnosi-*.txt
      da-mandare\\  registri-<data>.zip     cio' che si manda per un problema

Prima stavano in cinque posti (ponte\\log, Scanner\\SDKCacheDir,
software\\collaudi, software\\diagnosi, e il diario di filmscan solo in
memoria): per una segnalazione bisognava ricordarsi dove guardare, e il
diario del programma, il pezzo piu' utile, si perdeva alla chiusura.

Sta dentro la cartella del software e non in C:\\MasterflexD: quella e' del
programma originale, e non ci si spargono file nostri (Alessandro,
31/10/2026). L'SDK il suo registro lo scrive comunque in
Scanner\\SDKCacheDir: qui se ne tiene una copia.

Solo libreria standard: lo usano anche manutenzione.py e i collaudi, che
devono girare prima che le librerie siano installate. L'agente ha la sua
copia della regola sul percorso (ponte/agente.py, cartella_log).

Dentro i registri ci sono codici d'ordine ed etichette dei rulli, a volte
nomi di clienti: restano sulla macchina, e lo zip da mandare lo si guarda
prima di mandarlo. Nel repository non vanno mai (.gitignore).
"""

from __future__ import annotations

import datetime
import io
import json
import logging
import os
import shutil
import time
import zipfile
from typing import List, Optional

SOFTWARE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOTTOCARTELLE = ("programma", "agente", "sdk", "collaudi", "diagnosi", "da-mandare")
GIORNI_TENUTI = 60

LEGGIMI = """Registri della macchina Masterflex D1008
========================================

programma\\   il diario di filmscan (interfaccia e scansione), un file al giorno
agente\\      i comandi dell'agente verso lo scanner e le risposte
sdk\\         le copie del registro dell'SDK Kodak (log_0000.txt)
collaudi\\    i rapporti dei collaudi (MASTERFLEX.bat, voce 4)
diagnosi\\    le diagnosi (MASTERFLEX.bat, voce 6)
da-mandare\\  gli zip da mandare quando c'e' un problema

Per una segnalazione: MASTERFLEX.bat, voce 6, oppure nell'interfaccia
Macchina > Scarica i registri. Lo zip finisce in da-mandare\\.
I file piu' vecchi di %d giorni si cancellano da soli.
""" % GIORNI_TENUTI


def cartella_log() -> str:
    """software/log, dovunque sia installato il software (MASTERFLEX_LOG
    per spostarla)."""
    return os.environ.get("MASTERFLEX_LOG") or os.path.join(SOFTWARE, "log")


def sottocartella(nome: str) -> str:
    base = cartella_log()
    p = os.path.join(base, nome)
    try:
        os.makedirs(p, exist_ok=True)
        leggimi = os.path.join(base, "LEGGIMI.txt")
        if not os.path.exists(leggimi):
            with io.open(leggimi, "w", encoding="utf-8") as f:
                f.write(LEGGIMI)
    except OSError:
        pass
    return p


def pulisci(giorni: int = GIORNI_TENUTI) -> int:
    """Toglie i file piu' vecchi di 'giorni' (solo dentro la cartella dei
    registri, solo file). Restituisce quanti."""
    limite = time.time() - giorni * 86400.0
    tolti = 0
    base = cartella_log()
    for sotto in SOTTOCARTELLE:
        radice = os.path.join(base, sotto)
        for cartella, _, file in os.walk(radice):
            for n in file:
                p = os.path.join(cartella, n)
                try:
                    if os.path.getmtime(p) < limite:
                        os.remove(p)
                        tolti += 1
                except OSError:
                    pass
    return tolti


# --- il diario del programma ---------------------------------------------

class _FileDelGiorno(logging.Handler):
    """Un file per giorno: filmscan-AAAAMMGG.log. Aperto e chiuso a ogni
    riga: a 32 bit e con l'antivirus di mezzo un file tenuto aperto per
    giorni e' un guaio in piu', e le righe sono poche."""

    def __init__(self, cartella: str):
        logging.Handler.__init__(self)
        self.cartella = cartella

    def emit(self, record):
        try:
            nome = os.path.join(self.cartella, "filmscan-%s.log"
                                % time.strftime("%Y%m%d"))
            with io.open(nome, "a", encoding="utf-8") as f:
                f.write(self.format(record) + "\n")
        except Exception:                               # noqa: BLE001
            pass


_configurato = False
_crash = None


def configura_programma() -> logging.Logger:
    """Il logger "filmscan" scrive in programma/; faulthandler nello stesso
    posto se Python muore. Una volta sola per processo."""
    global _configurato, _crash
    log = logging.getLogger("filmscan")
    if _configurato:
        return log
    _configurato = True
    cartella = sottocartella("programma")
    h = _FileDelGiorno(cartella)
    h.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
                                     "%H:%M:%S"))
    log.addHandler(h)
    log.setLevel(logging.INFO)
    try:
        import faulthandler
        vecchi = sorted(n for n in os.listdir(cartella) if n.startswith("crash-"))
        for n in vecchi[:max(0, len(vecchi) - 9)]:
            os.remove(os.path.join(cartella, n))
        _crash = open(os.path.join(cartella, "crash-%s.txt"
                                   % time.strftime("%Y%m%d-%H%M%S")), "w")
        _crash.write("filmscan avviato %s. Se il programma si e' chiuso da "
                     "solo, qui sotto c'e' dove.\n\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        _crash.flush()
        faulthandler.enable(file=_crash, all_threads=True)
    except Exception:                                   # noqa: BLE001
        pass
    try:
        pulisci()
    except Exception:                                   # noqa: BLE001
        pass
    log.info("---- filmscan avviato (registri in %s)", cartella_log())
    return log


# --- il registro dell'SDK -------------------------------------------------

def copia_log_sdk(scanner: str = r"C:\MasterflexD\Scanner") -> List[str]:
    """Copia i log_*.txt dell'SDK in sdk/, con data e ora: l'SDK li
    riscrive a ogni avvio, e il registro della sessione andata male si
    perderebbe alla riapertura. Non copia di nuovo un file invariato."""
    sorgente = os.path.join(scanner, "SDKCacheDir")
    if not os.path.isdir(sorgente):
        return []
    dest = sottocartella("sdk")
    copiati = []
    for n in sorted(os.listdir(sorgente)):
        if not (n.lower().startswith("log") and n.lower().endswith(".txt")):
            continue
        p = os.path.join(sorgente, n)
        try:
            quando = time.strftime("%Y%m%d-%H%M%S", time.localtime(os.path.getmtime(p)))
            nome = os.path.join(dest, "%s-%s" % (quando, n))
            if os.path.exists(nome) and os.path.getsize(nome) == os.path.getsize(p):
                continue
            shutil.copy2(p, nome)
            copiati.append(nome)
        except (IOError, OSError):
            continue
    return copiati


# --- lo zip da mandare ----------------------------------------------------

def raccogli(giorni: int = 7, ponte: Optional[str] = r"C:\MasterflexD\ponte",
             scanner: Optional[str] = r"C:\MasterflexD\Scanner",
             impostazioni: Optional[str] = None) -> str:
    """Uno zip con tutto quello che serve per capire un problema degli
    ultimi 'giorni': i registri, il registro dell'SDK di adesso, lo stato
    del ponte, le impostazioni (senza il PIN). Va in da-mandare/."""
    if scanner:
        copia_log_sdk(scanner)
    base = cartella_log()
    limite = time.time() - giorni * 86400.0
    nome = os.path.join(sottocartella("da-mandare"), "registri-%s.zip"
                        % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    with zipfile.ZipFile(nome, "w", zipfile.ZIP_DEFLATED) as z:
        leggimi = os.path.join(base, "LEGGIMI.txt")
        if os.path.exists(leggimi):
            z.write(leggimi, "LEGGIMI.txt")
        for sotto in SOTTOCARTELLE:
            if sotto == "da-mandare":
                continue
            radice = os.path.join(base, sotto)
            for cartella, _, file in os.walk(radice):
                for n in file:
                    p = os.path.join(cartella, n)
                    try:
                        if os.path.getmtime(p) < limite:
                            continue
                        # i grezzi delle sonde possono essere grossi: fino a 50 MB
                        if os.path.getsize(p) > 50 * 1024 * 1024:
                            continue
                        z.write(p, os.path.relpath(p, base))
                    except (IOError, OSError):
                        continue
        if ponte:
            for n in ("stato.json", "EMERGENZA.json"):
                p = os.path.join(ponte, n)
                if os.path.exists(p):
                    try:
                        z.write(p, "ponte/" + n)
                    except (IOError, OSError):
                        pass
            # registri dell'agente rimasti nella cartella vecchia
            vecchia = os.path.join(ponte, "log")
            if os.path.isdir(vecchia):
                for n in os.listdir(vecchia):
                    p = os.path.join(vecchia, n)
                    try:
                        if os.path.isfile(p) and os.path.getmtime(p) >= limite:
                            z.write(p, "ponte-log/" + n)
                    except (IOError, OSError):
                        pass
        if impostazioni and os.path.exists(impostazioni):
            try:
                with io.open(impostazioni, encoding="utf-8") as f:
                    dati = json.load(f)
                for k in list(dati):
                    if "pin" in k.lower():          # il PIN fisso non esce
                        dati[k] = "(tolto)"
                z.writestr("impostazioni.json", json.dumps(dati, indent=1))
            except (IOError, OSError, ValueError):
                pass
    return nome
