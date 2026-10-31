# -*- coding: utf-8 -*-
"""
Appunti di Windows e WhatsApp sul PC della macchina.

Il "trascinamento" dei file nella chat non si puo' simulare in modo serio:
WhatsApp Web e WhatsApp Desktop sono finestre Chromium, che accettano il
trascinamento OLE e ignorano il vecchio WM_DROPFILES; fingere un
trascinamento OLE vuol dire prendersi il mouse e indovinare dove sta la
finestra. Lo stesso risultato si ha, senza rubare niente a nessuno, con gli
appunti: si mettono li' i file, e nella chat basta Ctrl+V.

- **provino**: come immagine (CF_DIB). Qualunque browser la incolla in
  WhatsApp Web, e si apre l'anteprima con la didascalia.
- **foto**: come elenco di file (CF_HDROP, quello di "Copia" in Esplora
  risorse). Lo incollano WhatsApp Desktop, Esplora risorse e i browser
  recenti; e' quello che serve per mandare piu' foto in un colpo.

L'incolla da solo (facoltativo, cfg.whatsapp_incolla) preme Ctrl+V solo se
la finestra in primo piano si chiama WhatsApp: se l'operatore nel frattempo
ha cliccato altrove, non si incolla niente e lo si dice.

Fuori da Windows tutto risponde False: e' il PC della macchina che conta.
"""

from __future__ import annotations

import io
import os
import struct
import threading
import time
from typing import Callable, List, Optional

WINDOWS = os.name == "nt"

CF_DIB = 8
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002


def _api():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    u32 = ctypes.windll.user32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k32.GlobalFree.argtypes = [ctypes.c_void_p]
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.SetClipboardData.restype = ctypes.c_void_p
    u32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    u32.RegisterClipboardFormatW.restype = wintypes.UINT
    u32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    u32.GetForegroundWindow.restype = wintypes.HWND
    u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    return ctypes, k32, u32


def _metti(formati) -> bool:
    """formati: [(codice, bytes)]. Svuota gli appunti e li riempie."""
    if not WINDOWS:
        return False
    ctypes, k32, u32 = _api()
    for _ in range(10):                  # qualcun altro puo' averli aperti
        if u32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        u32.EmptyClipboard()
        for codice, dati in formati:
            h = k32.GlobalAlloc(GMEM_MOVEABLE, len(dati))
            if not h:
                return False
            p = k32.GlobalLock(h)
            ctypes.memmove(p, dati, len(dati))
            k32.GlobalUnlock(h)
            if not u32.SetClipboardData(codice, h):
                k32.GlobalFree(h)        # se non li ha presi, sono ancora nostri
                return False
        return True
    finally:
        u32.CloseClipboard()


def copia_immagine(percorso: str) -> bool:
    """Un JPEG negli appunti come immagine (CF_DIB: un BMP senza i primi 14
    byte di intestazione del file)."""
    if not WINDOWS:
        return False
    from PIL import Image
    im = Image.open(percorso)
    if im.mode != "RGB":
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="BMP")
    return _metti([(CF_DIB, buf.getvalue()[14:])])


def copia_file(percorsi: List[str]) -> bool:
    """File negli appunti come "Copia" di Esplora risorse (CF_HDROP)."""
    if not WINDOWS or not percorsi:
        return False
    _, _, u32 = _api()
    nomi = "".join(os.path.abspath(p) + "\0" for p in percorsi) + "\0"
    # DROPFILES: pFiles (offset dei nomi), pt (x, y), fNC, fWide = 1 (UTF-16)
    dati = struct.pack("<IiiII", 20, 0, 0, 0, 1) + nomi.encode("utf-16-le")
    effetto = u32.RegisterClipboardFormatW("Preferred DropEffect")
    return _metti([(CF_HDROP, dati), (effetto, struct.pack("<I", 1))])   # 1 = copia


def titolo_in_primo_piano() -> str:
    if not WINDOWS:
        return ""
    ctypes, _, u32 = _api()
    h = u32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(512)
    u32.GetWindowTextW(h, buf, 512)
    return buf.value or ""


def _ctrl_v() -> None:
    _, _, u32 = _api()
    VK_CONTROL, VK_V, SU = 0x11, 0x56, 0x0002
    u32.keybd_event(VK_CONTROL, 0, 0, 0)
    u32.keybd_event(VK_V, 0, 0, 0)
    u32.keybd_event(VK_V, 0, SU, 0)
    u32.keybd_event(VK_CONTROL, 0, SU, 0)


def incolla_in_whatsapp(attesa: float = 6.0, scadenza: float = 45.0,
                        esito: Optional[Callable[[str], None]] = None) -> bool:
    """In un thread: aspetta che in primo piano ci sia WhatsApp, lascia
    caricare la chat 'attesa' secondi, ricontrolla e preme Ctrl+V."""
    if not WINDOWS:
        return False

    def lavoro():
        fine = time.time() + scadenza
        while time.time() < fine:
            if "whatsapp" in titolo_in_primo_piano().lower():
                time.sleep(max(1.0, attesa))
                if "whatsapp" in titolo_in_primo_piano().lower():
                    _ctrl_v()
                    if esito:
                        esito("WhatsApp: allegato incollato nella chat, "
                              "manca solo Invio.")
                    return
            time.sleep(0.5)
        if esito:
            esito("WhatsApp non e' arrivato in primo piano: l'allegato e' "
                  "negli appunti, Ctrl+V nella chat.")

    threading.Thread(target=lavoro, name="incolla-whatsapp", daemon=True).start()
    return True


def apri(link: str) -> bool:
    """Apre un collegamento con il programma predefinito (sul PC della
    macchina): niente blocco dei popup, perche' non lo apre il browser."""
    if not WINDOWS:
        return False
    try:
        os.startfile(link)                              # noqa: S606
        return True
    except OSError:
        return False
