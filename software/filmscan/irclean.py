# -*- coding: utf-8 -*-
"""
Pulizia difetti tramite canale infrarosso.

I coloranti delle pellicole a colori sono in gran parte trasparenti all'IR, mentre
polvere, impronte e graffi non lo sono. Il canale IR risulta quasi uniforme e cio'
che vi appare scuro e' un difetto fisico, non contenuto dell'immagine.

DUE AVVERTENZE, entrambe importanti:

1) Non funziona sulle pellicole B/N tradizionali a base argento: sono opache
   all'infrarosso e l'intera immagine finirebbe nella maschera. Le B/N cromogeniche
   (sviluppo C-41) invece vanno bene. Da qui l'interruttore per stock.

2) La semplice divisione del canale visibile per l'IR lascia residui, perche'
   variazioni di contrasto, fuoco e apertura di illuminazione creano squilibri fra
   i due record. La strada robusta e' costruire una MASCHERA BINARIA e poi fare
   inpainting vero sui pixel mascherati.

Limite noto: l'inpainting funziona bene su aree omogenee (cielo, muri, sfondi
sfocati) e male su dettagli strutturati. Sui volti produce artefatti riconoscibili.
Da qui il controllo di intensita' e l'opzione di esportare la maschera a parte.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

try:
    from scipy import ndimage
    HAS_SCIPY = True
except ImportError:                                     # pragma: no cover
    HAS_SCIPY = False


def defect_mask(ir: np.ndarray, threshold: float = 0.82,
                dilate: int = 1, min_size: int = 2) -> np.ndarray:
    """Costruisce la maschera dei difetti dal canale IR.

    threshold : frazione della trasmittanza IR mediana sotto la quale un pixel
                e' considerato difettoso. Piu' basso = piu' conservativo.
    dilate    : allarga la maschera di qualche pixel, perche' i bordi di un graffio
                sfumano e lasciarli fuori produce aloni.
    min_size  : scarta le macchie troppo piccole, che di solito sono solo rumore.
    """
    ir = ir.astype(np.float32)

    # normalizza sulla trasmittanza tipica: rende la soglia indipendente
    # dal livello assoluto di illuminazione
    ref = float(np.percentile(ir, 90))
    if ref < 1e-6:
        return np.zeros(ir.shape, dtype=bool)
    norm = ir / ref

    mask = norm < threshold

    if HAS_SCIPY:
        if min_size > 1:
            labels, n = ndimage.label(mask)
            if n:
                sizes = np.bincount(labels.ravel())
                small = np.isin(labels, np.where(sizes < min_size)[0])
                mask = mask & ~small
        if dilate > 0:
            mask = ndimage.binary_dilation(mask, iterations=int(dilate))

    return mask


def inpaint(img: np.ndarray, mask: np.ndarray, iterations: int = 60,
            strength: float = 1.0, pad: int = 12) -> np.ndarray:
    """Riempie le zone mascherate per diffusione dai pixel validi circostanti.

    Metodo semplice ma stabile: si itera una media locale tenendo fissi i pixel
    buoni. Equivale a risolvere un'equazione di diffusione con condizioni al
    contorno note - il risultato e' liscio, che e' esattamente cio' che serve
    per polvere e graffi sottili.

    Il lavoro e' fatto SOLO sui riquadri che contengono difetti, non
    sull'immagine intera. Su una scansione a 4000 dpi la differenza e' fra
    iterare su 21 megapixel e iterare sull'uno per cento di essi: senza questo
    accorgimento, su Windows a 32 bit il tempo e la memoria diventano proibitivi.

    'strength' fonde il risultato con l'originale: 0 = nessuna correzione,
    1 = correzione piena.
    """
    if strength <= 0 or not mask.any():
        return img

    strength = float(np.clip(strength, 0.0, 1.0))
    out = img.astype(np.float32, copy=True)

    if not HAS_SCIPY:
        # senza scipy: riempimento grezzo con la media dei pixel validi
        valid = ~mask
        if not valid.any():
            return out
        if out.ndim == 2:
            out[mask] = float(out[valid].mean())
        else:
            for c in range(out.shape[2]):
                plane = out[:, :, c]
                plane[mask] = float(plane[valid].mean())
        return (img + (out - img) * strength).astype(np.float32)

    kernel = np.array([[0.0, 0.25, 0.0],
                       [0.25, 0.0, 0.25],
                       [0.0, 0.25, 0.0]], dtype=np.float32)

    # un riquadro per ogni gruppo di difetti contigui, con un margine che dia
    # alla diffusione pixel validi da cui attingere
    labels, n = ndimage.label(mask)
    if n == 0:
        return img
    h, w = mask.shape

    for sl in ndimage.find_objects(labels):
        if sl is None:
            continue
        y0 = max(0, sl[0].start - pad)
        y1 = min(h, sl[0].stop + pad)
        x0 = max(0, sl[1].start - pad)
        x1 = min(w, sl[1].stop + pad)

        sub_mask = mask[y0:y1, x0:x1]
        if not sub_mask.any():
            continue
        valid = ~sub_mask
        if not valid.any():
            continue

        # il numero di iterazioni si adatta alla dimensione del difetto: un
        # granello si chiude in poche passate, un graffio largo ne richiede di piu'
        span = max(sub_mask.shape)
        iters = int(np.clip(span, 8, iterations))

        planes = range(out.shape[2]) if out.ndim == 3 else [None]
        for c in planes:
            region = out[y0:y1, x0:x1] if c is None else out[y0:y1, x0:x1, c]
            work = region.astype(np.float32, copy=True)
            work[sub_mask] = float(work[valid].mean())
            fixed = region.copy()
            for _ in range(iters):
                work = np.where(sub_mask,
                                ndimage.convolve(work, kernel, mode="nearest"),
                                fixed)
            blended = fixed + (work - fixed) * strength
            if c is None:
                out[y0:y1, x0:x1] = blended
            else:
                out[y0:y1, x0:x1, c] = blended

    return out.astype(np.float32)


def clean(img: np.ndarray, ir: Optional[np.ndarray],
          strength: float = 1.0, threshold: float = 0.82,
          dilate: int = 1) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    """Punto d'ingresso: ritorna (immagine pulita, maschera).

    Se il canale IR non c'e' (pellicola B/N, preset senza IR) non fa nulla e lo
    dichiara ritornando None come maschera - meglio nessuna correzione che una
    correzione applicata su dati che non la sostengono.
    """
    if ir is None or strength <= 0:
        return img, None
    mask = defect_mask(ir, threshold=threshold, dilate=dilate)
    if not mask.any():
        return img, mask
    return inpaint(img, mask, strength=strength), mask


def mask_coverage(mask: Optional[np.ndarray]) -> float:
    """Percentuale di immagine coperta dalla maschera.

    Da mostrare in interfaccia: se supera qualche punto percentuale la soglia e'
    troppo aggressiva, oppure - piu' probabile - si sta usando l'IR su una
    pellicola B/N a base argento, dove non va usato affatto.
    """
    if mask is None:
        return 0.0
    return 100.0 * float(mask.mean())
