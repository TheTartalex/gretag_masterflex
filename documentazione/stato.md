# Stato del progetto

**Aggiornato al 26 ottobre 2026.** Questo è il documento da aggiornare a fine
di ogni sessione di lavoro: stato, problemi aperti e prossimi passi stanno
qui, e solo qui. Il piano a lungo termine sta in [piano-lavori.md](piano-lavori.md).

## Macchine

| Macchina | Ruolo | Note |
|---|---|---|
| Server di produzione | **non si tocca** finché non c'è un pacchetto completo provato | Win7 x86, driver originale `EKframe.sys` |
| Macchina di riserva | **banco di prova** | fisicamente identica alla produzione: server IBM del 2007, Win7 SP1 x86 (build 7601), 4 GB, CPU **senza AVX**, con la sua scheda IAQ e il suo scanner (matricola 60210). Python 3.8 a 32 bit. Si comanda da AnyDesk. |
| PC di sviluppo | sviluppo | Windows a 64 bit, nessun WDK 7.1: il driver si compila su Linux (vedi [decisione 0004](decisioni/0004-compilazione-mingw-linux.md)) |

## Componenti

| Componente | Dove | Stato |
|---|---|---|
| Driver **EKframePnP 2.1** | [`driver/EKframePnP/`](../driver/EKframePnP/) | **funziona sulla riserva**: la scheda si avvia e il software la riconosce |
| Software **filmscan** | [`software/`](../software/) | nel repository dal 26/10, con le correzioni e il menu unico `MASTERFLEX.bat`; **da provare sulla riserva** |
| Carrello **Capri** (COM5) | [carrello-capri.md](hardware/carrello-capri.md) | firmware **1.13** dal 2/8/2025, deve essere **1.15** ([decisione 0006](decisioni/0006-firmware-carrello-1.15.md)) |

## Fatto il 26 ottobre

- **filmscan nel repository**, ripulito: niente compilati, database di
  prova, wheel, doppioni di documentazione, interfaccia tkinter e backend
  inutilizzati.
- **Errori corretti** (provati con l'agente simulato, Python 3.8.20 e numpy
  1.22.4):
  - il passaggio al fotogramma successivo usava la regolazione fine per 38 mm
    (`regola_3800`): ora usa il comando dell'SDK *advance_one_frame*;
  - nessun limite ai movimenti: ora al massimo 3 mm per comando e 300 passi
    nell'agente, e i tasti di movimento sono bloccati durante un lavoro;
  - **dopo ogni calibrazione tutte le scansioni fallivano**: la
    calibrazione era a 524 pixel, le scansioni a 2100;
  - dark e flat erano lo stesso fotogramma, perché l'SDK non spegne la
    lampada, e flat − dark veniva quasi zero: **immagini rovinate**;
  - i metadati portavano 2000–4000 dpi invece di 1569, e il ritaglio
    automatico sbagliava;
  - comandi e risposte del ponte si potevano leggere a metà;
  - l'errore dell'SDK stampava un indirizzo invece dei codici;
  - il formato del carrello non veniva mai selezionato;
  - numpy 1.24.4 in `requirements.txt` (ora 1.22.4 con impronte).
- **MASTERFLEX.bat**: un solo file con menu. Scarica e installa da solo
  Python 3.8.10 a 32 bit e le librerie giuste, e fa aggiornamento, avvio
  automatico, collaudi, diagnosi, pacchetto offline e controllo del firmware
  ([decisione 0005](decisioni/0005-masterflex-bat-unico.md)).
- **Collaudi dello scanner** (`collaudo_scanner.py`): colori e intensità,
  trascinamento a mano con lettura dei sensori, trascinamento comandato con
  verso e scala misurati sull'immagine.
- **Pacchetto CapriFirmware analizzato**: protocollo COM5, errori, selftest,
  firmware, backup del flash. Vedi [carrello-capri.md](hardware/carrello-capri.md).

## Problemi aperti, in ordine di priorità

1. **Motori delle guide all'avvio.** Le guide sono i motori di larghezza
   del carrello Capri, che all'accensione o al reset cercano lo zero
   ("nulling"). Il sintomo, fine corsa e continua a spingere, è quello di un
   nulling che non vede il riferimento. Il codice di filmscan non comanda
   le guide: le inizializza il carrello, all'apertura dell'SDK o al reset.
   Le correzioni ai movimenti di filmscan servono comunque, ma da sole non
   bastano. Ipotesi in ordine:
   1. il **firmware 1.13** imposto il 2/8/2025 al posto della 1.15, con il
      flash scritto dalla 1.15;
   2. il flag **Crash Detect** spento;
   3. la fotocellula di riferimento di una guida guasta o sporca.

   Le prove sono in [carrello-capri.md §5](hardware/carrello-capri.md#5-procedura-consigliata-per-il-problema-delle-guide):
   versione, selftest di servizio (HOME e PRECISION delle guide),
   SensorTest, flag. Finché non è chiuso, usare **"motori bloccati"** o la
   simulazione. Attenzione: anche con i motori bloccati l'apertura dell'SDK
   può far partire il nulling. `APERTURA=leggera` in `MASTERFLEX.bat` serve a
   capire se è così.
2. **Le immagini non arrivano correttamente.** Due cause in filmscan sono
   corrette (calibrazione e dark/flat, vedi sopra). Il sintomo va
   ricontrollato con la versione nuova: `MASTERFLEX.bat` → 4 → 1 (colori e
   intensità, finestra vuota). Se le immagini sono ancora sbagliate, la
   prova che distingue resta ScanDiag/GretagEMEval: sbagliate anche lì vuol
   dire driver (DMA o ISR).
3. **IR**: il numero di canale 3 non è verificato. Il collaudo colori con
   `--ir` lo dice.
4. **Sensori e verso del trasporto** (piano 3.12 e 3.13): si misurano con
   il collaudo dello scanner, voci 2 e 3.

## Materiale chiesto e non ancora arrivato

- [ ] **i file del firmware 1.15 del carrello** (master e slave `.a37`,
      compilati il 28/11/2002): sulla produzione, in `AutomaticUpdate\`
- [ ] l'esito del selftest di servizio, del collaudo colori e della diagnosi
      (`MASTERFLEX.bat` voci 4, 6, 13) sulla riserva
- [ ] una foto delle immagini difettose, se lo sono ancora
- [ ] i log di `C:\MasterflexD\ponte\log` e `C:\MasterflexD\Scanner\SDKCacheDir`
      (non vanno nel repository: possono contenere dati dei clienti)
- [ ] dalla cartella di lavoro locale: `verifica-equivalenza/esegui.sh` con il
      banco di prova C ([dettagli](../driver/EKframePnP/verifica/README.md)) e
      le analisi di agosto (`storico-agosto/`)

## Prossimi passi

1. Copiare sulla riserva la cartella `software/` e lanciare
   `MASTERFLEX.bat` → 7 (installa/ripara). Sistema numpy se serve, e
   all'aggiornamento toglie i vecchi `.bat`.
2. Copiare `CapriFirmware` in `C:\MasterflexD\CapriFirmware`, poi voce 13
   (firmware) e voce 4 → 5 → 1 (selftest di servizio).
3. Cercare la 1.15 sulla produzione e reinstallarla con la voce 13.
4. Voce 2 (motori bloccati) e collaudo colori: le immagini sono giuste?

## Avvertenze operative

- Non lanciare `ektest` mentre filmscan o l'agente sono aperti: alla
  chiusura dell'handle il driver spegne la parte logica della scheda, come
  faceva l'originale. La diagnosi del menu lo salta da sola se l'agente è
  attivo.
- La COM5 la può aprire un solo programma: prima degli strumenti Gretag
  chiudere l'agente e il programma originale.
- Sulla produzione, il giorno in cui si installerà EKframePnP, `INSTALLA.bat`
  si fermerà finché il vecchio `ekframe.sys` non è tolto. Serve un riavvio.
- Su quella CPU: numpy non oltre la 1.22.4, scipy fermo alla 1.9.1.
- Il programma originale, all'avvio, impone al carrello il firmware del suo
  ini: con l'ini attuale, la 1.13.
