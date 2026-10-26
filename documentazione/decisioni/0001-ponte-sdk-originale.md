# 0001 — Comandare la macchina attraverso l'SDK originale

- Data: 2026-09-03
- Stato: Accettata (vedi la nota della [0002](0002-driver-pnp-sostitutivo.md))

## Contesto

Il software originale (MasterPlus) comanda bene la macchina ma ne butta via
quasi tutta l'immagine: legge 2100×1400 e salva 600×400. Sotto
l'interfaccia c'è l'SDK Kodak `scn_dll.dll`. Le firme dei suoi 310 metodi si
ricavano tutte dai nomi esportati. Fra questi c'è `scan_raw`, che legge un
canale grezzo a 16 bit con l'esposizione scelta dal chiamante.

Le alternative erano due: pilotare i server COM (`PhxScanner`,
`GateControl`), che però non hanno type library, oppure riscrivere da zero il
percorso immagine fino alla scheda, con mesi di lavoro e un rischio alto.

## Decisione

Si tengono **driver e SDK originali** e si buttano via le interfacce. Un
agente Python senza dipendenze (`ponte/agente.py`) carica `scn_dll.dll` con
ctypes e trampolini `__thiscall`. Riceve i comandi da una cartella. Gira in
un **processo separato** da filmscan, così se l'SDK si blocca l'interfaccia
resta viva.

## Conseguenze

- La macchina resta su **Windows 7 a 32 bit** con Python 3.8 a 32 bit: l'SDK
  è a 32 bit.
- Pipeline, ordini ed export non sanno niente dell'hardware: sta tutto dietro
  `hal.PonteSdkBackend`.
- Sulla macchina restano da scoprire i valori di `ColorChannel`, la mappa di
  `diag_input` sui sensori e il segno e la scala di `regola_<passi>`.
- Dettagli: [filmscan/ponte.md](../filmscan/ponte.md),
  [software-originale/api-sdk.md](../software-originale/api-sdk.md).
