# -*- coding: utf-8 -*-
"""
Osservatore del programma originale: copia le immagini grezze che
PhxScanner.exe riceve dallo scanner, mentre e' lui a scansionare.

Il programma originale acquisisce bene (log dell'SDK della riserva, 26/10/2026:
"xfer: image complete ... ok=1", RGB 2100x1400) ma non salva. Invece di
rifare la sua sequenza, ci si mette a guardare: ci si aggancia al processo
come un debugger, si annotano i buffer che passa all'SDK con
add_image_buffer, e quando il suo registro interno scrive "xfer: image
complete buffer=N ok=1" (il DMA di quel buffer e' finito) lo si copia in
<ponte>/mitm/. filmscan li elabora come se li
avesse acquisiti lui (collegamento "programma originale").

Non si modifica nessun file del programma originale ne' il driver. I punti
d'arresto sono int3 scritti in memoria in tre funzioni di scn_dll.dll, e all'uscita (Ctrl+C) si rimettono i byte originali e ci si
stacca con DebugActiveProcessStop: PhxScanner continua come se niente fosse.
DebugSetProcessKillOnExit(False) evita che, se questo programma cade,
Windows chiuda anche PhxScanner.

    python ponte\\osservatore.py [--condivisa C:\\MasterflexD\\ponte] [--processo PhxScanner.exe]
    python ponte\\osservatore.py --indirizzi <scn_dll.dll>      solo calcolo, non si aggancia

Va avviato PRIMA di aprire un ordine nel programma originale: i buffer si
registrano all'impostazione dell'ordine, e chi arriva dopo non li conosce.

Solo libreria standard. Gira solo su Windows a 32 bit (come PhxScanner).
"""

from __future__ import print_function

import argparse
import ctypes
import datetime
import json
import os
import hashlib
import signal
import struct
import sys
import time
import zlib

# Le due funzioni esportate di DMLScanner che servono (scn_dll.dll, SDK
# 27.4.0.0). PhxScanner.exe le importa: i suoi call passano dall'indirizzo
# esportato, quindi un int3 li' li vede tutti.
FUNZIONI = {
    "aggiungi": "?add_image_buffer@DMLScanner@@QAEXW4ImageResolution@1@PAG@Z",
    "togli": "?remove_all_image_buffer@DMLScanner@@QAEPAVErrStat@1@PAI@Z",
}

# Il registro interno dell'SDK (il thunk a 0x18002D42 salta a 0x1807C3B0).
# Lo chiamano, fra gli altri, i tre punti che servono, sempre come
# registro(formato, a, b, c), cdecl:
#   "xsize=%d ysize=%d divisor=%d"           (0x1807CAF5) dimensioni
#   "total scan size:  %d bytes"              (0x1807E2AE) byte della scansione
#   "xfer: image complete buffer=%d ok=%d"   (0x1807B7EE, 0x1807B842, 0x1807C179)
# L'ultimo arriva quando il DMA di quel buffer e' finito: e' il momento
# giusto per copiarlo, prima che l'SDK lo elabori o lo riusi. import_image
# no: i buffer sono due e si alternano, e mentre si importa il primo il
# secondo si sta gia' riempiendo.
# Sono indirizzi interni, non esportati: valgono solo per questa DLL, che si
# riconosce dall'impronta.
SCN_DLL_SHA256 = "2519fa07ca4e5662ce6b285573d0b6db9a82464d4ec63a4d19a8121f832d54e8"
RVA_REGISTRO = 0x2D42
RVA_FMT_DIMENSIONI = 0xBFEDC
RVA_FMT_TOTALE = 0xBFFF0
RVA_FMT_COMPLETA = 0xBFACC
IMAGE_BASE = 0x18000000

# ---------------------------------------------------------------------------
# Esportazioni di un PE, lette dal file: niente dipendenze
# ---------------------------------------------------------------------------

def esportazioni(percorso):
    """{nome: RVA} delle funzioni esportate da una DLL a 32 bit."""
    with open(percorso, "rb") as f:
        d = f.read()
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    if d[pe:pe + 4] != b"PE\0\0":
        raise ValueError("%s non e' un file PE" % percorso)
    opt = pe + 24
    if struct.unpack_from("<H", d, opt)[0] != 0x10B:
        raise ValueError("%s non e' a 32 bit" % percorso)
    exp_rva, _ = struct.unpack_from("<II", d, opt + 96)
    n_sez = struct.unpack_from("<H", d, pe + 6)[0]
    dim_opt = struct.unpack_from("<H", d, pe + 20)[0]
    sezioni = []
    for i in range(n_sez):
        s = pe + 24 + dim_opt + 40 * i
        vsize, va, rsize, raw = struct.unpack_from("<IIII", d, s + 8)
        sezioni.append((va, max(vsize, rsize), raw))

    def off(rva):
        for va, dim, raw in sezioni:
            if va <= rva < va + dim:
                return rva - va + raw
        raise ValueError("RVA %x fuori dalle sezioni" % rva)

    e = off(exp_rva)
    n_fun, n_nomi, t_fun, t_nomi, t_ord = struct.unpack_from("<IIIII", d, e + 20)
    risultato = {}
    for i in range(n_nomi):
        p_nome = struct.unpack_from("<I", d, off(t_nomi) + 4 * i)[0]
        o = off(p_nome)
        nome = d[o:d.index(b"\0", o)].decode("latin-1")
        ordinale = struct.unpack_from("<H", d, off(t_ord) + 2 * i)[0]
        risultato[nome] = struct.unpack_from("<I", d, off(t_fun) + 4 * ordinale)[0]
    return risultato


# ---------------------------------------------------------------------------
# Win32
# ---------------------------------------------------------------------------

if os.name == "nt":
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
else:                                   # per provare il resto altrove
    wintypes = None
    k32 = adv = None

DBG_CONTINUE = 0x00010002
DBG_EXCEPTION_NOT_HANDLED = 0x80010001
EXCEPTION_DEBUG_EVENT = 1
CREATE_THREAD_DEBUG_EVENT = 2
CREATE_PROCESS_DEBUG_EVENT = 3
EXIT_THREAD_DEBUG_EVENT = 4
EXIT_PROCESS_DEBUG_EVENT = 5
LOAD_DLL_DEBUG_EVENT = 6
EXCEPTION_BREAKPOINT = 0x80000003
EXCEPTION_SINGLE_STEP = 0x80000004
PROCESS_ALL_ACCESS = 0x1F0FFF
THREAD_GET_SET_CONTEXT = 0x0008 | 0x0010 | 0x0040
CONTEXT_FULL = 0x10007
TH32CS_SNAPPROCESS = 0x2
TH32CS_SNAPMODULE = 0x8
MEM_COMMIT = 0x1000
PAGE_EXECUTE_READWRITE = 0x40
INFINITE = 0xFFFFFFFF


class DEBUG_EVENT(ctypes.Structure):
    # u e' un'unione: la si legge a mano per i soli campi che servono
    _fields_ = [("codice", ctypes.c_uint32), ("pid", ctypes.c_uint32),
                ("tid", ctypes.c_uint32), ("u", ctypes.c_ubyte * 160)]

    def dword(self, n):
        return struct.unpack_from("<I", bytes(self.u), 4 * n)[0]


class CONTEXT(ctypes.Structure):
    """CONTEXT x86 (716 byte): servono Eip, Esp, Ecx ed EFlags."""
    _fields_ = [("dati", ctypes.c_ubyte * 0x2CC)]
    OFF = {"flags": 0x00, "ecx": 0xAC, "eip": 0xB8, "eflags": 0xC0, "esp": 0xC4}

    def leggi(self, campo):
        return struct.unpack_from("<I", bytes(self.dati), self.OFF[campo])[0]

    def scrivi(self, campo, valore):
        struct.pack_into("<I", self.dati, self.OFF[campo], valore & 0xFFFFFFFF)


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
                ("th32ProcessID", ctypes.c_uint32), ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", ctypes.c_uint32), ("cntThreads", ctypes.c_uint32),
                ("th32ParentProcessID", ctypes.c_uint32), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_uint32), ("szExeFile", ctypes.c_char * 260)]


class MODULEENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("th32ModuleID", ctypes.c_uint32),
                ("th32ProcessID", ctypes.c_uint32), ("GlblcntUsage", ctypes.c_uint32),
                ("ProccntUsage", ctypes.c_uint32), ("modBaseAddr", ctypes.c_void_p),
                ("modBaseSize", ctypes.c_uint32), ("hModule", ctypes.c_void_p),
                ("szModule", ctypes.c_char * 256), ("szExePath", ctypes.c_char * 260)]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", ctypes.c_uint32), ("RegionSize", ctypes.c_size_t),
                ("State", ctypes.c_uint32), ("Protect", ctypes.c_uint32),
                ("Type", ctypes.c_uint32)]


def _errore(cosa):
    return OSError("%s: errore di Windows %d" % (cosa, ctypes.get_last_error()))


def trova_processo(nome):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    voce = PROCESSENTRY32()
    voce.dwSize = ctypes.sizeof(voce)
    try:
        ok = k32.Process32First(snap, ctypes.byref(voce))
        while ok:
            if voce.szExeFile.decode("latin-1").lower() == nome.lower():
                return voce.th32ProcessID
            ok = k32.Process32Next(snap, ctypes.byref(voce))
    finally:
        k32.CloseHandle(snap)
    return None


def trova_modulo(pid, nome):
    """(base, percorso) di una DLL caricata nel processo."""
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, pid)
    voce = MODULEENTRY32()
    voce.dwSize = ctypes.sizeof(voce)
    try:
        ok = k32.Module32First(snap, ctypes.byref(voce))
        while ok:
            if voce.szModule.decode("latin-1").lower() == nome.lower():
                return voce.modBaseAddr, voce.szExePath.decode("latin-1")
            ok = k32.Module32Next(snap, ctypes.byref(voce))
    finally:
        k32.CloseHandle(snap)
    return None, None


def abilita_debug_privilege():
    """SeDebugPrivilege, se l'utente lo ha (amministratore). Senza, ci si
    aggancia lo stesso ai processi dello stesso utente."""
    try:
        TOKEN_ADJUST_PRIVILEGES, TOKEN_QUERY = 0x20, 0x8
        token = wintypes.HANDLE()
        if not adv.OpenProcessToken(k32.GetCurrentProcess(),
                                    TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                                    ctypes.byref(token)):
            return False
        luid = ctypes.c_int64()
        if not adv.LookupPrivilegeValueW(None, "SeDebugPrivilege", ctypes.byref(luid)):
            return False
        tp = struct.pack("<IqI", 1, luid.value, 2)     # SE_PRIVILEGE_ENABLED
        buf = ctypes.create_string_buffer(tp)
        return bool(adv.AdjustTokenPrivileges(token, False, buf, 0, None, None))
    except Exception:                                   # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# L'osservatore
# ---------------------------------------------------------------------------

class Osservatore(object):
    def __init__(self, pid, condivisa, log=print):
        self.pid = pid
        self.uscita = os.path.join(condivisa, "mitm")
        if not os.path.isdir(self.uscita):
            os.makedirs(self.uscita)
        self.log = log
        self.processo = None
        self.punti = {}           # indirizzo -> (nome, byte originale)
        self.da_riarmare = {}     # tid -> indirizzo da rimettere dopo il passo
        self.buffer = []          # (indirizzo, risoluzione) in ordine di registrazione
        self.dimensioni = None    # (x, y, divisore) dall'ultimo "xsize="
        self.totale = None        # byte dall'ultimo "total scan size"
        self.copiate = 0
        self.attivo = False
        self.armati = True

    # -- memoria del processo --------------------------------------------

    def leggi(self, indirizzo, quanti):
        buf = ctypes.create_string_buffer(quanti)
        letti = ctypes.c_size_t(0)
        if not k32.ReadProcessMemory(self.processo, ctypes.c_void_p(indirizzo), buf,
                                     quanti, ctypes.byref(letti)):
            raise _errore("lettura di %d byte a %08x" % (quanti, indirizzo))
        return buf.raw[:letti.value]

    def scrivi(self, indirizzo, dati):
        # il codice della DLL e' in pagine di sola esecuzione: si apre la
        # pagina il tempo di scrivere e la si richiude com'era
        vecchia = ctypes.c_uint32(0)
        aperta = k32.VirtualProtectEx(self.processo, ctypes.c_void_p(indirizzo), len(dati),
                                      PAGE_EXECUTE_READWRITE, ctypes.byref(vecchia))
        scritti = ctypes.c_size_t(0)
        try:
            if not k32.WriteProcessMemory(self.processo, ctypes.c_void_p(indirizzo), dati,
                                          len(dati), ctypes.byref(scritti)):
                raise _errore("scrittura a %08x" % indirizzo)
        finally:
            if aperta:
                k32.VirtualProtectEx(self.processo, ctypes.c_void_p(indirizzo), len(dati),
                                     vecchia.value, ctypes.byref(ctypes.c_uint32(0)))
        k32.FlushInstructionCache(self.processo, ctypes.c_void_p(indirizzo), len(dati))

    def leggibili(self, indirizzo, quanti):
        """Quanti byte da 'indirizzo' sono davvero leggibili (al massimo
        'quanti'): la dimensione esatta del buffer non la sappiamo, e
        leggere oltre la sua regione fallirebbe."""
        mbi = MEMORY_BASIC_INFORMATION()
        totale, a = 0, indirizzo
        while totale < quanti:
            if not k32.VirtualQueryEx(self.processo, ctypes.c_void_p(a),
                                      ctypes.byref(mbi), ctypes.sizeof(mbi)):
                break
            if mbi.State != MEM_COMMIT:
                break
            fine = mbi.BaseAddress + mbi.RegionSize
            totale += fine - a
            a = fine
        return min(totale, quanti)

    # -- punti d'arresto -------------------------------------------------

    def arma(self, indirizzo, nome):
        originale = self.leggi(indirizzo, 1)
        self.punti[indirizzo] = (nome, originale)
        self.scrivi(indirizzo, b"\xCC")

    def disarma_tutti(self):
        for indirizzo, (_, originale) in self.punti.items():
            try:
                self.scrivi(indirizzo, originale)
            except OSError as exc:
                self.log("ATTENZIONE: non riesco a rimettere il byte a %08x: %s"
                         % (indirizzo, exc))

    def contesto(self, tid):
        h = k32.OpenThread(THREAD_GET_SET_CONTEXT, False, tid)
        ctx = CONTEXT()
        ctx.scrivi("flags", CONTEXT_FULL)
        if not k32.GetThreadContext(h, ctypes.byref(ctx)):
            k32.CloseHandle(h)
            raise _errore("GetThreadContext")
        return h, ctx

    # -- cosa si fa a ogni punto ------------------------------------------

    def su_aggiungi(self, ctx):
        # __thiscall: this in ecx, argomenti sullo stack dopo l'indirizzo di
        # ritorno: (ImageResolution risoluzione, unsigned short* buffer)
        esp = ctx.leggi("esp")
        risoluzione, indirizzo = struct.unpack("<II", self.leggi(esp + 4, 8))
        self.buffer.append((indirizzo, risoluzione))
        self.log("buffer %d registrato: %08x, risoluzione %d"
                 % (len(self.buffer) - 1, indirizzo, risoluzione))

    def su_togli(self, ctx):
        if self.buffer:
            self.log("buffer tolti dal programma originale (%d)" % len(self.buffer))
        del self.buffer[:]

    def su_registro(self, ctx):
        """Una riga del registro dell'SDK: si guardano solo i tre formati
        che servono, confrontando l'indirizzo della stringa."""
        esp = ctx.leggi("esp")
        fmt, a, b, c = struct.unpack("<IIII", self.leggi(esp + 4, 16))
        if fmt == self.fmt_dimensioni:
            self.dimensioni = (a, b, max(1, c))
        elif fmt == self.fmt_totale:
            self.totale = a
        elif fmt == self.fmt_completa:
            if b & 0xFF:
                self.copia(a)
            else:
                self.log("immagine nel buffer %d non riuscita (ok=0): non copiata" % a)

    def copia(self, indice):
        if not 0 <= indice < len(self.buffer):
            self.log("buffer %d sconosciuto (%d registrati): l'osservatore e' "
                     "partito dopo l'apertura dell'ordine?" % (indice, len(self.buffer)))
            return
        if not self.dimensioni or not self.totale:
            self.log("immagine completa ma dimensioni non note: non copiata")
            return
        indirizzo, risoluzione = self.buffer[indice]
        x, y, divisore = self.dimensioni
        w, h = x // divisore, y // divisore
        quanti = self.leggibili(indirizzo, self.totale)
        if quanti < self.totale:
            self.log("buffer %d: leggibili %d byte su %d" % (indice, quanti, self.totale))
        dati = self.leggi(indirizzo, quanti)
        ora = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        base = os.path.join(self.uscita, "%s_b%d" % (ora, indice))
        piano = w * h * 2
        with open(base + ".raw.tmp", "wb") as f:
            f.write(dati)
        with open(base + ".json", "w") as f:
            json.dump({"larghezza": w, "altezza": h, "divisore": divisore,
                       "risoluzione": risoluzione,
                       "piani": quanti // piano if piano else 0,
                       "dtype": "uint16", "ordine": "little",
                       "buffer": indice, "indirizzo": "%08x" % indirizzo,
                       "byte": quanti, "ora": ora, "crc32": zlib.crc32(dati),
                       "nota": "dati del DMA prima delle correzioni dell'SDK; "
                               "piani uno dopo l'altro, da verificare"}, f)
        # il .raw compare per ultimo e tutto intero: chi lo aspetta
        # (filmscan) non legge mai un file a meta'
        os.replace(base + ".raw.tmp", base + ".raw")
        self.copiate += 1
        self.log("immagine %d copiata: buffer %d, %dx%d, %d piani"
                 % (self.copiate, indice, w, h, quanti // piano if piano else 0))

    # -- il ciclo del debugger --------------------------------------------

    def aggancia(self, base_dll, percorso_dll):
        with open(percorso_dll, "rb") as f:
            impronta = hashlib.sha256(f.read()).hexdigest()
        if impronta != SCN_DLL_SHA256:
            raise RuntimeError(
                "%s non e' la versione studiata (SHA-256 %s): gli indirizzi "
                "interni non varrebbero, non mi aggancio." % (percorso_dll, impronta))
        nomi = esportazioni(percorso_dll)
        mancanti = [n for n in FUNZIONI.values() if n not in nomi]
        if mancanti:
            raise RuntimeError("in %s mancano: %s" % (percorso_dll, ", ".join(mancanti)))
        # la DLL puo' essere caricata a un indirizzo diverso da quello
        # preferito: tutto si sposta insieme
        sposta = base_dll - IMAGE_BASE
        self.fmt_dimensioni = IMAGE_BASE + RVA_FMT_DIMENSIONI + sposta
        self.fmt_totale = IMAGE_BASE + RVA_FMT_TOTALE + sposta
        self.fmt_completa = IMAGE_BASE + RVA_FMT_COMPLETA + sposta
        abilita_debug_privilege()
        if not k32.DebugActiveProcess(self.pid):
            raise _errore("DebugActiveProcess(%d)" % self.pid)
        self.attivo = True
        # se questo programma cade, PhxScanner deve restare in piedi
        k32.DebugSetProcessKillOnExit(False)
        self.processo = k32.OpenProcess(PROCESS_ALL_ACCESS, False, self.pid)
        if not self.processo:
            raise _errore("OpenProcess")
        self.gestori = {}
        punti = [(chiave, base_dll + nomi[nome]) for chiave, nome in FUNZIONI.items()]
        punti.append(("registro", base_dll + RVA_REGISTRO))
        for chiave, indirizzo in punti:
            self.gestori[indirizzo] = getattr(self, "su_" + chiave)
            self.arma(indirizzo, chiave)
            self.log("punto d'arresto su %s a %08x" % (chiave, indirizzo))

    def gira(self, smetti, un_giro=False):
        evento = DEBUG_EVENT()
        while un_giro or not smetti():
            if not k32.WaitForDebugEvent(ctypes.byref(evento), 250):
                if un_giro:
                    return
                continue
            esito = DBG_CONTINUE
            codice = evento.codice
            if codice == EXCEPTION_DEBUG_EVENT:
                eccezione, _, _, indirizzo = (evento.dword(0), evento.dword(1),
                                              evento.dword(2), evento.dword(3))
                if eccezione == EXCEPTION_BREAKPOINT and indirizzo in self.punti \
                        and self.armati:
                    self.al_punto(evento.tid, indirizzo)
                elif eccezione == EXCEPTION_BREAKPOINT and indirizzo in self.punti:
                    # un thread era gia' sull'int3 quando li abbiamo tolti:
                    # il byte giusto e' gia' a posto, si torna indietro di uno
                    self.indietro_di_uno(evento.tid)
                elif eccezione == EXCEPTION_SINGLE_STEP and evento.tid in self.da_riarmare:
                    da_rimettere = self.da_riarmare.pop(evento.tid)
                    if self.armati:
                        self.scrivi(da_rimettere, b"\xCC")
                elif eccezione == EXCEPTION_BREAKPOINT:
                    pass        # il breakpoint iniziale dell'aggancio
                else:
                    # eccezioni del programma: sono sue, le gestisce lui
                    esito = DBG_EXCEPTION_NOT_HANDLED
            elif codice == CREATE_PROCESS_DEBUG_EVENT:
                k32.CloseHandle(evento.dword(0))       # hFile
            elif codice == LOAD_DLL_DEBUG_EVENT:
                k32.CloseHandle(evento.dword(0))       # hFile
            elif codice == EXIT_PROCESS_DEBUG_EVENT:
                self.log("PhxScanner si e' chiuso.")
                self.attivo = False
                k32.ContinueDebugEvent(evento.pid, evento.tid, DBG_CONTINUE)
                return
            k32.ContinueDebugEvent(evento.pid, evento.tid, esito)
            if un_giro:
                return

    def al_punto(self, tid, indirizzo):
        """Si rimette il byte originale, si torna indietro di un'istruzione,
        si fa il lavoro e si chiede un passo singolo: al passo dopo l'int3
        torna al suo posto (EXCEPTION_SINGLE_STEP qui sopra)."""
        h, ctx = self.contesto(tid)
        try:
            self.scrivi(indirizzo, self.punti[indirizzo][1])
            ctx.scrivi("eip", ctx.leggi("eip") - 1)
            try:
                self.gestori[indirizzo](ctx)
            except Exception as exc:                    # noqa: BLE001
                # un errore nostro non deve fermare il programma originale
                self.log("errore dell'osservatore su %s: %s"
                         % (self.punti[indirizzo][0], exc))
            ctx.scrivi("eflags", ctx.leggi("eflags") | 0x100)      # passo singolo
            if not k32.SetThreadContext(h, ctypes.byref(ctx)):
                raise _errore("SetThreadContext")
            self.da_riarmare[tid] = indirizzo
        finally:
            k32.CloseHandle(h)

    def indietro_di_uno(self, tid):
        h, ctx = self.contesto(tid)
        try:
            ctx.scrivi("eip", ctx.leggi("eip") - 1)
            k32.SetThreadContext(h, ctypes.byref(ctx))
        finally:
            k32.CloseHandle(h)

    def stacca(self):
        """Toglie gli int3 e si stacca. Prima aspetta i passi singoli in
        corso: un'eccezione di passo singolo che arrivasse a PhxScanner dopo
        lo stacco lo farebbe cadere."""
        if not self.attivo:
            return
        self.armati = False
        self.disarma_tutti()
        inizio = time.time()
        while time.time() - inizio < 3.0:
            self.gira(lambda: True, un_giro=True)
            if not self.da_riarmare and time.time() - inizio > 0.5:
                break
        k32.DebugActiveProcessStop(self.pid)
        self.attivo = False
        self.log("staccato: PhxScanner continua da solo.")


def principale(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--condivisa", default=r"C:\MasterflexD\ponte")
    ap.add_argument("--processo", default="PhxScanner.exe")
    ap.add_argument("--indirizzi", metavar="SCN_DLL",
                    help="mostra solo gli RVA delle funzioni osservate")
    a = ap.parse_args(argv)

    if a.indirizzi:
        nomi = esportazioni(a.indirizzi)
        for chiave, nome in FUNZIONI.items():
            print("%-9s RVA %08x  %s" % (chiave, nomi.get(nome, 0), nome))
        with open(a.indirizzi, "rb") as f:
            impronta = hashlib.sha256(f.read()).hexdigest()
        print("registro  RVA %08x  (interno: vale solo per la DLL studiata)" % RVA_REGISTRO)
        print("impronta del file: %s (%s)" % (impronta,
              "quella studiata" if impronta == SCN_DLL_SHA256 else "DIVERSA"))
        return 0

    if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 4:
        print("Serve Windows e Python a 32 bit, come PhxScanner.")
        return 2

    # anche su file, in software/log/agente con quelli dell'agente
    cartella_log = os.environ.get("MASTERFLEX_LOG") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "log")
    cartella_log = os.path.join(cartella_log, "agente")

    def log(testo):
        riga = "%s  %s" % (time.strftime("%H:%M:%S"), testo)
        print(riga)
        try:
            if not os.path.isdir(cartella_log):
                os.makedirs(cartella_log)
            with open(os.path.join(cartella_log, "osservatore-%s.log"
                                   % time.strftime("%Y%m%d")), "a") as f:
                f.write(riga + "\n")
        except (IOError, OSError):
            pass

    pid = trova_processo(a.processo)
    if not pid:
        print("%s non e' in esecuzione: avviare prima il programma originale." % a.processo)
        return 1
    base, percorso = trova_modulo(pid, "scn_dll.dll")
    if not base:
        print("%s non ha caricato scn_dll.dll." % a.processo)
        return 1
    log("%s pid %d, scn_dll.dll a %08x (%s)" % (a.processo, pid, base, percorso))

    oss = Osservatore(pid, a.condivisa, log=log)
    # Ctrl+C alza solo un flag: un KeyboardInterrupt fra WaitForDebugEvent e
    # ContinueDebugEvent lascerebbe PhxScanner fermo su un evento.
    fermati = []
    signal.signal(signal.SIGINT, lambda *_: fermati.append(1))
    try:
        oss.aggancia(base, percorso)
        log("in ascolto: aprire un ordine e scansionare nel programma originale. "
            "Ctrl+C per staccarsi.")
        oss.gira(lambda: bool(fermati))
    finally:
        oss.stacca()
        log("immagini copiate: %d, in %s" % (oss.copiate, oss.uscita))
    return 0


if __name__ == "__main__":
    sys.exit(principale())
