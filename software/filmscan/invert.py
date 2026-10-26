# -*- coding: utf-8 -*-
"""
Inversione del negativo e colore.

Invertire un negativo NON e' 1 meno il valore del pixel. Un negativo a colori e'
fatto di tre strati di coloranti sovrapposti su una base arancione, e le risposte
spettrali degli strati si sovrappongono tra loro.

Pipeline (in spazio densita', come da sistema Cineon e da negadoctor di darktable):

  1. campiona la base non esposta      -> D_min, il riferimento della maschera
  2. normalizza dividendo per la base  -> la dominante arancione sparisce
  3. matrice 3x3 pre-log               -> crosstalk sensore/coloranti in lineare
  4. logaritmo                         -> spazio densita', dove i coloranti sono lineari
  5. matrice 3x3 post-log              -> disaccoppiamento dei coloranti
  6. curva di tono                     -> esposizione, nero, gradazione

Nota sul nostro vantaggio: con illuminazione a bande separate R/G/B partiamo
meglio di chi fotografa il negativo con una reflex. Le bande sono strette e note,
quindi il crosstalk e' minore e le matrici piu' semplici da calibrare.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import numpy as np


IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


@dataclass
class FilmProfile:
    """Profilo di conversione per uno stock pellicola.

    Le matrici partono dall'identita': cosi' la pipeline funziona subito, e si
    raffinano con un target di riferimento fotografato su quello stock. E' il
    modo giusto per ricavarle - a occhio non si ottiene niente di ripetibile.
    """
    name: str = "generico C-41"
    dmin: Optional[Tuple[float, float, float]] = None   # None = autodetect
    m_pre: list = field(default_factory=lambda: [r[:] for r in IDENTITY])
    m_post: list = field(default_factory=lambda: [r[:] for r in IDENTITY])
    gamma_rgb: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    print_exposure: float = 1.0
    paper_black: float = 0.0
    paper_grade: float = 1.0
    monochrome: bool = False

    # True per le pellicole invertibili (diapositive): l'immagine e' gia'
    # positiva, non c'e' maschera arancione da togliere e non si inverte nulla.
    # E' un campo esplicito e non un controllo sul nome del profilo: dedurre il
    # comportamento dalla stringa del nome si rompe al primo profilo
    # personalizzato che l'utente chiama "Velvia" invece di "Diapositiva".
    positive: bool = False


PROFILES: Dict[str, FilmProfile] = {
    "c41": FilmProfile("C-41 generico"),
    "bn": FilmProfile("Bianco e nero", monochrome=True, paper_grade=1.15),
    "diapositiva": FilmProfile(
        "Diapositiva (positivo)", positive=True, paper_grade=1.0),
}


# --------------------------------------------------------------------------

def estimate_dmin(planes: Dict[str, np.ndarray],
                  border_px: int = 24) -> Tuple[float, float, float]:
    """Stima la densita' della base dai bordi non esposti del fotogramma.

    Prende il percentile alto della trasmittanza sul bordo: la base nuda e' la
    parte piu' trasparente di tutta la pellicola. Il percentile invece del massimo
    perche' un singolo pixel caldo rovinerebbe la stima.
    """
    out = []
    presenti = [c for c in ("R", "G", "B") if c in planes]
    if len(presenti) == 1:
        presenti = presenti * 3
    for ch in presenti:
        img = planes[ch]
        b = min(border_px, img.shape[0] // 4, img.shape[1] // 4)
        edges = np.concatenate([
            img[:b, :].ravel(), img[-b:, :].ravel(),
            img[:, :b].ravel(), img[:, -b:].ravel()])
        out.append(float(np.percentile(edges, 97)))
    return tuple(out)


def sample_dmin_region(planes: Dict[str, np.ndarray],
                       box: Tuple[int, int, int, int]) -> Tuple[float, float, float]:
    """Come sopra, ma da un rettangolo scelto a mano dall'operatore.
    box = (y0, x0, y1, x1). Da usare quando i bordi non sono visibili nel campo."""
    y0, x0, y1, x1 = box
    return tuple(float(np.percentile(planes[c][y0:y1, x0:x1], 97)) for c in ("R", "G", "B"))


def _apply_matrix(stack: np.ndarray, m: list) -> np.ndarray:
    """stack ha forma (h, w, 3)."""
    m = np.asarray(m, dtype=np.float32)
    return np.einsum("ij,hwj->hwi", m, stack)


def _stack_rgb(planes: Dict[str, np.ndarray]) -> np.ndarray:
    """Impila i canali visibili in (h, w, 3).

    Con un solo canale acquisito - il caso del bianco e nero, dove leggerne
    tre sarebbe lavoro triplicato per lo stesso risultato - lo si replica sui
    tre. Non e' un ripiego: e' proprio l'informazione che c'e'.
    """
    presenti = [c for c in ("R", "G", "B") if c in planes]
    if not presenti:
        raise ValueError("nessun canale visibile nella scansione")
    if len(presenti) == 1:
        uno = planes[presenti[0]]
        return np.repeat(uno[:, :, None], 3, axis=2).astype(np.float32)
    if len(presenti) == 2:
        raise ValueError("due canali visibili: combinazione non prevista")
    return np.stack([planes["R"], planes["G"], planes["B"]],
                    axis=-1).astype(np.float32)


def invert_negative(planes: Dict[str, np.ndarray],
                    profile: FilmProfile,
                    dmin: Optional[Tuple[float, float, float]] = None
                    ) -> np.ndarray:
    """Da trasmittanza a immagine positiva. Ritorna (h, w, 3) float32 in 0..1."""
    stack = _stack_rgb(planes)
    stack = np.clip(stack, 1e-5, None)

    if profile.positive:
        return _positive(stack, profile)

    base = dmin or profile.dmin or estimate_dmin(planes)
    base = np.asarray(base, dtype=np.float32).reshape(1, 1, 3)
    base = np.clip(base, 1e-5, None)

    # 2. normalizzazione sulla base: la maschera arancione se ne va per costruzione
    ratio = np.clip(stack / base, 1e-5, 1.0)

    # 3. crosstalk in lineare
    ratio = np.clip(_apply_matrix(ratio, profile.m_pre), 1e-5, None)

    # 4. spazio densita'
    density = -np.log10(ratio)

    # 5. disaccoppiamento dei coloranti
    density = _apply_matrix(density, profile.m_post)

    # gamma per canale: compensa i contrasti diversi dei tre strati
    g = np.asarray(profile.gamma_rgb, dtype=np.float32).reshape(1, 1, 3)
    density = density * g

    # ritorno al lineare, gia' positivo
    positive = 10.0 ** (-density)
    positive = positive / max(float(np.percentile(positive, 99.5)), 1e-6)

    if profile.monochrome:
        lum = positive.mean(axis=2, keepdims=True)
        positive = np.repeat(lum, 3, axis=2)

    return _tone_curve(np.clip(positive, 0.0, 1.0), profile)


def _positive(stack: np.ndarray, profile: FilmProfile) -> np.ndarray:
    """Elaborazione di una pellicola invertibile (diapositiva).

    Non e' semplicemente "il negativo senza l'inversione". Due differenze che
    contano:

    - non c'e' maschera arancione, quindi non si normalizza sulla base: il
      punto di riferimento e' il bianco, cioe' la zona piu' trasparente della
      diapositiva, che corrisponde alle luci della scena.
    - la gamma di densita' e' molto piu' ampia. Nelle ombre profonde passa
      pochissima luce, e quel poco e' quasi tutto rumore del sensore. Per
      questo si taglia sul percentile basso invece che sul minimo assoluto:
      inseguire il minimo significherebbe scalare tutta l'immagine sul pixel
      piu' rumoroso del fotogramma.
    """
    bianco = float(np.percentile(stack, 99.5))
    if bianco < 1e-6:
        bianco = 1.0
    out = np.clip(stack / bianco, 0.0, 1.0)

    if profile.monochrome:
        lum = out.mean(axis=2, keepdims=True)
        out = np.repeat(lum, 3, axis=2)

    return _tone_curve(out, profile)


def _tone_curve(img: np.ndarray, profile: FilmProfile) -> np.ndarray:
    """Curva finale slope / offset / power.

    E' l'equivalente digitale della scelta della carta e del tempo di esposizione
    in camera oscura: esposizione di stampa, nero della carta, gradazione.
    """
    out = img * profile.print_exposure + profile.paper_black
    out = np.clip(out, 1e-6, None) ** profile.paper_grade
    return np.clip(out, 0.0, 1.0).astype(np.float32)


# --------------------------------------------------------------------------
# Bilanciamento automatico
# --------------------------------------------------------------------------

def auto_white_balance(img: np.ndarray, strength: float = 1.0,
                       percentile: float = 99.0) -> np.ndarray:
    """Grey-world temperato dai percentili alti.

    Assume che la scena media sia neutra: falso su un tramonto o su una foto
    prevalentemente verde, quindi 'strength' permette di dosarlo o spegnerlo.
    """
    if strength <= 0:
        return img
    high = np.percentile(img.reshape(-1, 3), percentile, axis=0)
    high = np.where(high < 1e-6, 1.0, high)
    gain = high.mean() / high
    gain = 1.0 + (gain - 1.0) * float(strength)
    return np.clip(img * gain.reshape(1, 1, 3), 0.0, 1.0).astype(np.float32)


def auto_levels(img: np.ndarray, black_pct: float = 0.1,
                white_pct: float = 99.8) -> np.ndarray:
    """Livelli automatici sui percentili, calcolati sulla luminanza per non
    alterare il colore: se si tagliassero i canali separatamente si
    introdurrebbe una dominante."""
    lum = img.mean(axis=2)
    lo = float(np.percentile(lum, black_pct))
    hi = float(np.percentile(lum, white_pct))
    if hi - lo < 1e-6:
        return img
    return np.clip((img - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)


def apply_curve(img: np.ndarray, points: list) -> np.ndarray:
    """Curva di tono definita da punti di controllo [(x0,y0), (x1,y1), ...] in 0..1.

    Interpolazione lineare su LUT a 1024 voci: sufficiente e prevedibile.
    E' l'aggancio per l'editor di curve dell'interfaccia.
    """
    if not points or len(points) < 2:
        return img
    pts = sorted(points)
    xs = np.array([p[0] for p in pts], dtype=np.float32)
    ys = np.array([p[1] for p in pts], dtype=np.float32)
    lut_x = np.linspace(0.0, 1.0, 1024, dtype=np.float32)
    lut_y = np.interp(lut_x, xs, ys).astype(np.float32)
    idx = np.clip((img * 1023).astype(np.int32), 0, 1023)
    return lut_y[idx]


def saturation(img: np.ndarray, amount: float = 1.0) -> np.ndarray:
    lum = img.mean(axis=2, keepdims=True)
    return np.clip(lum + (img - lum) * amount, 0.0, 1.0).astype(np.float32)


def to_srgb(img: np.ndarray) -> np.ndarray:
    """Codifica sRGB. La pipeline lavora in lineare, il file va scritto codificato:
    saltare questo passaggio da' immagini scure e desaturate."""
    a = 0.055
    low = img * 12.92
    high = (1 + a) * np.clip(img, 1e-8, None) ** (1 / 2.4) - a
    return np.clip(np.where(img <= 0.0031308, low, high), 0.0, 1.0).astype(np.float32)
