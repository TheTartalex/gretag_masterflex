# -*- coding: utf-8 -*-
"""
Individuazione del fotogramma, raddrizzamento, ritaglio, centratura.

Tre problemi distinti, spesso confusi tra loro:

  1. DOVE FINISCE LA PELLICOLA - trovare i bordi laterali della striscia
  2. DOVE INIZIA IL FOTOGRAMMA - trovare gli stacchi tra un frame e il successivo
  3. DOVE STA IL SOGGETTO      - per un eventuale ritaglio piu' stretto

Il primo e il secondo sono geometria e vanno risolti con precisione: sono loro a
guidare l'avanzamento automatico. Il terzo e' una scelta estetica e va sempre
lasciato opzionale e reversibile: un ritaglio automatico sbagliato butta via
informazione che non si recupera.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

try:
    from scipy import ndimage
    HAS_SCIPY = True
except ImportError:                                     # pragma: no cover
    HAS_SCIPY = False


@dataclass
class FrameBox:
    y0: int
    x0: int
    y1: int
    x1: int
    angle: float = 0.0          # inclinazione stimata, in gradi
    confidence: float = 0.0     # 0..1

    @property
    def w(self) -> int:
        return self.x1 - self.x0

    @property
    def h(self) -> int:
        return self.y1 - self.y0

    def as_tuple(self):
        return (self.y0, self.x0, self.y1, self.x1)


# --------------------------------------------------------------------------
# 1. Bordi della striscia
# --------------------------------------------------------------------------

def find_strip_edges(ir_or_density: np.ndarray,
                     margin_frac: float = 0.02) -> Tuple[int, int]:
    """Trova i bordi laterali della pellicola.

    Fuori dalla pellicola non c'e' nulla che assorba: la trasmittanza e' massima
    e uniforme. Il salto di segnale rispetto alla base della pellicola e' netto.
    Il canale IR e' il migliore per questo lavoro perche' e' quasi insensibile
    all'immagine.
    """
    profile = ir_or_density.mean(axis=0)
    profile = profile - profile.min()
    if profile.max() < 1e-6:
        return 0, ir_or_density.shape[1]

    profile = profile / profile.max()
    inside = profile < 0.85          # dentro la pellicola il segnale cala
    idx = np.where(inside)[0]
    if idx.size < 8:
        return 0, ir_or_density.shape[1]

    m = int(ir_or_density.shape[1] * margin_frac)
    return max(0, int(idx[0]) + m), min(ir_or_density.shape[1], int(idx[-1]) - m)


# --------------------------------------------------------------------------
# 2. Stacchi tra fotogrammi
# --------------------------------------------------------------------------

def frame_gaps(density: np.ndarray, expected_pitch_px: int,
               tolerance: float = 0.25) -> List[int]:
    """Trova le posizioni degli spazi interfotogramma lungo l'asse di scorrimento.

    Lo stacco tra due frame e' pellicola non esposta: densita' bassa e soprattutto
    varianza quasi nulla, perche' non c'e' immagine. La varianza e' un indicatore
    molto piu' affidabile della densita' media, che su un frame molto scuro puo'
    somigliare a uno stacco.

    'expected_pitch_px' viene dal formato scelto e serve a scartare i falsi
    positivi: un cielo uniforme ha varianza bassa quanto uno stacco, ma non
    ricorre col passo giusto.
    """
    var = density.var(axis=1)
    if HAS_SCIPY:
        var = ndimage.uniform_filter1d(var, size=max(3, expected_pitch_px // 40))

    if var.max() - var.min() < 1e-9:
        return []

    norm = (var - var.min()) / (var.max() - var.min())
    is_gap = norm < 0.18

    # raggruppa i pixel contigui e prendi il centro di ogni gruppo
    gaps: List[int] = []
    start = None
    for i, g in enumerate(is_gap):
        if g and start is None:
            start = i
        elif not g and start is not None:
            if i - start > max(4, expected_pitch_px // 60):
                gaps.append((start + i) // 2)
            start = None
    if start is not None:
        gaps.append((start + len(is_gap)) // 2)

    # filtro sul passo atteso: tiene solo gli stacchi coerenti col formato
    if len(gaps) > 2 and expected_pitch_px > 0:
        lo = expected_pitch_px * (1 - tolerance)
        hi = expected_pitch_px * (1 + tolerance)
        kept = [gaps[0]]
        for g in gaps[1:]:
            if lo <= g - kept[-1] <= hi or g - kept[-1] > hi:
                kept.append(g)
        gaps = kept

    return gaps


def next_advance_mm(gaps_px: List[int], current_px: int, dpi: int,
                    fallback_pitch_mm: float) -> float:
    """Quanto avanzare per arrivare al prossimo fotogramma.

    Se sono stati trovati stacchi affidabili si usa la distanza misurata; in caso
    contrario si ripiega sul passo nominale del formato. Il secondo caso accumula
    errore, quindi in interfaccia va segnalato: dopo qualche frame alla cieca
    conviene che l'operatore riallinei.
    """
    ahead = [g for g in gaps_px if g > current_px]
    if len(ahead) >= 2:
        return (ahead[1] - ahead[0]) * 25.4 / dpi
    if ahead:
        return (ahead[0] - current_px) * 25.4 / dpi
    return fallback_pitch_mm


# --------------------------------------------------------------------------
# 3. Raddrizzamento
# --------------------------------------------------------------------------

def estimate_skew(density: np.ndarray, max_angle: float = 2.0,
                  steps: int = 41) -> float:
    """Stima l'inclinazione del fotogramma provando piccole rotazioni.

    L'angolo giusto e' quello che rende piu' netta la varianza delle righe: se lo
    stacco interfotogramma e' storto, la sua traccia si spalma su piu' righe e il
    contrasto del profilo cala.
    """
    if not HAS_SCIPY:
        return 0.0

    best_angle, best_score = 0.0, -1.0
    for a in np.linspace(-max_angle, max_angle, steps):
        rot = ndimage.rotate(density, a, reshape=False, order=1, mode="nearest")
        score = float(rot.var(axis=1).var())
        if score > best_score:
            best_score, best_angle = score, float(a)
    return best_angle


def straighten(img: np.ndarray, angle: float) -> np.ndarray:
    if not HAS_SCIPY or abs(angle) < 0.01:
        return img
    return ndimage.rotate(img, angle, reshape=False, order=3, mode="nearest")


# --------------------------------------------------------------------------
# 4. Ritaglio del fotogramma
# --------------------------------------------------------------------------

def detect_frame(density: np.ndarray, ir: Optional[np.ndarray],
                 expected_w_px: int, expected_h_px: int,
                 margin_px: int = 0) -> FrameBox:
    """Individua il rettangolo del fotogramma.

    Combina i bordi della striscia (asse trasversale) con gli stacchi
    interfotogramma (asse di scorrimento). Dove la ricerca fallisce, ripiega
    sulle dimensioni nominali del formato centrate sull'immagine: sbagliare per
    difetto e' preferibile a un ritaglio arbitrario.
    """
    h, w = density.shape
    src = ir if ir is not None else density

    x0, x1 = find_strip_edges(src)
    if x1 - x0 < expected_w_px * 0.5:
        cx = w // 2
        x0, x1 = max(0, cx - expected_w_px // 2), min(w, cx + expected_w_px // 2)

    gaps = frame_gaps(density, expected_h_px)
    if len(gaps) >= 2:
        y0, y1 = gaps[0], gaps[1]
        conf = 0.9
    else:
        cy = h // 2
        y0, y1 = max(0, cy - expected_h_px // 2), min(h, cy + expected_h_px // 2)
        conf = 0.4

    m = margin_px
    box = FrameBox(min(y0 + m, h - 2), min(x0 + m, w - 2),
                   max(y1 - m, 1), max(x1 - m, 1), 0.0, conf)
    if box.h < 8 or box.w < 8:
        return FrameBox(0, 0, h, w, 0.0, 0.0)
    return box


def crop(img: np.ndarray, box: FrameBox) -> np.ndarray:
    return img[box.y0:box.y1, box.x0:box.x1]


# --------------------------------------------------------------------------
# 5. Soggetto e centratura
# --------------------------------------------------------------------------

def saliency_map(img: np.ndarray, sigma: float = 6.0) -> np.ndarray:
    """Mappa di interesse basata sul contrasto locale.

    Deliberatamente semplice: nessun riconoscimento semantico, nessun modello.
    Il soggetto di una fotografia sta di solito dove c'e' piu' dettaglio, e questo
    basta per suggerire una centratura. Non basta a decidere un ritaglio da solo,
    ed e' il motivo per cui il ritaglio sul soggetto resta una proposta da
    confermare, mai un'operazione automatica.
    """
    g = img.mean(axis=2) if img.ndim == 3 else img
    if not HAS_SCIPY:
        return np.ones_like(g)
    blur = ndimage.gaussian_filter(g, sigma)
    detail = np.abs(g - blur)
    return ndimage.gaussian_filter(detail, sigma * 2)


def subject_center(img: np.ndarray) -> Tuple[float, float]:
    """Baricentro dell'interesse, in coordinate normalizzate 0..1 (y, x)."""
    s = saliency_map(img)
    total = s.sum()
    if total < 1e-9:
        return 0.5, 0.5
    ys = (s.sum(axis=1) * np.arange(s.shape[0])).sum() / total / s.shape[0]
    xs = (s.sum(axis=0) * np.arange(s.shape[1])).sum() / total / s.shape[1]
    return float(ys), float(xs)


def suggest_crop(img: np.ndarray, aspect: Optional[float] = None,
                 zoom: float = 1.0) -> FrameBox:
    """Propone un ritaglio centrato sul soggetto, con rapporto d'aspetto opzionale.

    zoom = 1.0 significa il piu' grande ritaglio possibile con quel rapporto;
    valori superiori stringono. Il risultato e' sempre contenuto nell'immagine.
    """
    h, w = img.shape[:2]
    cy, cx = subject_center(img)
    aspect = aspect or (w / h)

    if w / h > aspect:
        ch = h / zoom
        cw = ch * aspect
    else:
        cw = w / zoom
        ch = cw / aspect

    cw, ch = min(cw, w), min(ch, h)
    x0 = int(np.clip(cx * w - cw / 2, 0, w - cw))
    y0 = int(np.clip(cy * h - ch / 2, 0, h - ch))
    return FrameBox(y0, x0, int(y0 + ch), int(x0 + cw), 0.0, 0.6)
