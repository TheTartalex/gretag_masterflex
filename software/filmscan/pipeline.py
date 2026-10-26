# -*- coding: utf-8 -*-
"""
Orchestrazione: dal grezzo al file finale, e scansione automatica del rullo.

Ordine delle operazioni, che NON e' arbitrario:

  1. calibrazione (gia' applicata in acquisizione)
  2. fusione delle esposizioni multiple
  3. allineamento dei canali          <- prima di tutto il resto
  4. maschera difetti dal canale IR   <- sul negativo, dove i difetti sono netti
  5. inversione in spazio densita'
  6. bilanciamento e curve
  7. inpainting dei difetti           <- dopo l'inversione, sui valori finali
  8. raddrizzamento, ritaglio, export

Il punto 3 va per primo perche' tutto il resto presuppone canali sovrapposti.
Il 4 e il 7 sono separati apposta: la maschera si costruisce meglio prima
dell'inversione, la correzione si applica meglio dopo.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np

from . import colore
from . import enhance as enh
from . import framing, invert, irclean, orient, registration
from .acquire import Calibration, RawScan, scan_frame
from .config import AppConfig, FilmFormat, ScanPreset, mm_to_px
from .hal import ScannerBackend


Progress = Optional[Callable[[str, float], None]]


def _tick(cb: Progress, msg: str, frac: float) -> None:
    if cb:
        cb(msg, max(0.0, min(1.0, frac)))


# --------------------------------------------------------------------------

@dataclass
class ProcessOptions:
    profile: invert.FilmProfile = field(default_factory=lambda: invert.PROFILES["c41"])
    auto_wb: float = 1.0
    auto_levels: bool = True
    saturation: float = 1.0
    curve: List = field(default_factory=list)

    # --- controlli dell'operatore (modulo colore) ---
    # Zero, uno o vuoto vuol dire "non toccare": il caso normale non costa
    # niente ne' in tempo ne' in qualita'.
    esposizione: float = 0.0            # in stop
    temperatura_k: float = 0.0          # 0 = bilanciamento in kelvin non usato
    tinta_vm: float = 0.0               # -1 verde ... +1 magenta
    wb_guadagni: Optional[List[float]] = None   # da campionamento neutro
    barra_r: float = 0.0                # barre tricromia, in scatti di densita'
    barra_g: float = 0.0
    barra_b: float = 0.0
    gamma_tono: float = 1.0
    contrasto: float = 0.0
    luminosita: float = 0.0
    chiarezza: float = 0.0
    hue: float = 0.0                    # gradi
    curva_r: List = field(default_factory=list)
    curva_g: List = field(default_factory=list)
    curva_b: List = field(default_factory=list)

    ir_strength: float = 1.0
    ir_threshold: float = 0.82
    auto_crop: bool = True
    auto_straighten: bool = True
    crop_margin_px: int = 0
    subject_crop: bool = False          # ritaglio sul soggetto: proposta, non default
    subject_aspect: Optional[float] = None

    # --- miglioramento immagine ---
    grain_reduction: float = 0.0        # 0 = nessuna. Chroma-only per default
    grain_chroma_only: bool = True
    scratch_repair: float = 0.0         # rilevamento graffi SENZA infrarosso
    dust_repair: float = 0.0
    sharpen: float = 0.0
    grain_add: float = 0.0              # grana sintetica, resa estetica

    # --- orientamento ---
    auto_rotate: bool = False           # spento di default: e' un'euristica
    rotate_min_confidence: float = 0.45
    roll_mirror: Optional[orient.RollMirror] = None


@dataclass
class ProcessResult:
    image: np.ndarray                    # (h, w, 3) float32 sRGB 0..1
    mask: Optional[np.ndarray] = None
    box: Optional[framing.FrameBox] = None
    shifts: Dict[str, tuple] = field(default_factory=dict)
    mask_coverage: float = 0.0
    warnings: List[str] = field(default_factory=list)
    orientation: Optional[orient.Orientation] = None

    # Il negativo com'e' uscito dal sensore, prima dell'inversione, ridotto a
    # dimensione da schermo. Serve al confronto prima/dopo nell'interfaccia:
    # senza, il "prima" sarebbe una ricostruzione, e mostrare una ricostruzione
    # spacciandola per il grezzo non aiuta a giudicare niente.
    # uint8 e gia' piccolo: qualche centinaio di KB, non un secondo originale.
    negativo: Optional[np.ndarray] = None

    # L'immagine lineare pronta per i controlli dell'operatore: e' la base da
    # cui si ricalcola l'anteprima a ogni movimento di un cursore, senza
    # rifare l'intera catena.
    base: Optional[np.ndarray] = None


def _negativo_rgb(planes: Dict[str, np.ndarray]) -> Optional[np.ndarray]:
    """Il negativo come lo vede il sensore, in RGB, pronto per lo schermo.

    Niente inversione e nessuna correzione: si normalizza soltanto, perche' un
    negativo grezzo a video e' quasi nero e non si giudicherebbe nulla. La
    maschera arancione resta, ed e' giusto che resti: e' quello che la macchina
    ha davvero letto.
    """
    canali = [c for c in ("R", "G", "B") if c in planes]
    if not canali:
        return None
    if len(canali) == 1:                      # bianco e nero: un canale solo
        canali = canali * 3
    piani = []
    for c in canali:
        p = planes[c].astype(np.float32)
        lo, hi = np.percentile(p, (0.5, 99.5))
        piani.append(np.clip((p - lo) / max(hi - lo, 1e-6), 0, 1))
    return np.dstack(piani)


def _riduci_per_schermo(img: Optional[np.ndarray],
                        max_lato: int = 900) -> Optional[np.ndarray]:
    """Porta l'immagine a dimensione da schermo, per decimazione e non per media.

    La decimazione mostra la grana e i graffi com'e' giusto che si vedano a chi
    deve decidere se ripetere la scansione; una media li nasconderebbe.
    """
    if img is None:
        return None
    h, w = img.shape[:2]
    passo = max(1, int(max(h, w) / float(max_lato)))
    return (np.clip(img[::passo, ::passo], 0, 1) * 255).astype(np.uint8)


def finiture(base: np.ndarray, opts: ProcessOptions):
    """Dalla base al fotogramma finito: i controlli dell'operatore e l'sRGB.

    Sta separata dal resto per una ragione pratica: e' l'unica parte che cambia
    quando si sposta un cursore, ed e' l'unica abbastanza leggera da poter
    essere rifatta a ogni movimento. Tutto cio' che la precede - allineamento,
    inversione, ritaglio - non dipende dai cursori e non ha senso ricalcolarlo.

    I controlli manuali vengono DOPO gli automatismi: l'automatico porta
    l'immagine a un punto di partenza ragionevole, la mano di chi guarda decide
    da li' in poi. Il contrario vanificherebbe il lavoro fatto.
    """
    img = colore.applica(base, opts)

    if abs(opts.saturation - 1.0) > 1e-3:
        img = invert.saturation(img, opts.saturation)

    extra_mask = None
    if (opts.grain_reduction or opts.scratch_repair or opts.dust_repair
            or opts.sharpen or opts.grain_add):
        img, extra_mask = enh.enhance(
            img,
            grain_reduction=opts.grain_reduction,
            chroma_only=opts.grain_chroma_only,
            scratch_repair=opts.scratch_repair,
            dust_repair=opts.dust_repair,
            sharpen=opts.sharpen,
            grain_add=opts.grain_add)

    return invert.to_srgb(img), extra_mask


def process(raw: RawScan, opts: ProcessOptions,
            progress: Progress = None) -> ProcessResult:
    warnings: List[str] = []

    # --- 2. fusione esposizioni ---
    _tick(progress, "Fusione esposizioni", 0.05)
    planes: Dict[str, np.ndarray] = {}
    exposures = raw.meta.exposures or [1.0]
    for ch, stack in raw.planes.items():
        planes[ch] = registration.merge_exposures(stack, exposures[:len(stack)]) \
            if len(stack) > 1 else stack[0]

    # --- 3. allineamento canali ---
    _tick(progress, "Allineamento canali", 0.15)
    planes, shifts = registration.register_channels(planes, reference="G")
    big = [c for c, (dy, dx) in shifts.items() if abs(dy) > 2 or abs(dx) > 2]
    if big:
        warnings.append(
            "Disallineamento oltre 2 px sui canali %s: probabile gioco meccanico "
            "nel trascinamento, da verificare sulla macchina." % ", ".join(big))

    ir = planes.get("IR")

    # --- 4. maschera difetti ---
    _tick(progress, "Rilevamento difetti (IR)", 0.30)
    mask = None
    if ir is not None and opts.ir_strength > 0:
        mask = irclean.defect_mask(ir, threshold=opts.ir_threshold)
        cov = irclean.mask_coverage(mask)
        if cov > 8.0:
            warnings.append(
                "La maschera IR copre il %.1f%% del fotogramma. Se la pellicola e' "
                "bianco e nero a base argento l'IR non va usato: e' opaca "
                "all'infrarosso e la maschera non significa nulla." % cov)
    else:
        cov = 0.0

    # --- 5. inversione ---
    _tick(progress, "Inversione negativo", 0.45)
    # Il negativo segue la stessa strada geometrica del positivo: se il
    # confronto prima/dopo mostrasse due inquadrature diverse ingannerebbe
    # invece di aiutare, ed e' esattamente cio' che serve NON fare.
    negativo = _negativo_rgb(planes)
    img = invert.invert_negative(planes, opts.profile)

    # --- 6. colore ---
    _tick(progress, "Bilanciamento colore", 0.60)
    if opts.auto_wb > 0:
        img = invert.auto_white_balance(img, opts.auto_wb)
    if opts.auto_levels:
        img = invert.auto_levels(img)
    if opts.curve:
        img = invert.apply_curve(img, opts.curve)

    # --- 7. inpainting ---
    if mask is not None and mask.any() and opts.ir_strength > 0:
        _tick(progress, "Correzione difetti", 0.72)
        img = irclean.inpaint(img, mask, strength=opts.ir_strength)

    # --- 8. geometria ---
    box = None
    riferimento = "G" if "G" in planes else next(
        (c for c in planes if c != "IR"), None)
    density = -np.log10(np.clip(planes[riferimento], 1e-5, None))

    if opts.auto_straighten:
        _tick(progress, "Raddrizzamento", 0.82)
        angle = framing.estimate_skew(density)
        if abs(angle) > 0.02:
            img = framing.straighten(img, angle)
            density = framing.straighten(density, angle)
            if negativo is not None:
                negativo = framing.straighten(negativo, angle)
            if mask is not None:
                mask = framing.straighten(mask.astype(np.float32), angle) > 0.5

    if opts.auto_crop:
        _tick(progress, "Ritaglio fotogramma", 0.88)
        ew = mm_to_px(36.0, raw.meta.dpi)
        eh = mm_to_px(24.0, raw.meta.dpi)
        box = framing.detect_frame(density, ir, ew, eh, opts.crop_margin_px)
        if box.confidence < 0.5:
            warnings.append(
                "Bordi del fotogramma non individuati con certezza: usato il "
                "ritaglio nominale del formato. Verificare a occhio.")
        img = framing.crop(img, box)
        if negativo is not None:
            negativo = framing.crop(negativo, box)
        if mask is not None:
            mask = framing.crop(mask, box)

    if opts.subject_crop:
        _tick(progress, "Centratura sul soggetto", 0.92)
        sbox = framing.suggest_crop(img, opts.subject_aspect)
        img = framing.crop(img, sbox)
        if negativo is not None:
            negativo = framing.crop(negativo, sbox)
        if mask is not None:
            mask = framing.crop(mask, sbox)

    # --- 9. orientamento ---
    orientation = None
    if opts.auto_rotate or opts.roll_mirror is not None:
        _tick(progress, "Orientamento", 0.93)
        img, orientation = orient.auto_orient(
            img, roll_mirror=opts.roll_mirror, rotate=opts.auto_rotate,
            min_confidence=opts.rotate_min_confidence)
        if orientation is not None and not orientation.is_identity:
            if negativo is not None:
                negativo = orient.apply_orientation(negativo, orientation)
            if mask is not None:
                mask = orient.apply_orientation(
                    mask.astype(np.float32), orientation) > 0.5
        if (opts.auto_rotate and orientation is not None
                and orientation.rotation == 0 and orientation.confidence < opts.rotate_min_confidence):
            warnings.append(
                "Orientamento non determinato con sicurezza (%s): il fotogramma "
                "e' stato lasciato com'e'." % orientation.reason)

    # --- 10. la base, e da qui in poi solo la mano dell'operatore ---
    #
    # Tutto quello che viene prima non dipende dai cursori: allineamento,
    # maschera IR, inversione, ritaglio, orientamento. E' anche la parte che
    # costa - decine di secondi su un PC del 2009. Tenerla da parte significa
    # che spostare "contrasto" non la rifa' da capo.
    #
    # Da qui in giu' c'e' solo roba per-pixel, che su una copia ridotta si
    # calcola in un battito di ciglia. E' il motivo per cui i cursori adesso
    # rispondono subito.
    base = img
    img, extra_mask = finiture(base, opts)
    if extra_mask is not None:
        mask = extra_mask if mask is None else (mask | extra_mask)

    _tick(progress, "Fatto", 1.0)
    return ProcessResult(img, mask, box, shifts, cov, warnings, orientation,
                         _riduci_per_schermo(negativo), base)


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

def export(img: np.ndarray, path: str, fmt: str = "tiff16",
           jpeg_quality: int = 95) -> str:
    """Scrive il file finale. Il TIFF a 16 bit e' il default consigliato:
    conserva la gamma tonale per eventuali ritocchi successivi."""
    from PIL import Image

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    arr = np.clip(img, 0.0, 1.0)

    if fmt == "tiff16":
        # PIL non sa scrivere TIFF RGB a 16 bit: serve tifffile (o imageio).
        data = (arr * 65535).astype(np.uint16)
        try:
            import tifffile
            tifffile.imwrite(path, data, photometric="rgb")
        except ImportError:
            import imageio.v3 as iio
            iio.imwrite(path, data)
    elif fmt == "tiff8":
        Image.fromarray((arr * 255).astype(np.uint8), "RGB").save(path, format="TIFF")
    elif fmt == "png":
        import imageio.v3 as iio
        iio.imwrite(path, (arr * 65535).astype(np.uint16))
    else:
        Image.fromarray((arr * 255).astype(np.uint8), "RGB").save(
            path, format="JPEG", quality=jpeg_quality, subsampling=0)
    return path


def export_mask(mask: np.ndarray, path: str) -> str:
    """Esporta la maschera difetti come file separato.

    Serve quando l'inpainting automatico sbaglia - tipicamente sui volti - e si
    vuole rifare la correzione a mano in un editor esterno.
    """
    from PIL import Image
    Image.fromarray((mask.astype(np.uint8) * 255), mode="L").save(path)
    return path


# --------------------------------------------------------------------------
# Scansione automatica del rullo
# --------------------------------------------------------------------------

@dataclass
class BatchOptions:
    frames: int = 36
    stop_on_error: bool = False
    save_raw: bool = True
    name_template: str = "{roll}_{index:03d}"
    roll: str = "rullo"


def scan_roll(backend: ScannerBackend, cfg: AppConfig, preset: ScanPreset,
              fmt: FilmFormat, opts: ProcessOptions, batch: BatchOptions,
              calib: Optional[Calibration] = None,
              progress: Progress = None,
              should_stop: Optional[Callable[[], bool]] = None) -> List[str]:
    """Scansione automatica dell'intero rullo con avanzamento.

    Ogni fotogramma e' indipendente: se uno fallisce si annota e si prosegue,
    invece di perdere tutto il lavoro. La posizione viene ricalibrata a ogni
    frame dagli stacchi trovati, per non accumulare errore lungo il rullo.
    """
    written: List[str] = []
    errors: List[str] = []

    for i in range(batch.frames):
        if should_stop and should_stop():
            _tick(progress, "Interrotto dall'operatore", i / max(batch.frames, 1))
            break

        base = i / max(batch.frames, 1)
        _tick(progress, "Fotogramma %d di %d" % (i + 1, batch.frames), base)

        try:
            raw = scan_frame(backend, cfg, preset, fmt, calib, frame_index=i)
            name = batch.name_template.format(roll=batch.roll, index=i + 1)

            if batch.save_raw and cfg.keep_raw:
                raw.save(cfg.path("raw"), name)

            result = process(raw, opts)
            ext = {"tiff16": ".tif", "tiff8": ".tif",
                   "png": ".png", "jpeg": ".jpg"}.get(cfg.export_format, ".tif")
            out = os.path.join(cfg.path("out"), name + ext)
            export(result.image, out, cfg.export_format, cfg.jpeg_quality)
            written.append(out)

            if cfg.export_ir_mask and result.mask is not None:
                export_mask(result.mask, os.path.join(cfg.path("out"), name + "_mask.png"))

            # avanzamento al fotogramma successivo
            backend.advance(fmt.pitch)

        except Exception as exc:                       # noqa: BLE001
            errors.append("frame %d: %s" % (i + 1, exc))
            if batch.stop_on_error:
                raise
            # tenta comunque di avanzare, per non restare bloccati sullo stesso punto
            try:
                backend.advance(fmt.pitch)
            except Exception:                          # noqa: BLE001
                break

    if errors:
        _tick(progress, "Completato con %d errori" % len(errors), 1.0)
    else:
        _tick(progress, "Rullo completato: %d fotogrammi" % len(written), 1.0)
    return written
