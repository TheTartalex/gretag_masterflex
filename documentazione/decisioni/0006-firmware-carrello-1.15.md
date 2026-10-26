# 0006 — Il carrello Capri va tenuto al firmware 1.15

- Data: 2026-10-26
- Stato: Accettata (i file della 1.15 sono ancora da recuperare)

## Contesto

I backup del Parameter Flash del pacchetto `CapriFirmware` mostrano il
carrello alla 1.15 fino al 2 agosto 2025, e alla 1.13 dalle 14:08 di quel
giorno. Il programma originale ha riscaricato la 1.13 da solo, perché
`Capri1FirmwareVersion.ini` la chiede (`UpdateToVersion=00000071`). Il flash,
scritto dalla 1.15 (Para. Flash 7.00), non è stato toccato. Dettagli in
[carrello-capri.md §2](../hardware/carrello-capri.md#2-il-firmware-115-ma-non-è-lui-la-causa-probabile).

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

## Aggiornamento del 26/10/2026, sera

Decodificati i backup del flash e disassemblato il firmware
([carrello-capri.md §2–§4](../hardware/carrello-capri.md#2-il-firmware-115-ma-non-è-lui-la-causa-probabile)):

- il formato del flash è lo stesso (versione 7) dalla 0.94 alla 1.15;
- la 1.13 ha lavorato con questo formato dal 2002 al 2004;
- lo stesso declassamento automatico era già successo nell'aprile 2003.

La decisione resta: la versione da tenere è la 1.15. Cade però la
motivazione "la 1.13 legge male il flash della 1.15": il firmware non è più
la prima ipotesi per il problema delle guide. Lo sono le fotocellule di
riferimento.
