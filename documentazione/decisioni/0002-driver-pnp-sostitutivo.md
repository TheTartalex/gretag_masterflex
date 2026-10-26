# 0002 — Scrivere un driver PnP sostitutivo di `EKframe.sys`

- Data: 2026-10-25
- Stato: Accettata

## Contesto

La [0001](0001-ponte-sdk-originale.md) dava per scontato che `EKframe.sys`
originale si caricasse su Windows 7 a 32 bit, e il piano di settembre
riduceva il driver nativo (fase 5) a "curiosità". Sulla macchina di riserva
invece il driver originale **non funziona**:

- crea `\Device\EKframe0` e `\\.\EKFrameDev`;
- fallisce in `HalAssignSlotResources`;
- esce senza cancellare dispositivo e link, e senza aver registrato i
  gestori IRP.

Ne resta un dispositivo "zombie": l'SDK riceve errore 1, ScanDiag scrive "NO
scanner detected" (dal 31/12/2025) e qualunque altro driver che voglia quel
nome va in Codice 31. Dettagli in
[scheda-iaq.md §7](../hardware/scheda-iaq.md#7-il-difetto-che-blocca-windows-7).

## Decisione

Si scrive **EKframePnP**, un driver WDM Plug and Play che prende le risorse
dal PnP invece di `HalGetBusData`/`HalAssignSlotResources`. Verso le
applicazioni l'interfaccia è **identica** all'originale: nome del
dispositivo, 13 IOCTL, `ReadFile`/`WriteFile`, eventi. Il comportamento è
tradotto dal disassemblato e confrontato con l'originale in emulazione.

## Conseguenze

- La [0001](0001-ponte-sdk-originale.md) resta valida: SDK e agente sono gli
  stessi, cambia solo il driver sotto.
- Il protocollo della scheda ora è noto nei dettagli
  ([scheda-iaq.md](../hardware/scheda-iaq.md)). Le voci "IOCTL ignoti" e
  "protocollo dei mailbox ignoto" dei documenti di settembre sono superate.
- Sulla produzione resta l'originale finché il pacchetto non è provato del
  tutto, immagini comprese.
- Il driver nuovo non gestisce il risveglio dalla sospensione: l'installatore
  disattiva S3 e il risparmio energia della scheda.
