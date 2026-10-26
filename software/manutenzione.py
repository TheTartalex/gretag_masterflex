# -*- coding: utf-8 -*-
"""
Manutenzione della macchina: il lavoro che sta dietro al menu di MASTERFLEX.bat.

Solo libreria standard, di proposito: deve funzionare anche quando numpy e le
altre librerie non sono ancora installate, o sono quelle sbagliate.

    python manutenzione.py librerie              installa o corregge le librerie
    python manutenzione.py verifica              controlla versioni e moduli
    python manutenzione.py macchina              driver, scheda, SDK
    python manutenzione.py aggiorna [sorgente]   aggiorna il programma
    python manutenzione.py avvio on <modo>|off|stato
    python manutenzione.py diagnosi              raccoglie lo stato in un file
    python manutenzione.py offline               prepara pacchetti\\ per l'installazione senza rete
    python manutenzione.py firmware [--usa-1.15 cartella]
                                                 firmware, flag e posizioni del carrello
    python manutenzione.py porta-carrello        mette gli strumenti Gretag sulla COM5
    python manutenzione.py pulisci-vecchio       toglie i resti della versione con windows\\*.bat

Codice d'uscita 0 = tutto bene.
"""

from __future__ import print_function

import datetime
import hashlib
import io
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import zipfile

QUI = os.path.dirname(os.path.abspath(__file__))
REQUIREMENTS = os.path.join(QUI, "requirements.txt")
PACCHETTI = os.path.join(QUI, "pacchetti")
PONTE = r"C:\MasterflexD\ponte"
SCANNER = r"C:\MasterflexD\Scanner"

# L'installer di Python che si scarica quando manca. Impronta controllata il
# 26/10/2026 sul file di python.org, firmato da Steve Dower (Python Release
# Signing, 7ED1 0B65 31D7 C8E1 BC29 6021 FC62 4643 4870 34E5).
PYTHON_URL = "https://www.python.org/ftp/python/3.8.10/python-3.8.10.exe"
PYTHON_FILE = "python-3.8.10.exe"
PYTHON_SHA256 = "ad07633a1f0cd795f3bf9da33729f662281df196b4567fa795829f3bb38a30ac"

# Da dove scaricare un aggiornamento del programma. Funziona solo se il
# repository e' pubblico; altrimenti si scarica lo zip a mano da GitHub e si
# indica il file.
AGGIORNAMENTO_URL = ("https://github.com/TheTartalex/gretag_masterflex/"
                     "archive/refs/heads/main.zip")

# Quello che un aggiornamento puo' sostituire. Tutto il resto della cartella
# (ordini.db, scansioni, calibrazione, impostazioni.json, pacchetti) sono
# dati e non si toccano.
CODICE = ["filmscan", "ponte", "run.py", "selftest.py", "manutenzione.py",
          "requirements.txt", "CLAUDE.md", "README.md", "MASTERFLEX.bat"]

AVVIO_NOME = "Masterflex D1008.bat"
MODI_AVVIO = ("avvia", "avvia-senza-motori", "simulazione")


def titolo(testo):
    print("")
    print("  " + testo)
    print("  " + "-" * len(testo))


def esegui(cmd, **kw):
    """Lancia un comando e ne restituisce (codice, testo). Non solleva mai."""
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, **kw)
        out, _ = p.communicate()
        return p.returncode, out.decode("mbcs" if os.name == "nt" else "utf-8",
                                        "replace")
    except (OSError, ValueError) as e:
        return -1, str(e)


def bit_python():
    return struct.calcsize("P") * 8


# --------------------------------------------------------------------------
# Librerie
# --------------------------------------------------------------------------

def versioni_richieste():
    """{'numpy': '1.22.4', ...} letto da requirements.txt."""
    richieste = {}
    with io.open(REQUIREMENTS, encoding="utf-8") as f:
        for riga in f:
            m = re.match(r"^\s*([A-Za-z0-9_.-]+)==([0-9][^\s\\;]*)", riga)
            if m:
                richieste[m.group(1).lower().replace("_", "-")] = m.group(2)
    return richieste


def versioni_installate():
    """Versioni presenti, lette dai metadati dei pacchetti.

    Non importa le librerie: un numpy che usa AVX su una CPU senza AVX fa
    cadere Python all'import, e qui deve solo dire che versione e'.
    """
    try:
        from importlib import metadata
    except ImportError:                                     # Python < 3.8
        return None
    presenti = {}
    for d in metadata.distributions():
        nome = (d.metadata["Name"] or "").lower().replace("_", "-")
        if nome:
            presenti[nome] = d.version
    return presenti


def confronta_librerie():
    """[(nome, richiesta, installata)] per le librerie fuori posto."""
    richieste = versioni_richieste()
    presenti = versioni_installate() or {}
    return [(n, v, presenti.get(n)) for n, v in sorted(richieste.items())
            if presenti.get(n) != v]


def assicura_pip():
    codice, _ = esegui([sys.executable, "-m", "pip", "--version"])
    if codice == 0:
        return True
    print("  pip manca: lo installo con ensurepip...")
    codice, testo = esegui([sys.executable, "-m", "ensurepip", "--default-pip"])
    if codice != 0:
        print(testo)
    return codice == 0


def cmd_librerie(argv):
    """Installa le librerie alle versioni giuste, o corregge quelle sbagliate.

    Se c'e' numpy 1.24.4 (che sulla CPU senza AVX fa cadere Python) viene
    sostituito con la 1.22.4: e' lo stesso lavoro che faceva correggi-numpy.
    """
    titolo("Librerie")
    if bit_python() != 32:
        print("  ATTENZIONE: questo Python e' a %d bit. Le librerie si installano,"
              % bit_python())
        print("  ma l'SDK dello scanner (32 bit) non si potra' caricare.")
    if not assicura_pip():
        print("  ERRORE: pip non disponibile. Reinstallare Python 3.8.10.")
        return 1

    fuori = confronta_librerie()
    if not fuori:
        print("  Tutte le librerie sono gia' alle versioni giuste.")
        return 0
    for nome, voluta, presente in fuori:
        print("  %-12s %-12s -> %s" % (nome, presente or "assente", voluta))

    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
            "--only-binary=:all:", "-r", REQUIREMENTS]
    tentativi = []
    if os.path.isdir(PACCHETTI) and any(f.endswith(".whl")
                                        for f in os.listdir(PACCHETTI)):
        tentativi.append(("dalla cartella pacchetti, senza rete",
                          base + ["--no-index", "--find-links", PACCHETTI]))
    tentativi.append(("da internet (PyPI)", base))

    for descrizione, cmd in tentativi:
        print("")
        print("  Installo %s..." % descrizione)
        codice = subprocess.call(cmd)
        if codice == 0 and not confronta_librerie():
            print("")
            print("  Librerie a posto.")
            return 0
    print("")
    print("  ERRORE: installazione non riuscita. Cause tipiche:")
    print("   - niente internet: preparare pacchetti\\ su un altro PC con la")
    print("     voce \"prepara pacchetto offline\" del menu, poi riprovare;")
    print("   - \"mesonpy requires at least Python 3.10\": qualcuno ha cambiato")
    print("     una versione in requirements.txt oltre l'ultima wheel a 32 bit;")
    print("   - \"hashes do not match\": un file scaricato e' rovinato o non e'")
    print("     quello atteso; cancellare pacchetti\\ e riprovare.")
    return 1


def cmd_verifica(argv):
    titolo("Verifica dell'ambiente")
    errori = 0
    print("  Python   %s, %d bit, %s" % (platform.python_version(), bit_python(),
                                         sys.executable))
    if sys.version_info[:2] != (3, 8):
        print("  ATTENZIONE: serve Python 3.8 (l'ultimo per Windows 7)")
        errori += 1
    if bit_python() != 32:
        print("  ATTENZIONE: serve Python a 32 bit per l'SDK dello scanner")
        errori += 1
    fuori = confronta_librerie()
    for nome, voluta, presente in fuori:
        print("  %-8s %s invece di %s" % (nome, presente or "assente", voluta))
        errori += 1
    if not fuori:
        print("  Librerie alle versioni di requirements.txt")
    # import vero, in un processo separato: se numpy usa istruzioni che la CPU
    # non ha, a cadere e' quel processo e non questo
    codice, testo = esegui([sys.executable, "-c",
                            "import numpy, scipy.ndimage, PIL, tifffile;"
                            "import numpy as n; n.ones((64,64)).dot(n.ones((64,64)));"
                            "print('import e calcolo di prova: ok')"])
    print("  " + testo.strip().splitlines()[-1] if testo.strip() else "")
    if codice != 0:
        errori += 1
        if codice in (3221225501, -1073741795):        # 0xC000001D
            print("  ERRORE c000001d: istruzione non valida. E' il numpy che usa")
            print("  AVX su una CPU che non ce l'ha: rilanciare 'Installa / ripara'.")
    return 1 if errori else 0


# --------------------------------------------------------------------------
# Macchina: driver, scheda, SDK
# --------------------------------------------------------------------------

def agente_attivo(ponte=PONTE):
    """True se l'agente ha la scheda in mano (file pid e processo vivo)."""
    pid_file = os.path.join(ponte, "agente.pid")
    if not os.path.exists(pid_file):
        return False
    try:
        with open(pid_file) as f:
            pid = int(f.read().strip())
    except (IOError, ValueError):
        return False
    if os.name != "nt":
        return False
    codice, testo = esegui(["tasklist", "/FI", "PID eq %d" % pid, "/NH"])
    return str(pid) in testo


def cmd_macchina(argv):
    titolo("Driver, scheda e SDK")
    if os.name != "nt":
        print("  (controlli disponibili solo su Windows)")
        return 0
    problemi = 0
    windir = os.environ.get("WINDIR", r"C:\Windows")
    drivers = os.path.join(windir, "System32", "drivers")

    codice, testo = esegui(["sc", "query", "EKFrame"])
    if "RUNNING" in testo:
        print("  Servizio EKFrame: in esecuzione")
    elif codice == 1060 or "1060" in testo:
        print("  Servizio EKFrame: NON installato")
        problemi += 1
    else:
        print("  Servizio EKFrame: presente ma fermo")
        problemi += 1
    _, qc = esegui(["sc", "qc", "EKFrame"])
    m = re.search(r"BINARY_PATH_NAME\s*:\s*(.+)", qc)
    if m:
        print("  File del driver:  %s" % m.group(1).strip())

    vecchio = os.path.join(drivers, "ekframe.sys")
    if os.path.exists(vecchio) and not os.path.exists(
            os.path.join(drivers, "EKframePnP.sys")):
        print("  Driver originale ekframe.sys presente (va bene sulla produzione).")
    elif os.path.exists(vecchio):
        print("  ATTENZIONE: ci sono sia ekframe.sys sia EKframePnP.sys.")
        print("  Il vecchio lascia un dispositivo 'zombie' (Codice 31):")
        print("  lanciare rimuovi-driver-vecchio.bat del pacchetto EKframePnP.")
        problemi += 1

    _, pnp = esegui(["wmic", "path", "Win32_PnPEntity", "where",
                     "DeviceID like '%VEN_11B2%'", "get",
                     "Name,ConfigManagerErrorCode", "/format:list"])
    nomi = re.findall(r"Name=(.+)", pnp)
    codici = re.findall(r"ConfigManagerErrorCode=(\d+)", pnp)
    if not nomi:
        print("  Scheda IAQ: non vista da Windows")
        problemi += 1
    for nome, cod in zip(nomi, codici):
        stato = "funziona" if cod == "0" else "Codice %s" % cod
        print("  Scheda: %s - %s" % (nome.strip(), stato))
        if cod != "0":
            problemi += 1
            if cod == "31":
                print("    Codice 31 = nome occupato dal vecchio driver: vedi sopra.")

    dll = os.path.join(SCANNER, "Bin", "scn_dll.dll")
    if os.path.exists(dll):
        print("  SDK: %s" % dll)
    else:
        print("  SDK: MANCA %s" % dll)
        print("       copiare li' la cartella Scanner del software originale")
        problemi += 1

    if agente_attivo():
        print("  Agente: in esecuzione (ha la scheda in mano)")
    return 1 if problemi else 0


# --------------------------------------------------------------------------
# Aggiornamento del programma
# --------------------------------------------------------------------------

def _scarica(url, destinazione, sha256=None):
    try:
        from urllib.request import urlopen
    except ImportError:
        from urllib2 import urlopen          # noqa
    print("  Scarico %s" % url)
    h = hashlib.sha256()
    risposta = urlopen(url, timeout=60)
    with open(destinazione + ".parziale", "wb") as f:
        while True:
            blocco = risposta.read(1 << 16)
            if not blocco:
                break
            h.update(blocco)
            f.write(blocco)
    if sha256 and h.hexdigest() != sha256:
        os.remove(destinazione + ".parziale")
        raise IOError("impronta sbagliata per %s: il file non e' quello atteso"
                      % os.path.basename(destinazione))
    if os.path.exists(destinazione):
        os.remove(destinazione)
    os.rename(destinazione + ".parziale", destinazione)
    return destinazione


def _trova_software(radice):
    """La cartella 'software' dentro un repository estratto o copiato."""
    for base, cartelle, file in os.walk(radice):
        if "run.py" in file and "filmscan" in cartelle and "ponte" in cartelle:
            return base
    return None


def cmd_aggiorna(argv):
    """Sostituisce il codice con una versione nuova, tenendo tutti i dati.

    sorgente: una cartella (il repository o la sua cartella software), uno zip
    scaricato da GitHub, oppure niente per scaricare l'ultima versione.
    Il codice vecchio va in backup\\codice-<data>. MASTERFLEX.bat non si puo'
    sovrascrivere mentre gira: si scrive come MASTERFLEX.bat.nuovo e lo
    sostituisce il menu appena questo programma finisce.
    """
    titolo("Aggiornamento del programma")
    # una barra finale prima delle virgolette arriva come virgoletta: via
    sorgente = (argv[0] if argv else "").strip().strip('"').rstrip("\\/")
    lavoro = os.path.join(QUI, "backup", "_aggiornamento")
    if os.path.isdir(lavoro):
        shutil.rmtree(lavoro)
    os.makedirs(lavoro)

    try:
        if not sorgente:
            sorgente = _scarica(AGGIORNAMENTO_URL, os.path.join(lavoro, "main.zip"))
        if os.path.isfile(sorgente) and sorgente.lower().endswith(".zip"):
            with zipfile.ZipFile(sorgente) as z:
                z.extractall(os.path.join(lavoro, "estratto"))
            sorgente = os.path.join(lavoro, "estratto")
    except Exception as e:                                  # noqa: BLE001
        print("  Non riesco a prendere l'aggiornamento: %s" % e)
        print("  Se il repository e' privato: scaricare lo zip da GitHub")
        print("  (Code > Download ZIP), copiarlo qui e indicarlo al menu.")
        return 1

    nuovo = _trova_software(sorgente) if os.path.isdir(sorgente) else None
    if not nuovo:
        print("  In %s non c'e' il programma (manca software\\run.py)." % sorgente)
        return 1
    if os.path.abspath(nuovo) == os.path.abspath(QUI):
        print("  La sorgente e' questa stessa cartella: niente da fare.")
        return 0

    data = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(QUI, "backup", "codice-" + data)
    os.makedirs(backup)
    for nome in CODICE:
        vecchio = os.path.join(QUI, nome)
        fresco = os.path.join(nuovo, nome)
        if not os.path.exists(fresco):
            continue
        if os.path.exists(vecchio):
            (shutil.copytree if os.path.isdir(vecchio) else shutil.copy2)(
                vecchio, os.path.join(backup, nome))
        if nome == "MASTERFLEX.bat":
            shutil.copy2(fresco, vecchio + ".nuovo")
        elif os.path.isdir(fresco):
            if os.path.isdir(vecchio):
                shutil.rmtree(vecchio)
            shutil.copytree(fresco, vecchio,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(fresco, vecchio)
        print("  aggiornato: %s" % nome)
    shutil.rmtree(lavoro, ignore_errors=True)
    print("")
    print("  Codice precedente salvato in %s" % backup)
    cmd_pulisci_vecchio([])
    return 0


def cmd_pulisci_vecchio(argv):
    """Toglie i resti della versione di settembre, sostituita da MASTERFLEX.bat.

    Il collegamento in Esecuzione automatica puntava a windows\\avvia.bat, che
    dopo l'aggiornamento non esiste piu': all'accensione si sarebbe aperta
    una finestra d'errore.
    """
    tolti = []
    vecchia = os.path.join(QUI, "windows")
    if os.path.isdir(vecchia):
        shutil.rmtree(vecchia, ignore_errors=True)
        tolti.append("windows\\")
    for nome in ("strumenti", os.path.join("filmscan", "ui.py")):
        p = os.path.join(QUI, nome)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
            tolti.append(nome)
        elif os.path.isfile(p):
            os.remove(p)
            tolti.append(nome)
    lnk = os.path.join(_cartella_esecuzione_automatica(), "Masterflex D1008.lnk")
    if os.path.exists(lnk):
        os.remove(lnk)
        tolti.append("vecchio collegamento di avvio automatico")
    for t in tolti:
        print("  tolto: %s" % t)
    return 0


# --------------------------------------------------------------------------
# Avvio automatico
# --------------------------------------------------------------------------

def _cartella_esecuzione_automatica():
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                        "Start Menu", "Programs", "Startup")


def cmd_avvio(argv):
    """avvio on <modo> | off | stato

    Si usa un piccolo .bat in Esecuzione automatica invece di un collegamento
    .lnk: non serve PowerShell ne' COM, e si apre con il Blocco note per
    vedere cosa fa.
    """
    cartella = _cartella_esecuzione_automatica()
    file_avvio = os.path.join(cartella, AVVIO_NOME)
    azione = argv[0] if argv else "stato"

    if azione == "off":
        for p in (file_avvio, os.path.join(cartella, "Masterflex D1008.lnk")):
            if os.path.exists(p):
                os.remove(p)
        print("  Avvio automatico DISATTIVATO.")
        return 0

    if azione == "on":
        modo = argv[1] if len(argv) > 1 else "avvia-senza-motori"
        if modo not in MODI_AVVIO:
            print("  modo sconosciuto: %s" % modo)
            return 1
        if not os.path.isdir(cartella):
            os.makedirs(cartella)
        bat = os.path.join(QUI, "MASTERFLEX.bat")
        # "avvia" chiede conferma a ogni avvio; scelto qui, la conferma e'
        # gia' data, e all'accensione non c'e' nessuno a rispondere.
        argomenti = "avvia si" if modo == "avvia" else modo
        with open(file_avvio, "w") as f:
            f.write("@echo off\r\n")
            f.write("rem Creato da MASTERFLEX.bat: si toglie dal suo menu.\r\n")
            f.write('cd /d "%s"\r\n' % QUI)
            f.write('call "%s" %s\r\n' % (bat, argomenti))
        print("  Avvio automatico ATTIVATO: all'accensione parte '%s'." % modo)
        return 0

    if os.path.exists(file_avvio):
        with open(file_avvio) as f:
            riga = [r for r in f.read().splitlines() if r.startswith("call")]
        modo = riga[0].split('"')[-1].strip() if riga else "?"
        print("  Avvio automatico: ATTIVO (%s)" % modo)
    else:
        print("  Avvio automatico: non attivo")
    return 0


# --------------------------------------------------------------------------
# Diagnosi
# --------------------------------------------------------------------------

def _coda(percorso, righe=60):
    try:
        with io.open(percorso, encoding="latin-1") as f:
            return "".join(f.readlines()[-righe:])
    except (IOError, OSError) as e:
        return "(non leggibile: %s)\n" % e


def _trova_ektest():
    for p in (os.path.join(QUI, "..", "driver", "EKframePnP", "pacchetto", "ektest.exe"),
              os.path.join(QUI, "ektest.exe"),
              r"C:\MasterflexD\EKframePnP\ektest.exe"):
        if os.path.exists(p):
            return os.path.abspath(p)
    return None


def cmd_diagnosi(argv):
    """Raccoglie in un solo file tutto quello che serve per capire un problema.

    Il file si allega alla segnalazione; non va nel repository (percorsi e
    nomi della macchina).
    """
    cartella = os.path.join(QUI, "diagnosi")
    if not os.path.isdir(cartella):
        os.makedirs(cartella)
    nome = os.path.join(cartella, "diagnosi-%s.txt"
                        % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    vero_stdout = sys.stdout
    buf = io.StringIO()

    class Doppio(object):
        def write(self, t):
            vero_stdout.write(t)
            buf.write(t if isinstance(t, type(u"")) else t.decode("utf-8", "replace"))

        def flush(self):
            vero_stdout.flush()

    sys.stdout = Doppio()
    try:
        print("Diagnosi Masterflex D1008 - %s" % datetime.datetime.now())
        print("Sistema: %s %s, macchina %s" % (platform.system(), platform.version(),
                                               platform.machine()))
        if os.name == "nt":
            _, cpu = esegui(["wmic", "cpu", "get", "Name", "/format:list"])
            print("CPU: %s" % " ".join(re.findall(r"Name=(.+)", cpu)).strip())
        cmd_verifica([])
        cmd_macchina([])
        cmd_avvio(["stato"])
        cmd_firmware([])

        titolo("Agente e ponte")
        print("  Agente attivo: %s" % ("si" if agente_attivo() else "no"))
        log_dir = os.path.join(PONTE, "log")
        if os.path.isdir(log_dir):
            ultimi = sorted(os.listdir(log_dir))[-1:]
            for f in ultimi:
                print("  --- %s (ultime righe)" % f)
                print(_coda(os.path.join(log_dir, f), 80))
        sdk_log = os.path.join(SCANNER, "SDKCacheDir", "log_0000.txt")
        if os.path.exists(sdk_log):
            print("  --- log dell'SDK (ultime righe)")
            print(_coda(sdk_log, 60))

        titolo("ektest (driver EKframePnP)")
        ektest = _trova_ektest()
        if not ektest:
            print("  ektest.exe non trovato: saltato")
        elif agente_attivo():
            print("  Saltato: l'agente ha la scheda in mano, e chiudendo l'handle")
            print("  ektest la spegnerebbe anche per lui. Chiudere filmscan e rifare.")
        else:
            # solo "info": legge risorse, registri e contatori, non muove niente
            _, testo = esegui([ektest, "info"])
            print(testo)
    finally:
        sys.stdout = vero_stdout
    with io.open(nome, "w", encoding="utf-8") as f:
        f.write(buf.getvalue())
    print("")
    print("  Diagnosi salvata in:")
    print("    %s" % nome)
    print("  Allegarla alla segnalazione del problema.")
    return 0


# --------------------------------------------------------------------------
# Firmware del carrello Capri (COM5)
# --------------------------------------------------------------------------

# La versione che serve. Fino al 2 agosto 2025 il carrello aveva la 1.15
# (log del selftest: compilata il 28/11/2002, Para. Flash Vers. 7.00). Quel
# giorno il software originale l'ha riportato da solo alla 1.13, perche'
# AutomaticUpdate\Capri1FirmwareVersion.ini chiede UpdateToVersion=00000071:
# backup del Parameter Flash delle 14:06 con M1v15, delle 14:08 con M1v13.
# Era gia' successo l'8/4/2003. Il flash ha lo stesso formato (versione 7)
# con le due versioni, e la 1.13 ha lavorato dal 2002 al 2004: la 1.15 resta
# quella da tenere, ma difficilmente e' la causa del problema delle guide.
VERSIONE_RICHIESTA = "1.15"

# Come si riconoscono i file .a37 (S-record Motorola): la versione e' un
# numero formattato a runtime, nel file c'e' solo la data di compilazione.
# Il firmware dello slave non ha nemmeno quella: si riconosce dall'impronta.
DATE_FIRMWARE = {"Jun  3 2002": "1.13", "Nov 28 2002": "1.15"}
IMPRONTE_FIRMWARE = {
    "5667f2f8703843ddd137f34d1bbac16dc1ded09a2778db1beae211cdd5a9102d": "1.13",
    "3f31399cf3bb011d1055d3a7fd9659a168bc3288daf979f50b27480b4fb8da41": "1.13",
}

CARTELLE_CAPRI = [r"C:\MasterflexD\CapriFirmware",
                  r"C:\MasterflexD\Scanner\CapriFirmware",
                  r"C:\MasterflexD\InstallService\CapriFirmware"]


def trova_capri(indicata=None):
    """La cartella CapriFirmware (quella con AutomaticUpdate\\)."""
    candidati = ([indicata] if indicata else []) + CARTELLE_CAPRI
    for c in candidati:
        if c and os.path.isfile(os.path.join(c, "AutomaticUpdate",
                                             "Capri1FirmwareVersion.ini")):
            return c
    radice = r"C:\MasterflexD"
    if os.path.isdir(radice):
        for base, cartelle, file in os.walk(radice):
            if base.count(os.sep) - radice.count(os.sep) > 4:
                cartelle[:] = []
                continue
            if "Capri1FirmwareVersion.ini" in file:
                return os.path.dirname(base)
    return None


def versione_da_hex(testo):
    """'00000071' -> '1.13' (il numero esadecimale e' la versione per cento)."""
    try:
        n = int(testo.strip(), 16)
    except ValueError:
        return None
    return "%d.%02d" % (n // 100, n % 100)


def versione_file_a37(percorso):
    """Versione di un file firmware .a37: dall'impronta, se nota, altrimenti
    dalla data di compilazione scritta dentro."""
    try:
        nota = IMPRONTE_FIRMWARE.get(_sha256(percorso))
    except (IOError, OSError):
        return None
    if nota:
        return nota
    try:
        dati = bytearray()
        with io.open(percorso, encoding="latin-1") as f:
            for riga in f:
                riga = riga.strip()
                if riga[:2] in ("S1", "S2", "S3"):
                    lung_ind = {"S1": 2, "S2": 3, "S3": 4}[riga[:2]]
                    n = int(riga[2:4], 16)
                    dati += bytes(bytearray.fromhex(riga[4 + 2 * lung_ind:2 + 2 * n]))
    except (IOError, OSError, ValueError):
        return None
    for data, versione in DATE_FIRMWARE.items():
        if data.encode() in dati:
            return versione
    return None


def ultimo_backup_parametri(capri):
    """(data, master, slave, file) del backup del Parameter Flash piu' recente.

    Il software originale ne scrive uno a ogni aggiornamento del firmware,
    con le versioni nel nome: 'ParameterBackup M1v15 S1v15 2-8-2025 14-6-19.bin'.
    """
    cartella = os.path.join(capri, "AutomaticUpdate")
    migliore = None
    for nome in os.listdir(cartella):
        m = re.match(r"ParameterBackup M(\d)v(\d+) S(\d)v(\d+) "
                     r"(\d+)-(\d+)-(\d+) (\d+)-(\d+)-(\d+)\.bin$", nome)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        quando = datetime.datetime(g[6], g[5], g[4], g[7], g[8], g[9])
        voce = (quando, "%d.%02d" % (g[0], g[1]), "%d.%02d" % (g[2], g[3]), nome)
        if migliore is None or voce[0] > migliore[0]:
            migliore = voce
    return migliore


# Il Parameter Flash del carrello, come lo salva il software originale (297
# byte, versione 7 sia con la 1.13 sia con la 1.15). Struttura ricavata dalla
# funzione "Print parameter flash" del firmware master 1.13 (0x7d62) e
# confermata sui 125 backup del pacchetto: nel file i campi in coda stanno
# 0x0b byte piu' avanti che nella struttura in RAM.
PF_POSIZIONI = 0x4b         # 20 record da 8 byte: int32 posizione, uint16 velocita', ...
PF_MOTORI = ("tamburo", "guida sinistra", "guida destra", "lente", "CCD")
PF_FORMATI = ("110", "135", "APS", "servizio")
# Byte dei flag (RAM 0x100 -> file 0x10b): bit -> (numero Compad, nome)
PF_FLAG = {4: (3, "APS Handling"), 5: (2, "Check Revolver"), 6: (1, "Read MOF"),
           7: (0, "Eject short"), 3: (4, "Crash Detect"), 2: (5, "Eject to Back"),
           1: (6, "No Reset (all'accensione niente motori)"), 0: (7, "Special")}
# Secondo byte (RAM 0x110 -> file 0x11b)
PF_FLAG2 = {7: (8, "Packet Mode"), 6: (9, "Black Mask Detect")}
# Come li vuole Gretag (Features_and_Installation.txt e l'ini automatico)
PF_FLAG_CONSIGLIATI = {0: 1, 1: 1, 4: 1, 6: 1, 7: 0, 8: 0, 9: 1}


def leggi_parametri(percorso):
    """Decodifica un ParameterBackup*.bin. None se il formato non e' quello noto."""
    with open(percorso, "rb") as f:
        d = f.read()
    if len(d) != 297 or d[8:9] != b"\x07":
        return None
    posizioni = {}
    for i, motore in enumerate(PF_MOTORI):
        for j, formato in enumerate(PF_FORMATI):
            a = PF_POSIZIONI + 8 * (4 * i + j)
            posizioni[(motore, formato)] = struct.unpack(">i", d[a:a + 4])[0]
    flag = {}
    for bit, (n, nome) in PF_FLAG.items():
        flag[n] = (nome, (d[0x10b] >> bit) & 1)
    for bit, (n, nome) in PF_FLAG2.items():
        flag[n] = (nome, (d[0x11b] >> bit) & 1)
    return {
        "posizioni": posizioni,
        "flag": flag,
        "formato_avvio": d[0x10d],
        "rivelatore_fotogramma": struct.unpack(">I", d[0x10f:0x113])[0],
        "limite_ad_pellicola": struct.unpack(">H", d[0x113:0x115])[0],
        "matricola": struct.unpack(">I", d[0x115:0x119])[0],
    }


def _stampa_parametri(par):
    print("  Matricola del carrello: %d" % par["matricola"])
    for n in sorted(par["flag"]):
        nome, valore = par["flag"][n]
        voluto = PF_FLAG_CONSIGLIATI.get(n)
        nota = ""
        if voluto is not None and valore != voluto:
            nota = "   <-- Gretag consiglia %d" % voluto
        print("    flag %d %-42s %d%s" % (n, nome, valore, nota))
    print("  Posizioni salvate (passi dallo zero)   110   135   APS  servizio")
    for motore in PF_MOTORI:
        print("    %-34s" % motore + "".join(
            "%6d" % par["posizioni"][(motore, f)] for f in PF_FORMATI))


def _leggi_ini(percorso):
    sezioni, attuale = {}, None
    with io.open(percorso, encoding="latin-1") as f:
        for riga in f:
            riga = riga.strip()
            if riga.startswith("[") and riga.endswith("]"):
                attuale = sezioni.setdefault(riga[1:-1], {})
            elif "=" in riga and attuale is not None:
                k, v = riga.split("=", 1)
                attuale[k.strip()] = v.strip()
    return sezioni


def cmd_firmware(argv):
    """Controlla che il carrello sia (e resti) alla 1.15.

    Due cose separate, perche' possono non coincidere:
      - la versione che il carrello ha adesso (dall'ultimo backup scritto dal
        software originale; dal vivo si legge con Compad, CP_FirmwareDownload,
        tasto Get Version);
      - la versione che il software originale imporra' al prossimo avvio
        (Capri1FirmwareVersion.ini): se e' diversa dalla 1.15, riscarica la
        sua al posto di quella giusta.

    Con --usa-1.15 <cartella>, se li' ci sono i file della 1.15 (riconosciuti
    dalla data di compilazione), li copia in AutomaticUpdate\\ e corregge
    l'ini, cosi' il software originale porta il carrello alla 1.15 invece di
    toglierla. L'ini originale resta come .prima-di-noi.
    """
    titolo("Firmware del carrello (richiesta: %s)" % VERSIONE_RICHIESTA)
    indicata = None
    fonte_115 = None
    if "--cartella" in argv:
        indicata = argv[argv.index("--cartella") + 1]
    if "--usa-1.15" in argv:
        fonte_115 = argv[argv.index("--usa-1.15") + 1]
    capri = trova_capri(indicata)
    if not capri:
        print("  Cartella CapriFirmware non trovata sotto C:\\MasterflexD.")
        print("  Copiarla li' (C:\\MasterflexD\\CapriFirmware) o indicarla con --cartella.")
        return 1
    print("  Cartella: %s" % capri)
    problemi = 0

    backup = ultimo_backup_parametri(capri)
    if backup:
        quando, master, slave, nome = backup
        print("  Ultima versione registrata: master %s, slave %s (%s)"
              % (master, slave, quando.strftime("%d/%m/%Y %H:%M")))
        if master != VERSIONE_RICHIESTA or slave != VERSIONE_RICHIESTA:
            print("  ATTENZIONE: il carrello non e' alla %s." % VERSIONE_RICHIESTA)
            problemi += 1
        par = leggi_parametri(os.path.join(capri, "AutomaticUpdate", nome))
        if par:
            _stampa_parametri(par)
    else:
        print("  Nessun backup del Parameter Flash: versione attuale non nota.")

    ini_path = os.path.join(capri, "AutomaticUpdate", "Capri1FirmwareVersion.ini")
    ini = _leggi_ini(ini_path)
    imposta = {}
    for parte in ("Master", "Slave"):
        sez = ini.get(parte, {})
        voluta = versione_da_hex(sez.get("UpdateToVersion", ""))
        f = os.path.join(capri, "AutomaticUpdate", sez.get("FileName", ""))
        nel_file = versione_file_a37(f) if os.path.isfile(f) else None
        imposta[parte] = voluta
        print("  All'avvio il software originale impone al %s la %s (file %s: %s)"
              % (parte.lower(), voluta, sez.get("FileName", "?"),
                 nel_file or "non riconosciuto"))
        if voluta != VERSIONE_RICHIESTA:
            problemi += 1
    if any(v != VERSIONE_RICHIESTA for v in imposta.values()):
        print("  ATTENZIONE: al prossimo avvio del programma originale il carrello")
        print("  verra' riportato alla %s, come e' successo il 2 agosto 2025."
              % ", ".join(sorted(set(v for v in imposta.values() if v))))

    if fonte_115:
        return _installa_115(capri, ini_path, fonte_115)

    if problemi:
        print("")
        print("  Per tornare alla %s servono i file del firmware 1.15 (master e" % VERSIONE_RICHIESTA)
        print("  slave, compilati il 28/11/2002): non sono nel pacchetto CapriFirmware,")
        print("  che contiene solo la 1.13. Cercarli sulla macchina di produzione")
        print("  (stessa cartella AutomaticUpdate) e poi:")
        print("    python manutenzione.py firmware --usa-1.15 <cartella con i file>")
    else:
        print("  Tutto alla %s." % VERSIONE_RICHIESTA)
    return 1 if problemi else 0


def _installa_115(capri, ini_path, fonte):
    trovati = {}
    slave_candidati = []
    for nome in os.listdir(fonte):
        if not nome.lower().endswith(".a37"):
            continue
        p = os.path.join(fonte, nome)
        # master e slave si distinguono dall'intestazione S0 ("apsctrl")
        with io.open(p, encoding="latin-1") as f:
            slave = "6170736374726C" in f.readline().upper()
        versione = versione_file_a37(p)
        if not slave and versione == VERSIONE_RICHIESTA:
            trovati["Master"] = p
        elif slave and versione == VERSIONE_RICHIESTA:
            trovati["Slave"] = p
        elif slave and versione is None:
            slave_candidati.append(p)
    # Lo slave non porta la data: se accanto a un master 1.15 c'e' un solo
    # slave che non e' la 1.13 conosciuta, e' quello della stessa versione.
    if "Master" in trovati and "Slave" not in trovati and len(slave_candidati) == 1:
        trovati["Slave"] = slave_candidati[0]
        print("  Slave riconosciuto per esclusione: %s"
              % os.path.basename(slave_candidati[0]))
    if set(trovati) != {"Master", "Slave"}:
        print("  In %s non ci sono entrambi i file della %s (trovati: %s)."
              % (fonte, VERSIONE_RICHIESTA, ", ".join(sorted(trovati)) or "nessuno"))
        return 1
    dest = os.path.join(capri, "AutomaticUpdate")
    copia = ini_path + ".prima-di-noi"
    if not os.path.exists(copia):
        shutil.copy2(ini_path, copia)
    nuovi = {"Master": "MasterV115.a37", "Slave": "SlaveV115.a37"}
    for parte, p in trovati.items():
        shutil.copy2(p, os.path.join(dest, nuovi[parte]))
    with io.open(ini_path, encoding="latin-1") as f:
        testo = f.read()
    for parte in ("Master", "Slave"):
        testo = re.sub(r"(\[%s\][^\[]*?FileName=)[^\r\n]*" % parte,
                       r"\g<1>" + nuovi[parte], testo)
        testo = re.sub(r"(\[%s\][^\[]*?UpdateToVersion=)[^\r\n]*" % parte,
                       r"\g<1>00000073", testo)
    with io.open(ini_path, "w", encoding="latin-1", newline="") as f:
        f.write(testo)
    print("  Copiati MasterV115.a37 e SlaveV115.a37, ini aggiornato (copia")
    print("  dell'originale: %s)." % os.path.basename(copia))
    print("  Al prossimo avvio il software originale portera' il carrello alla 1.15.")
    print("  Dopo: Compad, CP_Change_Flags*.ini, controllare i flag come da")
    print("  Features_and_Installation.txt (in particolare Crash Det e No Reset).")
    return 0


def cmd_porta_carrello(argv):
    """Mette sulla COM5 gli strumenti Gretag che non ci sono gia'.

    Nel pacchetto CapriFirmware Compad e' impostato su ComPort=4 (COM5, gli
    indici partono da 0), ma SelfTestInterface\\selftest.ini ha ComPort=0,
    cioe' COM1: il selftest cosi' non trova il carrello. Gli ini cambiati
    restano anche come .prima-di-noi.
    """
    indicata = argv[argv.index("--cartella") + 1] if "--cartella" in argv else None
    capri = trova_capri(indicata)
    if not capri:
        print("  Cartella CapriFirmware non trovata sotto C:\\MasterflexD.")
        return 1
    cambiati = 0
    for sotto in ("SelfTestInterface", "Compad2_5"):
        cartella = os.path.join(capri, sotto)
        if not os.path.isdir(cartella):
            continue
        for nome in sorted(os.listdir(cartella)):
            if not nome.lower().endswith(".ini"):
                continue
            p = os.path.join(cartella, nome)
            with io.open(p, encoding="latin-1", newline="") as f:
                testo = f.read()
            nuovo = re.sub(r"(?m)^(ComPort=)(\d+)", r"\g<1>4", testo)
            if nuovo == testo:
                continue
            copia = p + ".prima-di-noi"
            if not os.path.exists(copia):
                shutil.copy2(p, copia)
            with io.open(p, "w", encoding="latin-1", newline="") as f:
                f.write(nuovo)
            print("  %s\\%s: porta portata a COM5" % (sotto, nome))
            cambiati += 1
    if not cambiati:
        print("  Strumenti del carrello gia' sulla COM5.")
    return 0


# --------------------------------------------------------------------------
# Pacchetto per l'installazione senza rete
# --------------------------------------------------------------------------

def cmd_offline(argv):
    """Riempie pacchetti\\ con l'installer di Python e le wheel giuste.

    Si puo' fare da qualunque PC con Python e internet, anche moderno e a 64
    bit: le wheel si chiedono esplicitamente per Windows 32 bit e Python 3.8.
    Poi si copia la cartella sulla macchina e "Installa" non usa la rete.
    """
    titolo("Pacchetto per l'installazione senza rete")
    if not os.path.isdir(PACCHETTI):
        os.makedirs(PACCHETTI)
    installer = os.path.join(PACCHETTI, PYTHON_FILE)
    try:
        if not (os.path.exists(installer) and _sha256(installer) == PYTHON_SHA256):
            _scarica(PYTHON_URL, installer, PYTHON_SHA256)
        print("  Python 3.8.10: ok")
    except Exception as e:                                  # noqa: BLE001
        print("  ERRORE nello scaricare Python: %s" % e)
        return 1
    codice = subprocess.call([
        sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
        "-r", REQUIREMENTS, "-d", PACCHETTI, "--only-binary=:all:",
        "--platform", "win32", "--python-version", "3.8",
        "--implementation", "cp", "--abi", "cp38"])
    if codice != 0:
        print("  ERRORE nello scaricare le librerie.")
        return 1
    print("")
    for f in sorted(os.listdir(PACCHETTI)):
        print("   %s" % f)
    print("")
    print("  Fatto. Copiare l'intera cartella sulla macchina e scegliere")
    print("  \"Installa / ripara\": non servira' internet.")
    return 0


def _sha256(percorso):
    h = hashlib.sha256()
    with open(percorso, "rb") as f:
        for blocco in iter(lambda: f.read(1 << 16), b""):
            h.update(blocco)
    return h.hexdigest()


# --------------------------------------------------------------------------

COMANDI = {
    "librerie": cmd_librerie,
    "verifica": cmd_verifica,
    "macchina": cmd_macchina,
    "aggiorna": cmd_aggiorna,
    "avvio": cmd_avvio,
    "diagnosi": cmd_diagnosi,
    "offline": cmd_offline,
    "pulisci-vecchio": cmd_pulisci_vecchio,
    "firmware": cmd_firmware,
    "porta-carrello": cmd_porta_carrello,
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in COMANDI:
        print(__doc__)
        return 2
    return COMANDI[argv[0]](argv[1:]) or 0


if __name__ == "__main__":
    sys.exit(main())
