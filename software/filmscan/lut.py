# -*- coding: utf-8 -*-
"""
LUT .cube: un "look" applicato in fondo all'elaborazione.

Il formato .cube (Adobe/Resolve) e' quello che esportano quasi tutti i
programmi di color grading: testo, una riga per campione. Si leggono le LUT
3D (LUT_3D_SIZE, la tabella cubica, rosso che cambia piu' in fretta) e 1D
(LUT_1D_SIZE, una curva per canale).

L'estensione .lut non dice il formato: il formato si riconosce dal
contenuto. Si leggono:
- un .cube rinominato;
- la 1D di Autodesk/Discreet: intestazione facoltativa "LUT: 3 1024", un
  intero per riga (i tre canali uno dopo l'altro) oppure tre per riga;
- la 3D .3dl di Autodesk: una prima riga con la griglia ("0 64 ... 1023"),
  poi una terna per riga, con il blu che cambia piu' in fretta.

I valori interi si portano a 0..1 dal massimo che si trova (8, 10, 12 o
16 bit).

Si applica sull'immagine sRGB finita, valori 0..1: e' li' che le LUT "look"
si aspettano di lavorare. Dopo tutto il resto, perche' e' una scelta di resa,
non una correzione: il grezzo resta com'e' e il look si cambia rielaborando.

Memoria: a 2100x1400 l'interpolazione trilineare chiede otto campioni per
pixel. Fatta tutta insieme sono centinaia di MB, troppi per un processo a 32
bit; si fa a pezzi di righe.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

import numpy as np

ESTENSIONE = ".cube"
ESTENSIONI = (".cube", ".lut", ".3dl")


class Lut:
    def __init__(self, nome: str, tabella: np.ndarray, tipo: str,
                 minimo: np.ndarray, massimo: np.ndarray, titolo: str = ""):
        self.nome = nome
        self.tabella = tabella          # 3D: (N, N, N, 3) indicizzata [b, g, r]
        self.tipo = tipo                # "3d" | "1d"
        self.minimo = minimo
        self.massimo = massimo
        self.titolo = titolo

    @property
    def dimensione(self) -> int:
        return int(self.tabella.shape[0])


def leggi_cube(testo: str, nome: str = "") -> Lut:
    """Una LUT dal testo di un file .cube. ValueError se il file non torna:
    meglio dirlo al caricamento che produrre colori sbagliati in silenzio."""
    dim3 = dim1 = None
    minimo = [0.0, 0.0, 0.0]
    massimo = [1.0, 1.0, 1.0]
    titolo = ""
    righe: List[List[float]] = []
    for n, riga in enumerate(testo.splitlines(), start=1):
        riga = riga.strip()
        if not riga or riga.startswith("#"):
            continue
        parti = riga.split()
        chiave = parti[0].upper()
        if chiave == "TITLE":
            titolo = riga[5:].strip().strip('"')
            continue
        if chiave == "LUT_3D_SIZE":
            dim3 = int(parti[1])
            continue
        if chiave == "LUT_1D_SIZE":
            dim1 = int(parti[1])
            continue
        if chiave == "DOMAIN_MIN":
            minimo = [float(x) for x in parti[1:4]]
            continue
        if chiave == "DOMAIN_MAX":
            massimo = [float(x) for x in parti[1:4]]
            continue
        if chiave in ("LUT_3D_INPUT_RANGE", "LUT_1D_INPUT_RANGE"):
            minimo = [float(parti[1])] * 3
            massimo = [float(parti[2])] * 3
            continue
        if re.match(r"^[A-Z_]+$", chiave):
            continue            # altre parole chiave: non servono
        try:
            righe.append([float(x) for x in parti[:3]])
        except ValueError:
            raise ValueError("riga %d non valida: %s" % (n, riga[:40]))
        if len(righe[-1]) != 3:
            raise ValueError("riga %d: servono tre valori" % n)

    dati = np.asarray(righe, dtype=np.float32)
    if dim3:
        if not 2 <= dim3 <= 129:
            raise ValueError("LUT_3D_SIZE fuori misura: %d" % dim3)
        if len(dati) != dim3 ** 3:
            raise ValueError("la LUT 3D da %d ha %d righe invece di %d"
                             % (dim3, len(dati), dim3 ** 3))
        tabella = dati.reshape(dim3, dim3, dim3, 3)
        tipo = "3d"
    elif dim1:
        if not 2 <= dim1 <= 65536:
            raise ValueError("LUT_1D_SIZE fuori misura: %d" % dim1)
        if len(dati) != dim1:
            raise ValueError("la LUT 1D da %d ha %d righe" % (dim1, len(dati)))
        tabella = dati
        tipo = "1d"
    else:
        raise ValueError("manca LUT_3D_SIZE o LUT_1D_SIZE: non e' un .cube")
    mn = np.asarray(minimo, dtype=np.float32)
    mx = np.asarray(massimo, dtype=np.float32)
    if np.any(mx <= mn):
        raise ValueError("DOMAIN_MAX deve stare sopra DOMAIN_MIN")
    return Lut(nome, tabella, tipo, mn, mx, titolo)


def _numeri(testo: str):
    """Le righe con soli numeri, e l'intestazione "LUT: canali voci" se c'e'."""
    righe, testa = [], None
    for riga in testo.splitlines():
        riga = riga.strip()
        if not riga or riga.startswith("#"):
            continue
        m = re.match(r"^LUT\s*:\s*(\d+)\s+(\d+)", riga, re.I)
        if m:
            testa = (int(m.group(1)), int(m.group(2)))
            continue
        try:
            righe.append([float(x) for x in riga.split()])
        except ValueError:
            continue            # parole chiave sconosciute: si saltano
    return righe, testa


def _scala(valori: np.ndarray) -> float:
    """Il fondo scala dal valore piu' alto: 1, 255, 1023, 4095, 65535."""
    mx = float(np.max(valori)) if valori.size else 1.0
    for fondo in (1.0, 255.0, 1023.0, 4095.0, 65535.0):
        if mx <= fondo * 1.0001:
            return fondo
    raise ValueError("valori fuori scala (massimo %g)" % mx)


def leggi_1d_discreet(testo: str, nome: str = "") -> Lut:
    righe, testa = _numeri(testo)
    if not righe:
        raise ValueError("nessun valore")
    if all(len(r) >= 3 for r in righe):
        dati = np.asarray([r[:3] for r in righe], dtype=np.float32)
    elif all(len(r) == 1 for r in righe):
        v = np.asarray([r[0] for r in righe], dtype=np.float32)
        canali = testa[0] if testa else (3 if len(v) % 3 == 0 and len(v) // 3 in
                                          (256, 1024, 4096, 16384, 65536) else 1)
        if canali == 3:
            if len(v) % 3:
                raise ValueError("%d valori: non si dividono in tre canali" % len(v))
            dati = v.reshape(3, -1).T.copy()
        elif canali == 1:
            dati = np.repeat(v[:, None], 3, axis=1)
        else:
            raise ValueError("LUT 1D con %d canali: non gestita" % canali)
    else:
        raise ValueError("righe con un numero di valori diverso")
    if testa and len(dati) != testa[1]:
        raise ValueError("l'intestazione dice %d voci, ce ne sono %d"
                         % (testa[1], len(dati)))
    if not 2 <= len(dati) <= 65536:
        raise ValueError("LUT 1D di %d voci: fuori misura" % len(dati))
    dati = dati / _scala(dati)
    return Lut(nome, dati.astype(np.float32), "1d",
               np.zeros(3, np.float32), np.ones(3, np.float32), "Discreet 1D")


def leggi_3dl(testo: str, nome: str = "") -> Lut:
    righe, _ = _numeri(testo)
    if not righe or len(righe[0]) < 3:
        raise ValueError("manca la riga della griglia")
    griglia = righe[0]
    n = len(griglia)
    terne = [r for r in righe[1:] if len(r) == 3]
    if not 2 <= n <= 129 or len(terne) != n ** 3:
        raise ValueError(".3dl: griglia da %d, %d terne invece di %d"
                         % (n, len(terne), n ** 3))
    dati = np.asarray(terne, dtype=np.float32)
    dati = dati / _scala(dati)
    # nel .3dl il blu cambia piu' in fretta: [r, g, b] -> la nostra [b, g, r]
    tabella = dati.reshape(n, n, n, 3).transpose(2, 1, 0, 3).copy()
    return Lut(nome, tabella, "3d", np.zeros(3, np.float32),
               np.ones(3, np.float32), "Autodesk 3D")


def leggi(testo: str, nome: str = "") -> Lut:
    """Qualunque dei formati gestiti, riconosciuto dal contenuto."""
    if re.search(r"^\s*LUT_(3D|1D)_SIZE\b", testo, re.M | re.I):
        return leggi_cube(testo, nome)
    righe, testa = _numeri(testo)
    if not righe:
        raise ValueError("file vuoto o non numerico: non e' una LUT")
    if testa is not None or all(len(r) == 1 for r in righe):
        return leggi_1d_discreet(testo, nome)
    prima = righe[0]
    if len(prima) > 3 and all(float(x).is_integer() for x in prima):
        return leggi_3dl(testo, nome)
    if all(len(r) == 3 for r in righe):
        return leggi_1d_discreet(testo, nome)
    raise ValueError("formato non riconosciuto: mandare il file per vederlo")


# --- i profili del programma originale (CMMProfiles\*.lut) ---------------
#
# Formato ricavato dai file (vedi documentazione/software-originale/
# elaborazione-originale.md): intestazione di 5 int32 (magia 0x9E62A68F,
# 20 = lunghezza dell'intestazione, tipo, nodi per lato, bit), poi N^3 voci
# di 4 uint16 (tre valori e un riempimento). Tipo 1 = da RGB a Lab: gli assi
# sono R (il piu' lento), G, B su un RGB positivo in codifica sRGB, e il Lab
# e' quello ICC (bianco D50) a 15 bit: L = v/32767*100, a/b = (v-16384)/128.
# Verificato ricostruendo l'identita' da sRGB2Lab_LUT.lut (errore medio
# 0,004 su 0..1). Qui si compone con Lab -> sRGB e diventa una LUT 3D da
# sRGB a sRGB come le altre: Negative2Lab_P8_neg.lut da' la resa di stampa
# Gretag. I file restano del proprietario: non vanno nel repository.
MAGIA_GRETAG = 0x9E62A68F

_BRADFORD_D50_D65 = np.array([[0.9555766, -0.0230393, 0.0631636],
                              [-0.0282895, 1.0099416, 0.0210077],
                              [0.0122982, -0.0204830, 1.3299098]])
_XYZ_SRGB = np.array([[3.2406, -1.5372, -0.4986],
                      [-0.9689, 1.8758, 0.0415],
                      [0.0557, -0.2040, 1.0570]])


def _lab_d50_a_srgb(lab: np.ndarray) -> np.ndarray:
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16.0) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0

    def finv(t):
        return np.where(t > 6.0 / 29.0, t ** 3, 3 * (6.0 / 29.0) ** 2 * (t - 4.0 / 29.0))
    xyz = np.stack([0.9642 * finv(fx), finv(fy), 0.8249 * finv(fz)], axis=-1)
    lin = np.clip(xyz @ _BRADFORD_D50_D65.T @ _XYZ_SRGB.T, 0.0, 1.0)
    return np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * lin ** (1 / 2.4) - 0.055)


def leggi_gretag(dati: bytes, nome: str = "") -> Lut:
    """Un profilo CMM del programma originale (tipo 1, verso Lab) come LUT
    3D da sRGB a sRGB."""
    if len(dati) < 20:
        raise ValueError("file troppo corto per un profilo Gretag")
    magia, testa, tipo, nodi, bit = np.frombuffer(dati[:20], "<u4").tolist()
    if magia != MAGIA_GRETAG or testa != 20:
        raise ValueError("non e' un profilo Gretag")
    if tipo != 1:
        raise ValueError("profilo Gretag di tipo %d: si usano solo quelli verso "
                         "Lab (tipo 1, per esempio Negative2Lab_P8_neg.lut)" % tipo)
    if not 2 <= nodi <= 129 or len(dati) != 20 + nodi ** 3 * 8:
        raise ValueError("profilo Gretag con dimensioni che non tornano")
    t = np.frombuffer(dati[20:], "<u2").reshape(nodi, nodi, nodi, 4)[..., :3]
    t = t.astype(np.float64)
    pieno = float((1 << bit) - 1) if bit else 32767.0
    lab = np.stack([t[..., 0] / pieno * 100.0,
                    (t[..., 1] - (pieno + 1) / 2) / ((pieno + 1) / 256.0),
                    (t[..., 2] - (pieno + 1) / 2) / ((pieno + 1) / 256.0)], axis=-1)
    rgb = _lab_d50_a_srgb(lab)
    # la tabella Gretag e' [r, g, b]; le nostre sono [b, g, r]
    tabella = np.ascontiguousarray(rgb.transpose(2, 1, 0, 3)).astype(np.float32)
    return Lut(nome, tabella, "3d", np.zeros(3, np.float32), np.ones(3, np.float32),
               titolo="profilo Gretag %s" % os.path.splitext(nome)[0])


def carica(percorso: str) -> Lut:
    with open(percorso, "rb") as f:
        dati = f.read()
    if len(dati) >= 4 and int.from_bytes(dati[:4], "little") == MAGIA_GRETAG:
        return leggi_gretag(dati, os.path.basename(percorso))
    return leggi(dati.decode("utf-8", errors="replace"), os.path.basename(percorso))


def elenco(cartella: str) -> List[str]:
    try:
        return sorted(f for f in os.listdir(cartella)
                      if f.lower().endswith(ESTENSIONI))
    except OSError:
        return []


def nome_valido(nome: str) -> str:
    """Il nome con cui si salva una LUT caricata: niente percorsi e niente
    caratteri che Windows rifiuta."""
    # il browser manda il solo nome, ma qualcuno incolla un percorso di
    # Windows: si tiene l'ultimo pezzo, con qualunque barra
    nome = re.split(r"[\\/]", str(nome or ""))[-1].strip()
    nome = re.sub(r'[\\/:*?"<>|]+', "_", nome)
    if not nome.lower().endswith(ESTENSIONI):
        nome += ESTENSIONE
    if nome.lower() in ESTENSIONI:
        raise ValueError("nome della LUT vuoto")
    return nome


def _pezzo_3d(x: np.ndarray, lut: Lut) -> np.ndarray:
    n = lut.dimensione
    t = lut.tabella
    p = (x - lut.minimo) / (lut.massimo - lut.minimo)
    p = np.clip(p, 0.0, 1.0) * (n - 1)
    i0 = np.floor(p).astype(np.int32)
    np.minimum(i0, n - 2, out=i0)
    f = p - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    r1, g1, b1 = r0 + 1, g0 + 1, b0 + 1
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    # la tabella e' [b, g, r]: il rosso cambia piu' in fretta nel file
    c00 = t[b0, g0, r0] * (1 - fr) + t[b0, g0, r1] * fr
    c01 = t[b0, g1, r0] * (1 - fr) + t[b0, g1, r1] * fr
    c10 = t[b1, g0, r0] * (1 - fr) + t[b1, g0, r1] * fr
    c11 = t[b1, g1, r0] * (1 - fr) + t[b1, g1, r1] * fr
    c0 = c00 * (1 - fg) + c01 * fg
    c1 = c10 * (1 - fg) + c11 * fg
    return c0 * (1 - fb) + c1 * fb


def _pezzo_1d(x: np.ndarray, lut: Lut) -> np.ndarray:
    n = lut.dimensione
    out = np.empty_like(x)
    asse = np.linspace(0.0, 1.0, n, dtype=np.float32)
    for c in range(3):
        p = (x[..., c] - lut.minimo[c]) / (lut.massimo[c] - lut.minimo[c])
        out[..., c] = np.interp(np.clip(p, 0.0, 1.0), asse, lut.tabella[:, c])
    return out


def applica(img: np.ndarray, lut: Lut, intensita: float = 1.0,
            righe: int = 96) -> np.ndarray:
    """L'immagine (h, w, 3) 0..1 passata nella LUT. intensita' 0..1 miscela
    con l'originale: una LUT forte si puo' usare a meta'."""
    intensita = float(min(max(intensita, 0.0), 1.0))
    if intensita <= 0.0 or img.ndim != 3 or img.shape[2] != 3:
        return img
    x = img.astype(np.float32, copy=False)
    out = np.empty_like(x)
    pezzo = _pezzo_3d if lut.tipo == "3d" else _pezzo_1d
    for y in range(0, x.shape[0], righe):
        blocco = pezzo(x[y:y + righe], lut)
        if intensita < 1.0:
            blocco = x[y:y + righe] + (blocco - x[y:y + righe]) * intensita
        out[y:y + righe] = blocco
    np.clip(out, 0.0, 1.0, out=out)
    return out
