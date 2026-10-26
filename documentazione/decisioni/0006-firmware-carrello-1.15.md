# 0006 — Il carrello Capri va tenuto al firmware 1.15

- Data: 2026-10-26
- Stato: Accettata (i file della 1.15 sono ancora da recuperare)

## Contesto

I backup del Parameter Flash del pacchetto `CapriFirmware` mostrano il
carrello alla 1.15 fino al 2 agosto 2025, e alla 1.13 dalle 14:08 di quel
giorno. Il programma originale ha riscaricato la 1.13 da solo, perché
`Capri1FirmwareVersion.ini` la chiede (`UpdateToVersion=00000071`). Il flash,
scritto dalla 1.15 (Para. Flash 7.00), non è stato toccato. Dettagli in
[carrello-capri.md §2](../hardware/carrello-capri.md#2-il-firmware-deve-essere-la-115).

La macchina ha lavorato anni con la 1.15. La 1.13 è più vecchia di sei mesi.
Il problema delle guide all'avvio è comparso dopo.

## Decisione

La versione richiesta è la **1.15**, per master e slave. `manutenzione.py
firmware` (voce 13 del menu) la controlla e segnala sia la versione attuale
sia quella che il programma originale imporrebbe al prossimo avvio. Con i
file della 1.15 li mette in `AutomaticUpdate\` e porta l'ini a
`UpdateToVersion=00000073`.

## Conseguenze

- Finché i file della 1.15 non si trovano (macchina di produzione, archivio
  del tecnico), la riserva resta alla 1.13 e il controllo lo segnala.
- Non si modifica l'ini per impedire il download senza avere la 1.15: con il
  carrello alla 1.13 il programma originale riscaricherebbe a ogni avvio.
- Dopo il ritorno alla 1.15 vanno ricontrollati i flag (Crash Detect, No
  Reset) e rifatto il selftest di servizio.
