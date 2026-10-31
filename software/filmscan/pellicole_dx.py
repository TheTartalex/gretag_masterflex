# -*- coding: utf-8 -*-
"""
Dal codice DX alla pellicola: che cos'e', e che cosa aspettarsi.

Il codice DX stampato sul bordo del 135 porta due numeri: la parte 1
(7 bit, il fabbricante e la famiglia) e la parte 2 (4 bit, l'emulsione).
La macchina li legge da sola (get_media_info, vedi rullo.chiave_dx); qui li
si traduce nel nome della pellicola, nel tipo e nella sensibilita'.

**La tabella e' quella ufficiale**: "DX Codes for 135-Size Film", I3A,
gennaio 2009, l'ultima edizione pubblicata. Ci sono solo i numeri che
quell'elenco assegna; le pellicole uscite dopo (o mai dichiarate, come le
Fomapan e le Kentmere) non si riconoscono, e allora si dice "non in elenco"
invece di indovinare. Il fabbricante si sa comunque dalla parte 1.

**A cosa serve, e a cosa no.** Non si "simula" la pellicola sopra la
scansione: quello sarebbe rafforzarne il carattere. Si usa quel che si sa per
leggerla giusta fin dal primo fotogramma:

- il **tipo** decide l'inversione: un B/N all'argento e' opaco
  all'infrarosso (niente pulizia IR, niente ICE), un B/N cromogenico (XP2,
  BW400CN) si tratta come un C-41, una diapositiva non si inverte;
- la **pendenza attesa** (gamma) con lo sviluppo (normale, forzato,
  tirato) rimette la luce della scena al suo posto: un negativo forzato di
  uno stop e' piu' contrastato, e invertirlo come uno normale darebbe neri
  chiusi e bianchi bruciati;
- la **grana attesa** (sensibilita' e tecnologia dei cristalli) dosa una
  riduzione leggera, che non tocca le pellicole fini e la scelta
  dell'operatore vince sempre;
- la stessa pendenza scala le soglie delle classi di esposizione
  (rullo.py): un forzato denso non e' un sovraesposto.

I valori di gamma sono quelli tipici di uno sviluppo da laboratorio (indice
di contrasto 0,60-0,62 per lo sviluppo normale, circa +0,09 per stop di
forzatura sul B/N): bastano a non sbagliare di uno stop, non sostituiscono
le statistiche del rullo, che misurano davvero.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# Assegnatari della parte 1 (riepilogo I3A 2009).
ASSEGNATARI: Dict[str, Tuple[int, ...]] = {
    "Agfa-Gevaert": (1, 3, 5, 7, 17, 31, 44, 45, 46, 47, 49, 51, 113, 115),
    "China Lucky": (60, 90, 100),
    "Kodak": (4, 6, 11, 13, 14, 19, 20, 23, 43, 52, 53, 64, 67, 70, 78, 79,
              80, 81, 82, 83, 84, 91, 92, 93, 94, 95, 96, 97, 112, 116),
    "ERA": (68,),
    "Ferrania": (15, 18, 24, 66, 85, 86, 87),
    "Fujifilm": (8, 10, 12, 32, 33, 34, 35, 36, 37, 38, 39, 42),
    "Ilford (Harman)": (105, 106, 107, 108, 109, 110),
    "Konica Minolta": (2, 16, 25, 26, 28, 30, 40, 48, 50, 72, 77),
    "ORWO": (120, 121, 122, 123),
    "Shanghai": (88,),
    "Svema": (9,),
    "Xiamen Fuda": (98,),
}

# Tipi: "bn" (argento), "bn_c41" (cromogenica), "c41", "diapositiva",
# "bn_diapositiva" (Scala); "" = non si sa.
# Tecnologia (solo B/N): "cubica", "tabulare" (T-Max, Delta), "cromogenica".
# (parte 1, parte 2): (nome, tipo, ISO, tecnologia)
TABELLA: Dict[Tuple[int, int], Tuple[str, str, int, str]] = {
    (1, 3): ('AGFAPAN APX 400 Professional', 'bn', 400, 'cubica'),
    (1, 4): ('AGFAORTHO 25 Professional', 'bn', 25, 'cubica'),
    (1, 6): ('AGFAPAN APX 25 Professional', 'bn', 25, 'cubica'),
    (1, 7): ('AGFAPAN APX 100 Professional', 'bn', 100, 'cubica'),
    (1, 8): ('AGFA SCALA 200 Professional / AGFA SCALA 200x Professional', 'bn_diapositiva', 200, ''),
    (3, 6): ('AGFACHROME CT 100i / AGFACHROME RS 100 Plus Professional', 'diapositiva', 100, ''),
    (3, 9): ('AGFACHROME CTX 100 / AGFACHROME CT precisa 100 / AGFACHROME RSX 100 Professional / AGFACHROME RSX II 100 Professional / Perutz CR 100 Chrome', 'diapositiva', 100, ''),
    (4, 1): ('KODAK Technical Pan Film', 'bn', 25, 'cubica'),
    (5, 6): ('AGFACHROME RS 50 Plus Professional', 'diapositiva', 50, ''),
    (5, 9): ('AGFACHROME RSX 50 Professional / AGFACHROME RSX II 50 Professional', 'diapositiva', 50, ''),
    (6, 1): ('KODAK High Speed Infrared Film', 'bn', 0, 'cubica'),
    (6, 3): ('KODAK EKTACHROME Professional Infrared EIR Film', 'diapositiva', 0, ''),
    (7, 5): ('AGFACHROME CT 200 / AGFACHROME 200 RS Professional', 'diapositiva', 200, ''),
    (7, 6): ('AGFACHROME CTX 200 / AGFACHROME CT precisa 200 / AGFACHROME RSX 200 Professional / AGFACHROME RSX II 200 Professional', 'diapositiva', 200, ''),
    (8, 0): ('FUJICOLOR SUPERIA 100', 'c41', 100, ''),
    (8, 1): ('FUJICOLOR 200', 'c41', 200, ''),
    (8, 2): ('FUJICOLOR 200', 'c41', 200, ''),
    (8, 3): ('FUJICOLOR SUPERIA 200', 'c41', 200, ''),
    (8, 5): ('FUJICOLOR SUPERIA 100', 'c41', 100, ''),
    (8, 7): ('FUJICOLOR 100', 'c41', 100, ''),
    (8, 8): ('FUJICOLOR REALA ACE', 'c41', 100, ''),
    (8, 12): ('FUJICOLOR SUPERIA 100', 'c41', 100, ''),
    (8, 13): ('FUJICOLOR SUPERIA 200', 'c41', 200, ''),
    (8, 15): ('FUJICOLOR SUPER V 100', 'c41', 100, ''),
    (9, 1): ('SVEMA DS-100', '', 100, ''),
    (10, 0): ('FUJICOLOR PRO160S', 'c41', 160, ''),
    (10, 1): ('FUJICOLOR PRO160C', 'c41', 160, ''),
    (11, 3): ('KODAK Professional Traffic Surveillance Film', '', 0, ''),
    (12, 0): ('TRUE DEFINITION400', 'c41', 400, ''),
    (12, 1): ('FUJICOLOR 400', 'c41', 400, ''),
    (12, 3): ('FUJICOLOR SUPERIA 200', 'c41', 200, ''),
    (12, 5): ('FUJICOLOR SUPERIA X-TRA 400', 'c41', 400, ''),
    (12, 6): ('FUJICOLOR NPS 160 PROFESSIONAL', 'c41', 160, ''),
    (12, 8): ('FUJICOLOR SUPERIA VENUS 400', 'c41', 400, ''),
    (12, 12): ('FUJICOLOR SUPERIA 100', 'c41', 100, ''),
    (12, 14): ('FUJICOLOR 160 NS', 'c41', 160, ''),
    (16, 0): ('KONICA MINOLTA Chrome MC100', 'diapositiva', 100, ''),
    (16, 3): ('KONICA MINOLTA Chrome R100', 'diapositiva', 100, ''),
    (16, 4): ('KONICA MINOLTA Chrome SHINRA100', 'diapositiva', 100, ''),
    (16, 5): ('KONICA MINOLTA Chrome R200', 'diapositiva', 200, ''),
    (16, 6): ('KONICA MINOLTA Chrome SINBI100, Professional', 'diapositiva', 100, ''),
    (16, 7): ('KONICA MINOLTA Chrome SINBI100, KONICA MINOLTA Chrome CENTURIA100', 'diapositiva', 100, ''),
    (16, 8): ('KONICA MINOLTA Chrome SINBI200, KONICA MINOLTA Chrome CENTURIA200', 'diapositiva', 200, ''),
    (17, 1): ('Agfacolor XRS 100 Professional', 'c41', 100, ''),
    (17, 2): ('AGFACOLOR XRG 100 / AGFACOLOR Optima 125 Professional / AGFACOLOR Portrait 160 Professional', 'c41', 100, ''),
    (17, 3): ('AGFACOLOR ULTRA 50 Professional', 'c41', 50, ''),
    (17, 6): ('AGFACOLOR HDC 200', 'c41', 200, ''),
    (17, 7): ('AGFACOLOR XRG 400', 'c41', 400, ''),
    (17, 8): ('AGFACOLOR HDC 400', 'c41', 400, ''),
    (17, 10): ('AGFACOLOR XRS 1000 Professional', 'c41', 1000, ''),
    (17, 11): ('AGFACOLOR HDC 200-2', 'c41', 200, ''),
    (17, 12): ('AGFACOLOR HDC 100', 'c41', 100, ''),
    (17, 13): ('AGFACOLOR XRG 200', 'c41', 200, ''),
    (17, 15): ('AGFACOLOR XRG 200-2', 'c41', 200, ''),
    (18, 7): ('SCOTCH COLOR 100 AT', 'c41', 100, ''),
    (18, 9): ('SCOTCH COLOR 200 AT', 'c41', 200, ''),
    (18, 10): ('SCOTCH COLOR 400 AT', 'c41', 400, ''),
    (20, 1): ('KODAK PROFESSIONAL EKTACHROME Duplicating Film EDUPE', 'diapositiva', 0, ''),
    (20, 5): ('KODAK EKTACHROME 160 Film (Tungsten)', 'diapositiva', 160, ''),
    (20, 13): ('KODAK EKTACHROME Elite 400 Slide Film (Daylight)', 'diapositiva', 400, ''),
    (20, 15): ('KODAK Ektachrome 200 Film', 'diapositiva', 200, ''),
    (23, 10): ('KODAK PROFESSIONAL EKTACHROME E200 Film', 'diapositiva', 200, ''),
    (23, 11): ('KODAK Elite CHROME 200 Film', 'diapositiva', 200, ''),
    (23, 12): ('KODAK EKTACHROME Professional E100VS Film', 'diapositiva', 100, ''),
    (23, 13): ('KODAK Elite CHROME Extra Color 100 Film', 'diapositiva', 100, ''),
    (23, 14): ('KODAK Professional EKTACHROME E100G Film / KODAK EliteCHROME 100 Film', 'diapositiva', 100, ''),
    (23, 15): ('KODAK Professional EKTACHROME E100GX Film', 'diapositiva', 100, ''),
    (24, 1): ('IMATION CHROME 100', 'diapositiva', 100, ''),
    (24, 4): ('IMATION CHROME 400', 'diapositiva', 400, ''),
    (24, 6): ('IMATION CHROME 640T', 'diapositiva', 640, ''),
    (26, 0): ('KONICA MINOLTA New CENTURIA400', 'c41', 400, ''),
    (26, 1): ('KONICA MINOLTA New CENTURIA800', 'c41', 800, ''),
    (26, 2): ('KONICA MINOLTA CENTURIA SUPER100', 'c41', 100, ''),
    (26, 3): ('KONICA MINOLTA CENTURIA SUPER200', 'c41', 200, ''),
    (26, 4): ('KONICA MINOLTA CENTURIA SUPER1600', 'c41', 1600, ''),
    (26, 5): ('KONICA MINOLTA CENTURIA SUPER400', 'c41', 400, ''),
    (26, 6): ('KONICA MINOLTA CENTURIA SUPER800', 'c41', 800, ''),
    (26, 8): ('KONICA MINOLTA CENTURIA PORTRAIT400', 'c41', 400, ''),
    (26, 9): ('KONICA MINOLTA CENTURIA SUPER400', 'c41', 400, ''),
    (28, 4): ('KONICA MINOLTA VX400', 'c41', 400, ''),
    (28, 5): ('KONICA MINOLTA VX100 SUPER', 'c41', 100, ''),
    (28, 6): ('KONICA MINOLTA VX200 SUPER', 'c41', 200, ''),
    (28, 7): ('KONICA MINOLTA VX400 SUPER', 'c41', 400, ''),
    (28, 12): ('KONICA MINOLTA VX100', 'c41', 100, ''),
    (28, 13): ('KONICA MINOLTA VX-200', 'c41', 200, ''),
    (28, 14): ('KONICA MINOLTA VX400 SUPER', 'c41', 400, ''),
    (31, 1): ('Perutz Primera 200 (until 12/2001)', 'c41', 200, ''),
    (31, 2): ('Perutz Primera 800 (until 12/2001)*', 'c41', 800, ''),
    (32, 0): ('FUJICHROME fortia', 'diapositiva', 50, ''),
    (32, 1): ('FUJICHROME fortia SP', 'diapositiva', 50, ''),
    (32, 2): ('FUJICHROME T64 PROFESSIONAL', 'diapositiva', 64, ''),
    (32, 3): ('FUJICHROME SENSIA 100', 'diapositiva', 100, ''),
    (32, 4): ('FUJICHROME ASTIA 100F', 'diapositiva', 100, ''),
    (32, 5): ('FUJICHROME VELVIA RVP', 'diapositiva', 50, ''),
    (32, 6): ('FUJICHROME VELVIA 50', 'diapositiva', 50, ''),
    (32, 11): ('FUJICHROME VELVIA 100', 'diapositiva', 100, ''),
    (32, 14): ('FUJICHROME DUPLICATING FILM CDU II', 'diapositiva', 0, ''),
    (32, 15): ('FUJICHROME VELVIA 100F', 'diapositiva', 100, ''),
    (34, 0): ('FUJICHROME PROVIA 400X PROFESSIONAL', 'diapositiva', 400, ''),
    (34, 2): ('FUJICHROME SENSIA 400', 'diapositiva', 400, ''),
    (34, 6): ('FUJICHROME ASTIA 100 PROFESSIONAL', 'diapositiva', 100, ''),
    (34, 7): ('FUJICHROME SENSIA II 100', 'diapositiva', 100, ''),
    (34, 9): ('FUJICHROME TREBI 100C', 'diapositiva', 100, ''),
    (34, 10): ('FUJICHROME PROVIA 400F PROFESSIONAL', 'diapositiva', 400, ''),
    (34, 11): ('FUJICHROME SENSIA 200', 'diapositiva', 200, ''),
    (34, 13): ('FUJICHROME PROVIA 100F PROFESSIONAL', 'diapositiva', 100, ''),
    (34, 14): ('FUJICHROME 64T, TYPE II PROFESSIONAL', 'diapositiva', 64, ''),
    (34, 15): ('FUJICHROME DIGITAL OUTPUT FILM', 'diapositiva', 0, ''),
    (35, 0): ('FUJICOLOR SUPERIA 1600', 'c41', 1600, ''),
    (35, 1): ('FUJICOLOR SUPERIA XTRA 800', 'c41', 800, ''),
    (35, 2): ('FUJICOLOR SUPERIA 400', 'c41', 400, ''),
    (35, 3): ('FUJICOLOR SUPERIA 200', 'c41', 200, ''),
    (35, 4): ('FUJICOLOR NPC 160 PROFESSIONAL', 'c41', 160, ''),
    (35, 5): ('FUJICOLOR SUPERIA 100', 'c41', 100, ''),
    (35, 6): ('FUJICOLOR SUPERIA 100', 'c41', 100, ''),
    (35, 7): ('FUJICOLOR 100', 'c41', 100, ''),
    (35, 8): ('FUJICOLOR SUPERIA 1600', 'c41', 1600, ''),
    (35, 9): ('FUJICOLOR SUPERIA 800', 'c41', 800, ''),
    (35, 10): ('FUJICOLOR SUPER 400', 'c41', 400, ''),
    (35, 14): ('FUJICOLOR SUPER 100', 'c41', 100, ''),
    (36, 0): ('FUJICOLOR NPZ 800', 'c41', 800, ''),
    (36, 2): ('FUJICOLOR SUPER G PLUS 400', 'c41', 400, ''),
    (36, 7): ('FUJICOLOR SUPERIA REALA', 'c41', 100, ''),
    (36, 8): ('FUJICOLOR NPH 400', 'c41', 400, ''),
    (36, 10): ('FUJICOLOR SUPER G ACE 400', 'c41', 400, ''),
    (36, 11): ('FUJICOLOR PRO 400', 'c41', 400, ''),
    (36, 14): ('FUJICOLOR REALA ACE', 'c41', 100, ''),
    (39, 0): ('FUJICOLOR SUPERIA 200', 'c41', 200, ''),
    (39, 1): ('FUJICOLOR 200', 'c41', 200, ''),
    (39, 2): ('FUJICOLOR SUPERIA 400', 'c41', 400, ''),
    (39, 3): ('FUJICOLOR SUPERIA 400', 'c41', 400, ''),
    (39, 4): ('FUJICOLOR SUPERIA 400', 'c41', 400, ''),
    (39, 8): ('FUJICOLOR SUPERIA VENUS 1600', 'c41', 1600, ''),
    (39, 9): ('FUJICOLOR SUPERIA VENUS 800', 'c41', 800, ''),
    (39, 10): ('FUJICOLOR SUPERIA 1600', 'c41', 1600, ''),
    (39, 11): ('FUJICOLOR SUPERIA 800', 'c41', 800, ''),
    (39, 12): ('FUJICOLOR SUPERIA 1000', 'c41', 1000, ''),
    (40, 3): ('KONICA MINOLTA IMPRESA50', 'c41', 50, ''),
    (43, 4): ('KODAK MAX 800 Film', 'c41', 800, ''),
    (43, 5): ('KODAK MAX 800 Film – One Time Use Cameras / KODAK Plus Digital 800 Film – One Time Use Cameras', 'c41', 800, ''),
    (43, 6): ('KODAK PROFESSIONAL Supra 800 Film', 'c41', 800, ''),
    (43, 7): ('KODAK PROFESSIONAL Portra 800 Film', 'c41', 800, ''),
    (44, 1): ('Agfacolor Futura 100', 'c41', 100, ''),
    (44, 4): ('Agfacolor Futura 200', 'c41', 200, ''),
    (44, 5): ('Agfacolor Futura II 100', 'c41', 100, ''),
    (44, 6): ('Agfacolor Futura II 200', 'c41', 200, ''),
    (44, 8): ('Agfacolor APS star 200', 'c41', 200, ''),
    (44, 9): ('Agfacolor Futura 400', 'c41', 400, ''),
    (44, 10): ('Agfacolor Futura II 400', 'c41', 400, ''),
    (44, 11): ('Agfacolor APS star 400', 'c41', 400, ''),
    (45, 6): ('Perutz Primera 200 APS', 'c41', 200, ''),
    (45, 8): ('Perutz Primera 200 APS-N', 'c41', 200, ''),
    (46, 1): ('Agfacolor Ultra 100', 'c41', 100, ''),
    (46, 2): ('Agfacolor Optima 100-N', 'c41', 100, ''),
    (46, 3): ('Agfacolor Optima 200-N', 'c41', 200, ''),
    (46, 4): ('Agfacolor Optima 400-N', 'c41', 400, ''),
    (46, 5): ('Polaroid 100 Plus', 'c41', 100, ''),
    (46, 6): ('Polaroid 200 Plus', 'c41', 200, ''),
    (46, 7): ('Polaroid 400 Plus', 'c41', 400, ''),
    (46, 8): ('Polaroid 800 Plus', 'c41', 800, ''),
    (47, 1): ('Perutz Primera 200 (from 01/2002 on)', 'c41', 200, ''),
    (47, 2): ('Perutz Primera 800 (from 01/2002 on)', 'c41', 800, ''),
    (47, 3): ('Agfacolor 100-N', 'c41', 100, ''),
    (47, 4): ('Agfacolor 200-N / Perutz Primera 200-N', 'c41', 200, ''),
    (47, 5): ('Agfacolor 400-N / Perutz Primera 400-N', 'c41', 400, ''),
    (47, 6): ('Perutz Primera 800-N', 'c41', 800, ''),
    (47, 8): ('Perutz Primera 100-A', 'c41', 100, ''),
    (49, 1): ('AGFACOLOR OPTIMA 100-2', 'c41', 100, ''),
    (49, 2): ('AGFACOLOR XPS 160 Professional', 'c41', 160, ''),
    (49, 3): ('AGFACOLOR OPTIMA 100-3 Professional', 'c41', 100, ''),
    (49, 4): ('AGFACOLOR OPTIMA 200-3 Professional', 'c41', 200, ''),
    (49, 5): ('AGFACOLOR OPTIMA 200 Professional', 'c41', 200, ''),
    (49, 7): ('AGFACOLOR OPTIMA 400 Professional', 'c41', 400, ''),
    (49, 8): ('AGFACOLOR OPTIMA 400 Professional', 'c41', 400, ''),
    (49, 9): ('AGFACOLOR OPTIMA 400-2 Professional', 'c41', 400, ''),
    (49, 11): ('AGFACOLOR OPTIMA 200-2 Professional', 'c41', 200, ''),
    (49, 12): ('AGFACOLOR OPTIMA 100 Professional', 'c41', 100, ''),
    (49, 13): ('AGFACOLOR OPTIMA 200-4 Professional', 'c41', 200, ''),
    (50, 0): ('KONICA MINOLTA XG400', 'c41', 400, ''),
    (50, 1): ('KONICA MINOLTA Monochrome VX400', 'diapositiva', 400, ''),
    (50, 2): ('KONICA MINOLTA LV400', 'c41', 400, ''),
    (50, 3): ('KONICA MINOLTA VX100, LV100', 'c41', 100, ''),
    (50, 4): ('KONICA MINOLTA VX200, LV200', 'c41', 200, ''),
    (50, 5): ('KONICA MINOLTA VX400', 'c41', 400, ''),
    (50, 6): ('KONICA MINOLTA JX100', 'c41', 100, ''),
    (50, 7): ('KONICA MINOLTA CENTURIA400', 'c41', 400, ''),
    (50, 8): ('KONICA MINOLTA Monochrome VX400, SEPIA400', 'diapositiva', 400, ''),
    (50, 9): ('KONICA MINOLTA IMPRESA200, JX200M', 'c41', 200, ''),
    (50, 10): ('KONICA MINOLTA IMPRESA100', 'c41', 100, ''),
    (50, 11): ('KONICA MINOLTA PROFESSIONAL160', 'c41', 160, ''),
    (50, 12): ('KONICA MINOLTA CENTURIA100', 'c41', 100, ''),
    (50, 13): ('KONICA MINOLTA CENTURIA200', 'c41', 200, ''),
    (50, 14): ('KONICA MINOLTA CENTURIA800', 'c41', 800, ''),
    (52, 1): ('KODAK EKTACHROME 64 Professional Film (Daylight)', 'diapositiva', 64, ''),
    (52, 4): ('KODAK EKTACHROME 160 Professional Film (Tungsten)', 'diapositiva', 160, ''),
    (52, 7): ('KODAK EKTACHROME 100 Professional Film (Daylight) / KODAK PROFESSIONAL EKTACHROME 100 HC Film', 'diapositiva', 100, ''),
    (52, 9): ('KODAK EKTACHROME 64 Professional Film (Tungsten)', 'diapositiva', 64, ''),
    (52, 10): ('KODAK EKTACHROME 400 Professional Film', 'diapositiva', 400, ''),
    (52, 11): ('KODAK EKTACHROME 320 Professional Film', 'diapositiva', 320, ''),
    (52, 15): ('KODAK Ektachrome 100 Film', 'diapositiva', 100, ''),
    (53, 10): ('KODAK EKTACHROME P1600 Professional Film', 'diapositiva', 1600, ''),
    (53, 12): ('KODAK EKTACHROME 100 Professional Film (Daylight)', 'diapositiva', 100, ''),
    (60, 4): ('LUCKY COLOR CHROME 100CH', 'diapositiva', 100, ''),
    (64, 4): ('KODAK PLUS-X Pan Film', 'bn', 125, 'cubica'),
    (64, 5): ('KODAK TRI-X Pan Film', 'bn', 400, 'cubica'),
    (66, 5): ('SCOTCH 200 ATG1/SCOTCH COLOR EXL', 'c41', 200, ''),
    (66, 6): ('SCOTCH 100 ATG1/SCOTCH COLOR EXL', 'c41', 100, ''),
    (66, 10): ('SCOTCH 400 ATG1/SCOTCH COLOR EXL', 'c41', 400, ''),
    (66, 11): ('SCOTCH 200 ATG2/SCOTCH COLOR EXH', 'c41', 200, ''),
    (67, 7): ('KODAK T-MAX 100 Professional Film', 'bn', 100, 'tabulare'),
    (67, 8): ('KODAK T-MAX 400 Professional Film', 'bn', 400, 'tabulare'),
    (67, 9): ('KODAK T-MAX 3200 Professional Film', 'bn', 3200, 'tabulare'),
    (68, 8): ('ERA Color ER 100', 'c41', 100, ''),
    (78, 1): ('KODAK GOLD 100 Film', 'c41', 100, ''),
    (78, 2): ('KODAK GOLD 200 Film', 'c41', 200, ''),
    (78, 9): ('KODAK PROFESSIONAL PROIMAGE 100 Color Negative Film / KODAK Professional PROFOTO 100 Color Film', 'c41', 100, ''),
    (78, 13): ('KODAK Black-and-White + 400 Film', 'bn_c41', 400, 'cromogenica'),
    (79, 6): ('KODACOLOR VR100 Film', 'c41', 100, ''),
    (79, 7): ('KODACOLOR VR200 Film', 'c41', 200, ''),
    (79, 8): ('KODAK ISO 400 Color Negative Film', 'c41', 400, ''),
    (79, 10): ('KODAK Colour Negative 100 Film', 'c41', 100, ''),
    (79, 11): ('KODAK Professional PORTRA 160 Film', 'c41', 160, ''),
    (79, 12): ('KODAK Professional PORTRA 100T Film', 'c41', 100, ''),
    (79, 13): ('KODAK Professional PORTRA 400 Film', 'c41', 400, ''),
    (79, 15): ('KODAK Professional BW400CN Film', 'bn_c41', 400, 'cromogenica'),
    (82, 3): ('KODAK MAX 400 Film', 'c41', 400, ''),
    (82, 4): ('KODAK SUPER GOLD 400 Film / KODAK GOLD 400 Film', 'c41', 400, ''),
    (82, 12): ('KODAK Professional ULTIMA100 Color Film', 'c41', 100, ''),
    (84, 1): ('KODACHROME 64 Film (Daylight)', 'diapositiva', 64, ''),
    (84, 4): ('KODACHROME 200 Film', 'diapositiva', 200, ''),
    (84, 6): ('KODACHROME 64 Film (PCI)', 'diapositiva', 64, ''),
    (84, 8): ('KODACHROME 200 Film (PCI)', 'diapositiva', 200, ''),
    (85, 1): ('IMATION COLOR HP100', 'c41', 100, ''),
    (85, 2): ('IMATION COLOR HP200', 'c41', 200, ''),
    (85, 4): ('IMATION COLOR HP400', 'c41', 400, ''),
    (87, 1): ('Ferrania Imaging Color FG100', 'c41', 100, ''),
    (87, 2): ('Ferrania Imaging Color FG200', 'c41', 200, ''),
    (87, 3): ('Ferrania Imaging Color FG Plus 100', 'c41', 100, ''),
    (87, 4): ('Ferrania Imaging Color FG400', 'c41', 400, ''),
    (87, 5): ('Ferrania Imaging Color FG400i', 'c41', 400, ''),
    (87, 6): ('Ferrania Imaging Color FG Plus 200', 'c41', 200, ''),
    (87, 7): ('Ferrania Imaging Color FG Plus 400', 'c41', 400, ''),
    (87, 8): ('Ferrania Imaging Color FG800', 'c41', 800, ''),
    (87, 9): ('Ferrania Imaging Color FG800i', 'c41', 800, ''),
    (87, 10): ('Ferrania Imaging Color FG Plus 800', 'c41', 800, ''),
    (90, 8): ('LUCKY B/W SHD100 NEW', 'bn', 100, 'cubica'),
    (90, 10): ('LUCKY B/W SHD400 NEW', 'bn', 400, 'cubica'),
    (90, 12): ('LUCKY B/W SHD400 CN', 'bn_c41', 400, 'cromogenica'),
    (93, 1): ('KODAK Plus Digital 400 Film', 'c41', 400, ''),
    (95, 1): ('KODAK Professional ELITE COLOR 200 Film', 'c41', 200, ''),
    (95, 2): ('KODAK High Definition 400 Film / KODAK Hawkeye Traffic Surveillance Color Film', 'c41', 400, ''),
    (95, 3): ('KODAK Professional ELITE COLOR 400 Film', 'c41', 400, ''),
    (95, 4): ('KODAK Professional 400UC Film', 'c41', 400, ''),
    (95, 5): ('KODAK High Definition 200 Film', 'c41', 200, ''),
    (95, 6): ('KODAK Professional 100UC Film', 'c41', 100, ''),
    (96, 1): ('KODAK MAX 400 Film', 'c41', 400, ''),
    (96, 2): ('KODAK MAX 400 Film – One Time Use Cameras', 'c41', 400, ''),
    (96, 3): ('KODAK SUPER GOLD 400 Film', 'c41', 400, ''),
    (96, 6): ('KODAK Professional Ultima 100 Film', 'c41', 100, ''),
    (96, 7): ('KODAK PROFESSIONAL PORTRA 160NC Film', 'c41', 160, ''),
    (96, 8): ('KODAK PROFESSIONAL PORTRA 160VC Film', 'c41', 160, ''),
    (96, 9): ('KODAK PROFESSIONAL PORTRA 400NC Film', 'c41', 400, ''),
    (96, 10): ('KODAK PROFESSIONAL PORTRA 400VC Film', 'c41', 400, ''),
    (96, 11): ('KODAK GOLD 100 Film', 'c41', 100, ''),
    (96, 12): ('KODAK GOLD 200 Film', 'c41', 200, ''),
    (96, 13): ('KODACOLOR VR 100 Film', 'c41', 100, ''),
    (96, 14): ('KODACOLOR VR 200 Film', 'c41', 200, ''),
    (96, 15): ('KODAK PROFESSIONAL PROIMAGE 100 Color Negative Film / KODAK PROFESSIONAL PROFOTO 100 Color Negative Film', 'c41', 100, ''),
    (97, 1): ('KODAK Color Negative 100 Film', 'c41', 100, ''),
    (97, 2): ('KODAK Color Negative 200 Film', 'c41', 200, ''),
    (97, 3): ('KODAK Color Negative 400 Film', 'c41', 400, ''),
    (97, 4): ('KODAK Black&White 400 Film', 'bn_c41', 400, 'cromogenica'),
    (97, 5): ('KODAK Color Negative 800 Film', 'c41', 800, ''),
    (97, 6): ('KODAK Color Negative 400 Film', 'c41', 400, ''),
    (97, 7): ('KODAK Color Negative 100 Film', 'c41', 100, ''),
    (97, 8): ('KODAK Color Negative 200 Film', 'c41', 200, ''),
    (98, 1): ('135 PAN', 'bn', 0, 'cubica'),
    (98, 2): ('FUDA GA-100', 'c41', 100, ''),
    (98, 3): ('FUDA 135 COLOR', 'c41', 0, ''),
    (100, 2): ('LUCKY COLOR SUPER 100', 'c41', 100, ''),
    (100, 6): ('LUCKY COLOR SUPER 200', 'c41', 200, ''),
    (100, 8): ('LUCKY COLOR SUPER 400', 'c41', 400, ''),
    (100, 9): ('LUCKY COLOR SUPER GBR100', 'c41', 100, ''),
    (100, 11): ('LUCKY COLOR SUPER GBR 200', 'c41', 200, ''),
    (100, 13): ('LUCKY COLOR SUPER GBR400', 'c41', 400, ''),
    (108, 1): ('H 125', 'bn', 125, 'cubica'),
    (108, 2): ('H 400', 'bn', 400, 'cubica'),
    (108, 3): ('A 125', 'bn', 125, 'cubica'),
    (108, 4): ('A 400', 'bn', 400, 'cubica'),
    (108, 5): ('SP816T POLY', 'bn', 0, 'cubica'),
    (108, 6): ('OPTHALMIC', 'bn', 0, 'cubica'),
    (108, 7): ('SFX 200', 'bn', 200, 'cubica'),
    (108, 8): ('UNIVERSAL 400', 'bn', 400, 'cubica'),
    (108, 9): ('UNIVERSAL 100', 'bn', 100, 'cubica'),
    (108, 10): ('DELTA 3200 PRO', 'bn', 3200, 'tabulare'),
    (109, 1): ('PAN F (P1)', 'bn', 50, 'cubica'),
    (109, 2): ('PAN 100', 'bn', 100, 'cubica'),
    (109, 3): ('PAN 400', 'bn', 400, 'cubica'),
    (109, 4): ('SP815T', 'bn', 0, 'cubica'),
    (109, 5): ('PAN F', 'bn', 50, 'cubica'),
    (109, 6): ('FP4/O PAN', 'bn', 125, 'cubica'),
    (109, 7): ('HP5', 'bn', 400, 'cubica'),
    (109, 8): ('Delta 400 Pro / 400 Delta Pro', 'bn', 400, 'tabulare'),
    (109, 9): ('HP5 PLUS', 'bn', 400, 'cubica'),
    (109, 10): ('BLACK 400', 'bn', 400, 'cubica'),
    (109, 11): ('400 DELTA', 'bn', 400, 'tabulare'),
    (109, 12): ('FP4 PLUS', 'bn', 125, 'cubica'),
    (109, 13): ('PAN F PLUS', 'bn', 50, 'cubica'),
    (109, 14): ('100 DELTA', 'bn', 100, 'tabulare'),
    (109, 15): ('100 DELTA PRO', 'bn', 100, 'tabulare'),
    (110, 1): ('XP1 400 (P1)', 'bn_c41', 400, 'cromogenica'),
    (110, 2): ('XP1 400', 'bn_c41', 400, 'cromogenica'),
    (110, 3): ('XP2', 'bn_c41', 400, 'cromogenica'),
    (110, 4): ('XP2 SUPER', 'bn_c41', 400, 'cromogenica'),
    (112, 12): ('KODAK PROFESSIONAL PORTRA 100T Film', 'c41', 100, ''),
    (113, 1): ('AGFACOLOR HDC 100 Plus', 'c41', 100, ''),
    (113, 2): ('AGFACOLOR HDC 100 Plus-2', 'c41', 100, ''),
    (113, 3): ('AGFACOLOR HDC 200 Plus-2', 'c41', 200, ''),
    (113, 4): ('AGFACOLOR HDC 200 Plus', 'c41', 200, ''),
    (113, 5): ('AGFACOLOR Vista 100', 'c41', 100, ''),
    (113, 6): ('AGFACOLOR Vista 200', 'c41', 200, ''),
    (113, 7): ('AGFACOLOR Vista 800', 'c41', 800, ''),
    (113, 8): ('Agfacolor Vista 800-N', 'c41', 800, ''),
    (113, 9): ('AGFACOLOR HDC 400-2', 'c41', 400, ''),
    (113, 10): ('AGFACOLOR Vista 400', 'c41', 400, ''),
    (113, 11): ('Agfacolor Vista 100-N', 'c41', 100, ''),
    (113, 12): ('Agfacolor Vista 200-N', 'c41', 200, ''),
    (113, 13): ('Agfacolor Vista 400-N', 'c41', 400, ''),
    (115, 1): ('Perutz SC 100-2 Color', 'c41', 100, ''),
    (115, 3): ('Perutz SC 100-3 Color', 'c41', 100, ''),
    (115, 4): ('Perutz SC 200-3 Color', 'c41', 200, ''),
    (115, 5): ('Perutz SC 200-4 Color', 'c41', 200, ''),
    (115, 6): ('PERUTZ 200 Color', 'c41', 200, ''),
    (115, 7): ('Perutz Primera 100', 'c41', 100, ''),
    (115, 8): ('Perutz SC 400 Color', 'c41', 400, ''),
    (115, 9): ('Perutz SC 400-2 Color', 'c41', 400, ''),
    (115, 10): ('Perutz Primera 400', 'c41', 400, ''),
    (115, 11): ('Perutz SC 200-2 Color', 'c41', 200, ''),
    (115, 12): ('PERUTZ SC 100 Color', 'c41', 100, ''),
    (116, 1): ('KODACHROME 64 Professional Film (Daylight)', 'diapositiva', 64, ''),
    (116, 3): ('KODACHROME 200 Professional Film', 'diapositiva', 200, ''),
    (116, 4): ('KODAK Splicer Test Film Kodachrome 200', 'diapositiva', 200, ''),
    (116, 5): ('KODAK Splicer Test Film Process K-14', 'diapositiva', 0, ''),
    (120, 1): ('ORWO COLOR CNS 100', 'c41', 100, ''),
    (121, 1): ('ORWO COLOR CNS 200', 'c41', 200, ''),
    (121, 6): ('ORWO COLOR CNS 400', 'c41', 400, ''),
    (122, 1): ('ORWO COLOR CNN 100', 'c41', 100, ''),
    (122, 2): ('ORWO COLOR CNN 200', 'c41', 200, ''),
    (122, 3): ('ORWO COLOR OCN 100', 'c41', 100, ''),
    (122, 4): ('ORWO COLOR CNN 400', 'c41', 400, ''),
    (122, 5): ('ORWO COLOR OCN 200', 'c41', 200, ''),
    (122, 7): ('ORWO COLOR OCN 400', 'c41', 400, ''),
    (123, 1): ('ORWOPAN 100', 'bn', 100, 'cubica'),
    (123, 2): ('ORWOPAN 125', 'bn', 125, 'cubica'),
    (123, 3): ('ORWOPAN 400', 'bn', 400, 'cubica'),
}

# Il profilo di inversione (invert.PROFILES) per ogni tipo.
PROFILO_PER_TIPO = {"bn": "bn", "bn_c41": "bn_c41", "c41": "c41",
                    "diapositiva": "diapositiva", "bn_diapositiva": "bn_diapositiva"}

NOMI_TIPO = {"bn": "B/N all'argento", "bn_c41": "B/N cromogenica (C-41)",
             "c41": "negativa colore (C-41)", "diapositiva": "diapositiva (E-6)",
             "bn_diapositiva": "diapositiva B/N", "": "tipo non noto"}

NOMI_GRANA = ("finissima", "fine", "media", "marcata")

# Sviluppo del rullo, in stop: 0 normale, +1/+2/+3 forzato, -1 tirato.
SVILUPPI = {0: "normale", 1: "forzato +1", 2: "forzato +2", 3: "forzato +3",
            -1: "tirato -1"}


def marca(dx1: int) -> str:
    for nome, numeri in ASSEGNATARI.items():
        if dx1 in numeri:
            return nome
    return ""


@dataclass
class Pellicola:
    dx1: int
    dx2: int
    nome: str = ""
    marca: str = ""
    tipo: str = ""
    iso: int = 0
    tecnologia: str = ""

    @property
    def conosciuta(self) -> bool:
        return bool(self.nome)

    @property
    def profilo(self) -> str:
        return PROFILO_PER_TIPO.get(self.tipo, "")

    @property
    def argento(self) -> bool:
        """Opaca all'infrarosso: niente pulizia IR."""
        return self.tipo in ("bn", "bn_diapositiva")

    def descrizione(self) -> str:
        if not self.conosciuta:
            return "DX %d-%d: %s, pellicola non in elenco" % (
                self.dx1, self.dx2, self.marca or "fabbricante non assegnato")
        parti = [self.nome]
        if self.marca and self.marca.split()[0].lower() not in self.nome.lower():
            parti[0] = "%s %s" % (self.marca, self.nome)
        parti.append(NOMI_TIPO.get(self.tipo, ""))
        if self.iso:
            parti.append("ISO %d" % self.iso)
        return ", ".join(p for p in parti if p)


def riconosci(dx1, dx2) -> Optional[Pellicola]:
    try:
        dx1, dx2 = int(dx1), int(dx2)
    except (TypeError, ValueError):
        return None
    if dx1 <= 0:
        return None
    voce = TABELLA.get((dx1, dx2))
    if voce is None:
        return Pellicola(dx1, dx2, marca=marca(dx1))
    nome, tipo, iso, tec = voce
    return Pellicola(dx1, dx2, nome, marca(dx1), tipo, iso, tec)


def da_chiave(chiave: str) -> Optional[Pellicola]:
    """Dalla chiave salvata nel rullo ("DX 109-9")."""
    m = re.match(r"\s*DX\s+(\d+)-(\d+)", chiave or "")
    return riconosci(m.group(1), m.group(2)) if m else None


def attese(tipo: str, iso: int = 0, tecnologia: str = "",
           sviluppo: int = 0) -> Dict[str, object]:
    """Cosa aspettarsi da una pellicola di questo tipo con questo sviluppo.

    gamma           pendenza della curva caratteristica (invert.FilmProfile.gamma_negativo)
    scala_densita   rispetto a un C-41 normale: scala le soglie di rullo.py
    grana           0..3 (NOMI_GRANA), None se la sensibilita' non si sa
    riduzione_grana quanto ridurla di serie (0 = niente)
    solo_croma      True: la riduzione tocca solo il colore (pellicole colore)
    """
    sviluppo = max(-1, min(3, int(sviluppo or 0)))
    monocroma = tipo in ("bn", "bn_c41", "bn_diapositiva")
    if tipo == "bn":
        gamma = 0.62 + (0.09 if sviluppo > 0 else 0.10) * sviluppo
    elif tipo in ("diapositiva", "bn_diapositiva"):
        gamma = 0.60          # non usata: le diapositive non si invertono
    else:                     # C-41 e cromogeniche: lo sviluppo pesa meno
        gamma = 0.60 + 0.05 * sviluppo
    note = []
    grana = None
    if iso:
        # la grana segue la sensibilita' a cui si e' scattato: forzare di
        # uno stop vuol dire esporre come una pellicola il doppio piu' rapida
        efficace = iso * (2.0 ** max(sviluppo, 0))
        grana = 0 if efficace <= 64 else 1 if efficace <= 200 else 2 if efficace <= 640 else 3
        if tecnologia in ("tabulare", "cromogenica") and efficace < 1600:
            grana = max(0, grana - 1)
    if monocroma:
        riduzione = (0.0, 0.0, 0.06, 0.12)[grana] if grana is not None else 0.0
    else:
        riduzione = (0.0, 0.05, 0.10, 0.18)[grana] if grana is not None else 0.0
    if sviluppo > 0:
        note.append("sviluppo forzato di %d stop: contrasto atteso piu' alto, "
                    "ombre piu' povere" % sviluppo)
    elif sviluppo < 0:
        note.append("sviluppo tirato: contrasto atteso piu' basso")
    return {"gamma": round(gamma, 3),
            "scala_densita": round(gamma / 0.60, 3),
            "grana": grana,
            "nome_grana": NOMI_GRANA[grana] if grana is not None else "",
            "riduzione_grana": riduzione,
            "solo_croma": not monocroma,
            "note": note}


def attese_pellicola(pell: Optional[Pellicola], profilo: str,
                     sviluppo: int = 0) -> Dict[str, object]:
    """Le attese per un rullo: dalla pellicola riconosciuta se c'e', se no
    dal profilo scelto per il rullo (senza sensibilita': niente grana)."""
    if pell is not None and pell.tipo:
        return attese(pell.tipo, pell.iso, pell.tecnologia, sviluppo)
    tipo = {"bn": "bn", "bn_c41": "bn_c41", "diapositiva": "diapositiva",
            "bn_diapositiva": "bn_diapositiva"}.get(profilo, "c41")
    return attese(tipo, 0, "", sviluppo)
