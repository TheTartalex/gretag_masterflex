# -*- coding: utf-8 -*-
"""
Allineamento dei canali.

La macchina fa quattro passaggi separati muovendo il sensore. I quattro canali
non saranno MAI perfettamente sovrapposti: backlash del motore, slittamento
della pellicola, respiro della lente col fuoco.

Se si combinano senza registrarli si ottengono frange colorate sui bordi e, cosa
peggiore, una maschera IR che non coincide con i difetti veri. Va risolto qui,
prima di qualunque altra elaborazione, perche' tutto il resto ci si appoggia sopra.

Metodo: correlazione di fase con raffinamento sub-pixel. Il canale verde fa da
riferimento perche' e' quello con piu' contenuto e meno rumore nei negativi.
"""

from __future__ import annotations

from typing import Dict, Tuple, Optional

import numpy as np

try:
    from scipy import ndimage
    HAS_SCIPY = True
except ImportError:                                     # pragma: no cover
    HAS_SCIPY = False


def _window(shape: Tuple[int, int]) -> np.ndarray:
    """Finestra di Hann 2D. Senza, i bordi dell'immagine dominano la correlazione."""
    wy = np.hanning(shape[0])[:, None]
    wx = np.hanning(shape[1])[None, :]
    return wy * wx


def _center_tile(img: np.ndarray, size: int) -> np.ndarray:
    """Ritaglio centrale, per stimare lo spostamento senza toccare tutto il frame.

    A 4000 dpi un fotogramma e' 5669x3779: la sua FFT complessa occupa 343 MB, e
    la correlazione ne richiede piu' di una alla volta. Su Windows a 32 bit,
    dove il processo ha circa 1,8 GB, questo basta a far fallire tutto.

    Uno spostamento rigido e' lo stesso ovunque nell'immagine, quindi stimarlo su
    un riquadro centrale non perde nulla: e' semplicemente il modo corretto di
    farlo. Il riquadro deve solo essere abbastanza grande da contenere struttura.
    """
    h, w = img.shape
    if h <= size and w <= size:
        return img
    s = min(size, h, w)
    y0 = (h - s) // 2
    x0 = (w - s) // 2
    return img[y0:y0 + s, x0:x0 + s]


def phase_shift(ref: np.ndarray, mov: np.ndarray, upsample: int = 20,
                return_confidence: bool = False, tile: int = 1024):
    """Stima lo spostamento (dy, dx) di 'mov' rispetto a 'ref', in pixel.

    upsample controlla la finezza sub-pixel: 20 significa risoluzione 1/20 di pixel,
    piu' che sufficiente per il nostro scopo.
    """
    if ref.shape != mov.shape:
        raise ValueError("shape diverse: %s vs %s" % (ref.shape, mov.shape))

    # si lavora sul riquadro centrale: vedi _center_tile per il perche'
    ref = _center_tile(ref, tile)
    mov = _center_tile(mov, tile)

    w = _window(ref.shape)
    # complex64 invece del complex128 di numpy: meta' memoria, precisione
    # abbondante per stimare uno spostamento di pochi pixel
    a = np.fft.fft2((ref - ref.mean()) * w).astype(np.complex64)
    b = np.fft.fft2((mov - mov.mean()) * w).astype(np.complex64)

    cross = a * np.conj(b)
    del a, b
    mag = np.abs(cross)
    cross = np.divide(cross, mag, out=np.zeros_like(cross), where=mag > 1e-12)
    corr = np.abs(np.fft.ifft2(cross))

    peak = np.unravel_index(np.argmax(corr), corr.shape)
    dy, dx = float(peak[0]), float(peak[1])

    # Affidabilita': quanto il picco emerge dal fondo. Un canale privo di
    # struttura - tipicamente l'infrarosso, che e' quasi uniforme per costruzione -
    # produce un picco piatto, e in quel caso la stima non va usata.
    peak_val = float(corr[peak])
    background = float(np.median(corr))
    confidence = 0.0 if peak_val <= 1e-12 else \
        float(np.clip(1.0 - background / peak_val, 0.0, 1.0))

    # riporta nell'intervallo [-N/2, N/2)
    if dy > ref.shape[0] / 2:
        dy -= ref.shape[0]
    if dx > ref.shape[1] / 2:
        dx -= ref.shape[1]

    if upsample <= 1:
        return (dy, dx, confidence) if return_confidence else (dy, dx)

    # Raffinamento sub-pixel con DFT sovracampionata localmente attorno al picco.
    # NON si usa l'interpolazione parabolica: il picco della correlazione di fase
    # e' quasi una delta, quindi i campioni vicini valgono tutti circa zero e la
    # parabola restituisce sistematicamente 0. E' un errore facile da fare e
    # produce un allineamento apparentemente perfetto che in realta' non c'e'.
    dy, dx = _refine_upsampled(cross, dy, dx, upsample)
    return (dy, dx, confidence) if return_confidence else (dy, dx)


def _refine_upsampled(cross: np.ndarray, dy: float, dx: float,
                      upsample: int, radius: float = 1.5):
    """Valuta la correlazione su una griglia fine attorno alla stima grossolana,
    calcolando la DFT inversa solo dove serve (metodo di Guizar-Sicairos)."""
    ny, nx = cross.shape
    step = 1.0 / upsample
    n_side = int(np.ceil(2 * radius * upsample)) + 1

    oy = dy - radius
    ox = dx - radius
    ys = oy + np.arange(n_side) * step
    xs = ox + np.arange(n_side) * step

    fy = np.fft.fftfreq(ny)[:, None]
    fx = np.fft.fftfreq(nx)[:, None]

    ky = np.exp(2j * np.pi * ys[None, :] * fy)      # (ny, n_side)
    kx = np.exp(2j * np.pi * xs[None, :] * fx)      # (nx, n_side)

    # stesso segno della ifft2 usata per la stima grossolana: esponente positivo
    local = np.abs(ky.T @ cross @ kx)
    iy, ix = np.unravel_index(np.argmax(local), local.shape)
    return float(ys[iy]), float(xs[ix])


def shift_image(img: np.ndarray, dy: float, dx: float) -> np.ndarray:
    """Trasla un'immagine di una quantita' frazionaria.

    Con scipy si usa l'interpolazione spline cubica: la qualita' e'
    praticamente indistinguibile da quella di Fourier per spostamenti di pochi
    pixel, ma lavora sul posto invece di allocare due array complessi grandi
    quanto l'immagine. Su Windows a 32 bit e' la differenza fra funzionare e
    andare in MemoryError.

    Senza scipy si ripiega su Fourier, accettabile solo su immagini piccole.
    """
    if abs(dy) < 1e-6 and abs(dx) < 1e-6:
        return img.astype(np.float32, copy=False)

    if HAS_SCIPY:
        return ndimage.shift(img.astype(np.float32, copy=False), (dy, dx),
                             order=3, mode="nearest").astype(np.float32)

    ny, nx = img.shape
    fy = np.fft.fftfreq(ny)[:, None]
    fx = np.fft.fftfreq(nx)[None, :]
    phase = np.exp(-2j * np.pi * (fy * dy + fx * dx))
    return np.real(np.fft.ifft2(np.fft.fft2(img) * phase)).astype(np.float32)


def _prep(img: np.ndarray, tile: int = 1024) -> np.ndarray:
    """Prepara un canale per il confronto con un altro.

    Canali diversi NON differiscono solo per una traslazione: hanno base e gamma
    differenti, perche' i tre strati di coloranti rispondono diversamente. Se si
    correlano cosi' come sono, la stima e' dominata da queste differenze e
    restituisce spostamenti inventati.

    Rimedio: passare in densita' (il logaritmo linearizza il gamma in un semplice
    fattore di scala), togliere la componente lenta e normalizzare la varianza.
    Quel che resta e' la struttura dell'immagine, uguale in tutti i canali.
    """
    d = -np.log10(np.clip(_center_tile(img, tile).astype(np.float32), 1e-5, None))
    if HAS_SCIPY:
        d = d - ndimage.gaussian_filter(d, 12.0)      # passa-alto
    else:
        d = d - d.mean()
    s = float(d.std())
    return d / s if s > 1e-9 else d


def register_channels(planes: Dict[str, np.ndarray],
                      reference: str = "G",
                      max_shift_px: float = 8.0,
                      min_confidence: float = 0.25
                      ) -> Tuple[Dict[str, np.ndarray], Dict[str, Tuple[float, float]]]:
    """Allinea tutti i canali sul riferimento.

    Ritorna i piani allineati e gli spostamenti applicati. Gli spostamenti vanno
    guardati: se superano regolarmente un paio di pixel c'e' un problema
    meccanico da sistemare, non da compensare in software.

    Caso particolare del canale IR: essendo quasi privo di contenuto d'immagine -
    e' proprio questa la ragione per cui serve a trovare i difetti - la
    correlazione con i canali visibili puo' non agganciarsi. Quando l'affidabilita'
    e' troppo bassa si applica lo spostamento medio dei canali visibili, che e'
    una stima ragionevole perche' il disallineamento nasce dalla meccanica, la
    stessa per tutti i passaggi.
    """
    if reference not in planes:
        # con un solo canale visibile (bianco e nero) non c'e' niente da
        # allineare fra i canali: il riferimento e' l'unico che c'e'
        visibili = [c for c in planes if c != "IR"]
        reference = visibili[0] if visibili else sorted(planes)[0]

    ref = planes[reference]
    ref_prep = _prep(ref)
    aligned: Dict[str, np.ndarray] = {reference: ref}
    shifts: Dict[str, Tuple[float, float]] = {reference: (0.0, 0.0)}

    estimates: Dict[str, Tuple[float, float]] = {}
    weak: list = []

    for ch, img in planes.items():
        if ch == reference:
            continue
        dy, dx, conf = phase_shift(ref_prep, _prep(img), return_confidence=True)
        if abs(dy) > max_shift_px or abs(dx) > max_shift_px:
            conf = 0.0                     # stima implausibile
        if conf < min_confidence:
            weak.append(ch)
        else:
            estimates[ch] = (dy, dx)

    # ripiego per i canali senza aggancio affidabile
    if weak:
        if estimates:
            mdy = float(np.mean([v[0] for v in estimates.values()]))
            mdx = float(np.mean([v[1] for v in estimates.values()]))
        else:
            mdy = mdx = 0.0
        for ch in weak:
            estimates[ch] = (mdy, mdx)

    for ch, (dy, dx) in estimates.items():
        aligned[ch] = shift_image(planes[ch], dy, dx) if (dy or dx) else planes[ch]
        shifts[ch] = (dy, dx)

    return aligned, shifts


def merge_exposures(planes: list, exposures: list,
                    clip_high: float = 0.95, clip_low: float = 0.002) -> np.ndarray:
    """Fonde piu' esposizioni dello stesso canale.

    Nei negativi le zone dense (le luci della scena originale) hanno pochissima
    trasmittanza: l'esposizione lunga le recupera ma satura altrove. Si pesa ogni
    lettura in base a quanto e' lontana dai due estremi.
    """
    if len(planes) == 1:
        return planes[0]

    num = np.zeros_like(planes[0], dtype=np.float64)
    den = np.zeros_like(planes[0], dtype=np.float64)

    for p, e in zip(planes, exposures):
        p = p.astype(np.float64)
        good = (p < clip_high) & (p > clip_low)
        # peso a campana: massimo a meta' scala, nullo agli estremi
        w = np.where(good, 1.0 - (2.0 * np.clip(p, 0, 1) - 1.0) ** 2, 0.0)
        num += w * (p / e)
        den += w

    out = np.divide(num, den, out=np.zeros_like(num), where=den > 1e-9)
    # dove nessuna esposizione era utilizzabile, ripiega sulla piu' corta
    fallback = planes[int(np.argmin(exposures))].astype(np.float64) / min(exposures)
    out = np.where(den > 1e-9, out, fallback)
    return out.astype(np.float32)
