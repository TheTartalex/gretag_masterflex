# -*- coding: utf-8 -*-
"""
Riavvio di tutto con la versione installata: interfaccia, agente (o
osservatore), MASTERFLEX.bat nuovo se l'aggiornamento ne ha portato uno.

Lo lancia l'interfaccia (tasto "Riavvia con la versione nuova",
ScanService.riavvia) staccato da se': l'interfaccia esce con il codice 3 e
MASTERFLEX.bat, vedendolo, chiude la sua finestra. Poi qui:

1. si aspetta che il vecchio server sia uscito;
2. si chiede all'agente di chiudersi (comando "esci"), e se non risponde lo
   si chiude d'ufficio; si chiudono le finestre rimaste (agente, osservatore);
3. si mette al suo posto MASTERFLEX.bat.nuovo, se c'e' (a vecchio menu gia'
   chiuso: un .bat sostituito mentre cmd lo sta leggendo esegue righe a caso);
4. si riparte con MASTERFLEX.bat automatico: l'ultimo modo usato, senza
   domande, in una finestra nuova.

Solo libreria standard. Quello che fa lo scrive in log/programma/.
"""

from __future__ import print_function

import argparse
import io
import os
import subprocess
import sys
import time

QUI = os.path.dirname(os.path.abspath(__file__))
WINDOWS = os.name == "nt"


def _registro():
    cartella = os.environ.get("MASTERFLEX_LOG") or os.path.join(QUI, "log")
    cartella = os.path.join(cartella, "programma")
    try:
        os.makedirs(cartella)
    except OSError:
        pass
    percorso = os.path.join(cartella, "riavvio-%s.log" % time.strftime("%Y%m%d"))

    def log(testo):
        riga = "%s  %s" % (time.strftime("%H:%M:%S"), testo)
        print(riga)
        try:
            with io.open(percorso, "a", encoding="utf-8") as f:
                f.write(riga + u"\n")
        except (IOError, OSError):
            pass
    return log


def vivo(pid):
    if not pid:
        return False
    if WINDOWS:
        uscita = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return str(pid) in uscita.stdout.decode("ascii", "replace")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def aspetta_fine(pid, secondi):
    fine = time.time() + secondi
    while time.time() < fine and vivo(pid):
        time.sleep(0.5)
    return not vivo(pid)


def pid_agente(ponte):
    try:
        with open(os.path.join(ponte, "agente.pid")) as f:
            return int(f.read().strip())
    except (IOError, OSError, ValueError):
        return None


def chiudi_agente(ponte, log):
    pid = pid_agente(ponte)
    if not pid or not vivo(pid):
        log("agente: non in esecuzione")
        return
    token = "%d_9999_esci" % int(time.time() * 1000)
    cmd = os.path.join(ponte, "cmd", token)
    try:
        with open(cmd + ".tmp", "w") as f:
            f.write("esci")
        os.replace(cmd + ".tmp", cmd)
    except (IOError, OSError) as exc:
        log("agente: comando 'esci' non scritto (%s)" % exc)
    if aspetta_fine(pid, 30):
        log("agente %d chiuso" % pid)
    else:
        log("agente %d non si chiude da solo: lo chiudo" % pid)
        if WINDOWS:
            subprocess.call(["taskkill", "/PID", str(pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        aspetta_fine(pid, 10)
    try:
        os.remove(os.path.join(ponte, "cmd", token))
    except OSError:
        pass


def chiudi_finestre(log):
    """Le finestre cmd /k rimaste aperte dopo che il loro programma e' uscito."""
    if not WINDOWS:
        return
    for titolo in ("Agente scanner*", "Osservatore programma originale*"):
        subprocess.call(["taskkill", "/FI", "WINDOWTITLE eq %s" % titolo, "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log("finestre di agente e osservatore chiuse")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Riavvio con la versione installata")
    ap.add_argument("--pid-web", type=int, default=0)
    ap.add_argument("--ponte", default=r"C:\MasterflexD\ponte")
    a = ap.parse_args(argv)
    log = _registro()
    log("---- riavvio richiesto dall'interfaccia")

    if a.pid_web:
        if aspetta_fine(a.pid_web, 30):
            log("interfaccia %d uscita" % a.pid_web)
        else:
            log("interfaccia %d ancora viva dopo 30 s: la chiudo" % a.pid_web)
            if WINDOWS:
                subprocess.call(["taskkill", "/PID", str(a.pid_web), "/F"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    chiudi_agente(a.ponte, log)
    chiudi_finestre(log)
    # qualche secondo perche' la finestra del vecchio menu sia davvero chiusa
    time.sleep(3)
    nuovo = os.path.join(QUI, "MASTERFLEX.bat.nuovo")
    if os.path.exists(nuovo):
        try:
            os.replace(nuovo, os.path.join(QUI, "MASTERFLEX.bat"))
            log("MASTERFLEX.bat aggiornato")
        except OSError as exc:
            log("MASTERFLEX.bat non sostituito: %s" % exc)
    if not WINDOWS:
        log("fuori da Windows non riparto: lanciare a mano")
        return 0
    subprocess.Popen('start "Masterflex D1008" cmd /c call "%s" automatico'
                     % os.path.join(QUI, "MASTERFLEX.bat"), shell=True, cwd=QUI)
    log("ripartito: MASTERFLEX.bat automatico")
    return 0


if __name__ == "__main__":
    sys.exit(main())
