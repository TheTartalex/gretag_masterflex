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
C++ esportati dal DLL, che nel formato Microsoft contengono la firma completa,
e ricontrollati con llvm-undname il 26/10/2026 (tutti coerenti).
L'elenco integrale sta in documentazione/software-originale/api-sdk.md.

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
Gira SOLO sul PC della macchina: serve Python a 32 bit, scn_dll.dll, il
driver della scheda attivo (EKframe.sys originale, oppure EKframePnP sulla
macchina di riserva: stessa interfaccia) e la scheda nel suo slot. Su qualunque altra macchina
l'apertura fallisce con "Can't open scanner", che e' esattamente l'errore che
si legge in Scanner\\SDKCacheDir\\log_0000.txt.

E' codice che comanda motori del 1995. Va provato con il percorso pellicola
VUOTO, come prescrive il manuale di servizio prima di ogni test.
"""

from __future__ import annotations

import ctypes
import os
import struct
import time
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


def finestra_grezza(risoluzione):
    """(larghezza, altezza) in pixel a piena risoluzione del fotogramma
    intero, arrotondate a multipli di 2^risoluzione: scan_raw riduce
    contando i pixel e dividendo per 4^risoluzione, e con lati non multipli
    il buffer non tornerebbe con le dimensioni."""
    passo = 1 << risoluzione
    w, h = ImageResolution.DIMENSIONI[ImageResolution.PIENA]
    return w - w % passo, h - h % passo


# Dimensioni delle immagini di scan_raw per risoluzione: diverse da quelle di
# capture() (DIMENSIONI) per la 1, 3 e 4, che non sono meta' esatte.
DIMENSIONI_GREZZE = dict(
    (r, tuple(x >> r for x in finestra_grezza(r))) for r in range(5))


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
    """Errore dell'SDK. 'codici' sono le voci lette dall'ErrStat:
    (ScnError, dettaglio1, dettaglio2) per ciascun errore."""

    def __init__(self, messaggio, codici=()):
        RuntimeError.__init__(self, messaggio)
        self.codici = list(codici)


# I codici ScnError, dalla tabella di ErrStat::formatScnErrorEnum
# (scn_dll.dll, 0x18028AE0, switch su 0..0xCE). Solo quelli che servono a
# leggere i messaggi; per il resto si stampa il numero.
NOMI_ERRORI_SDK = {
    1: "ERR_SDK_SW", 2: "ERR_TIMEOUT", 3: "ERR_ILLEGAL_IN_THIS_STATE",
    4: "ERR_NO_SCANNER", 5: "ERR_COMMAND_PARAMETER", 6: "ERR_CALIBRATION_FAILED",
    7: "ERR_SENSOR_CAL_FAILED", 8: "ERR_NO_NK_GATE", 9: "ERR_NOT_IMPLEMENTED",
    10: "ERR_CANT_OPEN_SCANNER", 11: "ERR_NOT_CALIBRATED", 12: "ERR_NO_FILM_LOADED",
    13: "ERR_FILM_LOADED", 14: "ERR_NO_GATE_INSTALLED", 15: "ERR_FOCUS_FAILED",
    16: "ERR_NO_CALIB_DATA", 18: "ERR_BUFFER_NOT_ACCEPTED", 19: "ERR_NO_IMAGE_BUFFER",
    22: "ERR_POSITION_UNKNOWN", 24: "ERR_OS", 25: "ERR_OS_LAST_ERR",
    26: "ERR_MEMORY_ALLOCATION", 28: "ERR_FILM_JAM", 31: "ERR_MOVE",
    32: "ERR_END_OF_FILM", 33: "ERR_CANCEL_DETECTED", 39: "ERR_FILM_UNKNOWN",
    40: "ERR_NK_GATE", 41: "ERR_NVMEM", 42: "ERR_GENERAL_FW", 43: "ERR_SCANNER_HW",
    46: "ERR_SW_SCANNER_IFC", 48: "ERR_NK_GATE_FIRMWARE", 56: "ERR_DIAG_MOTOR",
    57: "ERR_TEST_ADV_FAILED", 58: "ERR_FEED_ON_FAILED", 59: "ERR_FEED_OFF_FAILED",
    67: "ERR_NK_SENSOR_CAL_REQUIRED",
    200: "EV_FILM_INSERTED", 201: "EV_VOLTAGE_RANGE", 202: "EV_GATE_CHANGE",
    203: "EV_STATE_CHANGE", 204: "EV_SCAN_COMPLETE",
    205: "EV_SCAN_COMPLETE_FILM_TOO_LONG", 206: "EV_SCANNER_CHANGED",
}
ERR_ILLEGAL_IN_THIS_STATE = 3
ERR_NO_SCANNER = 4
EV_STATE_CHANGE = 203
EV_FILM_INSERTED = 200

# Lo stato interno dell'SDK (variabile globale a 0x180BF92C, controllata da
# ScnState a 0x18079E40 prima di ogni comando). Vale 7 al caricamento della
# DLL. Nello stato 10 quasi ogni comando risponde ERR_NO_SCANNER piu'
# ERR_ILLEGAL_IN_THIS_STATE(10); nello stato 0 quasi tutti sono ammessi.
# Sulla riserva, il 26/10/2026, dopo l'apertura l'SDK e' rimasto nello stato
# 10 per una quarantina di secondi (collegamento, LED, firmware) prima di
# passare a 0: i comandi dati in quel tempo fallivano tutti.
# Il cambio arriva come EV_STATE_CHANGE (203) con (nuovo, vecchio):
# ErrStat(0xCB, nuovo, 2, vecchio, 2) a 0x1807AAB2.
STATO_SDK_PRONTO = 0
STATO_SDK_NON_COLLEGATO = 10
NOMI_STATI_SDK = {0: "pronto", 7: "appena caricato",
                  10: "scanner non ancora collegato"}


# Messaggi del carrello Capri (UserErrorDescriptionV1.0x.html del pacchetto
# CapriFirmware). Non si sa ancora se l'SDK li riporta con questi numeri:
# se uno dei valori di un errore coincide, il messaggio lo dice.
MESSAGGI_CARRELLO = {
    37502: "carrello non inizializzato (inizializzazione interrotta)",
    37503: "risposta inattesa dello slave (sezione APS)",
    37504: "lo slave del carrello non risponde",
    37505: "CCD o lente non trovano lo zero",
    37506: "il tamburo (revolver) non trova lo zero",
    37507: "la guida SINISTRA della pellicola non trova lo zero",
    37508: "la guida DESTRA della pellicola non trova lo zero",
    37509: "il tamburo non cambia formato",
    27502: "guide bloccate dal sollevatore APS: formato non cambiato",
    37510: "pellicola rimasta sui sensori di rilevamento fotogramma",
    37511: "pellicola rimasta sul sensore d'uscita",
    37512: "sensori di rilevamento fotogramma fuori finestra",
    37513: "sensore DX sinistro fuori finestra",
    37514: "sensore DX destro fuori finestra",
    37515: "Parameter Flash: errore di lettura o scrittura",
    37516: "Parameter Flash: checksum sbagliato o dati non validi",
    37525: "coperchio dello scanner aperto",
    37526: "pellicola inceppata fra tamburo e uscita",
    37531: "pellicola inceppata nel distaccatore",
}

# I due callback che il costruttore di DMLScannerAdv accetta. Prima si
# passava NULL a entrambi: l'SDK non aveva modo di dirci niente mentre
# lavorava, e quando i motori delle guide sono andati a fine corsa non e'
# rimasta traccia di cosa la macchina stesse segnalando. Adesso finiscono nel
# registro dell'agente.
#   void (__cdecl *)(DMLScanner::ErrStat *)   errore asincrono
#   void (__cdecl *)(unsigned int)            "Asynchronous Event [%d]"
CALLBACK_ERRORE = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
CALLBACK_EVENTO = ctypes.CFUNCTYPE(None, ctypes.c_uint)

# Le due bool in coda al costruttore. Il significato non si legge dai simboli;
# (True, False) e' quello con cui lo scanner si apre sulla riserva. L'agente
# permette di provare l'altra combinazione per capire se e' la prima a far
# partire l'inizializzazione completa della meccanica all'apertura.
APERTURA = {"completa": (True, False), "leggera": (False, False)}


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
    # Con il carrello Gretag diventa la richiesta 3 al GateControl,
    # ChangeFilmFormat: guide al formato e carrello in attesa della pellicola
    # (scn_dll 0x18037FA0, attesa fino a 30 s). PhxScanner.exe la chiama con
    # 1 o 2 (0x447860).
    ("modo_ingresso", "?set_scanner_input_mode@DMLScanner@@QAEPAVErrStat@1@W4__MIDL___MIDL_itf_FilmGate_0000_0003@@@Z", VP, [I]),
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
    # --- 26/10/2026: esposizione calibrata, pressore, temperatura, ASF ---
    # calibrate_all(NULL, NULL), come PhxScanner (0x447A2C): calibra i LED e
    # i tempi di esposizione dell'SDK a percorso vuoto.
    ("calibra_tutto", "?calibrate_all@DMLScanner@@QAEPAVErrStat@1@PAPAY143$$CBV?$vector@HV?$allocator@H@std@@@std@@PAX@Z", VP, [VP, VP]),
    # get_exp_time(&open_gate, &nominale): puntatori agli ExposureTime interni
    # (0x1802F790, "get open gate exposure time")
    ("tempi_esposizione", "?get_exp_time@DMLScannerAdv@@QAEXPAPAVExposureTime@1@0@Z", None, [VP, VP]),
    ("exp_assegna", "??4ExposureTime@DMLScannerAdv@@QAEAAV01@ABV01@@Z", VP, [VP]),
    # gi_clamp(true) = Gretag Clamp Down (0x18070B20), false = Clamp Up
    # (0x18070E10): il pressore della pellicola
    ("pressore", "?gi_clamp@DMLScannerAdv@@QAEPAVErrStat@DMLScanner@@_N@Z", VP, [B]),
    # get_temperature(double*, double*): comando 0x31 alla testa
    ("temperatura", "?get_temperature@DMLScannerAdv@@QAEPAVErrStat@DMLScanner@@PAN0@Z", VP, [VP, VP]),
    # get_voltage(SupplyVoltage**): comando 0x30 alla testa; SupplyVoltage e'
    # una vtable e 8 double (costruttore 0x1802FA90)
    ("tensioni", "?get_voltage@DMLScannerAdv@@QAEPAVErrStat@DMLScanner@@PAPAVSupplyVoltage@1@@Z", VP, [VP]),
    # gi_set_asf_mode(bool): comando 0x25 alla testa. ASF = Applied Science
    # Fiction, cioe' Digital ICE
    ("modo_asf", "?gi_set_asf_mode@DMLScanner@@QAEPAVErrStat@1@_N@Z", VP, [B]),

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

    def __init__(self, cartella_scanner=r"C:\MasterflexD\Scanner", dll=None,
                 apri_subito=True, apertura="completa", log=None):
        if os.name != "nt":
            raise ErroreScanner("l'SDK originale gira solo sul PC della macchina")
        # Prima il DLL era sempre quello di C:\MasterflexD, anche quando
        # --scanner indicava un'altra cartella: SDK di un posto, file di
        # configurazione di un altro.
        if dll is None:
            dll = os.path.join(cartella_scanner, "Bin", "scn_dll.dll")
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
        self.apertura = apertura
        self._log = log or (lambda testo: None)
        self._obj = None
        self._buffer = None
        # i callback vanno tenuti vivi finche' vive l'oggetto SDK
        self._cb_errore = CALLBACK_ERRORE(self._su_errore)
        self._cb_evento = CALLBACK_EVENTO(self._su_evento)
        self.eventi = []          # (ora, numero) degli eventi asincroni
        self.stato_sdk = None     # dagli eventi EV_STATE_CHANGE; None = non noto
        self.formato = FilmFormat.F135   # quello che l'SDK sceglie all'apertura
        self.pellicola_inserita = False  # dall'evento EV_FILM_INSERTED
        self._offset = {}         # offset XY per formato, letti una volta
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

    def _leggi_errori(self, err_ptr):
        """Le voci di un ErrStat: [(ScnError, dettaglio1, dettaglio2), ...].

        get_next_error(ScnError*, int*, int*, bool) le restituisce una alla
        volta dopo reset_read_index().
        """
        voci = []
        try:
            n = int(self._m["err_quanti"](err_ptr, False))
        except Exception:
            return None
        try:
            self._m["err_azzera_lettura"](err_ptr)
            for _ in range(min(n, 32)):
                codice = ctypes.c_int(0)
                a = ctypes.c_int(0)
                b = ctypes.c_int(0)
                self._m["err_prossimo"](err_ptr, ctypes.byref(codice),
                                        ctypes.byref(a), ctypes.byref(b), False)
                voci.append((codice.value, a.value, b.value))
        except Exception:
            # meglio il numero di errori senza dettaglio che nessun messaggio
            return [(None, None, None)] * n
        return voci

    def _verifica(self, err_ptr, cosa):
        """ErrStat* nullo = tutto bene. Altrimenti si leggono gli errori.

        Prima il messaggio riportava l'indirizzo dell'ErrStat ("ErrStat
        0x0123abcd"), che non dice niente a nessuno. Adesso riporta i codici
        ScnError con i due valori di dettaglio, che si possono confrontare con
        il log dell'SDK (SDKCacheDir\\log_0000.txt).
        """
        if not err_ptr:
            return
        voci = self._leggi_errori(err_ptr)
        if voci is None:
            raise ErroreScanner("%s: l'SDK segnala un errore illeggibile" % cosa)
        if not voci:
            return
        def descrivi(v):
            noti = [MESSAGGI_CARRELLO[x] for x in v if x in MESSAGGI_CARRELLO]
            base = "codice %s (%s, %s)" % v
            if v[0] in NOMI_ERRORI_SDK:
                base += " " + NOMI_ERRORI_SDK[v[0]]
            return base + (" = " + "; ".join(noti) if noti else "")
        testo = ", ".join(descrivi(v) for v in voci)
        raise ErroreScanner("%s: l'SDK segnala %d error%s: %s"
                            % (cosa, len(voci), "e" if len(voci) == 1 else "i",
                               testo), voci)

    def _su_errore(self, err_ptr):
        try:
            voci = self._leggi_errori(err_ptr) if err_ptr else []
            for codice, nuovo, vecchio in (voci or []):
                if codice == EV_FILM_INSERTED:
                    self.pellicola_inserita = True
                    self._log("SDK: pellicola inserita (%s, %s)" % (nuovo, vecchio))
                    return
                if codice == EV_STATE_CHANGE:
                    self.stato_sdk = nuovo
                    self._log("SDK: stato %s -> %s (%s)"
                              % (vecchio, nuovo, NOMI_STATI_SDK.get(nuovo, "?")))
                    return
            self._log("SDK, errore asincrono: %s" % (voci or "senza dettaglio"))
        except Exception:
            pass

    def pronto(self):
        """True quando l'SDK accetta i comandi (stato 0).

        Lo stato arriva con gli eventi EV_STATE_CHANGE. Se non ne e' ancora
        arrivato nessuno, lo si chiede con get_gate_type, che non muove
        niente: nello stato 10 risponde ERR_NO_SCANNER.
        """
        if self.stato_sdk is not None:
            return self.stato_sdk == STATO_SDK_PRONTO
        try:
            self.tipo_gate()
        except ErroreScanner as exc:
            if any(c == ERR_NO_SCANNER or
                   (c == ERR_ILLEGAL_IN_THIS_STATE and a == STATO_SDK_NON_COLLEGATO)
                   for c, a, b in exc.codici):
                self.stato_sdk = STATO_SDK_NON_COLLEGATO
                return False
        # da qui in poi bastano gli eventi
        self.stato_sdk = STATO_SDK_PRONTO
        return True

    def _su_evento(self, numero):
        try:
            self.eventi.append((time.time(), int(numero)))
            del self.eventi[:-200]
            self._log("SDK, evento asincrono %d" % numero)
        except Exception:
            pass

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

        flag1, flag2 = APERTURA[self.apertura]
        self._log("apertura dell'SDK (%s): %s, %s" % (self.apertura, flag1, flag2))
        self._m["costruttore"](self._obj, ctypes.byref(err),
                               self._w(cal), self._w(ini), self._w(fw),
                               ctypes.cast(self._cb_errore, ctypes.c_void_p),
                               ctypes.cast(self._cb_evento, ctypes.c_void_p),
                               flag1, flag2)
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
        """Azzeramento: ResetScanner (0x1802D210) manda il comando 0x13 alla
        testa e poi ScannerInterface::call_gretag_reset (0x18079510), cioe' la
        richiesta 14, "Gretag Reset", al GateControl: il carrello si
        reinizializza e porta a zero i suoi motori (tamburo, guide, lente,
        CCD; vedi carrello-capri.md §1). Il programma originale non la chiama:
        lo fa il GateControl quando parte. Senza programma originale, e con
        il flag "No Reset" del carrello acceso, nessun altro la fa."""
        self._verifica(self._m["reset"](self._obj), "azzeramento motori")

    def scegli_formato(self, formato=FilmFormat.F135):
        self._verifica(self._m["scegli_formato"](self._obj, formato), "scelta formato")
        self.formato = formato

    def arma_carrello(self, modo=1):
        """Mette il carrello in attesa della pellicola nel formato scelto.

        select_film_format da solo sceglie il formato nell'SDK ma non tocca
        il carrello: il 26/10/2026 sulla riserva lo scanner acquisiva ma il
        carrello non accettava la pellicola. Il programma originale fa
        select_film_format e poi set_scanner_input_mode (PhxScanner.exe,
        0x447820 e 0x447860), che per il Gretag e' il ChangeFilmFormat del
        GateControl. Il modo che usa e' 1 o 2; quale dei due sia il rullo
        intero e quale il fotogramma singolo va visto sulla macchina.
        """
        self.pellicola_inserita = False
        self._verifica(self._m["modo_ingresso"](self._obj, int(modo)),
                       "carrello in attesa della pellicola (modo %d)" % modo)

    def origine_finestra(self):
        """Angolo in alto a sinistra del fotogramma sul CCD, per il formato
        scelto: e' l'offset XY tarato in fabbrica (gretag_get_xy_offset).

        Il programma originale scansiona il 135 da [47,76] a [2146,1475]
        (log dell'SDK della riserva, 26/10/2026): 2100x1400 a partire
        dall'offset del 135, che e' 47, 76 anche in Scanner_Customer.ini.
        """
        if self.formato not in self._offset:
            try:
                self._offset[self.formato] = self.offset_xy(self.formato)
            except ErroreScanner as exc:
                self._log("offset XY del formato %s non letto (%s): uso 0, 0"
                          % (self.formato, exc))
                return 0, 0
            self._log("offset XY del formato %s: %s, %s"
                      % ((self.formato,) + self._offset[self.formato]))
        return self._offset[self.formato]

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
        """Il carrello Gretag porta in finestra il fotogramma successivo:
        richiesta 6 al GateControl (scn_dll 0x180386A0), quella che usa
        PhxScanner.exe. advance_one_frame e' per i carrelli Kodak."""
        self._verifica(self._m["fotogramma_successivo"](self._obj), "fotogramma successivo")

    def fotogramma_precedente(self):
        self._verifica(self._m["fotogramma_precedente"](self._obj), "fotogramma precedente")

    def espelli(self):
        """Espelle la pellicola con cancel_scan: nell'SDK porta a
        ScanCntr::OnCancelScan (0x18060000), che con il carrello Gretag
        manda la richiesta 5, Eject, al GateControl (0x18038580). Il verso lo
        decide il flag 5 del carrello ("Eject to Back"): a 0, come nel
        backup del 2025, la pellicola esce dal davanti, da dove e' entrata."""
        self.pellicola_inserita = False
        self._verifica(self._m["annulla_acquisizione"](self._obj), "espulsione")

    def espelli_retro(self, max_fotogrammi=45, max_secondi=180.0):
        """Fa uscire la pellicola dal retro, nel verso del trasporto.

        Il comando di espulsione del carrello (22, richiesta 5 dell'SDK) non
        porta il verso: lo decide il flag 5, che sta nel Parameter Flash e si
        cambia solo dalla COM5, occupata dall'SDK mentre l'agente lavora. Per
        il retro si fa quindi quello che fa il carrello a fine striscia: si
        va avanti a fotogrammi (gretag_NextFrame) finche' il carrello rifiuta,
        perche' la pellicola non c'e' piu'.

        Due tetti, fotogrammi e tempo: un rullo da 135 ne ha 40. Se si
        arriva al tetto senza che il carrello abbia rifiutato, ci si ferma e
        lo si dichiara: o la pellicola e' inceppata o il carrello non si
        muove, e continuare non aiuta.

        Restituisce (fotogrammi avanzati, errore che ha fermato il trasporto).
        Quale errore dia l'SDK a pellicola uscita va ancora visto sulla
        macchina: per questo viene restituito e scritto nel registro.
        """
        inizio = time.time()
        fatti = 0
        while fatti < max_fotogrammi and time.time() - inizio < max_secondi:
            try:
                self.fotogramma_successivo()
            except ErroreScanner as exc:
                self.pellicola_inserita = False
                self._log("espulsione dal retro: trasporto fermo dopo %d "
                          "fotogramm%s (%s)" % (fatti, "o" if fatti == 1 else "i", exc))
                return fatti, str(exc)
            fatti += 1
        raise ErroreScanner(
            "espulsione dal retro fermata dopo %d fotogrammi e %.0f s: il "
            "carrello accetta ancora il fotogramma successivo. Pellicola "
            "inceppata o lunghissima: controllare a mano."
            % (fatti, time.time() - inizio), [])

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
        documentazione/hardware/note-macchina.md.
        """
        stato = ctypes.c_bool(False)
        ok = self._m["leggi_ingresso"](self._obj, numero, ctypes.byref(stato), None)
        return bool(stato.value) if ok else None

    def sensori(self, numeri=range(0, 16)):
        """Fotografia di tutti gli ingressi digitali, per esplorazione."""
        return dict((n, self.ingresso(n)) for n in numeri)

    # Base dell'esposizione: True = i tempi calibrati dell'SDK (open gate,
    # dopo calibrate_all), False = i valori di fabbrica.
    tempi_calibrati = False

    def calibra_sdk(self):
        """calibrate_all: la calibrazione dell'SDK (LED e tempi), a percorso
        vuoto. E' quella che il programma originale fa all'avvio."""
        self._verifica(self._m["calibra_tutto"](self._obj, None, None),
                       "calibrazione dell'SDK")

    def esposizione_calibrata(self, moltiplicatore=1.0):
        """ExposureTime copiato dai tempi open gate calibrati dell'SDK e
        scalato. None se l'SDK non li ha (mai calibrato): allora si usano i
        valori di fabbrica."""
        og = ctypes.c_void_p()
        nom = ctypes.c_void_p()
        self._m["tempi_esposizione"](self._obj, ctypes.byref(og), ctypes.byref(nom))
        if not og.value:
            return None
        buf = ctypes.create_string_buffer(SIZEOF_EXPOSURE_TIME)
        this = ctypes.cast(buf, ctypes.c_void_p).value
        self._m["exp_costruttore"](this)
        self._m["exp_assegna"](this, og.value)
        if not self._m["exp_positiva"](this):
            return None
        if moltiplicatore != 1.0:
            self._m["exp_moltiplica"](this, ctypes.c_double(moltiplicatore))
        return buf

    def esposizione(self, moltiplicatore=1.0):
        if self.tempi_calibrati:
            buf = self.esposizione_calibrata(moltiplicatore)
            if buf is not None:
                return buf
        return self.esposizione_default(moltiplicatore)

    def pressore(self, giu):
        self._verifica(self._m["pressore"](self._obj, bool(giu)),
                       "pressore " + ("giu'" if giu else "su"))

    def temperatura(self):
        a = ctypes.c_double(0.0)
        b = ctypes.c_double(0.0)
        self._verifica(self._m["temperatura"](self._obj, ctypes.byref(a),
                                              ctypes.byref(b)), "temperatura")
        return a.value, b.value

    def tensioni(self):
        """Le 8 tensioni di alimentazione della testa. Quale sia quale non e'
        scritto da nessuna parte: si leggono in ordine."""
        p = ctypes.c_void_p()
        self._verifica(self._m["tensioni"](self._obj, ctypes.byref(p)), "tensioni")
        if not p.value:
            return []
        valori = (ctypes.c_double * 8).from_address(p.value + 8)
        return [float(v) for v in valori]

    def modo_asf(self, acceso):
        self._verifica(self._m["modo_asf"](self._obj, bool(acceso)),
                       "Digital ICE (ASF)")

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
                         x0=None, y0=None, larghezza=0, altezza=0, buio=False):
        """Una lettura grezza di un canale: il cuore di tutto.

        Ritorna i campioni a 16 bit cosi' come escono dall'ADC, senza nessuna
        correzione: e' esattamente cio' che il nostro software vuole, e cio'
        che il programma originale buttava via riducendo a 600x400.

        x0, y0, larghezza, altezza sono in pixel a PIENA risoluzione sul
        CCD; x0, y0 = None vuol dire l'offset XY del formato (come fa il
        programma originale), larghezza e altezza 0 = tutto il fotogramma. Il risultato e' ridotto di 2^risoluzione per
        lato. ExposureTime viaggia per valore (392 byte sullo stack), quindi
        la chiamata si costruisce a mano invece di passare dalla tabella.
        """
        piena_w, piena_h = finestra_grezza(risoluzione)
        larghezza = larghezza or piena_w
        altezza = altezza or piena_h
        if x0 is None or y0 is None:
            ox, oy = self.origine_finestra()
            x0 = ox if x0 is None else x0
            y0 = oy if y0 is None else y0
        passo = 1 << risoluzione
        larghezza -= larghezza % passo
        altezza -= altezza % passo
        w, h = larghezza >> risoluzione, altezza >> risoluzione

        buffer_img = (ctypes.c_uint16 * (w * h))()
        exp = self.esposizione(esposizione)

        # firma: (ushort*, int canale, ExposureTime valore, bool,
        #         int col_inizio, int col_fine, int riga_inizio, int riga_fine,
        #         ImageResolution, int)
        # I quattro interi NON sono x, y, larghezza, altezza: scan_raw
        # (scnr_ifc, 0x18078940) conta i pixel come (b-a+1)*(d-c+1), poi
        # divide per 4^risoluzione, e al firmware (0x18069F50) manda il
        # rettangolo (a, c, b, d), estremi compresi. Fino al 26/10/2026 si
        # passava (0, 0, 2100, 1400): conteggio negativo, e il firmware della
        # testa rispondeva ERR_FW_ERR_NO 1 ("set resolution failed, ErrNo =
        # 1" nel log dell'SDK, che stampava "[0,2100] to [0,1400]"). Il
        # programma originale passa colonne 47-2146 e righe 76-1475: a-b sono
        # le colonne, c-d le righe.
        class _Exp(ctypes.Structure):
            _fields_ = [("dati", ctypes.c_ubyte * SIZEOF_EXPOSURE_TIME)]

        proto = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_uint16), ctypes.c_int,
                                   _Exp, ctypes.c_bool, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int)
        chiama = proto(_crea_trampolino(self._scan_raw_addr))
        valore = _Exp.from_buffer_copy(bytes(exp))

        a, b = x0, x0 + larghezza - 1
        c, d = y0, y0 + altezza - 1
        self._log("scan_raw canale %s, colonne %d-%d, righe %d-%d, risoluzione %d%s"
                  % (canale, a, b, c, d, risoluzione, ", AL BUIO" if buio else ""))
        # Il booleano dopo l'ExposureTime arriva fino al comando di scansione
        # come bit 0x80 (scn_dll 0x1807E2BE). Tutte le acquisizioni d'immagine
        # dell'SDK lo passano False (DMinScan, FrameDetErr, PreviewJogScan,
        # ScanCntr 0x180649B4); True compare solo in un percorso a parte di
        # ScanCntr (0x18064B43). Fino al 26/10/2026 sera si passava True: le
        # immagini della riserva erano al livello del buio (media 19 su R, G
        # e B) e non cambiavano con l'esposizione. Il bit e' quindi, con ogni
        # probabilita', la lettura al buio: si usa solo se chiesta.
        err = chiama(self._obj, buffer_img, canale, valore, bool(buio),
                     a, b, c, d, risoluzione, 0)
        self._verifica(err, "scansione canale %s" % canale)
        return bytes(memoryview(buffer_img).cast("B")), w, h

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
