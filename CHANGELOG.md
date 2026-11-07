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

### 2026-11-07 (sera) — ripristinata la resa delle 19:13, desktop

- Tolte l'esposizione sui toni medi e la nuova misura della base (provate
  per un'ora): Alessandro preferisce nettamente la resa della versione
  delle 19:13 (confronto sui fotogrammi 10 e 12 del rullo 16). Il codice di
  colore ed esposizione torna a quello; i grezzi rielaborati coincidono con
  gli esportati della macchina.
- Desktop: la causa sulla riserva è la policy HKCU NoDesktop=1; "Togli i
  blocchi" ora riavvia explorer.

### 2026-11-07 (sera) — nitidezza, saturazione, luci

- Nitidezza di serie 0,2 (era 0), saturazione della carta C-41 al 95,5%
  (+8,5%), luci e alte luci ~10% più basse con nero e bianco fermi
  (pipeline._abbassa_luci).

### 2026-11-07 (sera) — bianco meno blu, un filo di contrasto, profilo Gretag

- Bilanciamento: 0,85 ai grigi medi e 0,15 alle luci, e un filo di caldo
  da stampa (R ×1,02, B ×0,97) dopo il bilanciamento automatico.
- Carta C-41: gradazione 1,4 (era 1,3).
- Il profilo di stampa della Gretag (Negative2Lab*.lut), se è sulla
  macchina in C:\MasterflexD, si copia da solo fra le LUT all'avvio.
- Confronto interno con RawTherapee (profilo Film Negative): senza scegliere
  a mano la base della pellicola esce ciano; non lo si usa.

### 2026-11-07 (sera) — colore ed esposizione sul centro, più morbidi

- Colore ed esposizione si valutano sul 60% centrale del negativo
  (invert.centro): bianco della stampa, livelli, bilanciamento, statistiche
  del rullo. La base resta misurata sui bordi.
- Bilanciamento del bianco: 2/3 grigi medi e 1/3 luci (prima solo le
  luci: un cielo bastava a dare una dominante blu).
- Carta C-41 più morbida: gradazione 1,3 sulla luminanza (era 1,6 per
  canale, che saturava e induriva), saturazione all'88%.

### 2026-11-07 (sera) — Preferenze e Geometria

- **Preferenze**, sezione nuova fra Ritocco e Macchina: esposizione
  automatica, avanzamento automatico, ritaglio automatico, rotazione
  automatica (raddrizzamento), correzione automatica della luce, del bianco,
  orientamento automatico. Salvate nelle impostazioni della macchina e
  applicate a ogni elaborazione dal servizio (non più dalle spunte della
  pagina). Tolte da Ritocco e da Macchina.
- **Geometria**, sezione sua (prima una barra sopra l'immagine): rotazione
  di 90°/180°, raddrizzamento a mano di decimi di grado (si vede subito,
  positivo = orario), ritaglio sul fotogramma intero con il riquadro
  automatico di partenza da allargare, ridurre, spostare o ridisegnare,
  canali. "Conferma e salva" rielabora e salva il solo file finale; i
  grezzi non cambiano. L'angolo a mano vale anche con la rotazione
  automatica spenta.

### 2026-11-07 (sera) — niente ritaglio nominale, ritaglio a mano

- Se i bordi del fotogramma non si trovano con certezza il file resta
  intero (prima: ritaglio nominale del formato, che poteva tagliare via un
  pezzo). Il diario lo segnala.
- **Ritaglio a mano** nella barra "Ruota e canali": Ritaglia mostra il
  fotogramma intero, si disegna il riquadro col dito o col mouse, Applica;
  si salva nella geometria del fotogramma (riaperture, Salva, Applica al
  rullo, negativi). "Togli ritaglio" torna all'automatico.
- Il codice DX si legge solo con l'acquisizione come l'originale.

### 2026-11-07 (sera) — fuoco, strisce vuote, immagini slavate, Ferma elaborazione

- **Strisce chiuse con 0 fotogrammi**: l'autofocus partiva sulla prima
  posizione (la coda), lì falliva (ERR_FOCUS_FAILED) e dopo un fuoco fallito
  il carrello rifiutava di avanzare (ERR_MOVE). Ora niente fuoco sulla prima
  posizione; se la striscia comincia su un fotogramma, fuoco e rilettura;
  dopo un fuoco fallito l'avanzamento si riprova una volta.
- **Immagini sfocate con "Autofocus fatto"** (campione rullo 9: nitidezza 8
  contro 81 del rullo 8): dopo il fuoco si misura la nitidezza del primo
  fotogramma vero; se è bassa si provano gli altri metodi, si rilegge e si
  tiene il migliore, che vale per il resto del rullo. Se il metodo scelto
  fallisce si provano gli altri. La nitidezza di ogni fotogramma va nel
  registro.
- **Immagini chiare e slavate**: il profilo C-41 ha una gradazione di
  "carta" 1,6 (era 1,0). Sui campioni: mediana della luminosità da 0,76 a
  0,65, ombre da 0,37 a 0,19.
- **Ferma elaborazione** si ferma davvero: anche il fotogramma a metà, e i
  grezzi rimasti si salvano senza compressione (secondi invece di minuti);
  i tocchi ripetuti non riempiono più il diario; il messaggio conta quelli
  che restano.

### 2026-11-07 — Digital ICE della Kodak, desktop di Windows, valori di serie

- **Digital ICE della Kodak** (`filmscan/ice_kodak.py`, `ponte/dice.py`): la
  libreria dell'SDK (DMLDICELib.dll) sui piani R G B IR, pixel intercalati a
  14 bit (la sonda delle 16:34 ha trovato 17679 pixel difettosi così). Gira
  nell'elaborazione sul PC della macchina, in un processo a parte; se manca,
  cade o cambia più del 15% dell'immagine si usa il Digital ICE nostro.
  Impostazione Macchina → Digital ICE; di serie "della Kodak".
- **Desktop di Windows** (`filmscan/desktop.py`): pannello Macchina →
  Controlla (shell sostituita, policy di Explorer, explorer chiuso, avvio
  automatico), Mostra il desktop (adesso, senza cambiare la configurazione),
  Togli/Rimetti i blocchi. Il resoconto va anche nei registri (desktop.txt).
- Elaboratori in rete accesi di serie, con la chiave creata e salvata al
  primo avvio.
- Corretto il collaudo con pellicola: nella build precedente una modifica
  aveva duplicato la funzione e restava in uso quella vecchia (capture
  appesa 60 s). Aggiunto il passo del Digital ICE Kodak.

### 2026-11-07 — elaboratori in rete, geometria salvata, capture dell'originale

- **Elaboratori in rete** (`filmscan/remoti.py`, `elaboratore.py`,
  `ELABORATORE.bat`): un PC Windows o un server della rete elabora i
  fotogrammi al posto del PC della macchina. Si accende dal pannello
  Macchina, "Elaboratori in rete"; da lì si scarica il pacchetto (script,
  .bat, indirizzo e chiave). L'elaboratore trova la macchina da solo se
  l'indirizzo cambia, scarica il codice dalla macchina (stessa versione),
  elabora più fotogrammi in parallelo. Se sparisce o sbaglia, il
  fotogramma lo rifà la macchina. Spento di serie.
- **Geometria salvata** (`filmscan/geometria.py`): allineamento dei canali,
  inclinazione, riquadro e orientamento si trovano una volta e si riusano
  a ogni riapertura, Applica al rullo e Salva. Nuova barra "Ruota e
  canali" sul fotogramma aperto: rotazione di 90°/180° a mano, rosso e blu
  spostati a mano di ¼–2 px, Azzera, Ricalcola.
- Raddrizzamento più veloce: la stima dell'angolo su una copia ridotta
  (era 10 s su 14 nella prova).
- **Anteprima dal vivo** per i fotogrammi non elaborati (elaborazione
  fermata o errore): si salva accanto al grezzo e fa da miniatura e da
  anteprima; tasto "Completa" per elaborare solo quelli.
- **Negativi per Photoshop dall'archivio**: "Scarica i negativi" propone
  DNG (Camera Raw, Lightroom) o TIFF 16 bit, fatti dai grezzi con la
  geometria salvata; vale anche per i rulli vecchi. `GREZZO IN TIFF.bat`
  fa lo stesso trascinandoci sopra i `.npz`.
- **Acquisizione "come il programma originale"** (Macchina → Acquisizione):
  codice DX e capture, R G B in una volta; sui rulli con ICE il Digital ICE
  dell'SDK. Comando `cattura` dell'agente: legge il DX se serve e alla
  prima risposta "stato non valido" si ferma invece di aspettare 60 s
  (era il blocco del collaudo con pellicola delle 16:08). Di serie resta
  "un canale per volta".
- Collaudo con pellicola: capture solo dopo un fotogramma avanti, con e
  senza Digital ICE; tempi misurati con un orologio che non salta.
- Motori: avvio "completo" di serie, niente più conferma "SI" e avviso
  delle guide (risolte dal 28/10); il modo bloccato resta per le prove.
  Canale IR attivo di serie (`ir_sdk`, verificato nei collaudi); l'ASF su
  scan_raw, sperimentale, tolto.

### 2026-11-07 — tempi dell'elaborazione nel registro

- `ProcessResult.tempi`: secondi per passo; `_esporta` scrive nel registro
  del programma elaborazione e salvataggio di ogni fotogramma, con i passi
  più lenti.
- Documentata la disposizione del buffer della capture (piani R, G, B).

### 2026-11-07 — la pellicola non usciva dal retro

- Espulsione dal retro: a fine striscia il carrello rifiuta il fotogramma
  successivo (ERR_MOVE) con la pellicola ancora dentro, e l'agente diceva
  "espulsa dal retro" anche se non lo era. Ora, se dal retro non esce, si
  espelle con l'Eject del carrello (dal davanti) e lo si dice; se non esce
  neanche così, errore con l'invito a toglierla a mano.
- Espulsione di fine rullo di serie dal davanti.

### 2026-11-07 — ferma elaborazione, ritaglio e colore a mano, formati di scarico

- Rullo: dopo "Fine rullo", il tasto diventa "Ferma elaborazione". Il
  fotogramma in elaborazione finisce, gli altri si salvano solo come
  grezzi (stato `da_elaborare`); alla fine un avviso con i numeri, da
  elaborare con Applica al rullo. Corretto anche `_chiudi_elaborazione`,
  che azzerava la coda prima di svuotarla (il secondo tocco non la
  trovava).
- Ritocco: "Colore automatico" (spento: niente equilibrio del rullo,
  bilanciamento, livelli né ritocco delle classi, resta l'inversione sulla
  base) accanto a "Ritaglio automatico". Cambiandoli il fotogramma si
  rielabora dal grezzo, con le correzioni del suo rullo; valgono anche per
  Salva, Applica al rullo e i rulli dopo.
- Archivio: "Scarica le foto" e "Scarica le foto da stampare" chiedono il
  formato (come salvate, JPEG alta qualità, JPEG 2048 px, TIFF 16 bit,
  TIFF 8 bit); la conversione si fa al momento, i file in archivio non si
  toccano.

### 2026-11-07 — fotogrammi visibili durante l'elaborazione, campioni

- Mentre il rullo finisce di elaborare, toccare un fotogramma della
  striscia lo mostra dal file già esportato (`mostra_fotogramma`, senza il
  lock dei lavori); prima registrava solo il voto e non si vedeva niente.
- "Invia per l'analisi" nella barra del fotogramma: grezzo a metà lato,
  file esportato ridotto, correzioni del rullo e calibrazione, nel
  repository dei registri (`remoto.carica_file`). Solo pellicole di prova.

### 2026-11-07 — collaudi riordinati dopo il secondo invio

- Collaudo con pellicola: il DX non si legge più prima delle letture (porta
  l'SDK nello stato 1, dove `scan_raw` è rifiutato); in fondo tre giri di
  DX + capture dell'originale, poi una lettura `scan_raw` per vedere se lo
  stato torna giusto; il Digital ICE per ultimo, con il riavvio automatico
  se resta appeso.
- Collaudo senza pellicola: niente più lettura unica; fuoco e regolazione
  fine senza pellicola annotati come rifiuti attesi.
- Lo zip dei registri prende anche i file delle sonde (`sonde/`).

### 2026-11-07 — dai primi registri arrivati da soli

- **Lettura unica spenta di serie**: sulla riserva `scan_raw` canale 4 ha
  dato "callback scan rgb failed", timeout dopo 60 s, e poi ogni lettura
  (video compreso) ERR_GENERAL_FW 6 ("restore (add) buffer failed"). Si
  riaccende solo dopo una prova riuscita.
- **Ripristino automatico**: una lettura rifiutata con ERR_GENERAL_FW 6 fa
  un Gretag Reset e si ripete una volta (`PonteSdkBackend._testa_bloccata`).
- **Comando `sensori` disattivato** nell'agente: `diag_input` lo faceva
  cadere (access violation, due volte). Tolto dal collaudo automatico.
- L'agente spegne la finestra di Windows "Python ha smesso di funzionare":
  bloccava il processo morto e quindi il riavvio automatico.
- Collaudo automatico senza pellicola: la lettura unica va in fondo, con
  la verifica del ripristino subito dopo.

### 2026-10-31 — riavvio con la versione nuova, certificati propri

- Tasto "Riavvia interfaccia e agente" (Macchina → Registri e
  aggiornamenti; dopo un'installazione diventa "Riavvia con la versione
  nuova"): l'interfaccia lancia `riavvia.py` ed esce con il codice 3,
  `MASTERFLEX.bat` chiude la sua finestra; `riavvia.py` chiude l'agente
  (comando nuovo `esci`, o d'ufficio dopo 30 s), le finestre rimaste,
  scambia `MASTERFLEX.bat.nuovo` e riparte con `MASTERFLEX.bat automatico`.
  La pagina si ricarica da sola quando il server nuovo risponde.
- `certificati/cacert.pem` (certifi 2026.10.23, Mozilla): `remoto.py` li
  usa insieme a quelli di Windows. Windows 7 non ha più Windows Update.

### 2026-10-31 — collegamento remoto: registri e aggiornamenti senza chiavetta

- `remoto.py`: invio dello zip dei registri a un repository GitHub privato,
  dopo aver tolto nomi, telefoni ed email dei clienti (da `ordini.db`) e il
  PIN; controllo, scarico e installazione dell'ultimo commit di `main`
  (con `manutenzione.py aggiorna`, backup del codice, dati intatti).
- Interfaccia, Macchina → Registri e aggiornamenti: stato, "Invia i registri
  adesso", "Installa l'aggiornamento". Il servizio controlla ogni ora e
  manda i registri ogni 6 ore (mai a lavoro in corso).
- I collaudi automatici mandano i registri appena finiti.
- `MASTERFLEX.bat`: voce 15 "Collegamento remoto" (configura, invia,
  aggiorna, stato); all'avvio automatico l'aggiornamento si installa da
  solo prima di partire (`aggiorna_all_avvio`), un errore di rete non
  ferma niente.
- Token e versione installata in `software/remoto.json` e `versione.json`,
  esclusi da git.

### 2026-10-31 — collaudi automatici, agente avviabile da solo

- `collaudo_auto.py senza`: senza pellicola e senza domande. Stato,
  temperature, tensioni, sensori, tempi del ponte, azzeramento fino a
  pronto, calibrazione della macchina, letture a finestra vuota (R, G, B da
  0,125x a 2x, linearità, profilo delle colonne), buio, rumore nel tempo,
  IR, tetto d'esposizione, lettura unica (RGB e RGBI), pressore, fuoco,
  piccoli movimenti, rifiuti attesi senza pellicola (DX, fotogramma
  successivo), video.
- `collaudo_auto.py pellicola`: chiede solo di inserire una striscia, poi
  DX all'inserimento e dopo ogni movimento, letture, esposizione per
  canale, quattro fotogrammi (lettura unica o un canale per volta),
  nitidezza con il pressore su e giù, tre autofocus, tetto d'esposizione,
  ritorno e regolazione fine, sonde capture e ICE, espulsione.
- Tutti e due: passo fallito → recupero (pressore su, attesa dello
  scanner, mezzo millimetro indietro) e un altro tentativo; agente morto o
  muto per 8 s → riavvio (`MASTERFLEX.bat agente`) e si riprende. Alla
  fine lo zip dei registri.
- Menu collaudi: solo i due automatici e gli strumenti del carrello; le
  prove manuali restano da riga di comando. Il collaudo avvia l'agente se
  non gira.
- `AVVIA AGENTE.bat` nella cartella del software, e azione
  `MASTERFLEX.bat agente`.
- L'agente scrive `agente.pid` appena parte, prima di aprire l'SDK: prima,
  nei secondi dell'apertura, sembrava spento e se ne poteva avviare un
  secondo sulla stessa cartella del ponte (i due si rubano i file).

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
