# -*- coding: utf-8 -*-
"""
Confronto dei motori Digital ICE sullo stesso fotogramma, per scegliere a
occhio (8/11/2026): senza pulizia, semplice, aperto, openICE, filmscan e,
sulla macchina, quello della Kodak.

    python -m filmscan.confronto_ice <grezzo.npz> [cartella]

Il grezzo e' uno di quelli salvati dal rullo (con IR) o quello del
collaudo IR (fotogramma_rgbi.npz). Nella cartella: un PNG con i ritagli
affiancati (positivo approssimato) per le due zone con piu' difetti e il
centro, e confronto.json con tempi e pixel cambiati per motore.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

MOTORI = ("nessuno", "semplice", "aperto", "openice", "filmscan", "kodak")
LATO = 300


def _positivo(piani: List[np.ndarray], rif: List[np.ndarray], y0: int, x0: int) -> np.ndarray:
    """Un positivo grezzo per guardare: densita' per canale fra i percentili
    del fotogramma senza pulizia (stessa scala per tutti i motori)."""
    out = []
    for p, r in zip(piani, rif):
        d = -np.log10(np.clip(p, 1e-4, None))
        dr = -np.log10(np.clip(r[::4, ::4], 1e-4, None))
        lo, hi = np.percentile(dr, [1, 99.5])
        out.append(np.clip((d[y0:y0 + LATO, x0:x0 + LATO] - lo) / max(hi - lo, 1e-6), 0, 1) ** 0.8)
    return (np.stack(out, -1) * 255).astype(np.uint8)


def _zone(ir: np.ndarray) -> List[Tuple[int, int]]:
    """Dove guardare: le due zone con piu' difetti nell'IR, e il centro."""
    from scipy import ndimage
    h, w = ir.shape
    scuro = (ir < 0.8 * np.percentile(ir[::4, ::4], 90)).astype(np.float32)
    dens = ndimage.uniform_filter(scuro, LATO // 2)
    zone = []
    for _ in range(2):
        y, x = np.unravel_index(int(np.argmax(dens)), dens.shape)
        y0 = int(min(max(0, y - LATO // 2), h - LATO))
        x0 = int(min(max(0, x - LATO // 2), w - LATO))
        zone.append((y0, x0))
        dens[max(0, y0 - LATO):y0 + 2 * LATO, max(0, x0 - LATO):x0 + 2 * LATO] = 0
    zone.append(((h - LATO) // 2, (w - LATO) // 2))
    return zone


def confronta(r, g, b, ir, dpi: int, cartella: str,
              motori=MOTORI) -> Dict[str, object]:
    """R, G, B, IR in trasmittanza (0..1, allineati). Scrive i PNG e
    restituisce, per motore, tempo, pixel cambiati o l'errore."""
    from PIL import Image
    from . import ice_aperto, ice_openice, irclean
    os.makedirs(cartella, exist_ok=True)
    rgb = [np.asarray(p, dtype=np.float32) for p in (r, g, b)]
    ir = np.asarray(ir, dtype=np.float32)
    esiti: Dict[str, object] = {}
    risultati: Dict[str, List[np.ndarray]] = {}
    for nome in motori:
        t0 = time.time()
        try:
            if nome == "nessuno":
                fuori, es = rgb, {}
            elif nome == "semplice":
                m = irclean.defect_mask(ir)
                img = irclean.inpaint(np.stack(rgb, -1), m, strength=1.0)
                fuori, es = [img[..., k] for k in range(3)], {"maschera_pct": round(float(m.mean()) * 100, 3)}
            elif nome in ("aperto", "filmscan"):
                f, es = ice_aperto.pulisci_piani(*rgb, ir, dpi=dpi, migliorie=(nome == "filmscan"))
                fuori = list(f)
            elif nome == "openice":
                f, es = ice_openice.pulisci_piani(*rgb, ir, dpi=dpi)
                fuori = list(f)
            elif nome == "kodak":
                from . import ice_kodak
                from .acquire import RawScan, ScanMetadata
                if not ice_kodak.disponibile():
                    raise RuntimeError("libreria Kodak non disponibile qui")
                raw = RawScan(ScanMetadata(time.time(), "135-full", "confronto", int(dpi), 16,
                                           ["R", "G", "B", "IR"], [1.0], 1, 0, 0.0, 0.0))
                for c, p in zip(("R", "G", "B", "IR"), rgb + [ir]):
                    raw.add(c, p)
                nuovo, es = ice_kodak.pulisci(raw)
                fuori = [nuovo.planes[c][0] for c in ("R", "G", "B")]
            else:
                continue
            risultati[nome] = fuori
            es = dict(es or {})
            es["secondi"] = round(time.time() - t0, 2)
            esiti[nome] = es
        except Exception as exc:                        # noqa: BLE001
            esiti[nome] = {"errore": str(exc)}
    h, w = ir.shape
    if h >= LATO and w >= LATO:
        nomi = [n for n in motori if n in risultati]
        righe = []
        for (y0, x0) in _zone(ir):
            tess = [np.pad(_positivo(risultati[n], rgb, y0, x0), ((22, 4), (4, 4), (0, 0)),
                           constant_values=40) for n in nomi]
            righe.append(np.concatenate(tess, 1))
        tavola = np.concatenate(righe, 0)
        im = Image.fromarray(tavola)
        try:
            from PIL import ImageDraw
            dr = ImageDraw.Draw(im)
            for k, n in enumerate(nomi):
                dr.text((k * (LATO + 8) + 8, 4), n, fill=(230, 230, 230))
        except Exception:                               # noqa: BLE001
            pass
        im.save(os.path.join(cartella, "confronto_ice.png"))
    with open(os.path.join(cartella, "confronto.json"), "w", encoding="utf-8") as f:
        json.dump(esiti, f, indent=1, default=float)
    return esiti


def da_grezzo(percorso: str) -> Tuple[List[np.ndarray], int]:
    """R, G, B, IR e dpi da un grezzo .npz: quelli del rullo (RawScan, con
    il .json accanto) o quello del collaudo IR (R, G, B, IR, dpi)."""
    z = np.load(percorso, allow_pickle=False)
    if all(k in z.files for k in ("R", "G", "B", "IR")):
        dpi = int(z["dpi"]) if "dpi" in z.files else 1569
        piani = [z[k].astype(np.float32) for k in ("R", "G", "B", "IR")]
        return [p / (4095.0 if p.max() > 2.0 else 1.0) for p in piani], dpi
    from .acquire import RawScan
    raw = RawScan.load(percorso)
    if "IR" not in raw.planes:
        raise RuntimeError("questo grezzo non ha l'infrarosso")
    from . import registration
    piani = dict((c, raw.planes[c][0]) for c in ("R", "G", "B", "IR"))
    allineati, _ = registration.register_channels(piani)
    return [allineati[c] for c in ("R", "G", "B", "IR")], int(raw.meta.dpi or 1569)


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    piani, dpi = da_grezzo(argv[0])
    cartella = argv[1] if len(argv) > 1 else os.path.splitext(argv[0])[0] + "_confronto_ice"
    esiti = confronta(*piani, dpi=dpi, cartella=cartella)
    for n, e in esiti.items():
        print("%-9s %s" % (n, e))
    print("immagini in %s" % cartella)
    return 0


if __name__ == "__main__":
    sys.exit(main())
