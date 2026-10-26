# -*- coding: utf-8 -*-
"""
Orientamento automatico e rilevamento della specchiatura.

Sono due problemi molto diversi per affidabilita', e conviene dirlo subito
invece di scoprirlo dopo aver ribaltato duecento scansioni.

ROTAZIONE (0/90/180/270)
    Determinabile dal contenuto con discreta affidabilita'. Il cielo e' piu'
    chiaro e tende al blu, sta in alto; le ombre stanno in basso; le strutture
    verticali dominano nei ritratti. Nessuno di questi indizi e' infallibile -
    una foto di un soffitto, un riflesso sull'acqua o una parete bianca li
    mandano fuori strada - ma insieme danno una stima utilizzabile.
    Si restituisce sempre una CONFIDENZA e non si applica mai nulla sotto soglia.

SPECCHIATURA (immagine ribaltata)
    NON e' determinabile dal contenuto fotografico in modo affidabile. Una scena
    speculare e' una scena perfettamente plausibile: non esiste un indizio
    generale che distingua una foto da quella ribaltata. Chi sostiene il
    contrario di solito sta usando il testo presente nella scena, che nella
    maggior parte delle fotografie non c'e'.

    Il segnale vero sta altrove: le SCRITTE SUL BORDO della pellicola - codice
    DX, marca, numeri di fotogramma - che sono stampate dal produttore e si
    leggono al rovescio se la pellicola e' inserita con l'emulsione dal lato
    sbagliato.

    E soprattutto: la specchiatura e' una proprieta' di COME E' STATA CARICATA
    LA PELLICOLA, non del singolo fotogramma. Si determina una volta per rullo e
    si applica a tutti. Questo modulo e' costruito attorno a questo fatto.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

try:
    from scipy import ndimage
    HAS_SCIPY = True
except ImportError:                                     # pragma: no cover
    HAS_SCIPY = False


# --------------------------------------------------------------------------

@dataclass
class Orientation:
    rotation: int = 0            # 0, 90, 180, 270 (gradi, senso antiorario)
    flip_h: bool = False
    flip_v: bool = False
    confidence: float = 0.0      # 0..1
    reason: str = ""

    @property
    def is_identity(self) -> bool:
        return self.rotation == 0 and not self.flip_h and not self.flip_v


# --------------------------------------------------------------------------
# Rotazione
# --------------------------------------------------------------------------

def _edge_scores(img: np.ndarray) -> Dict[str, float]:
    """Statistiche sui quattro bordi, usate per capire dove sta il cielo."""
    g = img.mean(axis=2) if img.ndim == 3 else img
    h, w = g.shape
    band_v = max(h // 6, 1)
    band_h = max(w // 6, 1)

    scores = {
        "top": float(g[:band_v, :].mean()),
        "bottom": float(g[-band_v:, :].mean()),
        "left": float(g[:, :band_h].mean()),
        "right": float(g[:, -band_h:].mean()),
    }

    if img.ndim == 3:
        # "quanto e' blu rispetto al rosso": il cielo lo e', il terreno no.
        # E' un indizio piu' specifico della sola luminosita', che una parete
        # bianca imiterebbe senza problemi.
        blue = img[:, :, 2] - img[:, :, 0]
        scores["top_blue"] = float(blue[:band_v, :].mean())
        scores["bottom_blue"] = float(blue[-band_v:, :].mean())
        scores["left_blue"] = float(blue[:, :band_h].mean())
        scores["right_blue"] = float(blue[:, -band_h:].mean())

    return scores


def _uniformity(img: np.ndarray) -> Dict[str, float]:
    """Quanto e' uniforme ciascun bordo. Il cielo e' liscio, il terreno no."""
    g = img.mean(axis=2) if img.ndim == 3 else img
    h, w = g.shape
    bv, bh = max(h // 6, 1), max(w // 6, 1)
    return {
        "top": -float(g[:bv, :].std()),
        "bottom": -float(g[-bv:, :].std()),
        "left": -float(g[:, :bh].std()),
        "right": -float(g[:, -bh:].std()),
    }


def detect_rotation(img: np.ndarray, min_confidence: float = 0.25) -> Orientation:
    """Stima quale lato dell'immagine e' l'alto.

    Combina tre indizi indipendenti: luminosita' del bordo, tendenza al blu,
    uniformita'. Nessuno basta da solo; presi insieme, e con una soglia di
    confidenza, danno un suggerimento onesto.

    La confidenza e' il margine fra il candidato migliore e il secondo: se due
    lati si somigliano, l'immagine semplicemente non dice dove sia l'alto - il
    che succede spesso, ed e' giusto ammetterlo invece di tirare a indovinare.
    """
    bright = _edge_scores(img)
    smooth = _uniformity(img)

    sides = ("top", "bottom", "left", "right")
    raw = {}
    for s in sides:
        score = bright[s]
        if (s + "_blue") in bright:
            score += 2.5 * bright[s + "_blue"]
        score += 1.2 * smooth[s]
        raw[s] = score

    values = np.array([raw[s] for s in sides], dtype=np.float64)
    spread = float(values.max() - values.min())
    if spread < 1e-6:
        return Orientation(0, False, False, 0.0, "immagine priva di indizi")

    order = np.argsort(values)[::-1]
    best, second = sides[order[0]], sides[order[1]]
    confidence = float((values[order[0]] - values[order[1]]) / spread)

    # il lato piu' "cielo" e' l'alto; la rotazione e' quella che lo porta su
    rotation = {"top": 0, "bottom": 180, "left": 270, "right": 90}[best]

    if confidence < min_confidence:
        return Orientation(0, False, False, confidence,
                           "indizi troppo deboli (%s vs %s): nessuna rotazione applicata"
                           % (best, second))

    return Orientation(rotation, False, False, confidence,
                       "lato piu' chiaro, uniforme e tendente al blu: %s" % best)


def apply_orientation(img: np.ndarray, o: Orientation) -> np.ndarray:
    """Applica rotazione e ribaltamenti. Solo multipli di 90 gradi: nessuna
    interpolazione, nessuna perdita."""
    out = img
    if o.rotation:
        out = np.rot90(out, k=(o.rotation // 90))
    if o.flip_h:
        out = out[:, ::-1]
    if o.flip_v:
        out = out[::-1, :]
    return np.ascontiguousarray(out)


# --------------------------------------------------------------------------
# Specchiatura
# --------------------------------------------------------------------------

def find_rebate_marks(strip: np.ndarray, frame_box: Tuple[int, int, int, int],
                      min_marks: int = 3) -> Optional[Tuple[int, int, int, int]]:
    """Individua la zona del bordo pellicola che contiene le scritte del produttore.

    Cerca, fuori dal riquadro del fotogramma, una fascia con molte transizioni di
    contrasto ravvicinate: e' la firma tipica di caratteri stampati, diversa sia
    dalla base uniforme sia dall'immagine.

    Ritorna il riquadro della fascia, oppure None. Serve a mostrare
    all'operatore DOVE guardare per decidere il verso, non a decidere al posto suo.
    """
    if not HAS_SCIPY:
        return None

    g = strip.mean(axis=2) if strip.ndim == 3 else strip
    h, w = g.shape
    y0, x0, y1, x1 = frame_box

    candidates = []
    if y0 > 20:
        candidates.append((0, 0, y0, w))
    if y1 < h - 20:
        candidates.append((y1, 0, h, w))

    best = None
    best_score = 0.0
    for (a, b, c, d) in candidates:
        band = g[a:c, b:d]
        if band.size < 100:
            continue
        edges = np.abs(np.diff(band, axis=1))
        thresh = float(np.percentile(edges, 98))
        if thresh < 1e-6:
            continue
        density = float((edges > thresh).mean())
        if density > best_score:
            best_score, best = density, (a, b, c, d)

    if best is None or best_score < 1e-4:
        return None
    return best


@dataclass
class RollMirror:
    """Decisione di specchiatura, valida per un intero rullo.

    E' deliberatamente una proprieta' del rullo e non del fotogramma: la
    pellicola viene caricata una volta, e o e' con l'emulsione dal lato giusto
    per tutti i fotogrammi o per nessuno. Decidere fotogramma per fotogramma
    significherebbe soltanto moltiplicare le occasioni di sbagliare.
    """
    flip_h: bool = False
    source: str = "predefinito"      # "predefinito" | "operatore" | "ocr"
    confirmed: bool = False

    def to_orientation(self) -> Orientation:
        return Orientation(0, self.flip_h, False,
                           1.0 if self.confirmed else 0.0,
                           "specchiatura del rullo (%s)" % self.source)


def mirror_hint_from_text(rebate: np.ndarray) -> Optional[bool]:
    """Prova a leggere le scritte del bordo per capire se sono al rovescio.

    Usa OCR se pytesseract e' installato: si legge la fascia cosi' com'e' e poi
    ribaltata, e vince quella che produce testo piu' plausibile. E' l'unico
    metodo automatico che si appoggi a un dato reale invece che a un'ipotesi
    sul contenuto.

    Ritorna True se la pellicola sembra specchiata, False se sembra dritta,
    None se non e' possibile decidere - che e' il caso piu' frequente e non e'
    un fallimento: significa solo che decide l'operatore.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return None

    def score(arr: np.ndarray) -> float:
        pil = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8))
        try:
            data = pytesseract.image_to_data(
                pil, output_type=pytesseract.Output.DICT)
        except Exception:                               # noqa: BLE001
            return 0.0
        total = 0.0
        for txt, conf in zip(data.get("text", []), data.get("conf", [])):
            try:
                c = float(conf)
            except (TypeError, ValueError):
                continue
            if c > 0 and txt.strip():
                total += c * len(txt.strip())
        return total

    g = rebate.mean(axis=2) if rebate.ndim == 3 else rebate
    direct = score(g)
    mirrored = score(g[:, ::-1])

    # margine minimo: due punteggi vicini non dicono nulla
    if max(direct, mirrored) < 50 or abs(direct - mirrored) < 0.25 * max(direct, mirrored):
        return None
    return mirrored > direct


# --------------------------------------------------------------------------

def auto_orient(img: np.ndarray, roll_mirror: Optional[RollMirror] = None,
                rotate: bool = True,
                min_confidence: float = 0.25) -> Tuple[np.ndarray, Orientation]:
    """Punto d'ingresso: applica rotazione stimata e specchiatura del rullo.

    La specchiatura viene applicata solo se qualcuno l'ha effettivamente
    confermata. La rotazione solo se la confidenza supera la soglia. In tutti
    gli altri casi l'immagine esce com'e', e il motivo viene riportato: e'
    sempre meglio consegnare un fotogramma da girare a mano che duecento girati
    male.
    """
    o = detect_rotation(img, min_confidence) if rotate else Orientation()

    if roll_mirror is not None and roll_mirror.confirmed and roll_mirror.flip_h:
        o.flip_h = True
        o.reason = (o.reason + "; " if o.reason else "") + \
                   "specchiatura del rullo (%s)" % roll_mirror.source

    if o.is_identity:
        return img, o
    return apply_orientation(img, o), o
