# Registro delle modifiche

Una sezione per componente; dentro, le versioni dalla più recente.
Le versioni del driver sono quelle scritte nell'`.inf` (`DriverVer`).

## Repository

### 2026-10-26 — prima pubblicazione

- Struttura: `documentazione/`, `driver/`, `software/`, `strumenti/`.
- Importata la documentazione di settembre e ottobre, con i collegamenti
  aggiornati e i punti superati segnati (ARCNET, IOCTL, driver su Win7,
  numpy).
- Nuovi: `documentazione/stato.md` (unico posto per lo stato), le schede
  delle decisioni 0001–0004, indice e regole dei documenti.
- `CLAUDE.md` alla radice al posto del file di contesto da allegare a mano.

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
