# -*- coding: utf-8 -*-
"""
Algoritmi di miglioramento immagine.

Tre famiglie distinte, che vanno tenute separate perche' rispondono a esigenze
opposte:

  RIDUZIONE GRANA    togliere rumore di pellicola senza spappolare il dettaglio
  RIPARAZIONE GRAFFI trovare e chiudere righe e macchie, anche SENZA canale IR
  SIMULAZIONE        aggiungere grana o carattere pellicola, come scelta estetica

La riparazione graffi qui e' complementare a irclean.py, non alternativa: quando
c'e' il canale IR quello resta la fonte migliore, perche' misura un fatto fisico
invece di indovinare. Questi metodi servono per il bianco e nero a base argento,
dove l'IR non e' utilizzabile.

Tutte le funzioni lavorano su float32 in 0..1 e restituiscono lo stesso formato.
Tutte hanno un parametro di intensita' e a zero non fanno nulla: nessuna
correzione deve essere obbligatoria.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

try:
    from scipy import ndimage
    HAS_SCIPY = True
except ImportError:                                     # pragma: no cover
    HAS_SCIPY = False


# --------------------------------------------------------------------------
# Riduzione grana
# --------------------------------------------------------------------------

def estimate_grain(img: np.ndarray, patch: int = 32) -> float:
    """Stima l'intensita' della grana come deviazione standard del rumore.

    Si prende il percentile BASSO delle deviazioni locali, non la media: nelle
    zone di dettaglio la varianza e' alta per via del contenuto, non del rumore.
    Le zone piu' piatte dell'immagine sono quelle che dicono la verita' sul
    rumore di fondo.
    """
    g = img.mean(axis=2) if img.ndim == 3 else img
    if not HAS_SCIPY:
        return float(g.std()) * 0.3

    mean = ndimage.uniform_filter(g, patch)
    sq = ndimage.uniform_filter(g * g, patch)
    local_std = np.sqrt(np.clip(sq - mean * mean, 0, None))
    return float(np.percentile(local_std, 15))


def reduce_grain(img: np.ndarray, strength: float = 0.5,
                 detail_preserve: float = 0.6,
                 chroma_only: bool = False) -> np.ndarray:
    """Riduzione grana che preserva i bordi.

    Il filtro e' guidato dal contrasto locale: dove l'immagine e' piatta smussa
    molto, dove c'e' struttura smussa poco. E' il compromesso che conta, perche'
    una gaussiana uniforme toglie la grana e insieme tutto il dettaglio fine.

    'chroma_only' agisce solo sul colore lasciando intatta la luminanza. Sui
    negativi e' spesso la scelta giusta: la grana cromatica e' quella fastidiosa,
    quella di luminanza fa parte del carattere della pellicola e toglierla rende
    l'immagine di plastica.
    """
    if strength <= 0 or not HAS_SCIPY:
        return img

    strength = float(np.clip(strength, 0.0, 1.0))
    sigma = 0.6 + 1.8 * strength

    if img.ndim == 2:
        return _guided_smooth(img, sigma, detail_preserve, strength)

    if chroma_only:
        lum = img.mean(axis=2, keepdims=True)
        chroma = img - lum
        for c in range(chroma.shape[2]):
            chroma[:, :, c] = ndimage.gaussian_filter(chroma[:, :, c], sigma * 1.6)
        return np.clip(lum + chroma, 0.0, 1.0).astype(np.float32)

    # il livello di rumore si stima una volta sull'intera immagine: e' una
    # proprieta' della pellicola e dello sviluppo, non del singolo canale
    noise = estimate_grain(img)
    out = np.empty_like(img)
    for c in range(img.shape[2]):
        out[:, :, c] = _guided_smooth(img[:, :, c], sigma, detail_preserve,
                                      strength, noise)
    return out


def _guided_smooth(plane: np.ndarray, sigma: float, detail_preserve: float,
                   strength: float, noise: Optional[float] = None) -> np.ndarray:
    """Smussamento con soglia di rumore.

    Il punto delicato: non basta guardare quanta energia ad alta frequenza c'e'
    localmente, perche' la grana ne produce ovunque e il filtro finisce per non
    fare nulla. Bisogna confrontare quell'energia con il livello di rumore
    STIMATO: quello che sta sopra e' immagine e va tenuto, quello che sta sotto
    e' grana e va attenuato.

    E' una soglia in stile Wiener, e la differenza rispetto al criterio ingenuo
    non e' sottile: senza, la riduzione grana e' puramente decorativa.
    """
    if noise is None:
        noise = estimate_grain(plane)

    smooth = ndimage.gaussian_filter(plane, sigma)
    detail = plane - smooth

    # energia locale del dettaglio (RMS), da confrontare col rumore
    energy = np.sqrt(np.clip(
        ndimage.gaussian_filter(detail * detail, sigma * 2.0), 0, None))

    floor = max(noise, 1e-5) * (0.8 + 1.4 * strength)
    gain = np.clip(1.0 - (floor * floor) / (energy * energy + 1e-12), 0.0, 1.0)

    # detail_preserve rialza il pavimento: piu' e' alto, piu' dettaglio
    # sopravvive anche dove il rapporto segnale/rumore e' modesto
    gain = gain + (1.0 - gain) * float(np.clip(detail_preserve, 0.0, 1.0)) * 0.35

    return np.clip(smooth + detail * gain, 0.0, 1.0).astype(np.float32)


def add_grain(img: np.ndarray, amount: float = 0.03, size: float = 1.0,
              monochrome: bool = True, seed: Optional[int] = None) -> np.ndarray:
    """Aggiunge grana sintetica.

    Serve in due casi: come effetto voluto, e per rimediare a una riduzione grana
    troppo aggressiva, che lascia un'immagine innaturalmente liscia.

    La grana e' modulata sui mezzitoni: nella realta' e' meno visibile nelle
    ombre chiuse e nelle alte luci bruciate, e riprodurre questo la rende
    convincente invece che appiccicata sopra.
    """
    if amount <= 0:
        return img

    rng = np.random.default_rng(seed)
    shape = img.shape[:2]

    def field() -> np.ndarray:
        n = rng.normal(0, 1, shape).astype(np.float32)
        if HAS_SCIPY and size > 0.3:
            n = ndimage.gaussian_filter(n, size * 0.7)
            sd = float(n.std())
            if sd > 1e-9:
                n /= sd
        return n

    lum = img.mean(axis=2) if img.ndim == 3 else img
    modulation = 4.0 * lum * (1.0 - lum)        # massimo a meta' scala

    if img.ndim == 2:
        return np.clip(img + field() * modulation * amount, 0, 1).astype(np.float32)

    if monochrome:
        layer = (field() * modulation * amount)[:, :, None]
        return np.clip(img + layer, 0, 1).astype(np.float32)

    out = np.empty_like(img)
    for c in range(img.shape[2]):
        out[:, :, c] = img[:, :, c] + field() * modulation * amount
    return np.clip(out, 0, 1).astype(np.float32)


# --------------------------------------------------------------------------
# Graffi e polvere senza canale infrarosso
# --------------------------------------------------------------------------

def detect_scratches(img: np.ndarray, sensitivity: float = 0.5,
                     min_length: int = 40) -> np.ndarray:
    """Trova graffi lineari senza usare l'infrarosso.

    Il principio: un graffio da trascinamento e' una riga sottile, molto piu'
    lunga che larga, orientata nel verso di scorrimento della pellicola. Si
    cerca quindi una struttura stretta in una direzione e continua nell'altra -
    cosa che il contenuto fotografico raramente e'.

    Rispetto all'IR questo metodo e' un ripiego, e va detto: l'IR misura un
    fatto fisico, questo fa un'ipotesi. Una ringhiera, un palo della luce e un
    cavo teso hanno esattamente le caratteristiche di un graffio. Da usare dove
    l'IR non e' disponibile - il bianco e nero a base argento - e sempre con la
    possibilita' di rivedere il risultato.
    """
    if not HAS_SCIPY or sensitivity <= 0:
        return np.zeros(img.shape[:2], dtype=bool)

    g = img.mean(axis=2) if img.ndim == 3 else img

    # un graffio e' piu' stretto in una direzione che nell'altra: la differenza
    # fra due smussamenti anisotropi lo mette in evidenza
    narrow = ndimage.gaussian_filter(g, (0.4, 3.0))
    wide = ndimage.gaussian_filter(g, (3.0, 0.4))
    ridge = np.abs(narrow - wide)

    thresh = float(np.percentile(ridge, 100.0 - 2.0 * sensitivity))
    mask = ridge > max(thresh, 1e-6)

    # tiene solo le strutture davvero allungate: e' questo che scarta il
    # dettaglio fotografico, che di solito non e' filiforme e continuo
    mask = ndimage.binary_opening(mask, structure=np.ones((1, 5)))
    labels, n = ndimage.label(mask)
    if n == 0:
        return mask

    keep = np.zeros(n + 1, dtype=bool)
    for i, sl in enumerate(ndimage.find_objects(labels), start=1):
        if sl is None:
            continue
        h = sl[0].stop - sl[0].start
        w = sl[1].stop - sl[1].start
        length = max(h, w)
        thickness = max(min(h, w), 1)
        if length >= min_length and length / float(thickness) >= 6.0:
            keep[i] = True

    return keep[labels]


def detect_dust(img: np.ndarray, sensitivity: float = 0.5,
                max_size: int = 60) -> np.ndarray:
    """Trova macchie compatte di polvere, sempre senza IR.

    Cerca piccole aree isolate molto piu' scure o chiare dell'intorno. Il limite
    di dimensione e' quello che evita di cancellare occhi, finestre lontane e
    uccelli in cielo - che a un algoritmo somigliano parecchio a granelli.
    """
    if not HAS_SCIPY or sensitivity <= 0:
        return np.zeros(img.shape[:2], dtype=bool)

    g = img.mean(axis=2) if img.ndim == 3 else img
    background = ndimage.median_filter(g, size=7)
    diff = np.abs(g - background)

    thresh = float(np.percentile(diff, 100.0 - 1.5 * sensitivity))
    mask = diff > max(thresh, 1e-6)

    labels, n = ndimage.label(mask)
    if n == 0:
        return mask
    sizes = np.bincount(labels.ravel())
    small = (sizes <= max_size) & (sizes >= 2)
    small[0] = False
    return small[labels]


# --------------------------------------------------------------------------
# Nitidezza
# --------------------------------------------------------------------------

def unsharp(img: np.ndarray, amount: float = 0.4, radius: float = 1.2,
            threshold: float = 0.01) -> np.ndarray:
    """Maschera di contrasto con soglia.

    La soglia serve a non esaltare la grana insieme al dettaglio: sotto quel
    livello di differenza locale non si tocca nulla. Senza soglia, applicare
    nitidezza a una scansione da pellicola significa soprattutto rendere piu'
    evidente il rumore.
    """
    if amount <= 0 or not HAS_SCIPY:
        return img

    if img.ndim == 2:
        blur = ndimage.gaussian_filter(img, radius)
        detail = img - blur
        detail = np.where(np.abs(detail) < threshold, 0.0, detail)
        return np.clip(img + detail * amount, 0, 1).astype(np.float32)

    # solo sulla luminanza: applicarla ai tre canali separatamente produce
    # frange colorate sui bordi ad alto contrasto
    lum = img.mean(axis=2, keepdims=True)
    blur = ndimage.gaussian_filter(lum[:, :, 0], radius)[:, :, None]
    detail = lum - blur
    detail = np.where(np.abs(detail) < threshold, 0.0, detail)
    return np.clip(img + detail * amount, 0, 1).astype(np.float32)


# --------------------------------------------------------------------------
# Catena combinata
# --------------------------------------------------------------------------

def enhance(img: np.ndarray,
            grain_reduction: float = 0.0,
            chroma_only: bool = True,
            scratch_repair: float = 0.0,
            dust_repair: float = 0.0,
            sharpen: float = 0.0,
            grain_add: float = 0.0) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    """Applica la catena di miglioramento nell'ordine corretto.

    L'ordine conta: prima si tolgono i difetti, poi si riduce la grana, poi si
    aggiunge nitidezza. Invertirlo significa spalmare i difetti con lo
    smussamento e poi esaltarne i residui con la nitidezza.

    Ritorna l'immagine e la maschera dei difetti riparati (None se nessuno).
    """
    from .irclean import inpaint

    out = img
    mask = None

    if scratch_repair > 0 or dust_repair > 0:
        parts = []
        if scratch_repair > 0:
            parts.append(detect_scratches(out, scratch_repair))
        if dust_repair > 0:
            parts.append(detect_dust(out, dust_repair))
        if parts:
            mask = parts[0]
            for extra in parts[1:]:
                mask = mask | extra
            if mask.any():
                out = inpaint(out, mask,
                              strength=max(scratch_repair, dust_repair))

    if grain_reduction > 0:
        out = reduce_grain(out, grain_reduction, chroma_only=chroma_only)

    if sharpen > 0:
        out = unsharp(out, sharpen)

    if grain_add > 0:
        out = add_grain(out, grain_add)

    return out, mask


# --------------------------------------------------------------------------
# Densita' locale (schiarire e scurire per zone)
# --------------------------------------------------------------------------

def _media_box(x: np.ndarray, r: int) -> np.ndarray:
    """Media su una finestra (2r+1)^2, con le somme cumulate: costa uguale
    qualunque sia r."""
    if HAS_SCIPY:
        return ndimage.uniform_filter(x, size=2 * r + 1, mode="reflect")
    p = np.pad(x, r + 1, mode="reflect")
    c = p.cumsum(0).cumsum(1)
    k = 2 * r + 1
    s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return (s / float(k * k))[:x.shape[0], :x.shape[1]].astype(x.dtype)


def _filtro_guidato(guida: np.ndarray, r: int, eps: float) -> np.ndarray:
    """Filtro guidato (He, Sun, Tang 2010) con l'immagine come guida di se'
    stessa: liscia le zone e si ferma ai bordi, quindi non fa aloni."""
    m = _media_box(guida, r)
    var = _media_box(guida * guida, r) - m * m
    a = var / (var + eps)
    b = m - a * m
    return _media_box(a, r) * guida + _media_box(b, r)


def densita_locale(img: np.ndarray, forza: float) -> np.ndarray:
    """La correzione di densita' locale, come la LDC del programma originale
    ma con un filtro guidato invece di una maschera a blocchi: le grandi zone
    troppo chiare o troppo scure si avvicinano al tono medio, il dettaglio
    resta com'e'. 'forza' 0..1; 0 non tocca niente.

    Lavora in log2 della luminanza su una copia di ~400 px (la parte grande
    dell'immagine e' fatta di zone, non di dettagli) e riporta la correzione
    a piena risoluzione come rapporto, uguale sui tre canali: il colore non
    cambia."""
    forza = float(np.clip(forza, 0.0, 1.0))
    if forza <= 0:
        return img
    x = np.clip(img, 0.0, 1.0).astype(np.float32)
    lum = 0.2126 * x[..., 0] + 0.7152 * x[..., 1] + 0.0722 * x[..., 2]
    L = np.log2(lum + 1e-4)
    h, w = L.shape
    passo = max(1, int(max(h, w) / 400.0))
    Lp = np.ascontiguousarray(L[::passo, ::passo])
    r = max(3, int(max(Lp.shape) / 24))
    base_p = _filtro_guidato(Lp, r, eps=0.25)
    if passo > 1:
        if HAS_SCIPY:
            base = ndimage.zoom(base_p, (h / float(base_p.shape[0]), w / float(base_p.shape[1])),
                                order=1)[:h, :w]
            if base.shape != L.shape:
                base = np.pad(base, ((0, h - base.shape[0]), (0, w - base.shape[1])), mode="edge")
        else:
            base = np.repeat(np.repeat(base_p, passo, 0), passo, 1)[:h, :w]
    else:
        base = base_p
    medio = float(np.median(base))
    nuova = medio + (base - medio) * (1.0 - 0.6 * forza)
    rapporto = np.exp2(nuova - base).astype(np.float32)
    return np.clip(x * rapporto[..., None], 0.0, 1.0)

