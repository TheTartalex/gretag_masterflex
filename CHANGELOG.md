# Registro delle modifiche

Una sezione per componente; dentro, le versioni dalla più recente.
Le versioni del driver sono quelle scritte nell'`.inf` (`DriverVer`).

## Repository

### 2026-10-28 — guide del carrello risolte col firmware 1.15

- Il problema delle guide all'avvio è risolto (135, riserva) tornando al
  firmware 1.15. Decisione 0006 confermata con l'esito; corretti
  carrello-capri.md §2 (il firmware era la causa), stato, README e la
  lezione in `CLAUDE.md`.

### 2026-10-26 — README

- `README.md` riscritto: cosa fa filmscan oggi, stato, installer unico,
  struttura con `strumenti/aggiornamento/`. `installazione.md` segnato come
  storico.

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

### 2026-10-31 — pressore e DX spenti di serie, prova del pressore

- Dalla riserva: con il DX acceso il rullo dà errore; con il pressore
  automatico il pressore scende ma il "su" fallisce. Tutti e due spenti di
  serie (`leggi_dx`, `pressore_auto`) finché non sono provati.
- Collaudo dello scanner, voce 7: prova guidata del pressore (giù, su, su
  con il pressore già su, giù + lettura + su), con l'operatore che dice
  cosa vede. Risponde a: la lettura rialza il pressore da sola?

### 2026-10-31 — letture bloccate dopo il codice DX

- **Dopo la lettura del DX a inizio rullo l'SDK passa allo stato 1** e
  l'agente, che accettava comandi solo nello stato 0, rifiutava ogni
  lettura ("non ancora pronto (stato 1)"). Ora l'agente si ferma da sé solo
  negli stati 7 e 10 (scanner non collegato); negli altri decide l'SDK.
- Nomi provvisori per gli stati 1 (dopo `get_media_info`) e 4 (durante
  `calibrate_all`).
- Impostazioni macchina: "Leggi il codice DX" (`leggi_dx`), per spegnere la
  lettura del DX senza toccare altro.

### 2026-10-31 — una cartella sola per i registri

- `software/log` (`registri.py`): `programma` (diario di filmscan su file,
  errori con traceback, problemi del ponte, crash di Python), `agente`
  (agente, osservatore, crash), `sdk` (copia del registro dell'SDK a ogni
  avvio dell'agente), `collaudi`, `diagnosi`, `da-mandare`. File più vecchi
  di 60 giorni tolti da soli. Niente più file nostri in `C:\MasterflexD`
  (prima: `ponte\log`).
- Zip da mandare: MASTERFLEX.bat voce 6 (con la diagnosi, poi apre la
  cartella) o Macchina → Scarica i registri; il PIN fisso non ci finisce.

### 2026-10-31 — Calibra senza azzeramento doppio

- L'agente pubblica in `stato.json` quando lo scanner si è caricato
  (`caricato`): a ogni caricamento il GateControl azzera già i motori.
  Calibra rifà l'azzeramento solo se lo scanner non si è caricato oggi o se
  c'è stata un'emergenza dopo (`azzera_in_calibrazione = "sempre"` per
  rifarlo comunque). Si risparmia un ricaricamento dell'SDK (40-90 s).

### 2026-10-31 — dalla riserva: calibrazione, ponte, collaudo

- **Calibra si fermava dopo l'azzeramento**: dopo il Gretag Reset l'SDK
  ricarica lo scanner (stato 7 → 10 → 0, ~40 s) e l'agente rifiutava il
  comando successivo con "non ancora pronto". `PonteSdkBackend` ora aspetta
  e ripete i comandi rifiutati così (fino a 180 s); la calibrazione lo dice.
- **File del ponte bloccati per un istante** (PermissionError, errno 13,
  sulla risposta di un `grab`): le letture di risposte e grezzi si
  ripetono per 2 s invece di far cadere collaudo o fotogramma.
- Collaudo colori: saturazione misurata sul fondo scala vero (12 bit,
  4095; prima 65000, che dava 0% con mezzo fotogramma saturo); esposizioni
  di serie da 0,125x.
- Collaudo SDK: senza pellicola arma il carrello e aspetta la striscia,
  invece di lanciare le sonde a vuoto (DX: errore 12, nessuna pellicola).
- Calibrazione: la nota dice quanta parte del flat è a fondo scala.

### 2026-10-31 — pellicola dal DX, bianco e nero, WhatsApp

- `pellicole_dx.py`: i 355 codici dell'elenco I3A "DX Codes for 135-Size
  Film" (gennaio 2009) con tipo (B/N all'argento, B/N cromogenica, C-41,
  diapositiva, diapositiva B/N), ISO e tecnologia; attese per tipo e
  sviluppo (pendenza, grana, scala delle densità).
- Rullo: il DX corregge da solo il profilo se il tipo non è quello scelto
  a mano (archivio e nota dell'ordine), spegne l'IR e l'ASF sul B/N
  all'argento, passa al preset diapositiva quando serve. Ogni striscia
  rilegge il DX: una pellicola diversa dà un avviso e si elabora a parte
  (profilo, equilibrio, memoria ed esposizione suoi). Statistiche salvate
  per pellicola.
- Rulli: colonna `sviluppo` (normale, forzato +1/+2/+3, tirato −1),
  scelta nel modulo del rullo; pendenza attesa nell'inversione e soglie
  delle classi scalate.
- Profili nuovi `bn_c41` (XP2, BW400CN) e `bn_diapositiva` (Scala).
- `bianconero.py`: toni (naturale, alto, basso, gamma estesa, duro) e
  viraggi (seppia, selenio, carta calda e fredda, cianotipia), in Ritocco,
  applicati subito.
- Grana attesa: riduzione leggera di serie per le pellicole granulose,
  solo se l'operatore non ne sceglie una.
- Metadati: pellicola riconosciuta nella descrizione, ISO in EXIF
  (`ISOSpeedRatings`), parole chiave di Windows (`XPKeywords`), XMP nei
  TIFF e nei DNG.
- WhatsApp: chat su WhatsApp Web (sempre la stessa scheda), sull'app o su
  un dispositivo collegato; sul PC della macchina provino o foto negli
  appunti (Ctrl+V nella chat, a scelta incollato da solo se in primo piano
  c'è WhatsApp); Condividi dal tablet (Web Share); a ordine finito il
  tasto "Avvisa il cliente", o la chat aperta da sola.
- **Correzione**: il provino a contatto saltava i TIFF RGB a 16 bit
  (Pillow non li legge): ora passano da tifffile.
- Simulatore: `FILMSCAN_DX=109-9` finge un codice DX.

### 2026-10-29 — rullo, classi di esposizione, densità locale, sonde SDK

- **Correzione**: `invert_negative` ora inverte (10^(+D/γ), γ 0,6);
  `MockBackend` fa un negativo vero.
- `rullo.py`: base ed equilibrio dei grigi di rullo, classi (sottoesposto,
  sovraesposto, velato, piatto), tungsteno, velo, memoria per pellicola
  (`pellicole.json`, chiave DX); collegato a rullo, anteprima rapida,
  Riapri/Salva/Applica.
- `enhance.densita_locale` (filtro guidato) e cursore in Ritocco.
- Rulli: colonna `dx`; DX nei metadati dei file.
- SDK/agente: `dx`, `sonda_capture`, `sonda_ice`; funzioni statiche
  dell'SDK chiamate come `__cdecl`. Collaudo dello scanner: prova `sdk`
  (menu, voce 6).

### 2026-10-29 — profili Gretag, codici d'errore dell'SDK

- `lut.py`: i profili CMM del programma originale (`*.lut` binari, magia
  0x9E62A68F, verso Lab D50) diventano LUT 3D sRGB→sRGB.
- `ponte/sdk.py`: nomi e spiegazioni per 29, 30, 35, 37 (pellicola inserita
  al contrario), 38; il registro dell'agente le scrive anche per gli errori
  asincroni.
- Documento `software-originale/elaborazione-originale.md`.

### 2026-10-28 — PIN fisso delle postazioni

- PIN fisso oltre a quello dell'avvio: impronta PBKDF2 in `impostazioni.json`
  (`pin_fisso`), impostato da Macchina; apre la sessione e reindirizza al
  PIN dell'avvio; "Ricorda" sul dispositivo.

### 2026-10-28 — riaprire i fotogrammi, Stampa/Scarta, metadati, DNG

- Striscia: un fotogramma si riapre dal grezzo; barra con Stampa, Scarta,
  Salva, Applica al rullo, Chiudi.
- Archivio: voto per fotogramma (`voto`), zip delle foto da stampare e dei
  negativi lineari.
- Metadati dentro TIFF e JPEG (descrizione, data, dpi, Make/Model, Software).
- Negativo lineare in DNG (LinearRaw) o TIFF 16 bit (`negativo_lineare`).

### 2026-10-28 — rullo veloce

- Lettura unica di R, G e B (`grabm`, `scan_raw` canale 4, un tempo per LED
  con `operator*=(const double*)`), disposizione dei piani riconosciuta da
  sola, verifica sul primo fotogramma e ritorno al canale per volta.
- Grezzo salvato e elaborato in sottofondo, coda a tetto di memoria
  (`memoria_elaborazione_mb`), espulsione prima di aspettare l'elaborazione.
- Protocollo col ponte a 10 ms; tempi di lettura e trasporto nel registro.

### 2026-10-28 — Scansiona rullo con Carica, anteprima in diretta

- Scansiona rullo prepara il carrello e parte da solo quando la pellicola
  è inserita (`_attendi_pellicola`); con la pellicola già dentro parte
  subito.
- Tasto *Diretta*: il video della macchina nel visore (`/api/diretta.jpg`),
  negativo o positivo.
- Rullo: anteprima ridotta subito dopo la lettura (`anteprima_rapida`) ed
  elaborazione completa in sottofondo (`_ElaborazioneInFondo`); la pagina
  aggiorna visore e striscia a rullo in corso (`job.anteprime`).

### 2026-10-27 — esposizione misurata sul primo fotogramma vero

- La misura dell'esposizione per canale si fa sul primo fotogramma con
  luce, non sulla coda annerita (che chiedeva x12): soglia
  `soglia_misura_esposizione` (15%), lettura a x1 delle zone opache, x1 con
  avviso alla terza.
- Niente autofocus sulle zone opache; la coda lunga piu' posizioni non
  prende numeri.
- Agente: `log/crash-*.txt` (faulthandler) per i crash dentro l'SDK.

### 2026-10-26 — autofocus a scelta, video dell'SDK, finestre Gretag, LUT

- Impostazioni macchina, *Autofocus*: `gi_focus` completo (di serie),
  breve, posizione nominale, spento; sul primo fotogramma di ogni striscia
  e col tasto Fuoco.
- Posizionamento a mano col video dell'SDK (`perform_video_mode_scan`);
  ripiego sulla lettura veloce.
- Scheda Macchina: *Finestre del carrello* (`show_gretag_ui`, 1/3/4/5).
- `filmscan/lut.py`: LUT `.cube`, `.lut` (Discreet 1D), `.3dl`; in fondo
  alla scheda Colore, applicata per ultima a tutti gli ordini, con
  intensità; scelta in `impostazioni.json`, file in `software/lut/`.
- Interfaccia: telefono e tablet verticale senza sovrapposizioni né
  scorrimento orizzontale.

### 2026-10-26 — stato della testa, Alza pressore

- `get_voltage`: le 8 tensioni della testa (SupplyVoltage, 8 double);
  scheda Macchina, "Stato della testa" con temperature e tensioni.
- "Alza pressore" a mano; vale anche con lo STOP EMERGENZA (ponte e agente
  lasciano passare solo `pressore_su`).
- api-sdk.md §9: l'elenco completo dei 119 metodi.

### 2026-10-26 — esposizione automatica, pressore, Digital ICE, video

- SDK: `calibrate_all`, `get_exp_time` + `operator=`, `gi_clamp`,
  `get_temperature`, `gi_set_asf_mode`; agente: `calibrasdk`,
  `esposizione_auto|fabbrica`, `pressore_giu|su`, `temperatura`, `ice_on|off`.
- Scheda Macchina: esposizione automatica, pressore automatico, avanzamento
  automatico (salvati in `impostazioni.json`), temperatura della testa.
- Calibra: azzeramento, poi calibrazione della macchina, poi dark/flat; la
  nota ricorda la base d'esposizione.
- Digital ICE per rullo (colonna `ice`): ASF dell'SDK e pulizia IR; senza,
  niente lettura IR.
- Posizionamento a mano con video, passi da 1 e 20 mm; si apre se il
  carrello non trova il fotogramma o ad avanzamento automatico spento.
- Numeri dal bordo (colonna `numero_bordo`) e riordino a fine rullo, con i
  file rinominati; la lettura dalla macchina non c'è ancora.
- `ERR_END_OF_FILM` (32) vale come fine striscia, come `ERR_MOVE`.
- Simulatore: pressore che blocca i movimenti, passi che spostano il
  fotogramma, `FILMSCAN_SIM_NON_TROVATO`.

### 2026-10-26 — rulli in strisce, Fine rullo, STOP EMERGENZA

- Rullo tagliato in strisce: a fine striscia si espelle, si riarma il
  carrello e si aspetta la successiva (`attesa_striscia_s`, 10 minuti); i
  numeri continuano, la coda iniziale non prende un numero.
- Scansiona rullo (verde) diventa Fine rullo (rosso) a rullo in corso:
  chiude dopo il fotogramma, espelle, rullo completato.
- STOP EMERGENZA: file `EMERGENZA.json` nel ponte, rispettato da ponte e
  agente; blocca subito ogni comando, copre gli schermi, sopravvive ai
  riavvii; si toglie con il nome del tecnico, poi serve una calibrazione.
- Simulatore: tre strisce con la coda dalla seconda.

### 2026-10-26 — installer unico

- `INSTALLA.bat` al posto di `AGGIORNA.bat`: installa su una macchina
  pulita o aggiorna quella che c'è, da solo. Si chiede i diritti di
  amministratore, poi fa *Installa / ripara* e propone l'avvio automatico.
- `prepara.sh` fa un solo zip, `masterflex-installer-*`, con Python 3.8.10
  e le wheel win32 dentro (`--leggero` per farne a meno); i pacchetti si
  tengono in `dist/cache-pacchetti`.

### 2026-10-26 — Disconnetti

- Interfaccia: Connetti tolto dalla barra principale (resta nella scheda
  Macchina), accanto c'è **Disconnetti** (`ScanService.disconnetti`,
  `/api/disconnetti`). Rifiuta a lavoro in corso e ferma anche la
  connessione automatica, che altrimenti si ricollegherebbe da sola.

### 2026-10-26 — avvio automatico, modi di lavoro, calibrazione giornaliera

- Avvio automatico da un'attività dell'Utilità di pianificazione, da
  amministratore senza richieste (`manutenzione.py avvio on|off|stato|adesso`,
  `MASTERFLEX.bat automatico`). Tolto il vecchio `.bat` in Esecuzione
  automatica.
- `processi.py` e `manutenzione.py termina`: chiudono i processi sotto
  `C:\MasterflexD`, solo prima di avviare l'agente.
- Modi di lavoro completo e man in the middle; l'ultimo usato si ricorda
  (`modo-avvio.txt`) ed è l'Invio del menu.
- `run.py --connetti`: l'interfaccia si connette da sola a scanner pronto.
- Calibrazione: prima l'azzeramento dei motori (`ResetScanner` → Gretag
  Reset, agente `azzera`); senza una calibrazione di oggi anteprima e
  scansione si rifiutano, e Calibra è evidenziato.
- Decisione 0008.

### 2026-10-26 — esposizione, fine striscia, fotogrammi vuoti

- `PonteSdkBackend`: al rifiuto del firmware (codice 47) sopra x1
  l'esposizione si dimezza e il limite si ricorda (`max_esposizione`);
  `acquire` scrive nei metadati quella usata.
- `measure_channel_exposure`: misura sotto il 3% della luce → x1 con avviso.
- `FinePellicola`: `ERR_MOVE` su "fotogramma successivo" chiude il rullo.
  Con l'SDK niente più `film_present` a ogni fotogramma.
- `fotogramma_vuoto` (escursione sotto `soglia_fotogramma_vuoto_d`, 0,10 D):
  fotogramma saltato, stato "vuoto", grezzo conservato.
- Simulatore dell'agente come la riserva: rifiuta oltre x4, `ERR_MOVE`
  dopo 8 fotogrammi, due fotogrammi vuoti.

### 2026-10-26 — fotogrammi ed espulsione

- Comandi `prossimo`/`precedente` (`gretag_NextFrame`/`PreviousFrame`),
  `espelli` (davanti, `cancel_scan`) ed `espelli_retro` (fotogramma
  successivo finché il carrello rifiuta, con tetto) nell'agente e nel
  ponte. Nell'interfaccia: Fotogramma › ed Espelli nella barra, ‹ / › ed
  Espelli davanti/retro nella scheda Macchina.
- A rullo finito si espelle dal retro (`verso_espulsione_fine_rullo`).
- Anteprima senza ritaglio né raddrizzamento automatici.
- Tolto da `PonteSdkBackend` un vecchio `eject()` che rispondeva "non
  disponibile" e copriva quello nuovo.

### 2026-10-26 — collegamento "programma originale"

- `ponte/osservatore.py` (nuovo): si aggancia a `PhxScanner.exe` come un
  debugger e copia in `<ponte>/mitm/` le immagini grezze che l'SDK riceve
  (int3 su `add_image_buffer`, `remove_all_image_buffer` e sul registro
  interno, filtrato su "xfer: image complete"). Rifiuta DLL diverse da
  quella studiata.
- `filmscan`: driver `originale` (`OriginaleBackend`). "Scansiona rullo"
  raccoglie quelle immagini nel rullo fino a STOP
  (`ScanService.ricevi_da_originale`).
- `MASTERFLEX.bat`: voce 14. `run.py --driver originale`.
- Decisione 0007.

### 2026-10-26 — immagini nere, pellicola dall'SDK

- `ponte/sdk.py`: `scan_raw` con il bit del buio spento (prima `True`:
  immagini al livello del buio). Lettura al buio a richiesta:
  `grab_<canale>_<res>_<esp>_buio`.
- `ponte/agente.py`: `stato.json` nel ponte quando cambiano lo stato
  dell'SDK o la pellicola inserita.
- Interfaccia: in alto "pellicola inserita / non inserita" dall'evento
  dell'SDK, invece di "ingressi non mappati".
- `collaudo_scanner.py`: PNG a metà lato e con compressione minima.

### 2026-10-26 — carrello in attesa della pellicola

- `ponte/sdk.py`: `arma_carrello(modo)` con `set_scanner_input_mode`;
  l'evento `EV_FILM_INSERTED` imposta `pellicola_inserita`.
- `ponte/agente.py`: comando `carica[_<modo>]` (è un movimento, quindi
  bloccato con i motori bloccati); `pellicola_inserita` nello stato.
- `filmscan`: `prepara_carico()` nel ponte, `carica()` nel servizio, tasto
  **Carica** nella barra dei comandi.
- Sotto i 1100 px l'avanzamento sta sopra i tasti.

### 2026-10-26 — interfaccia per il monitor 4:3

- `filmscan/webui.py`, pagina di scansione rifatta per il monitor della
  macchina (4:3, al massimo 1280x720):
  - tutto in uno schermo senza scorrere;
  - schede a sinistra (Lavoro, Colore, Ritocco, Macchina, Diario) come
    sottomenu, una alla volta;
  - l'immagine al centro, con sotto la striscia del rullo;
  - una barra fissa con i comandi principali: Connetti, Calibra, Anteprima,
    Scansiona rullo, Espelli.

  Provata a 1280x620, 1280x720, 1024x668, 960x640 e in verticale. Gli
  avvisi vanno nel Diario, con un numerino sulla scheda. L'Archivio non
  cambia.
- Chrome si apre in modalità app, senza barre.
- La scritta sui sensori dice cosa manca: gli ingressi non ancora mappati.

### 2026-10-26 — finestra di scan_raw

- `ponte/sdk.py`: `scan_raw` riceve colonne e righe inizio–fine a partire
  dall'offset XY del formato, come il programma originale (`[47,76] to
  [2146,1475]` per il 135). Prima riceveva `x, y, larghezza, altezza`, e il
  firmware rifiutava con l'errore 1.
- Dimensioni delle letture grezze per risoluzione: 2100x1400, 1050x700,
  525x350, 262x175, 131x87 (simulatore e collaudo allineati).

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
