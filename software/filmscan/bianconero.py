# -*- coding: utf-8 -*-
"""
Toni e viraggi per il bianco e nero.

Sono le scelte che in camera oscura si facevano con la carta e con il bagno
di viraggio, non correzioni: l'inversione (invert.py) e l'equilibrio del
rullo restano quelli, e qui si decide soltanto come stampare.

Lavorano sulla base lineare, come i cursori del colore (pipeline.finiture),
quindi si cambiano a fotogramma gia' letto e si vedono subito.

Toni (in spazio percettivo, x^(1/2,2), dove "contrasto" ha il senso che ha
sulla stampa; i due estremi, nero e bianco carta, non si spostano mai):

- naturale       nessun cambiamento;
- alto           carta di grado 4: curva a S, neri piu' profondi;
- basso          carta di grado 1: toni medi distesi, nero un po' alzato;
- esteso         gamma dinamica estesa: ombre aperte e alte luci
                 compresse con una curva logaritmica, perche' nulla vada
                 perso; va con i livelli automatici quasi senza taglio
                 (pipeline._bilancia), altrimenti si ritaglierebbe quello che
                 la curva ha salvato;
- duro           grafico, quasi lith: contrasto molto alto per i ritratti di
                 reportage e le architetture.

Viraggi: una potenza diversa per canale. x^g lascia fermi lo 0 e l'1, quindi
nero e bianco carta restano neutri e il colore va nei toni medi, come fa
un viraggio vero.
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np

TONI: Dict[str, str] = {
    "": "Naturale",
    "alto": "Alto contrasto (carta grado 4)",
    "basso": "Basso contrasto (carta grado 1)",
    "esteso": "Gamma dinamica estesa",
    "duro": "Duro (grafico)",
}

# esponenti R, G, B sulla luce lineare
VIRAGGI: Dict[str, Tuple[str, Tuple[float, float, float]]] = {
    "": ("Nessuno", (1.0, 1.0, 1.0)),
    "seppia": ("Seppia", (0.86, 1.0, 1.25)),
    "selenio": ("Selenio", (1.04, 1.08, 0.97)),
    "caldo": ("Carta calda", (0.96, 1.0, 1.08)),
    "freddo": ("Carta fredda", (1.05, 1.01, 0.95)),
    "cianotipia": ("Cianotipia", (1.55, 1.12, 0.86)),
}

_G = 2.2


def _s(p: np.ndarray, k: float) -> np.ndarray:
    """Curva a S logistica normalizzata, ferma in 0 e in 1, centrata a 0,45."""
    m = 0.45
    lo = 1.0 / (1.0 + np.exp(k * m))
    hi = 1.0 / (1.0 + np.exp(-k * (1.0 - m)))
    return (1.0 / (1.0 + np.exp(-k * (p - m))) - lo) / (hi - lo)


def tono(img: np.ndarray, nome: str) -> np.ndarray:
    if not nome or nome not in TONI:
        return img
    p = np.clip(img, 0.0, 1.0) ** (1.0 / _G)
    if nome == "alto":
        q = _s(p, 6.0)
    elif nome == "duro":
        q = _s(p, 11.0)
    elif nome == "basso":
        # S rovesciata leggera: pendenza a meta' ~0,75, nero a 0,04
        liscia = p * p * (3.0 - 2.0 * p)
        q = 0.04 + 0.96 * np.clip(p + 0.45 * (p - liscia), 0.0, 1.0)
    elif nome == "esteso":
        c = 2.5
        q = np.log1p(c * p) / np.log1p(c)
    else:
        return img
    return (np.clip(q, 0.0, 1.0) ** _G).astype(np.float32)


def viraggio(img: np.ndarray, nome: str) -> np.ndarray:
    if not nome or nome not in VIRAGGI:
        return img
    g = VIRAGGI[nome][1]
    x = np.clip(img, 0.0, 1.0)
    lum = x.mean(axis=2) if x.ndim == 3 else x
    return np.stack([lum ** g[0], lum ** g[1], lum ** g[2]],
                    axis=-1).astype(np.float32)
