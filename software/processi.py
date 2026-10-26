# -*- coding: utf-8 -*-
"""
Chiude i programmi del software originale prima che partano i nostri.

PERCHE'
-------
Il programma originale si apre da solo all'accensione, e con lui il
GateControl, che tiene la COM5, e l'SDK, che tiene la scheda. Se restano
aperti, l'agente trova la scheda e il carrello gia' occupati: due padroni
per lo stesso motore DMA e lo stesso carrello (vedi
documentazione/software-originale/programma-originale.md).

QUALI
-----
Tutti i processi il cui eseguibile sta sotto C:\\MasterflexD, e solo quelli.
E' li' che sta tutto il software Gretag e Kodak della macchina, e niente
d'altro: Windows, TeamViewer, AnyDesk, Chrome e il nostro Python stanno
altrove, quindi non si toccano per costruzione, senza bisogno di elenchi di
eccezioni da tenere aggiornati.

Se il percorso di un processo non si legge (succede senza diritti di
amministratore con i processi di un altro utente o di sistema) ci si
affida al nome, ma solo per i programmi originali noti.

Il programma originale ha un avvio che rilancia i componenti: si chiude
finche' per qualche secondo non ricompare niente, partendo dai processi
padri, cosi' chi rilancia muore per primo.

USO
    python processi.py              elenca cosa chiuderebbe, senza toccare
    python processi.py --chiudi     chiude
"""

from __future__ import print_function

import ctypes
import ntpath
import os
import sys
import time

RADICI = [r"C:\MasterflexD"]

# Ripiego quando il percorso non si legge: i programmi originali che
# conosciamo (documentazione/software-originale). In minuscolo.
NOMI_NOTI = set(n.lower() for n in (
    "PhxStartup.exe", "PhxScanner.exe", "PhxHelp.exe", "GateControl.exe",
    "DMLCmdTransfer.exe", "Compad.exe", "SelfTestInterface.exe",
    "SensorTest.exe", "ParaStore.exe", "ScanDiag.exe", "GretagEMEval.exe",
))

TH32CS_SNAPPROCESS = 0x2
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32),
                ("cntUsage", ctypes.c_uint32),
                ("th32ProcessID", ctypes.c_uint32),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", ctypes.c_uint32),
                ("cntThreads", ctypes.c_uint32),
                ("th32ParentProcessID", ctypes.c_uint32),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_uint32),
                ("szExeFile", ctypes.c_wchar * 260)]


def _k32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    k.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
    k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_uint32)]
    k.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    return k


def _percorso(k, pid):
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.c_uint32(len(buf))
        if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return None
    finally:
        k.CloseHandle(h)


def elenca():
    """[(pid, pid del padre, nome, percorso o None)] di tutti i processi."""
    k = _k32()
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        raise OSError("elenco dei processi non disponibile (errore %d)"
                      % ctypes.get_last_error())
    voci = []
    try:
        e = _PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = k.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            pid = e.th32ProcessID
            voci.append((pid, e.th32ParentProcessID, e.szExeFile,
                         _percorso(k, pid) if pid else None))
            ok = k.Process32NextW(snap, ctypes.byref(e))
    finally:
        k.CloseHandle(snap)
    return voci


def _sotto(percorso, radice):
    # ntpath e non os.path: i percorsi sono di Windows, e cosi' la scelta si
    # prova anche altrove
    p = ntpath.normcase(ntpath.normpath(percorso))
    r = ntpath.normcase(ntpath.normpath(radice))
    return p == r or p.startswith(r.rstrip("\\") + "\\")


def _antenati(voci, pid):
    """Questo processo e chi l'ha lanciato: non si chiudono mai, anche se per
    qualche ragione stessero sotto una delle radici."""
    padre = dict((v[0], v[1]) for v in voci)
    visti = set()
    while pid and pid not in visti:
        visti.add(pid)
        pid = padre.get(pid)
    return visti


def da_chiudere(voci=None, radici=None, escludi=()):
    """I processi del software originale, padri prima dei figli."""
    voci = elenca() if voci is None else voci
    radici = radici or RADICI
    protetti = _antenati(voci, os.getpid())
    escludi = [e for e in escludi if e]
    scelti = []
    for pid, ppid, nome, percorso in voci:
        if pid in protetti or pid in (0, 4):
            continue
        if percorso:
            if any(_sotto(percorso, e) for e in escludi):
                continue
            if not any(_sotto(percorso, r) for r in radici):
                continue
            motivo = "percorso"
        elif nome.lower() in NOMI_NOTI:
            motivo = "nome"
        else:
            continue
        scelti.append((pid, ppid, nome, percorso, motivo))

    # padri prima dei figli: chi rilancia gli altri deve morire per primo
    pids = set(s[0] for s in scelti)
    padre = dict((s[0], s[1]) for s in scelti)

    def profondita(pid):
        n = 0
        while padre.get(pid) in pids and n < 50:
            pid = padre[pid]
            n += 1
        return n
    scelti.sort(key=lambda s: profondita(s[0]))
    return scelti


def chiudi(pid):
    """Termina un processo. Restituisce None se e' andata, altrimenti il
    motivo."""
    k = _k32()
    h = k.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, 0, pid)
    if not h:
        err = ctypes.get_last_error()
        if err == 5:
            return "accesso negato: serve l'amministratore"
        if err == 87:
            return None          # gia' terminato nel frattempo
        return "non si apre (errore %d)" % err
    try:
        if not k.TerminateProcess(h, 1):
            return "non si termina (errore %d)" % ctypes.get_last_error()
        k.WaitForSingleObject(h, 5000)
        return None
    finally:
        k.CloseHandle(h)


def termina_originale(quiete=5.0, massimo=60.0, radici=None, escludi=(),
                      prova=False, log=print):
    """Chiude i processi del software originale finche', per 'quiete'
    secondi di fila, non ne compare piu' nessuno (al massimo 'massimo'
    secondi). Restituisce (chiusi, falliti) come liste di "nome (pid)"."""
    chiusi, falliti, rifiutati = [], [], set()
    inizio = time.time()
    ultimo = inizio
    while True:
        try:
            trovati = da_chiudere(radici=radici, escludi=escludi)
        except OSError as exc:
            log("  %s" % exc)
            break
        # un processo che ha gia' rifiutato non si riprova a ogni giro
        trovati = [t for t in trovati if t[0] not in rifiutati]
        for pid, _ppid, nome, percorso, motivo in trovati:
            dove = percorso or ("percorso non leggibile, riconosciuto dal nome")
            if prova:
                log("  da chiudere: %s (pid %d) - %s" % (nome, pid, dove))
                chiusi.append("%s (%d)" % (nome, pid))
                continue
            errore = chiudi(pid)
            if errore:
                log("  NON chiuso: %s (pid %d): %s" % (nome, pid, errore))
                falliti.append("%s (%d)" % (nome, pid))
                rifiutati.add(pid)
            else:
                log("  chiuso: %s (pid %d) - %s" % (nome, pid, dove))
                chiusi.append("%s (%d)" % (nome, pid))
        if prova:
            break
        adesso = time.time()
        if trovati:
            ultimo = adesso
        if adesso - ultimo >= quiete or adesso - inizio >= massimo:
            break
        time.sleep(1.0)
    return chiusi, falliti


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        description="Chiude i processi del software originale (C:\\MasterflexD)")
    ap.add_argument("--chiudi", action="store_true",
                    help="chiude davvero; senza, elenca soltanto")
    ap.add_argument("--quiete", type=float, default=5.0,
                    help="secondi senza processi nuovi prima di smettere")
    ap.add_argument("--massimo", type=float, default=60.0)
    ap.add_argument("--radice", action="append",
                    help="cartella da sorvegliare (ripetibile); "
                         "di serie C:\\MasterflexD")
    a = ap.parse_args(argv)
    if os.name != "nt":
        print("  (solo su Windows)")
        return 0
    qui = os.path.dirname(os.path.abspath(__file__))
    chiusi, falliti = termina_originale(
        quiete=a.quiete, massimo=a.massimo, radici=a.radice,
        escludi=(qui, os.path.dirname(sys.executable)), prova=not a.chiudi)
    if not chiusi and not falliti:
        print("  Nessun programma del software originale in esecuzione.")
    return 1 if falliti else 0


if __name__ == "__main__":
    sys.exit(main())
