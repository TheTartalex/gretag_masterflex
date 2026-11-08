# -*- coding: utf-8 -*-
"""
Digital ICE dal canale infrarosso, in numpy: l'algoritmo di Applied Science
Fiction (Nikon Scan 4) come l'ha ricostruito openICE, e sopra le nostre
migliorie.

Fonti (8/11/2026):
- openICE, https://github.com/marcinz606/openICE (GPL-3.0): la
  ricostruzione bit per bit del Digital ICE di Nikon Scan, con la
  descrizione dell'algoritmo (docs/pipeline.md). Il binario in C lo usa
  ice_openice.py.
- nkscan, src/dust.rs (MIT o Apache-2.0, https://docs.rs/crate/nkscan):
  la stessa pipeline riscritta in Rust. Questo modulo ne e' la traduzione,
  passo per passo e con gli stessi nomi (gate, confidence, decide,
  pyramids, reconstruct_core), con le convoluzioni fatte sull'immagine
  intera invece che pixel per pixel. Le costanti sono le loro.

Due motori:
- "aperto": la traduzione di nkscan, senza aggiunte (il riferimento);
- "filmscan": la stessa base piu' quello che sul banco di prova
  (8/11/2026, difetti noti su un fotogramma vero) ha aiutato davvero:
  1. l'infrarosso allineato al rosso prima di confrontarli (l'IR mette a
     fuoco in un punto un po' diverso e si legge a parte);
  2. "denso": dove l'IR perde piu' di 0,30 D la polvere e' quasi opaca e
     sotto non c'e' piu' luce da recuperare: li' si ricostruisce dai pixel
     attorno (diffusione di irclean) con meta' della grana misurata
     intorno, invece della correzione dall'IR. Errori grossolani (oltre
     0,15 D) dal 2,4% di openICE all'1,5%, errore medio piu' basso.
  Provate e scartate: aloni chiari come difetto e minimo su 3x3 (peggio),
  ripiego dove ICE "rinuncia" (mai scattato), grana al posto del rumore di
  Nikon (numeri uguali).

Lavora sui piani calibrati (trasmittanza 0..1) prima dell'allineamento dei
canali: pulisce R, G, B e toglie l'IR, che non serve piu'.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np

try:
    from scipy import ndimage
    HAS_SCIPY = True
except ImportError:                                     # pragma: no cover
    HAS_SCIPY = False

# --- costanti (nkscan, dust.rs) --------------------------------------------
M = 65535.0
TAU = 8847.23               # IR grezzo sopra il quale la pellicola e' "chiara"
SLOPE_LIMIT = 0.2           # pendenze rosso -> IR plausibili
W_FLOOR = 0.02
B_ANCHOR = 0.98
PHI_ANCHOR = 0.065
MIN3_DPI = 550
DETAIL_GAIN = {"normal": 1.25, "fine": 1.0}
ALPHA = (0.015, 0.015, 0.025)

# i profili ("kind") di Nikon Scan: 8 = LS-5000 (quello di serie di openICE)
PROFILI = {
    7: dict(theta=0.0, gamma=(1.1, 1.1, 1.1), dither=(0.04, 0.96), contrast_dpi=950,
            ramp=-960.42,
            a=(((1.360, 1.320), (1.370, 1.300), (1.340, 1.250)),
               ((1.370, 1.300), (1.350, 1.290), (1.300, 1.240)),
               ((1.340, 1.250), (1.320, 1.250), (1.250, 1.210)))),
    8: dict(theta=1.0, gamma=(1.1, 1.1, 1.1), dither=(0.01, 0.99), contrast_dpi=1600,
            ramp=-960.42,
            a=(((1.210, 1.090), (1.170, 1.080), (1.040, 0.960)),
               ((1.230, 1.130), (1.140, 1.050), (0.930, 0.840)),
               ((1.130, 1.040), (1.080, 1.020), (0.970, 0.890)))),
    9: dict(theta=1.0, gamma=(1.0, 1.0, 1.0), dither=(0.01, 0.99), contrast_dpi=2500,
            ramp=-960.52,
            a=(((2.210, 2.090), (2.170, 2.080), (2.040, 1.960)),
               ((2.230, 2.130), (2.140, 2.050), (1.930, 1.840)),
               ((2.130, 2.040), (2.080, 2.020), (1.970, 1.890)))),
}

# dove mettere la pellicola chiara nell'IR normalizzato (migliorie, punto 2)
LIVELLO_IR = 0.98


def _ottagono(r_max: int, r_somma: int) -> np.ndarray:
    """max(|dx|,|dy|) <= r_max e |dx|+|dy| <= r_somma (LEVEL0: 4 e 6, 69
    celle; LEVEL1: 2 e 3, 21 celle), peso uniforme."""
    y, x = np.mgrid[-r_max:r_max + 1, -r_max:r_max + 1]
    k = ((np.abs(x) <= r_max) & (np.abs(y) <= r_max) & (np.abs(x) + np.abs(y) <= r_somma))
    k = k.astype(np.float32)
    return k / k.sum()


KERNEL = (_ottagono(4, 6), _ottagono(2, 3),
          (np.outer([1.0, 2.0, 1.0], [1.0, 2.0, 1.0]) / 16.0).astype(np.float32))


# --- densita' ----------------------------------------------------------------
def densita(v: np.ndarray) -> np.ndarray:
    """D(v) = M/16 * log2(v + 1), v in [0, M]."""
    return (np.log2(np.asarray(v, dtype=np.float32) + 1.0) * (M / 16.0)).astype(np.float32)


def da_densita(d: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(np.exp2(np.asarray(d, dtype=np.float32) * (16.0 / M)) - 1.0), 0, M)


def _ancora(frazione: float) -> float:
    return float(np.floor(M * frazione))


# --- passo 2: calibrazione ------------------------------------------------
def calibra(rosso: np.ndarray, ir: np.ndarray) -> Optional[Tuple[float, float]]:
    """(c, IR_ref) da una versione ridotta del fotogramma: quanto il
    colorante rosso passa nell'IR e la densita' IR della pellicola chiara
    senza quel passaggio. None se non c'e' pellicola chiara."""
    d_r, d_ir = densita(rosso), densita(ir)
    chiaro = ir > TAU
    if not chiaro.any():
        return None
    w = ir.astype(np.float64) ** 2 * chiaro
    den = w.sum()
    r_ref = float((w * d_r).sum() / den)
    ir_raw = float((w * d_ir).sum() / den)
    h, l = (ir.shape[0] // 8) * 8, (ir.shape[1] // 8) * 8
    def tess(a):
        return a[:h, :l].reshape(h // 8, 8, l // 8, 8).transpose(0, 2, 1, 3)
    ch = tess(chiaro).all(axis=(2, 3))
    if ch.any():
        tr, ti, tirr = tess(d_r)[ch], tess(d_ir)[ch], tess(ir.astype(np.float32))[ch]
        m_r, m_i, s_irr = tr.mean(axis=(1, 2)), ti.mean(axis=(1, 2)), tirr.sum(axis=(1, 2))
        num = den2 = 0.0
        for dy in (0, 4):
            for dx in (0, 4):
                q_r = tr[:, dy:dy + 4, dx:dx + 4].mean(axis=(1, 2)) - m_r
                q_i = ti[:, dy:dy + 4, dx:dx + 4].mean(axis=(1, 2)) - m_i
                with np.errstate(divide="ignore", invalid="ignore"):
                    pend = q_i / q_r
                ok = np.isfinite(pend) & (np.abs(pend) <= SLOPE_LIMIT)
                peso = (q_r.astype(np.float64) ** 2) * (s_irr.astype(np.float64) ** 2)
                num += float((pend[ok] * peso[ok]).sum())
                den2 += float(peso[ok].sum())
        c = num / den2 if den2 > 0 else 0.0
    else:
        c = 0.0
    return c, (ir_raw - c * r_ref) / (1.0 - c)


# --- utilita' -------------------------------------------------------------------
def _correla(a: np.ndarray, k: np.ndarray) -> np.ndarray:
    return ndimage.correlate(a, k, mode="nearest")


def _and3(a: np.ndarray, k: int, asse: int) -> np.ndarray:
    """AND su x-k, x, x+k (o y), bordi ripetuti."""
    n = a.shape[asse]
    idx = np.arange(n)
    lo = np.take(a, np.clip(idx - k, 0, n - 1), axis=asse)
    hi = np.take(a, np.clip(idx + k, 0, n - 1), axis=asse)
    return a & lo & hi


def _sposta_idx(a: np.ndarray, d: int, asse: int) -> np.ndarray:
    n = a.shape[asse]
    return np.take(a, np.clip(np.arange(n) + d, 0, n - 1), axis=asse)


def _uniforme(n_pixel: np.ndarray, canale: int) -> np.ndarray:
    """Uniforme [0, 1) fissa per (pixel, canale): Mix13 di Stafford, come
    nkscan (ripetibile: lo stesso fotogramma esce sempre uguale)."""
    x = (n_pixel.astype(np.uint64) << np.uint64(2)) | np.uint64(canale)
    with np.errstate(over="ignore"):
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xbf58476d1ce4e5b9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94d049bb133111eb)
    x = (x ^ (x >> np.uint64(31))) & np.uint64(0x00ffffff)
    return x.astype(np.float32) / float(1 << 24)


def _gauss(n_pixel: np.ndarray, canale: int) -> np.ndarray:
    """Normale ripetibile (Box-Muller su due uniformi fisse)."""
    u1 = np.clip(_uniforme(n_pixel, canale), 1e-7, 1.0)
    u2 = _uniforme(n_pixel + np.uint64(0x9E3779B9), canale + 3)
    return (np.sqrt(-2.0 * np.log(u1)) * np.cos(2 * np.pi * u2)).astype(np.float32)


# --- la pulizia -------------------------------------------------------------
TUTTE = frozenset(("allinea", "denso"))
SOGLIA_DENSO = 0.30         # perdita IR (densita') oltre la quale si ricostruisce dai vicini
GRANA_DENSO = 0.5           # quanta della grana misurata attorno si rimette


def pulisci_piani(r: np.ndarray, g: np.ndarray, b: np.ndarray, ir: np.ndarray,
                  dpi: int, migliorie=False, kind: int = 8,
                  qualita: str = "normal") -> Tuple[Tuple[np.ndarray, np.ndarray, np.ndarray],
                                                    Dict[str, object]]:
    """R, G, B, IR in trasmittanza (0..1, stessa geometria) -> R, G, B
    puliti e un resoconto. Con migliorie=False e' nkscan tradotto."""
    if not HAS_SCIPY:
        raise RuntimeError("serve scipy")
    prof = PROFILI[int(kind)]
    # migliorie: False (nkscan com'e'), True (tutte) o un insieme di nomi
    # di TUTTE, per provarle una per volta sul banco
    usa = TUTTE if migliorie is True else frozenset(migliorie or ())
    esito: Dict[str, object] = {"motore": "filmscan" if usa else "aperto"}
    forma = r.shape
    colori = [np.clip(np.nan_to_num(np.asarray(p, dtype=np.float32)), 0.0, 1.0) * M
              for p in (r, g, b)]
    ir = np.nan_to_num(np.asarray(ir, dtype=np.float32))

    # migliorie 1: l'IR sul rosso (solo traslazione, sub-pixel)
    if "allinea" in usa:
        dy, dx = _spostamento_ir(colori[0], ir)
        esito["ir_spostato_px"] = [round(dy, 2), round(dx, 2)]
        if abs(dy) > 0.05 or abs(dx) > 0.05:
            ir = ndimage.shift(ir, (dy, dx), order=1, mode="nearest")

    # L'IR in conteggi a 16 bit. Nikon lo vuole con la pellicola chiara
    # vicina al fondo scala (le costanti ci contano). Il nostro e' gia'
    # diviso per la finestra vuota (~0,8-1): lo si porta li', e la
    # differenza dal fondo scala va nel gate (theta_for_metering_target di
    # nkscan). Con le migliorie il livello si misura su questo fotogramma.
    # Come ice_openice: la pellicola chiara (percentile 90) al 98% del
    # fondo scala, nessuna correzione del gate. La correzione di nkscan per
    # un IR esposto sotto il fondo scala (theta_for_metering_target) qui
    # non serve, perche' il livello lo si porta li' noi; anzi spostava la
    # stima della luce persa (banco di prova dell'8/11/2026).
    chiaro = float(np.percentile(ir[::4, ::4], 90))
    scala = LIVELLO_IR * M / max(chiaro, 1e-4)
    obiettivo = 1.0
    ir16 = np.clip(np.rint(ir * scala), 0, M).astype(np.float32)
    esito["scala_ir"] = round(scala, 1)

    # 2. calibrazione su una copia ridotta (Nikon: la prescansione)
    passo = max(1, int(round(dpi / 400.0)))
    def ridotto(a):
        h, l = (a.shape[0] // passo) * passo, (a.shape[1] // passo) * passo
        return a[:h, :l].reshape(h // passo, passo, l // passo, passo).mean(axis=(1, 3))
    cal = calibra(np.rint(ridotto(colori[0])), np.rint(ridotto(ir16)))
    if cal is None:
        raise RuntimeError("nessuna pellicola chiara nell'infrarosso")
    c, ir_ref = cal
    esito["crosstalk"], esito["ir_ref"] = round(c, 4), round(ir_ref, 1)

    theta = prof["theta"] + (float(densita(np.float32(_ancora(obiettivo)))) - M)
    ramp_bias = ir_ref + float(densita(np.float32(_ancora(B_ANCHOR)))) - M
    ramp_s = 1.0 / prof["ramp"]
    phi = float(densita(np.float32(_ancora(PHI_ANCHOR))))
    eta = [float(densita(np.float32(_ancora(f)))) for f in prof["dither"]]
    guadagno = DETAIL_GAIN[qualita]
    clamp_l3 = qualita == "normal"

    # 1 e 3. densita' e gate: l'IR senza il colorante rosso
    d = [densita(p) for p in colori]
    gate = (densita(ir16) - c * d[0]) / (1.0 - c) - theta
    del ir16

    # 4. fiducia che il pixel sia pulito
    if dpi > MIN3_DPI:
        gmin = np.minimum(np.minimum(_sposta_idx(gate, -1, 1), gate), _sposta_idx(gate, 1, 1))
    else:
        gmin = gate
    w = np.clip(1.0 + (ramp_bias - gmin) * ramp_s, W_FLOOR, 1.0).astype(np.float32)
    del gmin

    # 5. dove ricostruire: non dove tutto attorno e' scuro (difetto troppo
    # grande: Digital ICE "rinuncia")
    scuro = gate < phi
    righe = _and3(_and3(scuro, 1, 1), 3, 1)
    colonne = _and3(_and3(scuro, 1, 0), 3, 0)
    rinuncia = (_sposta_idx(righe, -4, 0) | _sposta_idx(righe, 4, 0)
                | _sposta_idx(colonne, -4, 1) | _sposta_idx(colonne, 4, 1))
    maschera = ~rinuncia
    if clamp_l3:
        maschera &= w < 1.0
    del righe, colonne

    # 6. le piramidi: convoluzioni normalizzate dalla fiducia
    P, C, L = [None] * 4, [None] * 4, [[None] * 4 for _ in range(3)]
    wg = w * gate
    for liv, k in enumerate(KERNEL):
        C[liv] = _correla(w, k)
        inv = np.where(C[liv] > 0, 1.0 / np.maximum(C[liv], 1e-12), 0.0).astype(np.float32)
        P[liv] = _correla(wg, k) * inv
        for ch in range(3):
            L[ch][liv] = _correla(w * d[ch], k) * inv
    C[3], P[3] = w, gate
    for ch in range(3):
        L[ch][3] = d[ch]
    del wg

    sel = np.flatnonzero(maschera.ravel())
    esito["ricostruiti"] = int(sel.size)
    esito["rinuncia_pct"] = round(float((rinuncia & (w < 0.5)).mean()) * 100, 3)
    pr = lambda a: a.ravel()[sel]
    lo = [None] + [pr(P[l]) - pr(P[l - 1]) for l in (1, 2, 3)]
    hi = [None] + [x.copy() for x in lo[1:]]
    if dpi > prof["contrast_dpi"]:
        for l in (1, 2, 3):
            banda = P[l] - P[l - 1]
            for dy, dx in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                v = pr(_sposta_idx(_sposta_idx(banda, dy, 0), dx, 1))
                lo[l] = np.minimum(lo[l], v)
                hi[l] = np.maximum(hi[l], v)

    # 7 e 8. ricostruzione, grana, tetto
    acc = []
    for ch in range(3):
        a = pr(L[ch][0]) + prof["gamma"][ch] * (ir_ref - pr(P[0]))
        for l in (1, 2, 3):
            dettaglio = (pr(L[ch][l]) - pr(L[ch][l - 1])) * guadagno
            a_hi, a_lo = prof["a"][ch][l - 1]
            neg = hi[l] < 0.0
            a_lo_e = np.where(neg, a_hi, a_lo)
            a_hi_e = np.where(neg, a_lo, a_hi)
            lo_t, hi_t = a_lo_e * lo[l], a_hi_e * hi[l]
            rr = np.where(dettaglio < lo_t, dettaglio - lo_t,
                          np.where(dettaglio > hi_t, dettaglio - hi_t, 0.0))
            if l == 1:
                fid = np.minimum(2.0 * pr(C[1]), 1.0)
            elif l == 2:
                fid = pr(C[2])
            else:
                fid = pr(C[3]) ** 2
            a = a + rr * fid
        # il rumore di Nikon sui pixel rifatti (dither), ripetibile
        lo_e, hi_e = eta
        inside = (a > lo_e) & (a < hi_e)
        env = 4.0 / (hi_e - lo_e) ** 2 * (a - lo_e) * (hi_e - a)
        dd = env * (_uniforme(sel.astype(np.uint64), ch) - 0.5) * ALPHA[ch] * a
        ok = inside & (a + dd > lo_e) & (a + dd < hi_e)
        a = a + np.where(ok, dd, 0.0)
        acc.append(a.astype(np.float32))
    tieni = np.ones(sel.size, dtype=bool)
    if clamp_l3:
        tieni = (acc[0] > 0) & (acc[1] > 0) & (acc[2] > 0)
        acc = [np.maximum(a, pr(L[ch][3])) for ch, a in enumerate(acc)]
    del P, C, L

    # 9. di nuovo lineare
    fuori = []
    for ch in range(3):
        piano = colori[ch].ravel().copy()
        v = da_densita(acc[ch][tieni])
        piano[sel[tieni]] = np.maximum(v, piano[sel[tieni]]) if clamp_l3 else v
        fuori.append(piano.reshape(forma))

    # "denso": dove l'IR dice che la polvere e' quasi opaca, dai vicini
    if "denso" in usa:
        perdita = -np.log10(np.clip(ir / max(chiaro, 1e-6), 1e-3, None))
        denso = ndimage.binary_dilation(perdita > SOGLIA_DENSO, iterations=1)
        if denso.any() and denso.mean() < 0.05:
            from .irclean import inpaint
            img = np.stack([f / M for f in fuori], axis=-1)
            pieno = inpaint(img, denso, strength=1.0)
            attorno = ~ndimage.binary_dilation(denso, iterations=3)
            idx = np.flatnonzero(denso.ravel())
            for ch in range(3):
                dd = np.log10(np.clip(fuori[ch] / M, 1e-5, None))
                fine = (dd - ndimage.gaussian_filter(dd, 1.0)) * attorno
                v = ndimage.uniform_filter(fine * fine, size=15, mode="nearest")
                n = ndimage.uniform_filter(attorno.astype(np.float32), size=15, mode="nearest")
                grana = np.sqrt(np.maximum(v / np.maximum(n, 1e-3), 0.0)).ravel()[idx]
                nuovo = np.log10(np.clip(pieno[..., ch].ravel()[idx], 1e-5, None))
                nuovo = nuovo + GRANA_DENSO * grana * _gauss(idx.astype(np.uint64), ch)
                p = fuori[ch].ravel()
                p[idx] = np.clip(10.0 ** nuovo, 0, 1) * M
                fuori[ch] = p.reshape(forma)
            esito["denso_px"] = int(denso.sum())
    out = tuple((f / M).astype(np.float32) for f in fuori)
    cambiati = np.zeros(forma, dtype=bool)
    for ch in range(3):
        cambiati |= np.abs(out[ch] - colori[ch] / M) > 0.002
    esito["cambiati_pct"] = round(float(cambiati.mean()) * 100, 3)
    return out, esito


def _spostamento_ir(rosso16: np.ndarray, ir: np.ndarray) -> Tuple[float, float]:
    """Di quanto spostare l'IR per sovrapporlo al rosso. L'IR non ha
    immagine, ma ha i bordi del fotogramma, le perforazioni e la polvere,
    che ci sono anche nel rosso: la correlazione di fase di registration.py
    (stessa preparazione, stesso verso: ndimage.shift(ir, (dy, dx))). Se il
    picco non e' affidabile, nessuno spostamento: meglio niente che uno
    inventato."""
    from .registration import _prep, phase_shift
    try:
        dy, dx, fid = phase_shift(_prep(rosso16 / M), _prep(np.clip(ir, 1e-5, None)),
                                  return_confidence=True)
    except Exception:                                   # noqa: BLE001
        return 0.0, 0.0
    if fid < 0.15 or abs(dy) > 4 or abs(dx) > 4:
        return 0.0, 0.0
    return float(dy), float(dx)


def pulisci(raw, migliorie: bool = False, dpi: Optional[int] = None):
    """Un nuovo RawScan con R, G, B puliti e senza IR, e il resoconto."""
    from .acquire import RawScan
    if not all(c in raw.planes for c in ("R", "G", "B", "IR")):
        raise RuntimeError("servono R, G, B e IR")
    from .irclean import ir_senza_immagine
    if ir_senza_immagine(raw.planes["IR"][0]):
        raise RuntimeError("canale IR saturo, senza immagine")
    piani = [raw.planes[c][0] for c in ("R", "G", "B", "IR")]
    (r, g, b), esito = pulisci_piani(*piani, dpi=int(dpi or raw.meta.dpi or 1569),
                                     migliorie=migliorie)
    nuovo = RawScan(raw.meta)
    for c, p in zip(("R", "G", "B"), (r, g, b)):
        nuovo.add(c, p)
        for extra in raw.planes[c][1:]:
            nuovo.add(c, extra)
    return nuovo, esito
