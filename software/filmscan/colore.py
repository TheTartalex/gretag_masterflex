# -*- coding: utf-8 -*-
"""
Controlli di colore e di tono: quelli che un operatore si aspetta di trovare.

Tutto quello che sta qui lavora sull'immagine POSITIVA in spazio lineare, dopo
l'inversione e prima della codifica sRGB. Non e' un dettaglio: esposizione,
temperatura e contrasto hanno senso fisico solo sui valori lineari. Applicati
dopo la codifica sRGB darebbero risultati che sembrano giusti sul monitor e
sbagliati in stampa - ed e' il tipo di errore che si scopre a cento stampe
fatte.

L'ordine in cui si applicano non e' casuale, ed e' quello di `applica()`:

    1. esposizione        porta l'immagine al livello giusto
    2. bilanciamento      toglie la dominante (neutro, automatico o in kelvin)
    3. barre tricromia    ritocco fine per canale, come sui minilab
    4. gamma              distribuisce i toni
    5. contrasto          curva a S attorno al grigio medio
    6. luminosita'        alza o abbassa i mezzitoni
    7. curve              l'ultima parola dell'operatore, per canale
    8. chiarezza          contrasto locale, solo sulla luminanza
    9. tinta e saturazione
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    from scipy import ndimage
    HAS_SCIPY = True
except ImportError:                                     # pragma: no cover
    HAS_SCIPY = False


# Il bianco di riferimento del nostro spazio di lavoro. 5500 K e' la luce
# diurna per cui sono costruite le pellicole a colori: partire da li' vuol dire
# che "temperatura 5500" non cambia niente, che e' il comportamento che chi
# lavora si aspetta.
BIANCO_RIFERIMENTO_K = 5500.0


# ---------------------------------------------------------------------------
# Temperatura di colore
# ---------------------------------------------------------------------------

def _xy_da_kelvin(k: float) -> Tuple[float, float]:
    """Coordinate xy del corpo nero (locus planckiano) per una temperatura.

    Approssimazione di Kim, con l'errore sotto la soglia percettibile fra 1667
    e 25000 K: piu' che sufficiente, visto che l'estremo caldo di una lampada
    da laboratorio sta sui 2800 K e il diurno sui 6500.
    """
    k = float(np.clip(k, 1667.0, 25000.0))
    if k <= 4000.0:
        x = (-0.2661239e9 / k ** 3 - 0.2343589e6 / k ** 2
             + 0.8776956e3 / k + 0.179910)
    else:
        x = (-3.0258469e9 / k ** 3 + 2.1070379e6 / k ** 2
             + 0.2226347e3 / k + 0.240390)
    if k <= 2222.0:
        y = -1.1063814 * x ** 3 - 1.34811020 * x ** 2 + 2.18555832 * x - 0.20219683
    elif k <= 4000.0:
        y = -0.9549476 * x ** 3 - 1.37418593 * x ** 2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x ** 3 - 5.87338670 * x ** 2 + 3.75112997 * x - 0.37001483
    return x, y


# sRGB lineare <-> XYZ (D65)
_M_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                       [0.2126729, 0.7151522, 0.0721750],
                       [0.0193339, 0.1191920, 0.9503041]], dtype=np.float64)
_M_XYZ2RGB = np.linalg.inv(_M_RGB2XYZ)

# Bradford: la matrice di adattamento cromatico che si usa da trent'anni
_BRADFORD = np.array([[0.8951, 0.2664, -0.1614],
                      [-0.7502, 1.7135, 0.0367],
                      [0.0389, -0.0685, 1.0296]], dtype=np.float64)
_BRADFORD_INV = np.linalg.inv(_BRADFORD)


def _xyz_da_xy(x: float, y: float) -> np.ndarray:
    y = max(y, 1e-6)
    return np.array([x / y, 1.0, (1.0 - x - y) / y], dtype=np.float64)


def guadagni_temperatura(kelvin: float, tinta: float = 0.0,
                         riferimento: float = BIANCO_RIFERIMENTO_K) -> np.ndarray:
    """Moltiplicatori RGB per portare il bianco da 'riferimento' a 'kelvin'.

    tinta: da -1 (verde) a +1 (magenta). Si muove perpendicolarmente al locus,
    perche' e' esattamente cio' che manca alla temperatura da sola: le lampade
    fluorescenti e le pellicole scadute cadono fuori dal corpo nero, e senza un
    asse verde-magenta non si raddrizzano.

    Non e' un trucco su R e B: si passa da XYZ e si adatta con Bradford, quindi
    il risultato regge anche ai margini, dove i trucchi si vedono.
    """
    # Verso, che e' la cosa che si sbaglia sempre: il cursore dice a che
    # temperatura era la LUCE della scena. Se l'operatore scrive 3200 K sta
    # dicendo "questa e' luce di lampadina", e il software deve TOGLIERE il
    # giallo, cioe' raffreddare. Percio' si adatta DA quella temperatura AL
    # riferimento, non il contrario. Come in camera oscura e come in Lightroom:
    # cursore a sinistra, immagine piu' fredda.
    xs_, ys_ = _xy_da_kelvin(kelvin)          # da qui: la luce dichiarata
    xd, yd = _xy_da_kelvin(riferimento)       # a qui: il nostro bianco

    orig = _xyz_da_xy(xs_, ys_)
    dest = _xyz_da_xy(xd, yd)

    co = _BRADFORD @ orig
    cd = _BRADFORD @ dest
    adatt = _BRADFORD_INV @ np.diag(cd / np.maximum(co, 1e-9)) @ _BRADFORD
    m = _M_XYZ2RGB @ adatt @ _M_RGB2XYZ

    # dei coefficienti ci interessa solo il guadagno per canale, normalizzato
    # sul verde: spostare la temperatura deve cambiare il colore, non la
    # luminosita' dell'immagine
    g = m.sum(axis=1)
    g = g / max(g[1], 1e-9)

    # La tinta e' l'asse verde-magenta, ed e' una cosa diversa dalla
    # temperatura: non sta sul corpo nero. Ricavarla ruotando nel diagramma
    # xy sembra elegante ma sposta anche rosso e blu, che e' esattamente cio'
    # che una tinta non deve fare. Fisicamente la tinta E' il guadagno del
    # verde, ed e' cosi' che la trattano gli sviluppatori raw: qui si fa
    # uguale, ricompensando la luminanza perche' l'immagine non cambi
    # chiarezza mentre si corregge una dominante.
    if abs(tinta) > 1e-6:
        g = g.copy()
        g[1] *= 1.0 / (1.0 + 0.35 * float(np.clip(tinta, -1.0, 1.0)))
        luce = float(g @ np.array([0.2126, 0.7152, 0.0722]))
        g = g / max(luce, 1e-9)
    return np.clip(g, 0.2, 5.0).astype(np.float32)


def bilancia_kelvin(img: np.ndarray, kelvin: float, tinta: float = 0.0) -> np.ndarray:
    g = guadagni_temperatura(kelvin, tinta)
    return np.clip(img * g[None, None, :], 0.0, None)


def guadagni_da_neutro(img: np.ndarray, x: float, y: float,
                       raggio: int = 6) -> np.ndarray:
    """Guadagni che rendono grigio il punto campionato.

    x, y sono in 0..1 sull'immagine. Si media un quadratino invece di leggere
    un pixel solo: su pellicola un pixel e' grana, non colore.
    """
    h, w = img.shape[:2]
    cx = int(np.clip(x, 0, 1) * (w - 1))
    cy = int(np.clip(y, 0, 1) * (h - 1))
    x0, x1 = max(0, cx - raggio), min(w, cx + raggio + 1)
    y0, y1 = max(0, cy - raggio), min(h, cy + raggio + 1)
    campione = img[y0:y1, x0:x1].reshape(-1, img.shape[2])[:, :3]
    medio = np.mean(campione, axis=0)
    medio = np.where(medio < 1e-5, 1e-5, medio)
    g = float(np.mean(medio)) / medio
    return np.clip(g, 0.2, 5.0).astype(np.float32)


# ---------------------------------------------------------------------------
# Tono
# ---------------------------------------------------------------------------

def esposizione(img: np.ndarray, stop: float) -> np.ndarray:
    """Esposizione in stop: +1 raddoppia la luce, come in macchina fotografica."""
    if abs(stop) < 1e-4:
        return img
    return img * float(2.0 ** stop)


def gamma(img: np.ndarray, g: float) -> np.ndarray:
    if abs(g - 1.0) < 1e-4:
        return img
    return np.power(np.clip(img, 1e-6, None), 1.0 / float(g))


def luminosita(img: np.ndarray, quanto: float) -> np.ndarray:
    """Alza o abbassa i mezzitoni lasciando quasi fermi neri e bianchi.

    Non e' una somma: sommare sposta anche il nero e l'immagine si slava.
    """
    if abs(quanto) < 1e-4:
        return img
    k = float(np.clip(quanto, -1.0, 1.0))
    x = np.clip(img, 0.0, 1.0)
    return x + k * x * (1.0 - x)


def contrasto(img: np.ndarray, quanto: float, perno: float = 0.18) -> np.ndarray:
    """Curva a S attorno al grigio medio.

    Il perno e' 0,18 - il grigio 18%, non 0,5: e' li' che sta l'esposizione
    corretta di una scena media, ed e' attorno a quel valore che il contrasto
    va aperto se non si vogliono spostare tutti i toni verso l'alto.
    """
    if abs(quanto) < 1e-4:
        return img
    k = float(np.clip(quanto, -1.0, 1.0))
    x = np.clip(img, 0.0, 1.0)
    if k > 0:
        y = x + k * np.sin(np.pi * (x - perno)) * x * (1.0 - x) * 2.0
    else:
        y = x + k * (x - perno) * 0.6
    return np.clip(y, 0.0, 1.0)


def chiarezza(img: np.ndarray, quanto: float, raggio: float = 12.0) -> np.ndarray:
    """Contrasto locale sui mezzitoni: la 'clarity'.

    Si applica alla sola luminanza, e pesata sui mezzitoni: sulle luci
    sporcherebbe i bianchi, sulle ombre tirerebbe su il rumore. E' un raggio
    largo e un'intensita' bassa - il contrario della maschera di contrasto,
    che serve ad altro.
    """
    if abs(quanto) < 1e-4 or not HAS_SCIPY:
        return img
    k = float(np.clip(quanto, -1.0, 1.0))
    lum = img[..., :3] @ np.array([0.2126, 0.7152, 0.0722], dtype=img.dtype)
    sfocata = ndimage.gaussian_filter(lum, raggio)
    dettaglio = lum - sfocata
    peso = 1.0 - np.abs(np.clip(lum, 0, 1) * 2.0 - 1.0) ** 2      # 0 agli estremi
    delta = (k * 1.2) * dettaglio * peso
    out = img + delta[..., None]
    return np.clip(out, 0.0, None)


def tinta_hue(img: np.ndarray, gradi: float) -> np.ndarray:
    """Rotazione di tinta, in gradi, con una matrice in spazio YIQ.

    Ruotare la tinta e' un'operazione da usare con parsimonia su materiale
    fotografico: sposta i colori tutti insieme, incarnati compresi. Serve
    soprattutto a raddrizzare una dominante che il bilanciamento non prende,
    e conviene fermarsi a pochi gradi.
    """
    if abs(gradi) < 1e-4:
        return img
    a = np.deg2rad(float(gradi))
    c, s = float(np.cos(a)), float(np.sin(a))
    rgb2yiq = np.array([[0.299, 0.587, 0.114],
                        [0.596, -0.274, -0.322],
                        [0.211, -0.523, 0.312]], dtype=np.float32)
    rot = np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float32)
    m = np.linalg.inv(rgb2yiq) @ rot @ rgb2yiq
    out = img[..., :3] @ m.T.astype(img.dtype)
    return np.clip(out, 0.0, None)


# ---------------------------------------------------------------------------
# Barre tricromia e curve per canale
# ---------------------------------------------------------------------------

def tricromia(img: np.ndarray, r: float = 0.0, g: float = 0.0,
              b: float = 0.0) -> np.ndarray:
    """Le tre barre R/G/B dei minilab, in 'scatti di densita''.

    Un valore di +1 schiarisce quel canale di circa il 6%, che e' il passo con
    cui ragionano gli operatori abituati alla stampa: piccolo, ripetibile,
    e sommabile a occhio.
    """
    if not (r or g or b):
        return img
    guad = np.array([1.06 ** r, 1.06 ** g, 1.06 ** b], dtype=np.float32)
    return np.clip(img * guad[None, None, :], 0.0, None)


def curva_canale(img: np.ndarray, punti: Sequence, canale: Optional[int] = None) -> np.ndarray:
    """Curva definita da punti di controllo, su tutti i canali o su uno solo.

    Interpolazione lineare fra i punti: una spline sarebbe piu' morbida ma puo'
    scavalcare i punti e creare inversioni di tono che l'operatore non ha
    chiesto. Meglio prevedibile che elegante.
    """
    if not punti or len(punti) < 2:
        return img
    p = sorted((float(a), float(b)) for a, b in punti)
    xs = np.array([a for a, _ in p], dtype=np.float32)
    ys = np.array([b for _, b in p], dtype=np.float32)
    if xs[0] > 0:
        xs, ys = np.r_[0.0, xs], np.r_[ys[0], ys]
    if xs[-1] < 1:
        xs, ys = np.r_[xs, 1.0], np.r_[ys, ys[-1]]

    out = img.copy()
    x = np.clip(img, 0.0, 1.0)
    if canale is None:
        out = np.interp(x, xs, ys).astype(img.dtype)
    else:
        out[..., canale] = np.interp(x[..., canale], xs, ys).astype(img.dtype)
    return out


# ---------------------------------------------------------------------------

def applica(img: np.ndarray, o) -> np.ndarray:
    """Applica tutti i controlli, nell'ordine che conta.

    `o` e' un ProcessOptions. Ogni passo che vale zero non tocca l'immagine e
    non costa niente: e' il caso normale.
    """
    out = img

    out = esposizione(out, getattr(o, "esposizione", 0.0))

    guad = getattr(o, "wb_guadagni", None)
    if guad:                                   # campionamento neutro gia' fatto
        out = np.clip(out * np.asarray(guad, dtype=np.float32)[None, None, :], 0.0, None)
    elif getattr(o, "temperatura_k", 0):
        out = bilancia_kelvin(out, o.temperatura_k, getattr(o, "tinta_vm", 0.0))

    out = tricromia(out, getattr(o, "barra_r", 0.0), getattr(o, "barra_g", 0.0),
                    getattr(o, "barra_b", 0.0))
    out = gamma(out, getattr(o, "gamma_tono", 1.0))
    out = contrasto(out, getattr(o, "contrasto", 0.0))
    out = luminosita(out, getattr(o, "luminosita", 0.0))

    for canale, chiave in ((None, "curva"), (0, "curva_r"), (1, "curva_g"), (2, "curva_b")):
        punti = getattr(o, chiave, None)
        if punti:
            out = curva_canale(out, punti, canale)

    out = chiarezza(out, getattr(o, "chiarezza", 0.0))
    out = tinta_hue(out, getattr(o, "hue", 0.0))
    return out
