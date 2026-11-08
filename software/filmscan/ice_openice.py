# -*- coding: utf-8 -*-
"""
Il Digital ICE con openICE: il programma in C che riproduce bit per bit il
Digital ICE di Nikon Scan 4 (software/terzi/openice, GPL-3.0).

Gira in un processo a parte, come la libreria Kodak (ice_kodak.py): gli si
da' un TIFF a 16 bit R, G, B, IR e restituisce un DNG con R, G, B puliti
(nella sotto-immagine: la prima e' la copia del TIFF d'ingresso).

A differenza della libreria Kodak puo' girare anche sugli elaboratori:
c'e' il binario per Windows a 32 bit (la macchina, i PC Windows) e quello
per Linux (Unraid). La macchina li mette nel codice che gli elaboratori
scaricano (remoti.codice_zip).

Il livello dell'infrarosso: le costanti di Nikon presuppongono la
pellicola chiara vicino al fondo scala. Il nostro IR e' diviso per la
finestra vuota: lo si porta con la pellicola chiara (percentile 90) al 98%.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from typing import Dict, Optional, Tuple

import numpy as np

M = 65535.0
LIVELLO_IR = 0.98
TETTO_CAMBIATI = 0.15           # come ice_kodak: oltre, il risultato si scarta


def binario() -> Optional[str]:
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "terzi", "openice", "bin")
    if os.name == "nt":
        nome = "openice-win32.exe"
    elif sys.platform.startswith("linux") and os.uname().machine in ("x86_64", "amd64"):
        nome = "openice-linux-x86_64"
    else:
        return None
    p = os.path.join(base, nome)
    if not os.path.exists(p):
        return None
    if os.name != "nt" and not os.access(p, os.X_OK):
        # dallo zip del codice (elaboratori) arriva senza il permesso
        try:
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR | stat.S_IXGRP)
        except OSError:
            return None
    return p


def disponibile() -> bool:
    return binario() is not None


def pulisci_piani(r, g, b, ir, dpi: int, fine: bool = False
                  ) -> Tuple[Tuple[np.ndarray, np.ndarray, np.ndarray], Dict[str, object]]:
    import tifffile
    exe = binario()
    if exe is None:
        raise RuntimeError("openICE non c'e' per questo sistema")
    ir = np.nan_to_num(np.asarray(ir, dtype=np.float32))
    chiaro = float(np.percentile(ir[::4, ::4], 90))
    scala_ir = LIVELLO_IR * M / max(chiaro, 1e-4)
    dentro = np.stack([np.clip(np.asarray(p, dtype=np.float32), 0, 1) * M for p in (r, g, b)]
                      + [np.clip(ir * scala_ir, 0, M)], axis=-1)
    dentro = np.ascontiguousarray(np.rint(dentro).astype(np.uint16))
    with tempfile.TemporaryDirectory(prefix="openice-") as tmp:
        fin, fout = os.path.join(tmp, "in.tif"), os.path.join(tmp, "out.dng")
        tifffile.imwrite(fin, dentro, photometric="rgb", extrasamples=["unspecified"],
                         planarconfig="contig", resolution=(int(dpi), int(dpi)),
                         resolutionunit="INCH")
        comando = [exe, fin, "-o", fout, "-dpi", str(int(dpi))] + (["-fine"] if fine else [])
        try:
            p = subprocess.run(comando, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=180,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise RuntimeError("openICE: nessuna risposta in 180 s")
        if p.returncode != 0 or not os.path.exists(fout):
            raise RuntimeError("openICE non riuscito (uscita %s): %s" % (
                p.returncode, (p.stderr or p.stdout).decode("utf-8", "replace")[-300:].strip()))
        with tifffile.TiffFile(fout) as t:
            sotto = t.pages[0].pages if hasattr(t.pages[0], "pages") else []
            fuori = sotto[0].asarray() if len(sotto) else t.pages[0].asarray()[..., :3]
        testo = p.stdout.decode("utf-8", "replace")
    fuori = fuori.astype(np.float32)
    cambiati = float((np.abs(fuori - dentro[..., :3].astype(np.float32)) > 0.002 * M)
                     .any(axis=-1).mean())
    esito: Dict[str, object] = {"motore": "openice", "cambiati_pct": round(cambiati * 100, 3),
                                "scala_ir": round(scala_ir, 1)}
    for riga in testo.splitlines():
        if "installed" in riga:
            esito["calibrazione"] = riga.split(":", 1)[-1].strip()
    if cambiati > TETTO_CAMBIATI:
        raise RuntimeError("openICE scartato: ha cambiato il %.0f%% dell'immagine" % (cambiati * 100))
    return tuple((fuori[..., k] / M).astype(np.float32) for k in range(3)), esito


def pulisci(raw, dpi: Optional[int] = None):
    """Un nuovo RawScan con R, G, B puliti e senza IR, e il resoconto."""
    from .acquire import RawScan
    from .irclean import ir_senza_immagine
    if not all(c in raw.planes for c in ("R", "G", "B", "IR")):
        raise RuntimeError("servono R, G, B e IR")
    if ir_senza_immagine(raw.planes["IR"][0]):
        raise RuntimeError("canale IR saturo, senza immagine")
    piani = [raw.planes[c][0] for c in ("R", "G", "B", "IR")]
    (r, g, b), esito = pulisci_piani(*piani, dpi=int(dpi or raw.meta.dpi or 1569))
    nuovo = RawScan(raw.meta)
    for c, p in zip(("R", "G", "B"), (r, g, b)):
        nuovo.add(c, p)
        for extra in raw.planes[c][1:]:
            nuovo.add(c, extra)
    return nuovo, esito
