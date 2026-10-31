# -*- coding: utf-8 -*-
"""
Statistiche di rullo: base, equilibrio dei grigi, classi di esposizione.

L'idea viene dal Digital EyeTech del programma originale (vedi
documentazione/software-originale/elaborazione-originale.md): il colore di un
fotogramma non si decide guardando solo quel fotogramma. Un prato, una parete
rossa o un tramonto spostano il "grigio medio" del singolo scatto, e un
bilanciamento per fotogramma li tratta come dominanti da togliere. Su un
rullo intero invece la pellicola, lo sviluppo e la luce media sono gli stessi:
l'asse dei grigi stimato su tutti i fotogrammi normali e' molto piu' solido,
e il singolo fotogramma se ne discosta solo un poco.

Fatto con i mezzi di oggi:

- tutto in densita' sopra la base (log10), su copie ridotte (~256 px):
  costa millisecondi per fotogramma;
- la base del rullo e' la piu' chiara fra quelle dei fotogrammi, scartando
  le zone senza pellicola;
- i fotogrammi anomali (sottoesposti, sovraesposti, velati, poco contrastati)
  non entrano nell'equilibrio del rullo, come i "Skip" di EyeTech;
- la luce al tungsteno si riconosce come scostamento caldo forte dal rullo e
  si corregge di piu', dicendolo;
- una memoria per tipo di pellicola (codice DX), aggiornata rullo dopo
  rullo, da' il punto di partenza alle strisce corte.

Niente qui tocca la macchina: lavora sui grezzi gia' letti.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Dict, List, Optional, Tuple

import numpy as np

LATO_RIDOTTO = 256          # lato lungo delle copie su cui si misura
LUCE_SENZA_PELLICOLA = 0.97 # trasmittanza oltre cui non c'e' pellicola
PESO_FOTOGRAMMA = 0.35      # quanto il fotogramma si discosta dal rullo
LIMITE_FOTOGRAMMA = 0.10    # e fino a quanto (D)
PESO_TUNGSTENO = 0.80
LIMITE_TUNGSTENO = 0.30
SOGLIA_TUNGSTENO = 0.15     # (dR - dB) oltre il rullo, in D
PESO_MEMORIA = 4.0          # la memoria della pellicola vale 4 fotogrammi
ALFA_MEMORIA = 0.3          # aggiornamento della memoria a fine rullo


def _riduci(piani: Dict[str, List[np.ndarray]]) -> Dict[str, np.ndarray]:
    canali = [c for c in ("R", "G", "B") if c in piani]
    if not canali:
        canali = [next(c for c in piani if c != "IR")]
    h, w = piani[canali[0]][0].shape[:2]
    passo = max(1, int(max(h, w) / float(LATO_RIDOTTO)))
    out = {}
    for c in canali:
        out[c] = np.ascontiguousarray(piani[c][0][::passo, ::passo]).astype(np.float32)
    if len(out) == 1:
        unico = next(iter(out.values()))
        out = {"R": unico, "G": unico, "B": unico}
    return out


def _base_fotogramma(piccoli: Dict[str, np.ndarray]) -> np.ndarray:
    """La base vista da un fotogramma: il percentile alto dei bordi, senza le
    zone dove passa luce piena (fuori dalla pellicola)."""
    out = []
    for c in ("R", "G", "B"):
        p = piccoli[c]
        b = max(2, min(p.shape) // 16)
        bordi = np.concatenate([p[:b].ravel(), p[-b:].ravel(),
                                p[:, :b].ravel(), p[:, -b:].ravel()])
        bordi = bordi[bordi < LUCE_SENZA_PELLICOLA]
        out.append(float(np.percentile(bordi, 97)) if bordi.size else np.nan)
    return np.array(out, dtype=np.float64)


class Fotogramma(object):
    def __init__(self, numero: int, piccoli: Dict[str, np.ndarray]):
        self.numero = numero
        self.piccoli = piccoli
        self.base = _base_fotogramma(piccoli)
        # misure in densita', rifatte quando cambia la base del rullo
        self.livello = 0.0
        self.escursione = 0.0
        self.minimo = 0.0
        self.croma = np.zeros(3)
        self.classe = "normale"

    def misura(self, base: np.ndarray) -> None:
        stack = np.stack([self.piccoli[c] for c in ("R", "G", "B")], axis=-1)
        d = -np.log10(np.clip(stack / base.reshape(1, 1, 3), 1e-4, 1.0))
        dm = d.mean(axis=2)
        # si escludono base nuda e saturazioni: e' l'immagine che conta
        utile = (dm > 0.04) & (dm < 3.0)
        if utile.sum() < 50:
            utile = np.ones_like(dm, dtype=bool)
        sel = d[utile]
        medie = sel.mean(axis=0)
        self.livello = float(medie.mean())
        self.croma = medie - self.livello
        self.escursione = float(np.percentile(dm, 98) - np.percentile(dm, 2))
        self.minimo = float(np.percentile(dm, 2))


class StatisticheRullo(object):
    """Le statistiche di un rullo, alimentate fotogramma per fotogramma.

    Thread-safe: le alimenta il ciclo di lettura e le legge il thread di
    elaborazione."""

    def __init__(self, memoria: Optional[Dict[str, object]] = None,
                 scala_densita: float = 1.0):
        self._lock = threading.Lock()
        # pendenza attesa della pellicola rispetto a un C-41 normale
        # (pellicole_dx.attese): un forzato e' piu' denso senza essere
        # sovraesposto, e le soglie delle classi si scalano con lei
        self.scala = max(0.5, min(2.0, float(scala_densita or 1.0)))
        self._fot: Dict[int, Fotogramma] = {}
        self.memoria = memoria or None
        self._base: Optional[np.ndarray] = None
        self._croma: Optional[np.ndarray] = None
        self._livello_mediano = 0.6
        self._sporco = True

    # --- alimentazione --------------------------------------------------
    def aggiungi(self, numero: int, raw) -> None:
        try:
            piccoli = _riduci(raw.planes)
        except (StopIteration, KeyError, IndexError, ValueError):
            return
        with self._lock:
            self._fot[int(numero)] = Fotogramma(int(numero), piccoli)
            self._sporco = True

    def __len__(self) -> int:
        return len(self._fot)

    def numeri(self) -> List[int]:
        with self._lock:
            return sorted(self._fot)

    # --- calcolo --------------------------------------------------------
    def _aggiorna(self) -> None:
        if not self._sporco:
            return
        fot = list(self._fot.values())
        if not fot:
            return
        basi = np.array([f.base for f in fot])
        base = np.empty(3)
        for k in range(3):
            v = np.sort(basi[:, k][np.isfinite(basi[:, k])])[::-1]
            # la mediana delle tre piu' chiare: un fotogramma con un buco
            # o un riflesso non sposta la base di tutto il rullo
            base[k] = float(np.median(v[:3])) if v.size else 1.0
        if self.memoria and self.memoria.get("base") and len(fot) < 3:
            # pochi fotogrammi: la base della pellicola conosciuta conta
            mem = np.array(self.memoria["base"], dtype=np.float64)
            base = np.maximum(base, mem * 0.97)
        self._base = np.clip(base, 1e-3, 1.0)
        for f in fot:
            f.misura(self._base)
        livelli = np.array([f.livello for f in fot])
        self._livello_mediano = float(np.median(livelli))
        for f in fot:
            f.classe = self._classe(f)
        normali = [f.croma for f in fot if f.classe == "normale"] or [f.croma for f in fot]
        croma = np.median(np.array(normali), axis=0)
        n = float(len(normali))
        if self.memoria and self.memoria.get("croma"):
            mem = np.array(self.memoria["croma"], dtype=np.float64)
            croma = (croma * n + mem * PESO_MEMORIA) / (n + PESO_MEMORIA)
        self._croma = croma
        self._sporco = False

    def _classe(self, f: Fotogramma) -> str:
        """Sottoesposto, sovraesposto, velato, piatto o normale. Le soglie sono
        in densita' sopra la base, e relative al rullo dove ha senso: un rullo
        intero un po' chiaro non e' fatto di fotogrammi tutti sottoesposti."""
        k = self.scala
        rel = f.livello - self._livello_mediano
        if f.livello < 0.30 * k or (rel < -0.35 * k and f.livello < 0.55 * k):
            return "sottoesposto"
        if f.livello > 1.55 * k or rel > 0.45 * k:
            return "sovraesposto"
        if f.minimo > 0.35 * k and f.escursione < 0.50 * k:
            return "velato"
        if f.escursione < 0.30 * k:
            return "piatto"
        return "normale"

    # --- risultati ------------------------------------------------------
    def base(self) -> Optional[Tuple[float, float, float]]:
        with self._lock:
            self._aggiorna()
            return None if self._base is None else tuple(float(x) for x in self._base)

    def correzione(self, numero: int) -> Dict[str, object]:
        """Per un fotogramma: base del rullo, correzione di densita' per
        canale (da togliere), classe di esposizione e avvisi."""
        with self._lock:
            self._aggiorna()
            f = self._fot.get(int(numero))
            if f is None or self._base is None or self._croma is None:
                return {}
            avvisi = []
            scarto = f.croma - self._croma
            peso, limite = PESO_FOTOGRAMMA, LIMITE_FOTOGRAMMA
            tungsteno = (scarto[0] - scarto[2]) > SOGLIA_TUNGSTENO
            if tungsteno:
                # luce artificiale calda: qui il fotogramma ha ragione di
                # scostarsi dal rullo, e lo si corregge di piu'
                peso, limite = PESO_TUNGSTENO, LIMITE_TUNGSTENO
                avvisi.append("luce al tungsteno riconosciuta: dominante calda "
                              "corretta all'%d%%" % int(PESO_TUNGSTENO * 100))
            togli = self._croma + np.clip(scarto, -limite, limite) * peso
            if f.classe == "sottoesposto":
                avvisi.append("negativo sottoesposto (%.2f D sopra la base): "
                              "contrasto nei toni bassi e rumore di colore "
                              "ridotto" % f.livello)
            elif f.classe == "sovraesposto":
                avvisi.append("negativo sovraesposto (%.2f D sopra la base): "
                              "alte luci compresse" % f.livello)
            elif f.classe == "velato":
                avvisi.append("fotogramma velato (ombre a %.2f D sopra la "
                              "base): nero corretto" % f.minimo)
            return {"base": tuple(float(x) for x in self._base),
                    "densita": tuple(float(x) for x in togli),
                    "classe": f.classe,
                    "tungsteno": bool(tungsteno),
                    "avvisi": avvisi}

    def riepilogo(self) -> Dict[str, object]:
        with self._lock:
            self._aggiorna()
            classi: Dict[str, int] = {}
            for f in self._fot.values():
                classi[f.classe] = classi.get(f.classe, 0) + 1
            return {"fotogrammi": len(self._fot),
                    "base": None if self._base is None else [round(float(x), 5) for x in self._base],
                    "croma": None if self._croma is None else [round(float(x), 4) for x in self._croma],
                    "livello_mediano": round(self._livello_mediano, 3),
                    "classi": classi}

    def velo_di_base(self) -> Optional[float]:
        """Quanto la base del rullo e' piu' densa di quella memorizzata per la
        stessa pellicola (D). Oltre ~0,08: pellicola velata o sviluppo
        diverso dal solito."""
        if not self.memoria or not self.memoria.get("base") or self._base is None:
            return None
        mem = np.array(self.memoria["base"], dtype=np.float64)
        return float(np.mean(-np.log10(np.clip(self._base / mem, 1e-4, 10.0))))


# --- memoria per tipo di pellicola (codice DX) ----------------------------

class MemoriaPellicole(object):
    """Base e asse dei grigi per tipo di pellicola, in un file JSON accanto
    all'archivio. Come FilmChan.fcs del programma originale: ogni rullo
    finito aggiorna la media della sua pellicola."""

    def __init__(self, percorso: str):
        self.percorso = percorso
        self._lock = threading.Lock()

    def _leggi(self) -> Dict[str, Dict[str, object]]:
        try:
            with open(self.percorso, encoding="utf-8") as f:
                return json.load(f)
        except (IOError, OSError, ValueError):
            return {}

    def per(self, chiave: str) -> Optional[Dict[str, object]]:
        if not chiave:
            return None
        with self._lock:
            return self._leggi().get(chiave)

    def aggiorna(self, chiave: str, stat: StatisticheRullo) -> None:
        if not chiave:
            return
        r = stat.riepilogo()
        if not r["base"] or not r["croma"] or r["classi"].get("normale", 0) < 3:
            return          # troppo poco per insegnare qualcosa
        with self._lock:
            tutto = self._leggi()
            v = tutto.get(chiave)
            if v:
                a = ALFA_MEMORIA
                base = [(1 - a) * x + a * y for x, y in zip(v["base"], r["base"])]
                croma = [(1 - a) * x + a * y for x, y in zip(v["croma"], r["croma"])]
                rulli = int(v.get("rulli", 1)) + 1
            else:
                base, croma, rulli = r["base"], r["croma"], 1
            tutto[chiave] = {"base": base, "croma": croma, "rulli": rulli}
            tmp = self.percorso + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(tutto, f, indent=1)
            os.replace(tmp, self.percorso)


def chiave_dx(dx: Optional[Dict[str, object]]) -> str:
    """'DX 115-3' dai due numeri del codice DX; vuota se non letto."""
    if not dx or dx.get("dx1") in (None, 0):
        return ""
    return "DX %s-%s" % (dx.get("dx1"), dx.get("dx2", 0))
