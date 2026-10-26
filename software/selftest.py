#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Collaudo dell'intera pipeline senza hardware.

Usa il backend mock: calibra, acquisisce un fotogramma sintetico, lo elabora ed
esporta. Serve a verificare che allineamento canali, inversione, pulizia IR e
ritaglio funzionino prima ancora di accendere la macchina.
"""
import os, sys, time

from filmscan.acquire import run_calibration, scan_frame
from filmscan.config import AppConfig, FORMATS, PRESETS
from filmscan.hal import MockBackend
from filmscan.invert import PROFILES
from filmscan.pipeline import ProcessOptions, export, process


def bar(msg, frac):
    sys.stdout.write("\r  %-42s %3d%%" % (msg[:42], frac * 100))
    sys.stdout.flush()


def main():
    cfg = AppConfig(driver="mock", optical_dpi=1000, sensor_pixels=1400,
                    raw_dir="./out/raw", out_dir="./out/export",
                    calib_dir="./out/calib")
    backend = MockBackend(sensor_pixels=cfg.sensor_pixels, dpi=cfg.optical_dpi)
    backend.connect()

    print("1. Calibrazione (pellicola fuori dal percorso ottico)")
    backend.film_present = False
    calib = run_calibration(backend, lines=32, repeats=3, progress=bar)
    print("\n   salvata in", calib.save(cfg.path("calib")))

    print("2. Acquisizione fotogramma (pellicola inserita)")
    backend.film_present = True
    preset = PRESETS["standard"]
    preset.dpi = cfg.optical_dpi
    fmt = FORMATS["135-full"]
    raw = scan_frame(backend, cfg, preset, fmt, calib, progress=bar)
    print("\n   canali:", sorted(raw.planes), "dimensioni:", raw.planes["G"][0].shape)

    print("3. Elaborazione")
    opts = ProcessOptions(profile=PROFILES["c41"], ir_strength=1.0,
                          auto_crop=True, auto_straighten=False)
    res = process(raw, opts, progress=bar)
    print("\n   immagine:", res.image.shape,
          "| maschera IR: %.2f%% del fotogramma" % res.mask_coverage)
    print("   spostamenti canali:",
          {k: (round(v[0], 2), round(v[1], 2)) for k, v in res.shifts.items()})
    for w in res.warnings:
        print("   avviso:", w)

    print("4. Modalita' pellicola")
    from filmscan.acquire import measure_channel_exposure, film_present
    g = measure_channel_exposure(backend, ["R", "G", "B", "IR"], calib)
    print("   esposizione per canale:",
          ", ".join("%s x%.2f" % (k, v) for k, v in g.items()))
    print("   (il blu chiede piu' esposizione: la maschera arancione lo assorbe)")
    backend.film_present = False
    senza = film_present(backend, calib)
    backend.film_present = True
    print("   rilevamento pellicola: con %s / senza %s"
          % (film_present(backend, calib), senza))

    for nome, prof in (("bn", "bn"), ("diapositiva", "diapositiva")):
        pr = PRESETS[nome]
        pr.dpi = cfg.optical_dpi
        rr = scan_frame(backend, cfg, pr, fmt, calib)
        res2 = process(rr, ProcessOptions(profile=PROFILES[prof], auto_crop=False))
        print("   %-12s canali=%-9s passaggi=%d -> %s"
              % (nome, ",".join(rr.meta.channels), pr.total_passes, res2.image.shape))

    print("\n5. Export")
    p = export(res.image, os.path.join(cfg.path("out"), "collaudo.tif"), "tiff16")
    print("   scritto:", p, "(%.1f KB)" % (os.path.getsize(p) / 1024))
    print("\nCollaudo completato.")


if __name__ == "__main__":
    main()
