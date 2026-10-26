# Registro delle modifiche

Una sezione per componente; dentro, le versioni dalla più recente.
Le versioni del driver sono quelle scritte nell'`.inf` (`DriverVer`).

## Repository

### 2026-10-26 — firmware del carrello

- `carrello-capri.md` riscritto dal firmware disassemblato e dai backup del
  flash decodificati: zero dei motori con i limiti, fotocellule, formato del
  flash, flag, protocollo. L'ipotesi "1.13 incompatibile con il flash della
  1.15" è caduta (decisione 0006 aggiornata).

### 2026-10-26 — pacchetto di aggiornamento

- `strumenti/aggiornamento/`: `prepara.sh` crea lo zip per la macchina dal
  commit corrente (con `--offline` anche Python e librerie); `AGGIORNA.bat`
  lo applica sulla cartella installata, con backup e senza toccare i dati.

### 2026-10-26 — filmscan e carrello

- `software/`: il codice di filmscan, ripulito e corretto (vedi sotto).
- `documentazione/hardware/carrello-capri.md`: il carrello Capri dal
  pacchetto CapriFirmware (guide, firmware, flag, selftest, errori,
  protocollo COM5).
- Decisioni 0005 (un solo script) e 0006 (firmware del carrello 1.15).
- `stato.md` riscritto con i risultati del giorno.

### 2026-10-26 — prima pubblicazione

- Struttura: `documentazione/`, `driver/`, `software/`, `strumenti/`.
- Importata la documentazione di settembre e ottobre, con i collegamenti
  aggiornati e i punti superati segnati (ARCNET, IOCTL, driver su Win7,
  numpy).
- Nuovi: `documentazione/stato.md` (unico posto per lo stato), le schede
  delle decisioni 0001–0004, indice e regole dei documenti.
- `CLAUDE.md` alla radice al posto del file di contesto da allegare a mano.

## filmscan

### 2026-10-26 — stato dell'SDK

- `ponte/sdk.py`: nomi dei codici `ScnError` letti in `scn_dll.dll`; stato
  interno dell'SDK seguito dagli eventi `EV_STATE_CHANGE`; `pronto()`.
- `ponte/agente.py`: finché l'SDK non è pronto (stato 10, circa 40 s dopo
  l'apertura) rifiuta i comandi con un messaggio chiaro, poi scrive
  "scanner pronto dopo N s". Il simulatore imita l'attesa.
- `filmscan/hal.py`: `connect()` aspetta lo scanner pronto, fino a 120 s.

### 2026-10-26 — dopo la prima prova dell'agente sulla riserva

- `ponte/agente.py`: lo "stato" legge ogni voce per conto suo (sulla
  riserva fallisce solo il tipo di gate); gli errori dell'SDK si scrivono su
  una riga, senza traceback.
- `collaudo_scanner.py`: riporta le voci dello stato non lette.
- `manutenzione.py diagnosi`: 200 righe del log dell'SDK invece di 60.

### 2026-10-26 — dopo la prima diagnosi dalla riserva

- `manutenzione.py`: `collaudo_scanner.py` e `ektest.exe` nella lista
  `CODICE`, così l'aggiornamento li sostituisce; la diagnosi mostra lo stato
  della cartella del ponte e gli ultimi rapporti di collaudo.
- `collaudo_scanner.py`: l'errore che ferma il collaudo finisce nel rapporto.
- `ponte/agente.py`: avvisa a schermo se non riesce a scrivere il registro.
- `prepara.sh`: mette `ektest.exe` nello zip e si ferma se un file di
  `software/` non è in `CODICE`.

### 2026-10-26 — strumenti del carrello

- `manutenzione.py firmware` (voce 13) decodifica l'ultimo backup del
  Parameter Flash: flag, matricola, posizioni di guide, lente e CCD.
- `manutenzione.py porta-carrello`, lanciato dalla voce 4 → 5: porta sulla
  COM5 gli ini degli strumenti Gretag (`selftest.ini` era sulla COM1).

### 2026-10-26 — prima versione nel repository

Il codice di settembre, importato dalla macchina di riserva, con:

- **movimenti**: fotogramma successivo con *advance_one_frame* invece di
  `regola_3800`; limiti di 3 mm e 300 passi; movimenti bloccati durante un
  lavoro; formato del carrello selezionato prima di scansionare; agente con
  `--senza-motori`, spegnimento automatico del trascinamento, registro su
  file, `--apertura leggera`, `--prova-motori`;
- **immagini via SDK**: stessa risoluzione per calibrazione e scansione,
  niente dark finto senza controllo della lampada, dpi veri nei metadati,
  IR saltato finche' non e' verificato;
- **affidabilita'**: scambio comandi atomico, stato senza interrogare la
  macchina ogni secondo, errori dell'SDK con i codici (e i messaggi del
  carrello), impostazioni salvate;
- **MASTERFLEX.bat** al posto dei dodici `.bat`; `manutenzione.py`
  (installazione di Python e librerie con impronta, aggiornamento,
  diagnosi, pacchetto offline, avvio automatico, firmware del carrello);
  `collaudo_scanner.py` (colori e intensita', sensori, passi);
- **dipendenze**: numpy 1.22.4 con impronte SHA-256;
- tolti `ui.py`, `BridgeBackend`, `NativeBackend`, `pipeline.scan_roll`.

## Driver EKframePnP

### 2.1 — 2026-10-25

Prima versione pubblicata. Funziona sulla macchina di riserva: la scheda si
avvia e il software la riconosce.

- Driver WDM Plug and Play con risorse dal PnP, al posto di
  `HalGetBusData`/`HalAssignSlotResources`.
- Interfaccia identica a `EKframe.sys`: 13 IOCTL, `ReadFile`/`WriteFile`,
  5 eventi. In più `IOCTL_EK_DIAG`.
- Verifica di equivalenza: ARCNET 105/105, DMA 7/7.
- Corretti nove difetti dell'originale (elenco nel
  [README del driver](driver/EKframePnP/README.md#differenze-volute-rispetto-alloriginale)).
- `INSTALLA.bat` riconosce il vecchio `ekframe.sys` e lancia
  `rimuovi-driver-vecchio.bat`.
- Nel repository (26/10): compilazione riproducibile
  (`--no-insert-timestamp`), `ektest` compilato da `build.sh`,
  `confronta.py` per controllare che il pacchetto venga dai sorgenti. Il
  binario del pacchetto resta quello collaudato.
