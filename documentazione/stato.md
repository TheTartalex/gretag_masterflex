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
| Carrello **Capri** (COM5) | [carrello-capri.md](hardware/carrello-capri.md) | firmware **1.15** dal 26/10/2026 sulla riserva ([decisione 0006](decisioni/0006-firmware-carrello-1.15.md)) |

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
- **Firmware del carrello disassemblato e backup del flash decodificati**
  (sera):
  - trovati l'algoritmo dello zero, i limiti di passi e le fotocellule;
  - trovato il formato del flash e il vero significato dei flag;
  - `SelfTestInterface` nel pacchetto è sulla COM1: il menu ora lo porta
    sulla COM5 da solo;
  - la voce 13 mostra flag, matricola e posizioni salvate.

- **Prima diagnosi dalla riserva** (16:52), con questi risultati:
  - i due collaudi si sono fermati alla connessione, perché l'agente era
    chiuso dalle 16:49;
  - il rapporto non lo diceva, e l'agente non aveva lasciato registri in
    `ponte\log`;
  - `AGGIORNA.bat` non aggiornava `collaudo_scanner.py` (mancava nella lista
    `CODICE`).

  Corretto: l'errore va nel rapporto; la diagnosi mostra lo stato della
  cartella del ponte e gli ultimi collaudi; lo zip porta `ektest.exe`;
  `prepara.sh` controlla la lista.

- **Prima prova dell'agente sulla riserva** (17:11), con i codici decifrati
  in `scn_dll.dll` ([api-sdk.md §7](software-originale/api-sdk.md#7-errori-e-stati-dellsdk)):
  - `get_gate_type` ha dato `ERR_NO_SCANNER` e `ERR_ILLEGAL_IN_THIS_STATE`
    con lo stato 10;
  - `scan_raw` ha dato `ERR_CANCEL_DETECTED`;
  - alle 17:11:28 è arrivato `EV_STATE_CHANGE` da 10 a 0.

  **Causa**: dopo l'apertura l'SDK collega lo scanner in sottofondo, per
  circa 40 s, e in quel tempo (stato 10) rifiuta i comandi. Il collaudo era
  partito prima. Ora l'agente segue lo stato, rifiuta i comandi con un
  messaggio chiaro finché non è pronto e scrive "scanner pronto dopo N s";
  il ponte aspetta fino a 120 s.

  Che anche `ERR_CANCEL_DETECTED` venga dallo stato 10 è da confermare
  rifacendo il collaudo a scanner pronto.

- **Diagnosi completa delle 17:35** sulla riserva:
  - **il programma originale acquisisce immagini con EKframePnP**: alle
    17:03 il log dell'SDK mostra decine di `xfer: image complete ... ok=1`
    in RGB 2100x1400. Driver, DMA e testa di scansione funzionano;
  - **il carrello è tornato alla 1.15** (backup delle 16:59), con l'ini che
    ora la chiede;
  - l'agente aspetta lo scanner pronto (39 s);
  - `scan_raw` falliva per colpa nostra: passavamo `x, y, larghezza,
    altezza`, mentre l'SDK vuole colonne inizio–fine e righe inizio–fine,
    a partire dall'offset XY del formato. L'originale usa `[47,76] to
    [2146,1475]`, noi mandavamo `[0,2100] to [0,1400]`, e il firmware
    rispondeva "set resolution failed, ErrNo = 1". Corretto
    ([api-sdk.md §7](software-originale/api-sdk.md#7-errori-e-stati-dellsdk));
  - `regola` (`frame_detect_fine_adjust`) a percorso vuoto risponde
    `ERR_ILLEGAL_IN_THIS_STATE`. È normale: tutti i `frame_detect_*` sono
    vietati nello stato 0 e servono con la pellicola caricata.

- **Prime immagini grezze dallo scanner vero** (17:45–17:52): `scan_raw`
  con la finestra corretta dà R, G e B a 2100x1400 in circa 1,2 s a
  canale. La calibrazione è passata.
- **Il carrello non accettava la pellicola**: `select_film_format` sceglie
  il formato nell'SDK ma non arma il carrello. Il programma originale
  chiama anche `set_scanner_input_mode`, con modo 1 o 2 (`PhxScanner.exe`
  `0x447860`), che per il Gretag diventa il ChangeFilmFormat del
  GateControl: guide al formato e carrello in attesa (`scn_dll`
  `0x18037FA0`).

  Nuovo tasto **Carica** nella barra (agente: `carica_1`). L'agente annota
  anche l'evento `EV_FILM_INSERTED`. Quale dei modi 1 e 2 sia il rullo
  intero va visto sulla macchina.

- **Collaudo colori sullo scanner vero (18:08)**: le immagini erano al
  livello del buio (media 19 su R, G e B) e non cambiavano con
  l'esposizione. Ci sono due cause possibili, e vanno provate tutte e due:
  1. `scan_raw` veniva chiamato con il booleano a `True`, cioè il bit
     `0x80` del comando, che con ogni probabilità vuol dire lettura al buio
     (tutte le acquisizioni d'immagine dell'SDK lo passano `False`).
     Corretto;
  2. il **tamburo dei formati** fermo fra due posizioni davanti alla luce
     (segnalato da Alessandro). Prima del collaudo si preme **Carica**, che
     porta tamburo e guide al formato.

  Il salvataggio delle PNG del collaudo era tanto lento da sembrare
  bloccato: ora a metà lato e con compressione minima, meno di 5 s.

- **Collegamento "programma originale"**: voce 14 di `MASTERFLEX.bat` e
  `ponte/osservatore.py`. Il programma originale scansiona, l'osservatore
  copia le immagini grezze e filmscan le elabora nel rullo
  ([decisione 0007](decisioni/0007-osservatore-programma-originale.md)).
  - Provato qui: indirizzi sulla `scn_dll.dll` della macchina; immagini
    finte che diventano fotogrammi del rullo.
  - **Non provato** su Windows: l'aggancio al processo.
  - Da verificare con le prime immagini: l'ordine dei piani R, G, B.

- **Lettura vuota dopo il carico (sera)**: la pellicola entrava, ma la
  finestra era sulla coda iniziale, non su un fotogramma. Nuovi comandi:
  - **Fotogramma ›** nella barra e **‹ / ›** nella scheda Macchina:
    `gretag_NextFrame` / `gretag_PreviousFrame`, il carrello cerca il
    fotogramma. Procedura: Carica → inserire → Fotogramma › finché c'è un
    fotogramma in finestra → Anteprima;
  - **Espelli** (barra) e **Espelli davanti / Espelli retro** (scheda
    Macchina). Davanti è il comando di espulsione del carrello, che esce
    dal davanti perché il flag 5 è a 0. Retro è "fotogramma successivo"
    ripetuto finché il carrello rifiuta, al massimo 45 fotogrammi o 180 s
    ([api-sdk.md §7](software-originale/api-sdk.md#7-errori-e-stati-dellsdk)).
    A rullo finito filmscan espelle dal retro
    (`verso_espulsione_fine_rullo`);
  - l'**anteprima** non fa più ritaglio e raddrizzamento automatici, la
    parte più lenta. Si fanno nella scansione del rullo.

  Da vedere sulla macchina: che errore dà l'SDK quando la pellicola è
  uscita dal retro (lo scrive il registro dell'agente).

- **Primo rullo vero, 20 fotogrammi (19:00)**: il trasporto va, G a x1 si
  legge (2100x1400 in 1,4–2,3 s), ma **ogni R a x12 è rifiutato** dal
  firmware della testa (46/47/42, come per la finestra sbagliata), quindi
  nessun fotogramma completo. Il x12 veniva dalla misura dell'esposizione
  fatta sulla coda iniziale annerita, non su un'immagine. Dopo il
  ventesimo, `prossimo` risponde **`ERR_MOVE` (31)**: è la fine della
  striscia. Correzioni:
  - il ponte impara il limite dell'esposizione: al rifiuto dimezza e
    riprova, poi non chiede più di così. Nei metadati va l'esposizione
    usata davvero. L'SDK da solo taglia a 30000 unità per R, G e B
    (`0x1807C710`); il limite del firmware va misurato, si sa solo che x1
    va e x12 no;
  - una misura sotto il 3% della luce non vale: si resta a x1 e lo si dice;
  - `ERR_MOVE` sul fotogramma successivo chiude il rullo come fine striscia;
  - con l'SDK la fine striscia la dice il carrello: niente più lettura di
    prova in G a ogni fotogramma;
  - **fotogrammi vuoti** (non esposti o tutti opachi, anche 3–4 in mezzo
    alla striscia): sotto 0,10 D di escursione il fotogramma si salta, si
    segna "vuoto" nella striscia e il grezzo si conserva comunque.

  Le immagini di quel rullo non si recuperano: il solo G letto stava in
  memoria, e un fotogramma si salva solo a canali completi.

  Da vedere: dove finisce la pellicola dopo `ERR_MOVE` (l'espulsione dal
  retro a quel punto risponde subito `ERR_MOVE` anche lei), e come si
  comporta il carrello sui fotogrammi non esposti.

- **Avvio e modi di lavoro** ([decisione 0008](decisioni/0008-avvio-automatico-e-modi.md)):
  - avvio automatico dall'Utilità di pianificazione, da amministratore e
    senza richieste; parte con l'ultimo modo usato;
  - modo **completo**: chiude tutto ciò che parte da `C:\MasterflexD`
    (solo prima di avviare l'agente), poi agente e interfaccia, che si
    connette da sola a scanner pronto;
  - modo **man in the middle**: non chiude niente, niente agente, aspetta
    `PhxScanner.exe` e apre osservatore e interfaccia già connessa;
  - **calibrazione obbligatoria ogni giorno**: senza una calibrazione di
    oggi anteprima e scansione si rifiutano;
  - **azzeramento dei motori** come primo passo della calibrazione:
    `ResetScanner` dell'SDK, che manda al GateControl la richiesta 14,
    "Gretag Reset" (`0x18038100`, da `call_gretag_reset` `0x18079510`).
    Il programma originale non la chiama: la fa il GateControl quando
    parte. Con l'agente a motori bloccati si salta. Lo zero lo fa il
    firmware del carrello, con i suoi limiti (750 passi in avanti, 200
    indietro per le guide).

  Da provare sulla riserva, nell'ordine:
  1. `python processi.py`, per vedere cosa verrebbe chiuso;
  2. voce 9, poi "prova adesso";
  3. Calibra a motori attivi, guardando le guide.

- **Esposizione, pressore, Digital ICE, video** (sera):
  - scheda Macchina, *Impostazioni macchina*:
    - **esposizione automatica**: tempi calibrati dell'SDK (`calibrate_all`
      dentro Calibra, poi `get_exp_time`) più la misura per canale; spenta,
      tempi di fabbrica e x1. La calibrazione ricorda con quale base è stata
      fatta: cambiandola va rifatta;
    - **pressore automatico** (acceso di serie): giù per ogni lettura, su
      prima di muovere. Se non si rialza, il rullo si ferma
      (`PressoreBloccato`);
    - **avanzamento automatico**: spento, ogni fotogramma si mette in
      finestra a mano;
    - temperatura della testa a richiesta;
  - **Digital ICE** si sceglie alla creazione del rullo: ASF dell'SDK più la
    nostra pulizia IR, che però resta spenta finché l'IR dell'SDK non è
    verificato;
  - **posizionamento a mano con video** (verde a 262x175, ~1 immagine al
    secondo), passi da 1 e 20 mm. Si apre da solo se il carrello non trova
    il fotogramma, o ad avanzamento automatico spento;
  - **numeri dal bordo**: campo e riordino a fine rullo pronti; la lettura
    no (vedi api-sdk.md §8).

  Da provare sulla macchina, nell'ordine:
  1. Calibra con l'esposizione automatica: deve comparire "Calibrazione
     della macchina fatta". Poi il collaudo colori: i livelli cambiano?
  2. Il pressore durante l'anteprima: si sente scendere e salire?
  3. Il verso e la scala dei passi da 1 e 20 mm nel video.
  4. Un rullo con Digital ICE: cosa cambia con l'ASF acceso.

## Problemi aperti, in ordine di priorità

1. **Motori delle guide all'avvio.** Le guide sono i motori 3 e 4 del
   carrello Capri. Lo zero lo fa il firmware: ogni guida va verso la sua
   fotocellula di riferimento per al massimo 750 passi, poi si ferma con
   l'errore 37507 o 37508 e resta in spinta. Il sintomo, fine corsa e
   continua a spingere, è quello di una fotocellula che non si copre mai
   ([carrello-capri.md §1](hardware/carrello-capri.md#1-i-motori-e-come-trovano-lo-zero)).
   Con il flag "No Reset" acceso, all'accensione i motori non si muovono:
   lo zero lo chiede il PC. Il codice di filmscan non comanda le guide.
   Ipotesi, in ordine:
   1. la **fotocellula** di una guida, sporca, spostata o guasta; per tutte
      e due le guide insieme, alimentazione, connettore o cavo comuni;
   2. il PC che ripete l'inizializzazione, se la spinta riparte più volte;
   3. il **firmware 1.13** al posto della 1.15: improbabile, perché il
      flash ha lo stesso formato e la 1.13 ha già lavorato dal 2002 al 2004.

   Esclusa l'ipotesi del flag Crash Detect spento: nel backup del 2/8/2025 è
   acceso, come tutti i flag consigliati da Gretag.

   Le prove sono in [carrello-capri.md §6](hardware/carrello-capri.md#6-procedura-consigliata-per-il-problema-delle-guide):
   accensione senza programmi, selftest di servizio (HOME e PRECISION delle
   guide), SensorTest, flag. Finché non è chiuso, usare **"motori bloccati"**
   o la simulazione. Attenzione: anche con i motori bloccati l'apertura
   dell'SDK può far partire lo zero. `APERTURA=leggera` in `MASTERFLEX.bat`
   serve a capire se è così.
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

- [x] `scn_dll.dll`, `PhxScanner.exe`, i proxy COM e `PhxTextDB.mdb`
      (arrivati il 26/10, fuori dal repository): codici decifrati

- [x] i file del firmware 1.15 del carrello: installati sulla riserva il
      26/10. Mancano le impronte in `manutenzione.py`: lo slave risulta
      "non riconosciuto" (servono i due `.a37`, che restano fuori dal
      repository)
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

- Nel log dell'SDK della riserva compaiono tre messaggi da tenere presenti:
  - `CalibDataDir … last err= 2` e `CheckForFirmwareMismatch … 135anm.wrp`
    c'erano anche nella sessione precedente, prima dei nostri cambi;
  - `ioctl failed (2): 87` / `unable to unregister buffer #1, #2` compare
    alla chiusura. Lo dà EKframePnP quando si sgancia un buffer che non
    risulta agganciato (`EkDmaRelease`, ricostruito da `0x121BE`).

  Va confrontato con il driver originale nel banco di prova. Se i buffer
  vengono sganciati prima del tempo, le immagini ne risentono.

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
