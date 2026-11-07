# -*- coding: utf-8 -*-
"""
Il Digital ICE della Kodak sui grezzi, sul PC della macchina.

La libreria (DMLDICELib.dll) e' dell'SDK: c'e' solo sulla macchina, e da li'
non esce (non si manda agli elaboratori della rete e non va nel repository).
Si usa nell'elaborazione, non nella lettura: la pellicola non aspetta. Gli
elaboratori della rete ricevono il grezzo gia' pulito (service._esporta).

Gira in un processo a parte (ponte/dice.py): se la libreria cade, cade
quello, e si ripiega sul Digital ICE nostro (irclean, dal canale IR).

Il controllo dopo: Digital ICE corregge polvere e graffi, cioe' pochi punti
dell'immagine. Se cambia sensibilmente piu' del 15% dei pixel visibili, il
risultato non e' quello atteso (formato del buffer sbagliato, scala...): lo
si scarta e si usa il nostro. Cosi' si puo' tenere acceso di serie senza
rischiare un rullo rovinato.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from typing import Dict, Optional, Tuple

import numpy as np

_registro = logging.getLogger("filmscan.ice")

CARTELLE = (r"C:\MasterflexD\Scanner\Bin", r"C:\MasterflexD\Scanner")
SCALA = 16383.0             # 14 bit, come chiede la libreria (statica: 14)
TETTO_CAMBIATI = 0.15       # oltre, il risultato si scarta
_stato: Dict[str, object] = {}


def cartella_dll() -> Optional[str]:
    if os.name != "nt":
        return None
    for c in ([os.environ["MASTERFLEX_DICE"]] if os.environ.get("MASTERFLEX_DICE") else []) + list(CARTELLE):
        if os.path.exists(os.path.join(c, "DMLDICELib.dll")):
            return c
    return None


def disponibile() -> bool:
    return cartella_dll() is not None and not _stato.get("rotto")


def pulisci(raw) -> Tuple[object, Dict[str, object]]:
    """Un nuovo RawScan con R, G, B puliti dal Digital ICE della Kodak e
    senza IR (la pulizia e' fatta), e cosa e' successo. RuntimeError se non
    si e' potuto: chi chiama tiene il grezzo com'era (Digital ICE nostro)."""
    from .acquire import RawScan
    cartella = cartella_dll()
    if cartella is None:
        raise RuntimeError("DMLDICELib.dll non c'e' (solo sulla macchina)")
    if not all(c in raw.planes for c in ("R", "G", "B", "IR")):
        raise RuntimeError("servono R, G, B e IR")
    piani = [np.asarray(raw.planes[c][0], dtype=np.float32) for c in ("R", "G", "B", "IR")]
    h, w = piani[0].shape
    dentro = np.stack([np.clip(p, 0.0, 1.0) * SCALA for p in piani], axis=-1)
    dentro = np.ascontiguousarray(np.rint(dentro).astype("<u2"))
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "ponte", "dice.py")
    with tempfile.TemporaryDirectory(prefix="dice-") as tmp:
        fin, fout = os.path.join(tmp, "in.raw"), os.path.join(tmp, "out.raw")
        dentro.tofile(fin)
        try:
            p = subprocess.run([sys.executable, script, cartella, fin, fout, str(w), str(h)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise RuntimeError("Digital ICE Kodak: nessuna risposta in 90 s")
        if p.returncode != 0:
            if p.returncode < 0 or p.returncode > 255:
                # la libreria e' caduta (access violation): non la si riprova
                # a ogni fotogramma, fino al prossimo avvio
                _stato["rotto"] = True
            raise RuntimeError("Digital ICE Kodak non riuscito (uscita %s): %s" % (
                p.returncode, p.stderr.decode("utf-8", "replace")[-300:].strip()))
        esito = json.loads(p.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
        fuori = np.fromfile(fout, dtype="<u2").reshape(h, w, 4)
    rgb_prima = dentro[..., :3].astype(np.int32)
    rgb_dopo = fuori[..., :3].astype(np.int32)
    cambiati = float((np.abs(rgb_dopo - rgb_prima) > SCALA * 0.02).any(axis=-1).mean())
    esito["cambiati_pct"] = round(cambiati * 100, 2)
    if cambiati > TETTO_CAMBIATI:
        raise RuntimeError("Digital ICE Kodak scartato: ha cambiato il %.0f%% dell'immagine "
                           "(atteso: solo polvere e graffi)" % (cambiati * 100))
    nuovo = RawScan(raw.meta)
    for k, c in enumerate(("R", "G", "B")):
        nuovo.add(c, (fuori[..., k].astype(np.float32) / SCALA))
        for extra in raw.planes[c][1:]:
            nuovo.add(c, extra)
    return nuovo, esito
