# -*- coding: utf-8 -*-
"""
Un grezzo (.npz) in TIFF a 16 bit, da aprire con qualsiasi programma.

    python grezzo_in_tiff.py rullo_001.npz [altri.npz ...]

Accanto a ogni .npz scrive:
    <nome>_negativo.tif   R, G, B lineari a 16 bit, il negativo com'e' uscito
                          dallo scanner dopo nero e bianco, canali allineati,
                          nessuna inversione: Photoshop lo apre direttamente
    <nome>_negativo.dng   lo stesso in DNG: Photoshop lo apre in Camera Raw,
                          e lo leggono Lightroom e Negative Lab Pro
    <nome>_ir.tif         il canale infrarosso, se c'e' (grigio, 16 bit)
    <nome>_bn.tif         per i grezzi a un canale solo (bianco e nero)

Sono gli stessi file che l'interfaccia scrive da sola a ogni fotogramma con
Macchina > "Negativo lineare per altri programmi" (pipeline.export_negativo):
questo serve per i rulli scansionati prima di accenderla.

I grezzi tengono la trasmittanza 0..1 dopo la calibrazione (acquire.py,
apply_calibration): 1 = bianco della lampada. Con piu' esposizioni per
canale si prende la prima. Serve a guardare la pellicola fuori
dall'interfaccia, o a convertirla con altri programmi; per rielaborare si
usa Archivio > Riapri.

Lo usa "GREZZO IN TIFF.bat" (si trascinano i file sopra).
"""

from __future__ import annotations

import os
import sys

import numpy as np


def _a16(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, dtype=np.float32)
    scala = 65535.0 if float(np.nanmax(p)) <= 1.5 else 65535.0 / 4095.0
    return np.clip(np.nan_to_num(p) * scala, 0, 65535).astype(np.uint16)


def converti(percorso: str) -> list:
    import tifffile
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    z = np.load(percorso, allow_pickle=False)
    canali = {}
    for chiave in sorted(z.files):
        canale, _, indice = chiave.rpartition("_")
        if canale and indice == "0":
            canali[canale] = z[chiave].astype(np.float32)
    base = percorso[:-4]
    scritti = []
    rgb = [c for c in ("R", "G", "B") if c in canali]
    if len(rgb) == 3:
        # come lo scarico dall'archivio (consegne.negativo_da_grezzo)
        from filmscan.consegne import negativo_da_grezzo
        cartella = os.path.dirname(os.path.abspath(percorso))
        for fmt in ("negativo_tiff", "negativo_dng"):
            scritti.append(negativo_da_grezzo(percorso, fmt, cartella))
    else:
        for c in canali:
            if c != "IR":
                tifffile.imwrite(base + "_bn.tif", _a16(canali[c]), photometric="minisblack")
                scritti.append(base + "_bn.tif")
                break
    if "IR" in canali:
        tifffile.imwrite(base + "_ir.tif", _a16(canali["IR"]), photometric="minisblack")
        scritti.append(base + "_ir.tif")
    return scritti


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 1
    errori = 0
    for p in argv:
        if not p.lower().endswith(".npz"):
            print("salto %s: non e' un grezzo .npz" % p)
            continue
        try:
            for s in converti(p):
                print("scritto %s" % s)
        except Exception as exc:                        # noqa: BLE001
            errori += 1
            print("ERRORE su %s: %s" % (p, exc))
    return 1 if errori else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
