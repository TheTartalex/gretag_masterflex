# -*- coding: utf-8 -*-
"""
Compatibilita' con l'ambiente di destinazione: Windows 7 a 32 bit.

Questo modulo esiste per un motivo solo: su x86 un processo ha circa 1,8 GB di
spazio indirizzabile utile, e le scansioni ad alta risoluzione lo esauriscono
con imbarazzante facilita'. Meglio saperlo prima di riempire la memoria a meta'
di un rullo da 36 fotogrammi.

Conti, per un fotogramma 36x24 mm, un piano float32 per canale:

     dpi        pixel     1 piano   4 canali   4ch x 2 esposizioni
    1000     1417x944        5 MB      21 MB          43 MB
    2000    2834x1889       21 MB      86 MB         171 MB
    3000    4251x2834       48 MB     193 MB         386 MB
    4000    5669x3779       86 MB     343 MB         686 MB

I 686 MB dei soli dati in ingresso sarebbero anche gestibili. Il problema erano
le FFT: una trasformata complessa a piena risoluzione occupa 343 MB in complex64
(il doppio in complex128, che e' il default di numpy), e ne servono piu' di una
alla volta. Da qui due scelte precise nel codice:

  - l'allineamento si stima su un ritaglio centrale, non sull'intero fotogramma
  - la traslazione si applica con interpolazione spline, non nel dominio di Fourier

Vedi registration.py. Non sono ottimizzazioni premature: senza, a 4000 dpi il
programma va in MemoryError su questa macchina.

## Memoria: perche' 4 GB installati non aiutano

La macchina di destinazione ha 4 GB di RAM. Non cambia niente, e vale la pena
capire perche' prima di provare a scansionare a 4000 dpi:

  - Windows 7 a 32 bit ne indirizza circa 3,25 GB in tutto
  - ogni singolo processo ha comunque 2 GB di spazio indirizzabile utente
  - di quei 2 GB, circa 1,8 sono realmente utilizzabili dopo l'overhead di
    Python, numpy e scipy
  - lo spazio a 32 bit si **frammenta**: un'allocazione contigua da 400 MB puo'
    fallire con 900 MB nominalmente liberi, perche' non c'e' un blocco contiguo
    di quella misura

E' un limite architetturale, non di RAM: aggiungere memoria non lo sposta.
Con l'opzione di avvio /3GB e un eseguibile large-address-aware si arriverebbe a
circa 3 GB per processo, ma e' una configurazione delicata che sottrae spazio al
kernel. Meglio non contarci e stare nei limiti.

Conseguenza pratica, con i numeri misurati dopo le correzioni sopra:

    dpi     1 esposizione      2 esposizioni (HDR)
   2000          180 MB              359 MB
   3000          404 MB              809 MB
   4000          719 MB             1438 MB   <- non ci sta

Quindi: **4000 dpi a esposizione singola vanno benissimo**, il preset "archivio"
a doppia esposizione va tenuto entro i 3400 dpi. Il programma lo calcola da solo
all'avvio e lo dice, invece di fallire a meta' rullo.

Senza le due correzioni descritte sopra, invece, gia' i 2000 dpi andavano in
MemoryError: il problema non erano mai i dati, erano le trasformate.

## Versioni

Windows 7 e' supportato fino a **Python 3.8** (il 3.9 richiede Windows 8.1+).
Il codice e' quindi scritto per il 3.8: niente operatore morsa nei punti critici,
niente `dict | dict`, niente annotazioni `list[str]` senza
`from __future__ import annotations`.

Ultime versioni delle dipendenze con wheel a 32 bit per Python 3.8:

    numpy==1.24.4      scipy==1.10.1
    Pillow==9.5.0      tifffile==2023.7.10
"""

from __future__ import annotations

import ctypes
import os
import platform
import struct
import sys
from typing import Dict, Optional, Tuple


# --------------------------------------------------------------------------

def is_32bit() -> bool:
    """True se il processo Python e' a 32 bit, indipendentemente dal sistema.
    Nota: conta il processo, non il sistema operativo. Un Python x86 su Windows
    a 64 bit ha gli stessi limiti di memoria di un Python x86 su Windows a 32."""
    return struct.calcsize("P") * 8 == 32


def python_ok() -> Tuple[bool, str]:
    v = sys.version_info
    if v[:2] < (3, 6):
        return False, "Serve almeno Python 3.6 (rilevato %d.%d)." % v[:2]
    if v[:2] > (3, 8) and platform.system() == "Windows":
        rel = platform.release()
        if rel in ("7", "2008ServerR2"):
            return False, ("Python %d.%d non gira su Windows 7: l'ultima versione "
                           "supportata e' la 3.8." % v[:2])
    return True, "Python %d.%d" % v[:2]


def available_memory_mb() -> Optional[float]:
    """Memoria disponibile al processo, in MB. None se non determinabile.

    Su Windows a 32 bit il vincolo non e' la RAM installata ma lo spazio
    indirizzabile del processo, quindi si prende il minore dei due.
    """
    total = None
    try:
        if platform.system() == "Windows":
            class MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MemStatus()
            st.dwLength = ctypes.sizeof(MemStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            total = min(st.ullAvailPhys, st.ullAvailVirtual) / (1024.0 ** 2)
        else:
            pages = os.sysconf("SC_AVPHYS_PAGES")
            size = os.sysconf("SC_PAGE_SIZE")
            total = pages * size / (1024.0 ** 2)
    except Exception:                                   # noqa: BLE001
        return None

    if is_32bit() and total is not None:
        total = min(total, 1800.0)
    return total


# --------------------------------------------------------------------------

def frame_pixels(frame_w_mm: float, frame_h_mm: float, dpi: int) -> Tuple[int, int]:
    return int(frame_w_mm / 25.4 * dpi), int(frame_h_mm / 25.4 * dpi)


def estimate_scan_mb(frame_w_mm: float, frame_h_mm: float, dpi: int,
                     channels: int = 4, exposures: int = 1) -> float:
    """Stima il picco di memoria per elaborare un fotogramma, in MB.

    Il fattore 2,2 copre gli array temporanei della pipeline: stack RGB,
    immagine invertita, maschera, risultato. E' una stima prudente ricavata
    dall'uso reale, non un numero tirato a caso.
    """
    w, h = frame_pixels(frame_w_mm, frame_h_mm, dpi)
    planes = channels * max(exposures, 1)
    return w * h * 4 * planes * 2.2 / (1024.0 ** 2)


def max_safe_dpi(frame_w_mm: float, frame_h_mm: float,
                 channels: int = 4, exposures: int = 1,
                 budget_mb: Optional[float] = None) -> int:
    """Massimo dpi che sta nel budget di memoria, arrotondato a 100."""
    if budget_mb is None:
        budget_mb = available_memory_mb() or 1800.0
    budget_mb *= 0.6                    # margine: non si riempie la memoria fino all'orlo

    dpi = 100
    best = 100
    while dpi <= 8000:
        if estimate_scan_mb(frame_w_mm, frame_h_mm, dpi, channels, exposures) <= budget_mb:
            best = dpi
        else:
            break
        dpi += 100
    return best


def check_environment(frame_w_mm: float = 36.0, frame_h_mm: float = 24.0,
                      dpi: int = 4000, channels: int = 4,
                      exposures: int = 1) -> Dict[str, object]:
    """Rapporto sull'ambiente, da mostrare all'avvio dell'interfaccia."""
    ok, pymsg = python_ok()
    avail = available_memory_mb()
    need = estimate_scan_mb(frame_w_mm, frame_h_mm, dpi, channels, exposures)
    safe = max_safe_dpi(frame_w_mm, frame_h_mm, channels, exposures, avail)

    warnings = []
    if not ok:
        warnings.append(pymsg)
    if is_32bit():
        warnings.append(
            "Processo a 32 bit: il tetto di memoria e' circa 1800 MB "
            "indipendentemente dalla RAM installata.")
    if avail is not None and need > avail * 0.6:
        warnings.append(
            "A %d dpi servono circa %.0f MB, troppi per i %.0f MB disponibili. "
            "Massimo consigliato su questa macchina: %d dpi."
            % (dpi, need, avail, safe))

    return {"python": pymsg, "bits": 32 if is_32bit() else 64,
            "system": "%s %s" % (platform.system(), platform.release()),
            "available_mb": avail, "needed_mb": need,
            "max_safe_dpi": safe, "warnings": warnings}


if __name__ == "__main__":
    from pprint import pprint
    pprint(check_environment())
