# -*- coding: utf-8 -*-
"""
Calibrazione e acquisizione.

Regola di fondo: da qui esce SEMPRE il dato piu' grezzo possibile, con accanto i
frame di calibrazione e i metadati. L'elaborazione e' un'altra cosa e vive
altrove, cosi' l'intero archivio si puo' rielaborare senza rimettere la pellicola
nella macchina.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from .hal import ScannerBackend, CHANNELS
from .config import AppConfig, ScanPreset, FilmFormat, mm_to_px


Progress = Optional[Callable[[str, float], None]]


class Annullato(Exception):
    """L'operatore ha premuto STOP.

    Non e' un errore della macchina e non deve finire fra gli errori del
    lavoro: e' una decisione presa da chi guarda la pellicola. Si propaga come
    eccezione perche' l'acquisizione di un fotogramma e' fatta di anelli
    annidati - canali, esposizioni, passaggi - e uscirne con dei flag
    vorrebbe dire tre controlli per anello e uno dimenticato.
    """


def _controlla(annulla) -> None:
    """Punto di uscita pulito. Si chiama dove fermarsi non rompe niente."""
    if annulla is not None and annulla():
        raise Annullato("interrotto dall'operatore")


def _tick(cb: Progress, msg: str, frac: float) -> None:
    if cb:
        cb(msg, max(0.0, min(1.0, frac)))


# --------------------------------------------------------------------------
# Calibrazione
# --------------------------------------------------------------------------

@dataclass
class Calibration:
    """Dark frame e flat field per canale.

    dark : lettura a lampada spenta -> corrente di buio e offset dell'ADC
    flat : lettura a luce piena senza pellicola -> vignettatura dell'illuminazione
           e differenze di sensibilita' fra i pixel del CCD

    Su una macchina di trent'anni questi valori NON sono quelli di fabbrica.
    Vanno rifatti, e ripetuti ogni volta che la lampada viene sostituita o
    dopo un periodo lungo di inattivita'.
    """
    dark: Dict[str, np.ndarray]
    flat: Dict[str, np.ndarray]
    created: float
    note: str = ""

    def save(self, directory: str) -> str:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "calib_%d.npz" % int(self.created))
        payload = {}
        for c, a in self.dark.items():
            payload["dark_" + c] = a
        for c, a in self.flat.items():
            payload["flat_" + c] = a
        np.savez_compressed(path, created=self.created, note=self.note, **payload)
        return path

    @classmethod
    def load(cls, path: str) -> "Calibration":
        z = np.load(path, allow_pickle=False)
        dark = {k[5:]: z[k] for k in z.files if k.startswith("dark_")}
        flat = {k[5:]: z[k] for k in z.files if k.startswith("flat_")}
        return cls(dark, flat, float(z["created"]), str(z["note"]))

    @classmethod
    def latest(cls, directory: str) -> Optional["Calibration"]:
        if not os.path.isdir(directory):
            return None
        files = sorted(f for f in os.listdir(directory)
                       if f.startswith("calib_") and f.endswith(".npz"))
        return cls.load(os.path.join(directory, files[-1])) if files else None

    def age_days(self) -> float:
        return (time.time() - self.created) / 86400.0


def run_calibration(backend: ScannerBackend, channels=CHANNELS,
                    lines: int = 64, repeats: int = 8,
                    progress: Progress = None, annulla=None) -> Calibration:
    """Esegue la procedura di calibrazione.

    L'operatore deve aver tolto la pellicola dal percorso ottico prima di lanciarla.
    Le letture vengono mediate su piu' ripetizioni per abbattere il rumore: questi
    frame vengono sottratti da OGNI scansione futura, quindi devono essere puliti.

    Se il backend non sa spegnere la lampada (l'SDK della macchina vera non lo
    espone) il dark frame non si puo' leggere: prima si leggeva lo stesso, a
    lampada accesa, e flat - dark veniva quasi zero. Adesso il dark resta a
    zero e la calibrazione lo scrive nella nota. Si leggono solo i canali che
    il backend sa acquisire.
    """
    dark: Dict[str, np.ndarray] = {}
    flat: Dict[str, np.ndarray] = {}
    channels = [c for c in channels
                if c in getattr(backend, "canali_disponibili", CHANNELS)]
    buio = getattr(backend, "controlla_lampada", True)
    total = len(channels) * (2 if buio else 1)

    for i, ch in enumerate(channels if buio else []):
        _tick(progress, "Dark frame %s" % ch, i / total)
        backend.lamp(ch, False)
        acc = np.zeros(0)
        for r in range(repeats):
            _controlla(annulla)
            block = backend.capture_line_block(ch, lines).astype(np.float64)
            m = block.mean(axis=0)
            acc = m if acc.size == 0 else acc + m
        dark[ch] = acc / repeats

    fatti = len(dark)
    for i, ch in enumerate(channels):
        _tick(progress, "Flat field %s" % ch, (fatti + i) / total)
        backend.lamp(ch, True)
        acc = np.zeros(0)
        for r in range(repeats):
            _controlla(annulla)
            block = backend.capture_line_block(ch, lines).astype(np.float64)
            m = block.mean(axis=0)
            acc = m if acc.size == 0 else acc + m
        media = acc / repeats
        if ch not in dark:
            dark[ch] = np.zeros_like(media)
        flat[ch] = media - dark[ch]

    _tick(progress, "Calibrazione completata", 1.0)
    nota = "" if buio else "senza dark frame: il backend non controlla la lampada"
    return Calibration(dark, flat, time.time(), nota)


def apply_calibration(raw: np.ndarray, channel: str,
                      calib: Optional[Calibration]) -> np.ndarray:
    """Da unita' ADC grezze a trasmittanza relativa (0..1 circa).

    Senza calibrazione ritorna una semplice normalizzazione a fondo scala:
    utile per le prove, inadeguato per il risultato finale.
    """
    x = raw.astype(np.float32)
    if calib is None or channel not in calib.dark:
        return x / max(x.max(), 1.0)

    # Una calibrazione fatta con un sensore di larghezza diversa non e'
    # utilizzabile, e va detto invece di lasciare esplodere numpy con un
    # messaggio sulle forme che non si possono trasmettere.
    #
    # Non e' un caso di scuola: quando abbiamo corretto la risoluzione da 5600
    # a 2100 pixel, ogni calibrazione salvata prima e' diventata inservibile.
    # Chi ha un file vecchio sul disco lo scopre esattamente qui.
    larghezza = calib.dark[channel].shape[-1]
    if larghezza != x.shape[-1]:
        raise ValueError(
            "La calibrazione salvata e' larga %d pixel, il sensore ne legge %d. "
            "E' di una configurazione diversa e non si puo' usare: rifare la "
            "calibrazione (Calibra, con il percorso pellicola vuoto)."
            % (larghezza, x.shape[-1]))

    dark = calib.dark[channel][None, :].astype(np.float32)
    flat = calib.flat[channel][None, :].astype(np.float32)
    flat = np.where(np.abs(flat) < 1e-6, 1.0, flat)
    out = (x - dark) / flat
    return np.clip(out, 1e-5, None)



# --------------------------------------------------------------------------
# Esposizione per canale
# --------------------------------------------------------------------------

def measure_channel_exposure(backend: ScannerBackend, channels: List[str],
                             calib: Optional[Calibration] = None,
                             lines: int = 24, target: float = 0.80,
                             limits: Tuple[float, float] = (0.25, 12.0),
                             progress: Progress = None,
                             annulla=None) -> Dict[str, float]:
    """Misura quanto esporre ciascun canale, con una pre-scansione veloce.

    Perche' serve: la maschera arancione del negativo assorbe molto il blu.
    A parita' di tempo di esposizione il canale B arriva al sensore molto piu'
    debole di R e G, quindi usa una frazione ridotta della scala dell'ADC e
    viene su rumoroso. Il rumore nel blu, dopo l'inversione, si vede eccome.

    Il rimedio e' banale e la macchina lo permette gia': esporre ogni canale
    per il tempo che gli serve, invece di dare a tutti lo stesso.

    Si legge un blocco corto per canale, si guarda dove cade il percentile alto
    del segnale e si calcola il fattore che lo porterebbe a 'target' della
    scala. Il target sta sotto 1 di proposito: si lascia margine perche' il
    fotogramma vero puo' essere piu' chiaro del punto campionato, e saturare
    significa perdere informazione in modo irreversibile.
    """
    gains: Dict[str, float] = {}
    lo, hi = limits
    channels = [c for c in channels
                if c in getattr(backend, "canali_disponibili", CHANNELS)]

    for i, ch in enumerate(channels):
        _tick(progress, "Misura esposizione %s" % ch, i / max(len(channels), 1))
        _controlla(annulla)
        backend.lamp(ch, True)
        block = backend.capture_line_block(ch, lines, exposure=1.0)
        cal = apply_calibration(block, ch, calib)
        backend.lamp(ch, False)

        level = float(np.percentile(cal, 99.0))
        if level < 1e-5:
            gains[ch] = hi          # canale quasi cieco: esponi il massimo
            continue
        gains[ch] = float(np.clip(target / level, lo, hi))

    _tick(progress, "Esposizioni misurate", 1.0)
    return gains


# --------------------------------------------------------------------------
# Fine striscia
# --------------------------------------------------------------------------

def film_present(backend: ScannerBackend, calib: Optional[Calibration] = None,
                 channel: str = "G", lines: int = 16,
                 threshold: float = 0.92) -> bool:
    """Dice se c'e' ancora pellicola nel percorso ottico.

    Serve perche' le strisce hanno lunghezza variabile: non si puo' sapere in
    anticipo quanti fotogrammi ci sono, e continuare a comandare avanzamenti
    dopo l'ultimo significa tirare pellicola nel vuoto.

    Come si riconosce: senza pellicola la luce arriva al sensore quasi intatta
    e, soprattutto, in modo uniforme. Anche il fotogramma piu' chiaro di una
    striscia ha una base pellicola che assorbe, e ha variazione. Si guardano
    quindi entrambe le cose - livello alto E varianza bassa - perche' il solo
    livello confonderebbe un fotogramma sovraesposto con l'assenza di pellicola.
    """
    backend.lamp(channel, True)
    block = backend.capture_line_block(channel, lines, exposure=1.0)
    cal = apply_calibration(block, channel, calib)
    backend.lamp(channel, False)

    livello = float(np.median(cal))
    variazione = float(cal.std())
    return not (livello > threshold and variazione < 0.02)


# --------------------------------------------------------------------------
# Acquisizione
# --------------------------------------------------------------------------

@dataclass
class ScanMetadata:
    timestamp: float
    format_key: str
    preset_name: str
    dpi: int
    bit_depth: int
    channels: List[str]
    exposures: List[float]
    passes_per_channel: int
    frame_index: int
    position_mm: float
    focus_mm: float
    channel_exposure: Optional[Dict[str, float]] = None
    monochrome: bool = False
    operator_note: str = ""


class RawScan:
    """Il risultato grezzo di un fotogramma: un array per (canale, esposizione).

    Struttura: planes[canale][indice_esposizione] -> array 2D float32
    """

    def __init__(self, meta: ScanMetadata):
        self.meta = meta
        self.planes: Dict[str, List[np.ndarray]] = {}

    def add(self, channel: str, plane: np.ndarray) -> None:
        self.planes.setdefault(channel, []).append(plane)

    def save(self, directory: str, name: str) -> str:
        os.makedirs(directory, exist_ok=True)
        base = os.path.join(directory, name)
        arrays = {}
        for ch, planes in self.planes.items():
            for i, p in enumerate(planes):
                arrays["%s_%d" % (ch, i)] = p
        np.savez_compressed(base + ".npz", **arrays)
        with open(base + ".json", "w", encoding="utf-8") as f:
            json.dump(asdict(self.meta), f, indent=2, ensure_ascii=False)
        return base + ".npz"

    @classmethod
    def load(cls, npz_path: str) -> "RawScan":
        with open(npz_path[:-4] + ".json", encoding="utf-8") as f:
            meta = ScanMetadata(**json.load(f))
        obj = cls(meta)
        z = np.load(npz_path)
        for key in sorted(z.files):
            ch = key.rsplit("_", 1)[0]
            obj.add(ch, z[key])
        return obj


def scan_frame(backend: ScannerBackend, cfg: AppConfig, preset: ScanPreset,
               fmt: FilmFormat, calib: Optional[Calibration] = None,
               frame_index: int = 0, progress: Progress = None,
               channel_exposure: Optional[Dict[str, float]] = None,
               annulla=None) -> RawScan:
    """Acquisisce un singolo fotogramma con tutti i passaggi previsti dal preset.

    Le ripetizioni dello stesso canale vengono mediate qui (riduce il rumore di
    lettura). Le esposizioni diverse restano SEPARATE: la loro fusione e' una
    decisione di elaborazione, non di acquisizione.
    """
    # La risoluzione vera e' quella della macchina: i preset chiedono 2000 o
    # 4000 dpi, ma il sensore ne legge cfg.optical_dpi (1569 sul 135). Prima
    # nei metadati finiva il valore del preset, e il ritaglio automatico
    # cercava un fotogramma di 5669 pixel dentro un'immagine di 2100.
    dpi = min(preset.dpi, cfg.optical_dpi)
    lines = mm_to_px(fmt.frame_w, dpi)
    disponibili = getattr(backend, "canali_disponibili", CHANNELS)
    channels = [c for c in preset.channels if c in disponibili]

    # esposizione per canale: misurata, oppure quella fissata nel preset
    if channel_exposure is None:
        channel_exposure = preset.channel_exposure
    if channel_exposure is None and preset.auto_exposure:
        channel_exposure = measure_channel_exposure(backend, channels, calib,
                                                    annulla=annulla)
    gains = channel_exposure or {}

    st = backend.status()
    meta = ScanMetadata(
        timestamp=time.time(), format_key=fmt.name, preset_name=preset.name,
        dpi=dpi, bit_depth=preset.bit_depth, channels=channels,
        exposures=list(preset.multi_exposure),
        channel_exposure=dict(gains),
        monochrome=preset.monochrome,
        passes_per_channel=preset.passes_per_channel,
        frame_index=frame_index, position_mm=st.position_mm, focus_mm=st.focus_mm)
    scan = RawScan(meta)

    total = len(channels) * len(preset.multi_exposure)
    done = 0
    start_mm = st.position_mm

    def rewind() -> None:
        """Riporta il trasporto alla posizione di partenza del fotogramma.

        Si riposiziona in assoluto rispetto alla posizione misurata, non con un
        riavvolgimento fisso: acquisire consuma pellicola e passaggi diversi
        possono consumarne quantita' leggermente diverse. Riavvolgere di una
        quantita' fissa fa scivolare i canali l'uno rispetto all'altro, ed e'
        proprio il disallineamento che poi si fatica a spiegare.
        """
        delta = start_mm - backend.status().position_mm
        if abs(delta) > 1e-9:
            backend.advance(delta)

    for ch in channels:
        backend.lamp(ch, True)
        for exp in preset.multi_exposure:
            acc = None
            for _ in range(preset.passes_per_channel):
                try:
                    _controlla(annulla)
                except Annullato:
                    # la lampada accesa non si lascia accesa, e la pellicola
                    # si riporta dov'era: la macchina resta in uno stato da
                    # cui si puo' ripartire senza toccare niente a mano.
                    backend.lamp(ch, False)
                    rewind()
                    raise
                rewind()
                block = backend.capture_line_block(
                    ch, lines, exposure=exp * gains.get(ch, 1.0))
                cal = apply_calibration(block, ch, calib)
                acc = cal if acc is None else acc + cal
            scan.add(ch, (acc / preset.passes_per_channel).astype(np.float32))
            done += 1
            _tick(progress, "Canale %s, esposizione %.1fx" % (ch, exp), done / total)
        backend.lamp(ch, False)

    rewind()
    return scan
