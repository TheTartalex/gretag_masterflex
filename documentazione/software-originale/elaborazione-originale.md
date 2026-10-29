# L'elaborazione del programma originale, confrontata con filmscan

*29 ottobre 2026.* Analisi di `PhxScanner.exe`, `ImgProcPhoenix.dll`,
`AIDToolkit.dll`, `scn_dll.dll`, delle librerie `IIT_*` e dei file di
configurazione del programma originale (Phoenix V3.0, 2001-2002). Strumenti:
pefile e capstone per il disassemblato, angr 10.0.5 per il decompilato (REA
non aveva un motore di decompilazione in questo ambiente; Ghidra non era
scaricabile). I file Kodak/Gretag restano fuori dal repository: qui ci sono
indirizzi, formati e conclusioni, non i dati.

Criterio, deciso con Alessandro: dall'originale si prende **solo quello che
ci migliora**. In 25 anni la potenza di calcolo e gli algoritmi sono cambiati:
non si torna indietro sulla qualità per somigliare al 2002.

## 1. La catena dell'originale

1. **Scanner → fotogramma** (`CPhxColorFrame::bConvertScanner2Frame`,
   `ImgProcPhoenix.dll`): i valori dell'SDK in densità a 14 bit.
2. **Dmin dalla macchina**: all'inizio di ogni striscia l'SDK dà la densità
   della base per R, G, B (`FrameInfo_35_APS`, costante `NO_DMIN`);
   PhxScanner la scala ×4, la taglia a 0x3FFF e la passa a EyeTech
   ("Dmin values : %d %d %d", `0x435570`).
3. **Digital EyeTech** (dentro `PhxScanner.exe`, `o:\PhxV3.0\Scanner\EyeTech`):
   la correzione automatica di densità e colore, per striscia:
   - lavora su una copia ridotta del fotogramma (`CreateEyeTechFrame`);
   - **KLT** (Karhunen-Loève, `0x420900`): le tre densità meno una media si
     proiettano su tre assi, uno di luminosità e due di colore
     (`KLTMAT/KLTPAR/KLTUPD.INI`);
   - **statistiche di striscia e di rullo**: ogni fotogramma accumula
     campioni in una matrice di correlazione (`SetPixelsForCorMat`,
     `AccuStripeToCorMat`), conservata fra un ordine e l'altro (`Cormat.ini`,
     sulla macchina oltre 1,8 milioni di campioni);
   - **canale pellicola per codice DX** (`FilmChan.fcs`, `0x41f120`:
     "DX code for film channel outside of range"): le statistiche per tipo di
     pellicola;
   - **esclusioni** (`[Skip]`: contrasto basso, sovra e sottoesposto,
     parziale, velato): i fotogrammi anomali non entrano nelle statistiche;
   - **classi di scena** (`[CDF_ParRec]`): luce al tungsteno, neon,
     controluce, flash, velo;
   - **LDC**, correzione di densità locale (`CalculateFrameLDC`,
     `0x41e850`): una maschera a bassa risoluzione che schiarisce e scurisce
     per zone, applicata da `EyeTechTrans` (`0x426720`);
   - regolazioni (`EyeTech_Customer.ini`, sezione `ETSetup`, `0x40b380`):
     `EnableEyeTech`, `EyeTechAmount`, `StabilityFactor`, `DensPlus`,
     `DensMinus`, `Tungsten`.
4. **Densità → carta** (`Dens2Paper`: `D2PSlope`, `D2POffsetR/G/B`,
   `MapContrastToD2P`; `PrintAim.ini`): la curva di stampa verso le densità
   della carta.
5. **Correzioni per sorgente** (`DefaultCorrections_Customer.ini`,
   `0x40acd0`): per "Film Scanner", **"Film Scanner underexposed"** e
   **"Film Scanner overexposed"** un set suo di offset R/G/B, luminosità,
   contrasto, saturazione, nitidezza e raggio.
6. **CMM in Lab** (`CMMProfiles\*.lut`), poi sRGB o profilo della carta.
7. **Miglioramenti** (`IIT_ImgEnh.dll` 3.8.3, 2001): Retinex (contrasto
   locale) in Lab/RGB, occhi rossi automatici e manuali, stretch del
   contrasto, nitidezza adattiva; `IIT_FeatureA.dll`: riconoscimento dei
   volti (2002).

## 2. I profili CMM (`CMMProfiles\*.lut`)

Formato ricavato dai file e verificato:

| Byte | Contenuto |
|---|---|
| 0–3 | magia `0x9E62A68F` |
| 4–7 | 20, lunghezza dell'intestazione |
| 8–11 | tipo: 1 = verso Lab, 3 = da Lab a RGB |
| 12–15 | nodi per lato (65) |
| 16–19 | bit (15 per Lab, 14 per RGB) |
| 20– | N³ voci di 4 `uint16`: tre valori e un riempimento |

- Assi: R il più lento, poi G, poi B, su un RGB **positivo in codifica sRGB**
  (0 = nero). Verificato: `sRGB2Lab_LUT.lut` dà rosso L 54, verde L 88,
  e la griglia è uniforme in sRGB.
- Lab ICC, **bianco D50**: L = v/32767·100, a e b = (v−16384)/128.
  Con Lab D50 → Bradford → sRGB, `sRGB2Lab_LUT.lut` torna all'identità con
  errore medio 0,004 su 0..1 (con D65 sarebbe 0,023).

`Negative2Lab_P8_neg.lut` (il profilo del nostro scanner quando
`EnableCMMFilm` è acceso; `MinoltaNegative2Lab` è dello scanner esterno
Minolta per il 120) è una **resa di stampa**: grigi neutri e appena più
scuri nei medi (0,50 → 0,475), pelle e cielo quasi uguali, colori saturi
compressi forte nel gamut della carta (verde pieno → 0,41).

**Cosa ne facciamo.** `filmscan/lut.py` legge ora questi file
(`leggi_gretag`, riconosciuti dalla magia) e li trasforma in una LUT 3D da
sRGB a sRGB, come le altre: copiando `Negative2Lab_P8_neg.lut` in
`software\lut\` si sceglie dalla sezione LUT come "look stampa Gretag".
Non di serie: comprime i colori saturi, e per un file digitale è una
perdita; sulla stampa in drylab può piacere.

## 3. Confronto, punto per punto

| Tema | Originale | filmscan oggi | Cosa conviene prendere |
|---|---|---|---|
| Inversione e colore | densità → EyeTech → Dens2Paper → Lab | densità con base dai bordi, bilanciamento e livelli per fotogramma, curve, LUT | **statistiche di rullo** (vedi §4) |
| Base (Dmin) | dalla macchina, per striscia | stimata sui bordi di ogni fotogramma | la base **di rullo**: la più chiara fra i bordi di tutti i fotogrammi; quella dell'SDK solo se si trova come leggerla con `scan_raw` |
| Negativi chiari o scuri | classe sotto/sovraesposto + set di correzioni dedicato; esclusi dalle statistiche | esposizione misurata sul primo fotogramma vero; livelli automatici | **classificazione per densità** con correzioni dedicate (vedi §4) |
| Non esposti | rilevamento del fotogramma fallito → avanti da solo (`AutoAdvanceFrameDetectFailed=true` in `Ggc.ini`), tasto *Skip* | `fotogramma_vuoto` (escursione < 0,10 D): grezzo tenuto, numero contato | niente: il nostro fa di più (tiene il grezzo e lo dice) |
| Numeri sul bordo (135) | **non letti**: il 135 dà solo il codice DX, i numeri sono contati | contati; riordino pronto se un giorno arrivano | niente da prendere |
| Dati APS (IX) | `AIDToolkit.dll`: numero di fotogramma, formato C/H/P, orientamento, copie, titoli | niente | solo se si scansiona APS: formato C/H/P e orientamento per ritaglio e rotazione |
| Codice DX | letto per striscia ("Stripe has DX code : %d / %d") | non letto | **leggerlo** (§5): tipo di pellicola nei metadati e base per le statistiche per pellicola |
| Verso d'inserimento | l'SDK lo segnala: `ERR_FILM_ORIENTATION` (37) | il codice non aveva nome | **fatto**: 29, 30, 35, 37, 38 hanno nome e spiegazione in italiano |
| Scene difficili | tungsteno, neon, controluce, flash, velo | niente | tungsteno e velo: sì, con un rilevamento moderno |
| Contrasto locale | LDC + Retinex (2001) | chiarezza (manuale) | sì, ma con un metodo d'oggi (filtro guidato) e automatico |
| Occhi rossi, volti | `IIT_ImgEnh`, `IIT_FeatureA` | niente | no: per la scansione non servono, e gli algoritmi del 2002 sono superati |
| Digital ICE | `DMLDICELib.dll` di Kodak, con licenza | ASF dell'SDK + nostra pulizia IR | no: la licenza è legata alla macchina |

## 4. Le migliorie da EyeTech, in versione 2026

Non si copia EyeTech: si prendono le idee che reggono, con i mezzi di oggi.

1. **Bilanciamento di rullo invece che di fotogramma.** Un prato o una
   parete rossa spostano il bilanciamento del singolo fotogramma; EyeTech lo
   evitava stimando l'asse dei grigi su tutta la striscia, escludendo i
   fotogrammi anomali. Da noi: a fine rullo (i grezzi ci sono già) asse dei
   grigi robusto su tutti i fotogrammi, poi per ogni fotogramma solo uno
   scostamento limitato da quello di rullo. È anche più stabile fra un
   fotogramma e l'altro.
2. **Memoria per tipo di pellicola** (codice DX): lo stesso asse, salvato
   per pellicola e aggiornato rullo dopo rullo, come `FilmChan.fcs`. Un
   rullo corto (3-4 fotogrammi) parte già dalla media di quella pellicola.
3. **Classi di esposizione.** Densità media del fotogramma rispetto a quella
   del rullo: sottoesposto (negativo chiaro) → più contrasto nei toni
   bassi e più riduzione del rumore di colore; sovraesposto (negativo
   denso) → lettura con esposizione più lunga (HDR, già previsto) e
   compressione delle alte luci. I fotogrammi delle due classi non entrano
   nelle statistiche di rullo.
4. **Tungsteno e velo**: rilevati sull'asse dei colori di KLT (dominante
   calda a bassa densità; velo = base più densa del solito), con correzione
   proposta e scritta negli avvisi, come facciamo per l'orientamento.
5. **Correzione di densità locale automatica**: maschera a bassa risoluzione
   con filtro guidato (niente aloni, che erano il difetto dei Retinex del
   2001), forza regolabile e spenta di serie.

## 5. Da verificare sulla macchina

- **Codice DX**: `get_media_info` (già legata in `ponte/sdk.py` come
  `info_supporto`) restituisce un `MediaInfo*`; a +0x10 c'è un puntatore,
  e per il 135 i numeri DX 1 e 2 stanno ai byte +4 e +5 (PhxScanner
  `0x42F117`: `dx = b[+4] << 4 + b[+5]`). Per l'APS a +0xC c'è il numero di
  pose (1 → 40, 2 → 25, 4 → 15, `0x449944`).
- **Dmin dell'SDK**: arriva in `FrameInfo` con `import_image`, cioè nel
  percorso `capture` che non usiamo. Da capire se si ottiene anche col nostro
  `scan_raw`.
- **`ERR_FILM_ORIENTATION`**: quando arriva (inserimento, carica,
  fotogramma successivo) e se vale anche per il 135 o solo per l'APS.
