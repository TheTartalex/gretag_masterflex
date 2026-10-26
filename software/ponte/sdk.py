# -*- coding: utf-8 -*-
"""
Collegamento diretto all'SDK originale dello scanner (scn_dll.dll), da Python.

PERCHE' ESISTE QUESTO FILE
--------------------------
Il software originale della macchina fa quello che deve fare, ma lo fa dietro
un'interfaccia del 2001 che salva su floppy. Noi vogliamo i suoi comandi, non
le sue finestre.

Sotto quell'interfaccia c'e' un SDK in C++ - la classe DMLScanner dentro
scn_dll.dll - che parla con il driver EKframe.sys e quindi con la macchina.
Questo modulo chiama QUELLE funzioni, direttamente, da Python, senza compilare
niente e senza toccare una riga del software originale.

Firme, nomi e dimensioni non sono indovinati: sono stati ricavati dai simboli
C++ esportati dal DLL, che nel formato Microsoft contengono la firma completa.
L'elenco integrale sta in documentazione/API-SDK-ORIGINALE.md.

COME FUNZIONA LA CHIAMATA
-------------------------
I metodi C++ di MSVC a 32 bit usano la convenzione __thiscall: gli argomenti
sull'a stack, il puntatore all'oggetto in ECX. ctypes non la conosce. La si
aggira con un trampolino di sei byte generato a runtime:

    58        pop eax     ; indirizzo di ritorno
    59        pop ecx     ; this  -> finisce in ECX, dove il metodo lo cerca
    50        push eax    ; rimetto l'indirizzo di ritorno
    E9 rel32  jmp metodo

Si dichiara poi il metodo a ctypes come __stdcall con 'this' come primo
argomento. I conti sullo stack tornano: il metodo pulisce i suoi argomenti,
il trampolino ha gia' tolto 'this'.

DA SAPERE PRIMA DI USARLO
-------------------------
Gira SOLO sul PC originale: serve Python a 32 bit, scn_dll.dll, il driver
EKframe attivo e la scheda nel suo slot. Su qualunque altra macchina
l'apertura fallisce con "Can't open scanner", che e' esattamente l'errore che
si legge in Scanner\\SDKCacheDir\\log_0000.txt.

E' codice che comanda motori del 1995. Va provato con il percorso pellicola
VUOTO, come prescrive il manuale di servizio prima di ogni test.
"""

from __future__ import annotations

import ctypes
import os
import struct
from ctypes import wintypes


# ---------------------------------------------------------------------------
# Enumerazioni della macchina
#
# I valori con la nota "confermato" vengono dai file di servizio originali;
# gli altri sono l'ordine di dichiarazione piu' probabile e vanno verificati
# sulla macchina prima di fidarsene. Meglio un valore marcato incerto che un
# valore sbagliato che sembra certo.
# ---------------------------------------------------------------------------

class FilmFormat(object):
    F135 = 0          # confermato: set_35_XY_offset.bat  -> DMLCmdTransfer 8 0
    APS = 1           # confermato: set_APS_XY_offset.bat -> DMLCmdTransfer 8 1
    MAN_MOUNT = 2     # da verificare
    MAN_STRIP = 3     # da verificare
    F110 = 4          # confermato: set_110_XY_offset.bat -> DMLCmdTransfer 8 4


class ImageResolution(object):
    """Le cinque risoluzioni dell'SDK per il nostro tipo di scanner (tipo 3).

    Valori letti dalla tabella IMAGE_RESOLUTION dentro scn_dll.dll.
    """
    PIENA = 0         # 2100 x 1400
    META = 1          # 1052 x  700
    QUARTO = 2        #  524 x  350   (DMIN_RES dell'SDK)
    OTTAVO = 3        #  264 x  175
    MINIMA = 4        #  132 x   88   (usata per il rilevamento fotogrammi)

    DIMENSIONI = {0: (2100, 1400), 1: (1052, 700), 2: (524, 350),
                  3: (264, 175), 4: (132, 88)}


class ColorChannel(object):
    """N_CHANNELS nell'SDK vale 3: il percorso dati principale e' R, G, B.

    L'infrarosso sulla macchina c'e' ed e' gestito dalla libreria Digital ICE
    (DMLDICELib.dll), non come quarto canale di questo percorso. Se il valore 3
    non risponde, l'IR va cercato di la'.
    """
    R = 0
    G = 1
    B = 2
    IR = 3            # da verificare


class FocusType(object):
    NOMINALE = 0
    AUTO = 1          # da verificare


class GateMode(object):
    # da verificare sulla macchina con get_gate_mode()
    NORMALE = 0
    SERVIZIO = 1


# Dimensione, in byte, dell'oggetto ExposureTime passato PER VALORE a
# scan_raw. Non e' una stima: scan_raw termina con 'ret 0x1ac' (428 byte di
# argomenti) e gli altri argomenti ne occupano 36, quindi l'oggetto ne occupa
# 392. Nel corpo si vede infatti 'sub esp, 0x188' = 392.
SIZEOF_EXPOSURE_TIME = 392

# Quanto spazio riservare all'oggetto DMLScannerAdv. La dimensione vera non e'
# nota; se ne riserva molto di piu' del necessario, che non costa nulla e
# toglie di mezzo il rischio peggiore, cioe' che il costruttore scriva oltre.
DIM_OGGETTO_SCANNER = 256 * 1024


class ErroreScanner(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Trampolino __thiscall
# ---------------------------------------------------------------------------

_PAGE_EXECUTE_READWRITE = 0x40
_MEM_COMMIT_RESERVE = 0x3000

_kernel32 = ctypes.windll.kernel32 if os.name == "nt" else None
_trampolini = []          # tiene vive le pagine allocate


def _crea_trampolino(indirizzo_metodo):
    """Genera il ponte fra la convenzione stdcall di ctypes e __thiscall."""
    if _kernel32 is None:
        raise ErroreScanner("questo modulo gira solo su Windows")

    _kernel32.VirtualAlloc.restype = ctypes.c_void_p
    _kernel32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                                       wintypes.DWORD, wintypes.DWORD]
    pagina = _kernel32.VirtualAlloc(None, 16, _MEM_COMMIT_RESERVE,
                                    _PAGE_EXECUTE_READWRITE)
    if not pagina:
        raise ErroreScanner("VirtualAlloc fallita per il trampolino")

    # il jmp rel32 occupa 5 byte e parte dall'offset 3
    rel = indirizzo_metodo - (pagina + 8)
    codice = b"\x58\x59\x50\xE9" + struct.pack("<i", rel)
    ctypes.memmove(pagina, codice, len(codice))
    _trampolini.append(pagina)
    return pagina


class _Metodo(object):
    """Un metodo C++ pronto da chiamare: metodo(this, arg1, ...)."""

    def __init__(self, dll, nome_mangled, restype, argtypes, etichetta):
        try:
            indirizzo = ctypes.cast(dll[nome_mangled], ctypes.c_void_p).value
        except Exception:
            raise ErroreScanner("simbolo assente in scn_dll.dll: %s" % etichetta)
        proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        self._chiama = proto(_crea_trampolino(indirizzo))
        self.etichetta = etichetta

    def __call__(self, this, *args):
        return self._chiama(this, *args)


# ---------------------------------------------------------------------------
# Tabella dei metodi: (etichetta, nome C++ esportato, tipo di ritorno, argomenti)
#
# I nomi lunghi sono i simboli reali dentro il DLL. Non vanno "puliti":
# GetProcAddress li vuole esattamente cosi'.
# ---------------------------------------------------------------------------

VP = ctypes.c_void_p
I = ctypes.c_int
U = ctypes.c_uint
UL = ctypes.c_ulong
B = ctypes.c_bool
PU16 = ctypes.POINTER(ctypes.c_uint16)

METODI = [
    # --- ciclo di vita ---
    ("costruttore", "??0DMLScannerAdv@@QAE@PAPAVErrStat@DMLScanner@@PBG11P6AXPAV12@@ZP6AXI@Z_N5@Z",
     VP, [VP, VP, VP, VP, VP, VP, B, B]),
    ("distruttore", "??1DMLScannerAdv@@UAE@XZ", None, []),
    ("reset", "?ResetScanner@DMLScanner@@QAEPAVErrStat@1@XZ", VP, []),
    ("tipo_scanner", "?get_attached_scanner_type@DMLScanner@@QAE?AW4AttachedScannerType@1@XZ", I, []),
    ("id_scanner", "?gretag_GetScannerID@DMLScanner@@QAEPAVErrStat@1@PAI@Z", VP, [VP]),
    ("percorso_firmware", "?SetFirmwarePath@DMLScanner@@QAEXPBG@Z", None, [VP]),

    # --- formato e ottica ---
    ("scegli_formato", "?select_film_format@DMLScanner@@QAEPAVErrStat@1@W4FilmFormat@1@@Z", VP, [I]),
    ("tipo_gate", "?get_gate_type@DMLScanner@@QAEPAVErrStat@1@PAW4GateType@1@@Z", VP, [VP]),
    ("modo_gate", "?get_gate_mode@DMLScanner@@QAEPAVErrStat@1@PAW4GateMode@1@@Z", VP, [VP]),
    ("imposta_modo_gate", "?set_gate_mode@DMLScanner@@QAEPAVErrStat@1@W4GateMode@1@@Z", VP, [I]),
    ("leggi_offset_xy", "?gretag_get_xy_offset@DMLScanner@@QAEPAVErrStat@1@W4FilmFormat@1@PAG1@Z", VP, [I, VP, VP]),
    ("scrivi_offset_xy", "?gretag_set_xy_offset@DMLScanner@@QAEPAVErrStat@1@W4FilmFormat@1@GG@Z", VP, [I, ctypes.c_ushort, ctypes.c_ushort]),
    ("ingrandimento", "?get_magnification@DMLScanner@@QAENW4GateType@1@W4ManGateQual@1@@Z", ctypes.c_double, [I, I]),

    # --- trasporto pellicola ---
    ("alimenta_on", "?gate_feed_on@DMLScanner@@QAEPAVErrStat@1@XZ", VP, []),
    ("alimenta_off", "?gate_feed_off@DMLScanner@@QAEPAVErrStat@1@XZ", VP, []),
    ("avanza_fotogramma", "?advance_one_frame@DMLScanner@@QAEPAVErrStat@1@XZ", VP, []),
    ("avanza_mezzo", "?advance_one_half_frame@DMLScanner@@QAEPAVErrStat@1@XZ", VP, []),
    ("fotogramma_successivo", "?gretag_NextFrame@DMLScanner@@QAEPAVErrStat@1@XZ", VP, []),
    ("fotogramma_precedente", "?gretag_PreviousFrame@DMLScanner@@QAEPAVErrStat@1@XZ", VP, []),
    ("avanzamento_prova", "?gate_test_advance@DMLScanner@@QAEPAVErrStat@1@PAH@Z", VP, [VP]),
    ("avanzamento_prova_fine", "?gate_post_test_advance@DMLScanner@@QAEPAVErrStat@1@H@Z", VP, [I]),

    # --- rilevamento fotogrammi ---
    ("cerca_fotogrammi", "?frame_detect_scan@DMLScanner@@QAEPAVErrStat@1@W4ImageResolution@1@PAEW4FilmType@1@@Z", VP, [I, VP, I]),
    ("correggi_posizione", "?frame_detect_adjust@DMLScanner@@QAEPAVErrStat@1@H@Z", VP, [I]),
    ("correggi_posizione_fine", "?frame_detect_fine_adjust@DMLScanner@@QAEPAVErrStat@1@J@Z", VP, [ctypes.c_long]),
    ("fine_rilevamento", "?frame_detect_done@DMLScanner@@QAEPAVErrStat@1@XZ", VP, []),
    ("calibra_sensori_gate", "?gate_sensor_cal@DMLScanner@@QAEPAVErrStat@1@XZ", VP, []),

    # --- fuoco ---
    ("fuoco", "?focus@DMLScanner@@QAEPAVErrStat@1@W4FocusType@1@@Z", VP, [I]),
    ("fuoco_avanzato", "?focus_adv@DMLScannerAdv@@QAEPAVErrStat@DMLScanner@@W4FocusMode@3@PAUFocusResult@3@W4FocusAlg@3@W4FocusMotorMode@3@@Z", VP, [I, VP, I, I]),

    # --- acquisizione ---
    ("aggiungi_buffer", "?add_image_buffer@DMLScanner@@QAEXW4ImageResolution@1@PAG@Z", None, [I, PU16]),
    ("togli_buffer", "?remove_all_image_buffer@DMLScanner@@QAEPAVErrStat@1@PAI@Z", VP, [VP]),
    ("acquisisci", "?capture@DMLScanner@@QAEPAVErrStat@1@_NW4ImageResolution@1@0@Z", VP, [B, I, B]),
    ("annulla_acquisizione", "?cancel_scan@DMLScanner@@QAEPAVErrStat@1@XZ", VP, []),
    ("dati_acquisizione", "?get_scan_data@DMLScanner@@QAE_NPAPAVScanData@@@Z", B, [VP]),
    # scan_raw prende ExposureTime PER VALORE: 392 byte sullo stack. Si passa
    # come buffer grezzo, vedi Scanner.scansiona_canale().
    ("scansiona_grezzo", "?scan_raw@DMLScannerAdv@@QAEPAVErrStat@DMLScanner@@PAGW4ColorChannel@1@VExposureTime@1@_NHHHHW4ImageResolution@3@H@Z",
     VP, None),

    # --- esposizione ---
    ("exp_costruttore", "??0ExposureTime@DMLScannerAdv@@QAE@XZ", VP, []),
    ("exp_default", "?set_to_default_texp@ExposureTime@DMLScannerAdv@@QAEXXZ", None, []),
    ("exp_moltiplica", "??XExposureTime@DMLScannerAdv@@QAEXN@Z", None, [ctypes.c_double]),
    ("exp_positiva", "?is_positive@ExposureTime@DMLScannerAdv@@QBE_NXZ", B, []),
    ("exp_leggi_file", "?read_file@ExposureTime@DMLScannerAdv@@QAEPAVErrStat@DMLScanner@@PBGPAVEvLog@@@Z", VP, [VP, VP]),
    ("exp_scrivi_file", "?write_file@ExposureTime@DMLScannerAdv@@QBEPAVErrStat@DMLScanner@@PBGPAVEvLog@@@Z", VP, [VP, VP]),

    # --- calibrazione ---
    ("stato_calibrazione", "?get_calibration_status@DMLScanner@@QAEPAVErrStat@1@PAW4CalibrationStatus@1@PAPAY143$$CBV?$vector@HV?$allocator@H@std@@@std@@@Z", VP, [VP, VP]),

    # --- diagnostica e memoria macchina ---
    ("leggi_ingresso", "?diag_input@DMLScannerAdv@@QAE_NHPA_NPAVScanCallback@@@Z", B, [I, VP, VP]),
    ("scrivi_uscita", "?diag_output@DMLScannerAdv@@QAE_NH_NPAVScanCallback@@@Z", B, [I, B, VP]),
    ("info_ccd", "?diag_GetCCDInfo@DMLScannerAdv@@QAE_NPAVCCD_Region@1@PAVScanCallback@@@Z", B, [VP, VP]),
    ("leggi_memoria_scheda", "?readIAQMemory@DMLScannerAdv@@QAE_NKKPAK@Z", B, [UL, UL, VP]),
    ("scrivi_memoria_scheda", "?writeIAQMemory@DMLScannerAdv@@QAE_NKKK@Z", B, [UL, UL, UL]),
    ("nvm_grezza", "?nvm_raw@DMLScannerAdv@@QAEPAVErrStat@DMLScanner@@PAUNVMemRaw@1@_N@Z", VP, [VP, B]),
    ("nvm_fuoco", "?nvm_focus_mag_pos@DMLScannerAdv@@QAEPAVErrStat@DMLScanner@@PAUFocusMagPos@1@W4GateType@3@W4ManGateQual@3@I_N@Z", VP, [VP, I, I, U, B]),
    ("nvm_offset_xy", "?nvm_xy_offset@DMLScannerAdv@@QAEPAVErrStat@DMLScanner@@PAUCCDReadOut@1@W4GateType@3@W4ManGateQual@3@_N@Z", VP, [VP, I, I, B]),
    ("nvm_matricola", "?nvm_serial_number@DMLScannerAdv@@QAEPAVErrStat@DMLScanner@@PAUSerialNumber@3@_N@Z", VP, [VP, B]),

    # --- informazioni sul supporto ---
    ("info_supporto", "?get_media_info@DMLScanner@@QAEPAVErrStat@1@PAPAVMediaInfo@1@@Z", VP, [VP]),
    ("stato_dispositivo", "?get_device_status@DMLScanner@@QAEPAVDeviceStatus@1@PAPAY143$$CBV?$vector@HV?$allocator@H@std@@@std@@_N@Z", VP, [VP, B]),

    # --- errori ---
    ("err_quanti", "?get_n_error@ErrStat@DMLScanner@@QAEI_N@Z", U, [B]),
    ("err_prossimo", "?get_next_error@ErrStat@DMLScanner@@QAEXPAW4ScnError@2@PAH1_N@Z", None, [VP, VP, VP, B]),
    ("err_azzera_lettura", "?reset_read_index@ErrStat@DMLScanner@@QAEXXZ", None, []),
]

# Funzioni statiche: sono __cdecl, non serve trampolino.
STATICHE = [
    ("dimensione_buffer", "?get_image_buffer_size@DMLScanner@@SAIW4ImageResolution@1@_N@Z", U, [I, B]),
    ("risoluzione_immagine", "?get_image_resolution@DMLScanner@@SAPAVErrStat@1@W4FilmFormat@1@W4ImageResolution@1@PAI2@Z", VP, [I, I, VP, VP]),
]


# ---------------------------------------------------------------------------

class Scanner(object):
    """Lo scanner vero, comandato attraverso l'SDK originale.

    Uso tipico:

        with Scanner(r"C:\\MasterflexD\\Scanner") as s:
            s.scegli_formato(FilmFormat.F135)
            s.avanza_fotogramma()
            dati = s.scansiona_canale(ColorChannel.G)
    """

    def __init__(self, cartella_scanner=r"C:\MasterflexD\Scanner",
                 dll=r"C:\MasterflexD\Scanner\Bin\scn_dll.dll",
                 apri_subito=True):
        if os.name != "nt":
            raise ErroreScanner("l'SDK originale gira solo sul PC della macchina")
        if not os.path.exists(dll):
            raise ErroreScanner("scn_dll.dll non trovato in %s" % dll)

        # il DLL cerca le sue compagne (AIDToolkit, DMLDICELib) accanto a se'
        cartella_bin = os.path.dirname(dll)
        os.environ["PATH"] = cartella_bin + os.pathsep + os.environ.get("PATH", "")
        self._dll = ctypes.WinDLL(dll)

        self._m = {}
        self._mancanti = []
        for etichetta, nome, restype, argtypes in METODI:
            if argtypes is None:          # firma speciale, costruita a mano
                continue
            try:
                self._m[etichetta] = _Metodo(self._dll, nome, restype, argtypes, etichetta)
            except ErroreScanner:
                self._mancanti.append(etichetta)
        for etichetta, nome, restype, argtypes in STATICHE:
            try:
                f = self._dll[nome]
                f.restype = restype
                f.argtypes = argtypes
                self._m[etichetta] = f
            except Exception:
                self._mancanti.append(etichetta)

        self._scan_raw_addr = self._indirizzo("?scan_raw@DMLScannerAdv@@QAEPAVErrStat@DMLScanner@@PAGW4ColorChannel@1@VExposureTime@1@_NHHHHW4ImageResolution@3@H@Z")

        self.cartella = cartella_scanner
        self._obj = None
        self._buffer = None
        if apri_subito:
            self.apri()

    # -- utilita' interne --------------------------------------------------

    def _indirizzo(self, nome):
        try:
            return ctypes.cast(self._dll[nome], ctypes.c_void_p).value
        except Exception:
            return None

    def _w(self, testo):
        """Stringa larga (const unsigned short*), come vuole l'SDK."""
        return ctypes.c_wchar_p(testo)

    def _verifica(self, err_ptr, cosa):
        """ErrStat* nullo = tutto bene. Altrimenti si legge quanti errori porta."""
        if not err_ptr:
            return
        try:
            n = self._m["err_quanti"](err_ptr, False)
        except Exception:
            n = -1
        if n:
            raise ErroreScanner("%s: l'SDK segnala %s errori (ErrStat %#x)"
                                % (cosa, n, err_ptr))

    # -- ciclo di vita -----------------------------------------------------

    def apri(self):
        """Costruisce l'oggetto DMLScannerAdv: e' qui che la macchina si apre.

        Se la scheda non c'e' o il driver non e' attivo, l'SDK scrive
        "Can't open scanner" nel proprio log dentro Scanner\\SDKCacheDir\\.
        """
        self._buffer = ctypes.create_string_buffer(DIM_OGGETTO_SCANNER)
        self._obj = ctypes.cast(self._buffer, ctypes.c_void_p).value

        err = ctypes.c_void_p(0)
        cal = os.path.join(self.cartella, "SDKCacheDir")
        ini = os.path.join(self.cartella, "IniFiles")
        fw = os.path.join(self.cartella, "Firmware")
        for c in (cal, ini, fw):
            if not os.path.isdir(c):
                os.makedirs(c)

        self._m["costruttore"](self._obj, ctypes.byref(err),
                               self._w(cal), self._w(ini), self._w(fw),
                               None, None, True, False)
        self._verifica(err.value, "apertura scanner")
        return self

    def chiudi(self):
        if self._obj and "distruttore" in self._m:
            try:
                self._m["distruttore"](self._obj)
            except Exception:
                pass
        self._obj = None
        self._buffer = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.chiudi()

    # -- comandi -----------------------------------------------------------

    def tipo_scanner(self):
        return self._m["tipo_scanner"](self._obj)

    def id_scanner(self):
        v = ctypes.c_uint(0)
        self._verifica(self._m["id_scanner"](self._obj, ctypes.byref(v)), "id scanner")
        return v.value

    def reset(self):
        self._verifica(self._m["reset"](self._obj), "reset")

    def scegli_formato(self, formato=FilmFormat.F135):
        self._verifica(self._m["scegli_formato"](self._obj, formato), "scelta formato")

    def tipo_gate(self):
        v = ctypes.c_int(-1)
        self._verifica(self._m["tipo_gate"](self._obj, ctypes.byref(v)), "tipo gate")
        return v.value

    def alimentazione(self, accesa=True):
        chiave = "alimenta_on" if accesa else "alimenta_off"
        self._verifica(self._m[chiave](self._obj), chiave)

    def avanza_fotogramma(self, mezzo=False):
        chiave = "avanza_mezzo" if mezzo else "avanza_fotogramma"
        self._verifica(self._m[chiave](self._obj), chiave)

    def fotogramma_successivo(self):
        self._verifica(self._m["fotogramma_successivo"](self._obj), "fotogramma successivo")

    def regola_fine(self, passi):
        """Sposta la pellicola di N passi motore (0,01 mm l'uno).

        E' la regolazione fine del rilevamento fotogrammi: la stessa che il
        programma di servizio usa nella Length Calibration con le frecce.
        Va verificata sulla macchina - che il segno e la scala siano quelli
        che ci aspettiamo si controlla in un minuto guardando la pellicola.
        """
        self._verifica(self._m["correggi_posizione_fine"](self._obj, int(passi)),
                       "regolazione fine")

    def fuoco(self, tipo=FocusType.AUTO):
        self._verifica(self._m["fuoco"](self._obj, tipo), "autofocus")

    def offset_xy(self, formato=FilmFormat.F135):
        x = ctypes.c_ushort(0); y = ctypes.c_ushort(0)
        self._verifica(self._m["leggi_offset_xy"](self._obj, formato,
                                                  ctypes.byref(x), ctypes.byref(y)),
                       "lettura offset XY")
        return x.value, y.value

    def ingresso(self, numero):
        """Legge un ingresso digitale della macchina: i sensori stanno qui.

        Quale numero corrisponda a quale sensore va scoperto sulla macchina,
        provandoli uno per uno con il percorso libero e poi con la pellicola
        inserita. E' il modo pulito per compilare la sezione sensori di
        NOTE-MACCHINA.md.
        """
        stato = ctypes.c_bool(False)
        ok = self._m["leggi_ingresso"](self._obj, numero, ctypes.byref(stato), None)
        return bool(stato.value) if ok else None

    def sensori(self, numeri=range(0, 16)):
        """Fotografia di tutti gli ingressi digitali, per esplorazione."""
        return dict((n, self.ingresso(n)) for n in numeri)

    def esposizione_default(self, moltiplicatore=1.0):
        """Costruisce un ExposureTime, lo porta ai valori di fabbrica e lo scala."""
        buf = ctypes.create_string_buffer(SIZEOF_EXPOSURE_TIME)
        this = ctypes.cast(buf, ctypes.c_void_p).value
        self._m["exp_costruttore"](this)
        self._m["exp_default"](this)
        if moltiplicatore != 1.0:
            self._m["exp_moltiplica"](this, ctypes.c_double(moltiplicatore))
        return buf

    def scansiona_canale(self, canale=ColorChannel.G, esposizione=1.0,
                         risoluzione=ImageResolution.PIENA,
                         x0=0, y0=0, larghezza=0, altezza=0):
        """Una lettura grezza di un canale: il cuore di tutto.

        Ritorna i campioni a 16 bit cosi' come escono dall'ADC, senza nessuna
        correzione: e' esattamente cio' che il nostro software vuole, e cio'
        che il programma originale buttava via riducendo a 600x400.

        ExposureTime viaggia per valore (392 byte sullo stack), quindi la
        chiamata si costruisce a mano invece di passare dalla tabella.
        """
        w, h = ImageResolution.DIMENSIONI[risoluzione]
        if not larghezza:
            larghezza = w
        if not altezza:
            altezza = h

        buffer_img = (ctypes.c_uint16 * (larghezza * altezza))()
        exp = self.esposizione_default(esposizione)

        # firma: (ushort*, int canale, ExposureTime valore, bool, int x4, int res, int)
        class _Exp(ctypes.Structure):
            _fields_ = [("dati", ctypes.c_ubyte * SIZEOF_EXPOSURE_TIME)]

        proto = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_uint16), ctypes.c_int,
                                   _Exp, ctypes.c_bool, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int)
        chiama = proto(_crea_trampolino(self._scan_raw_addr))
        valore = _Exp.from_buffer_copy(bytes(exp))

        err = chiama(self._obj, buffer_img, canale, valore, True,
                     x0, y0, larghezza, altezza, risoluzione, 0)
        self._verifica(err, "scansione canale %s" % canale)
        return bytes(memoryview(buffer_img).cast("B")), larghezza, altezza

    def salva_parametri_flash(self, percorso):
        """Copia il Parameter Flash della macchina in un file.

        E' il primo lavoro da fare, prima di ogni altra cosa: dentro ci sono
        la length calibration, gli offset XY e le posizioni di fuoco, tarate
        una volta sola da un tecnico con pellicole di prova che oggi non si
        comprano piu'. Se si perdono, non si rifanno.

        La dimensione della struttura NVMemRaw non e' nota: si riserva
        abbondante e si salva tutto.
        """
        buf = ctypes.create_string_buffer(64 * 1024)
        self._verifica(self._m["nvm_grezza"](self._obj, buf, False),
                       "lettura Parameter Flash")
        with open(percorso, "wb") as f:
            f.write(buf.raw)
        return percorso

    def diagnosi(self):
        """Riepilogo di cosa il collegamento riesce e non riesce a fare."""
        return {
            "metodi_collegati": len(self._m),
            "metodi_mancanti": list(self._mancanti),
            "oggetto_creato": self._obj is not None,
        }
