# Stato del progetto

**Aggiornato al 22 novembre 2026.** Questo è il documento da aggiornare a
fine di ogni sessione di lavoro: stato, problemi aperti e prossimi passi
stanno qui, e solo qui. Cosa è cambiato e quando sta nel
[CHANGELOG](../CHANGELOG.md); il perché delle scelte in
[decisioni/](decisioni/).

## Macchine

| Macchina | Ruolo | Note |
|---|---|---|
| Server di produzione | **non si tocca** finché la riserva non ha provato tutto, immagini comprese | Win7 x86, driver originale `EKframe.sys` |
| Macchina di riserva | **banco di prova** | fisicamente identica alla produzione: server IBM del 2007, Win7 SP1 x86, 4 GB, CPU **senza AVX**, scheda IAQ e scanner (matricola 60210). Si comanda da AnyDesk; si aggiorna da sola dal ramo `main` |
| PC di sviluppo | sviluppo | Windows a 64 bit, senza WDK 7.1: il driver si compila su Linux ([decisione 0004](decisioni/0004-compilazione-mingw-linux.md)) |

## Componenti

| Componente | Dove | Stato |
|---|---|---|
| Driver **EKframePnP 2.1** | [`driver/EKframePnP/`](../driver/EKframePnP/) | funziona sulla riserva: scheda avviata, immagini buone nei rulli veri; manca la verifica di equivalenza col banco di prova C ([verifica/](../driver/EKframePnP/verifica/README.md)) |
| Software **filmscan** | [`software/`](../software/) | **in uso sulla riserva** con la macchina vera: rulli interi a strisce, calibrazione, autofocus, colore di rullo, Digital ICE, archivio, elaboratori in rete, collegamento remoto |
| Carrello **Capri** (COM5) | [carrello-capri.md](hardware/carrello-capri.md) | firmware **1.15** ([decisione 0006](decisioni/0006-firmware-carrello-1.15.md)): guide a posto col 135. Condensatore C8 da risaldare (vedi sotto) |

## Problemi aperti, in ordine di priorità

1. **Scanner rosso dopo un errore di trasporto.** Dopo ogni `ERR_MOVE`
   (il carrello non muove la pellicola) l'apertura successiva dell'SDK
   restava nello stato 10, senza taratura dei LED, finché non si spegneva
   la Masterflex; il programma originale lo chiama evento 37509 (il tamburo
   non cambia formato). Visto il 7/11 e il 14/11.
   - Sulla scheda del carrello si è trovato staccato **C8**, il tantalio
     da 10 µF 35 V accanto al pilota motori L293DD e al connettore
     d'alimentazione P4: da risaldare o sostituire (SMD, misura D 7343),
     striscia "+" verso il "+" della scheda.
   - Nel software (14/11): l'agente annota l'errore, manda il Gretag Reset
     prima di uscire, e all'avvio, dopo 180 s nello stato 10, prova un
     Gretag Reset e poi avvisa di spegnere e riaccendere.
   - **Da provare** dopo la riparazione: un rullo fino a fine striscia,
     chiudere e riaprire il programma; nei registri dell'agente le righe
     "Gretag Reset". Se si blocca ancora, il sospettato è l'alimentatore
     del carrello (tensione su P4 mentre i motori si azzerano).
2. **Digital ICE sul rullo vero.** Il livello d'esposizione dell'IR si
   decide col collaudo dell'infrarosso (`MASTERFLEX.bat` → 4 → 3), che
   salva anche `confronto_ice.png` con tutti i motori: da fare sulla
   riserva con una pellicola di prova. Nelle impostazioni della riserva il
   motore è ancora "kodak": per usare quello di serie va scelto
   **filmscan** in Macchina.
3. **Codice DX**: si legge, ma il numero nella risposta dell'SDK non è
   ancora trovato. Collaudo dedicato `MASTERFLEX.bat` → 4 → 4 con una
   pellicola nota.
4. **Pagina che cadeva alla riapertura** (`STATUS_BREAKPOINT`, 14/11):
   corretto l'avvio di explorer; da confermare chiudendo e riaprendo il
   programma dopo un riavvio del PC.
5. **Colonne difettose del sensore**: sull'etichetta del carrello sono
   scritte a mano (357, 1356, 1829, 1338, 1747 e una o due illeggibili).
   Da trascrivere da una foto nitida e confrontare con
   `get_image_column_defects` dell'SDK.
6. **Elaboratori in rete**: pronti, ma sulla riserva sono spenti
   (`elaboratori_rete: false`); da accendere dal pannello Macchina e provare
   con i PC veri.
7. **APS e 110** con i motori veri: mai provati. La prima volta, mano
   sull'interruttore.

## Da sapere

- **Sensore**: l'SDK rifiuta ogni rettangolo più grande della finestra del
  formato (2100×1400 per il 135, errori 46/47/42, collaudo del 14/11):
  l'impostazione "sensore intero" non porta niente in più.
- **Resa del colore**: decisa con Alessandro il 7/11, più il caldo da
  stampa del 10/11 (grigi b +1,5, a +0,5). Non si cambia senza una sua
  scelta. Confrontati e non adottati: la catena di Lightcraft/Film Lab e le
  matrici KLT del programma originale.
- **Firmware del carrello**: l'ini deve restare a
  `UpdateToVersion=00000073` (1.15), altrimenti il programma originale
  riscarica la 1.13 e tornano i problemi delle guide. Lo controlla la voce
  5 del menu.

## Prossimi passi

1. Risaldare C8, riaccendere, fare la prova del problema 1.
2. Collaudo dell'infrarosso e scelta del motore di Digital ICE.
3. Collaudo del DX con una pellicola nota.
4. Sulla produzione, solo dopo che la riserva ha provato tutto: versione e
   ini del carrello prima di ogni altra cosa (voce 5), poi installer.

## Avvertenze operative

- Nel registro dell'SDK della riserva compaiono messaggi da tenere
  presenti:
  - `CalibDataDir … last err= 2` e `CheckForFirmwareMismatch … 135anm.wrp`
    c'erano già prima dei nostri cambi;
  - `ioctl failed (2): 87` / `unable to unregister buffer #1, #2` alla
    chiusura: lo dà EKframePnP quando si sgancia un buffer che non risulta
    agganciato (`EkDmaRelease`, ricostruito da `0x121BE`). Da confrontare
    con il driver originale nel banco di prova.
- Non lanciare `ektest` mentre filmscan o l'agente sono aperti: alla
  chiusura dell'handle il driver spegne la parte logica della scheda, come
  faceva l'originale.
- La COM5 la può aprire un solo programma: prima degli strumenti Gretag
  chiudere l'agente e il programma originale.
- Sulla produzione, il giorno in cui si installerà EKframePnP, `INSTALLA.bat`
  si fermerà finché il vecchio `ekframe.sys` non è tolto. Serve un riavvio.
- Su quella CPU: numpy non oltre la 1.22.4, scipy fermo alla 1.9.1.
- "Invia per l'analisi" (i campioni) solo con pellicola di prova, mai con
  quella di un cliente.
