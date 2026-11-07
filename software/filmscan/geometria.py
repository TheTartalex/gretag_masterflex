# -*- coding: utf-8 -*-
"""
La geometria dei fotogrammi, salvata: un file per rullo accanto ai grezzi.

    <raw>/rullo_<id>_geometria.json   {"<numero>": {...}, ...}

Per ogni fotogramma ci sono gli spostamenti dei canali trovati
dall'allineamento, l'inclinazione del raddrizzamento, il riquadro del
ritaglio, l'orientamento, e le scelte a mano dell'operatore (canali spostati,
rotazione). Il contenuto lo decide pipeline.process (ProcessResult.geometria)
e lo rilegge (ProcessOptions.geometria).

Perche': riaprire un fotogramma rifaceva da capo le stime, che sono la parte
cara dell'elaborazione (correlazione di fase sui canali, 41 rotazioni di
prova per l'inclinazione, ricerca dei bordi). La geometria di un grezzo non
cambia: si trova una volta e si riusa, finche' l'operatore non la cambia a
mano o chiede di rifarla (Alessandro, 7/11/2026).

Il file e' separato dalle statistiche del rullo (rullo_<id>_statistiche.json):
quelle si riscrivono a fine rullo, questa fotogramma per fotogramma, anche da
piu' thread (elaborazione in casa e sugli elaboratori della rete).
"""

from __future__ import annotations

import io
import json
import os
import threading
from typing import Dict, Optional

_lock = threading.Lock()


def percorso(cartella_raw: str, rullo_id: int) -> str:
    return os.path.join(cartella_raw, "rullo_%d_geometria.json" % int(rullo_id))


def _leggi(p: str) -> Dict[str, dict]:
    try:
        with io.open(p, encoding="utf-8") as f:
            dati = json.load(f)
        return dati if isinstance(dati, dict) else {}
    except (IOError, OSError, ValueError):
        return {}


def leggi(cartella_raw: str, rullo_id: int, numero: int) -> Optional[dict]:
    with _lock:
        g = _leggi(percorso(cartella_raw, rullo_id)).get(str(int(numero)))
    return dict(g) if g else None


def scrivi(cartella_raw: str, rullo_id: int, numero: int,
           geometria: Optional[dict]) -> None:
    """Salva (None: toglie) la geometria di un fotogramma. Un errore di
    disco non deve far perdere il fotogramma: si ignora, la prossima volta
    si rifanno le stime."""
    p = percorso(cartella_raw, rullo_id)
    with _lock:
        dati = _leggi(p)
        if geometria:
            dati[str(int(numero))] = geometria
        else:
            dati.pop(str(int(numero)), None)
        try:
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            with io.open(p + ".tmp", "w", encoding="utf-8") as f:
                json.dump(dati, f, indent=0)
            os.replace(p + ".tmp", p)
        except (IOError, OSError):
            pass


def dalla_vista(dy: float, dx: float, rotazione: int, specchio: bool):
    """Uno spostamento visto sullo schermo (immagine gia' girata e
    specchiata) riportato nelle coordinate del grezzo, dove lo applica
    l'allineamento. L'orientamento (orient.apply_orientation) e': rot90
    antiorario 'rotazione/90' volte, poi lo specchio orizzontale; qui si
    disfa al contrario. Il raddrizzamento (meno di 2 gradi) si trascura."""
    if specchio:
        dx = -dx
    for _ in range((int(rotazione) // 90) % 4):
        # np.rot90: vista[i, j] = grezzo[j, W-1-i]: uno spostamento (dy, dx)
        # nella vista e' (dx, -dy) prima della rotazione
        dy, dx = dx, -dy
    return dy, dx


def nella_vista(dy: float, dx: float, rotazione: int, specchio: bool):
    """Il contrario di dalla_vista: uno spostamento nelle coordinate del
    grezzo come lo si vede sullo schermo."""
    for _ in range((int(rotazione) // 90) % 4):
        dy, dx = -dx, dy
    if specchio:
        dx = -dx
    return dy, dx
