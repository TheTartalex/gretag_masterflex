# Il ponte: la macchina originale, la nostra interfaccia

> Scritto il 3 settembre 2026, decisione in
> [0001](../decisioni/0001-ponte-sdk-originale.md). **Aggiornamento di
> ottobre:** dove qui si dice che `EKframe.sys` del 2001 si carica su Windows 7
> a 32 bit, vale per la produzione ma non per la riserva. Lì serve il driver
> nuovo [EKframePnP](../../driver/EKframePnP/), con la stessa interfaccia.
> L'agente e l'SDK non cambiano.

## Il problema, detto senza giri

Il software originale funziona. Comanda i motori, legge il CCD, mette a fuoco,
riconosce i fotogrammi. Nessuno di noi rifara' meglio quel lavoro, e provarci
significa mesi di reverse engineering su un driver PCI del 2001.

Ma la sua interfaccia e' del 2001 anche lei: complicata, lenta, pensata per un
minilab che stampava, e con un'idea di "salvataggio" che passa dal floppy.
E soprattutto butta via quasi tutto: la macchina legge 2100x1400 e lei salva
600x400.

Quindi: **dei suoi comandi teniamo tutto, della sua interfaccia niente.**

## Una macchina sola, due processi

Tutto gira sullo stesso PC: quello del 2009 con **Windows 7 a 32 bit**, con la
scheda originale dentro. Non c'e' nessun "PC moderno" dall'altra parte: quella
macchina avvia, comanda lo scanner, elabora e pubblica l'interfaccia.

```
   PC del 2009 - Windows 7 x86                     RETE LOCALE
   ---------------------------------------         -----------
                                                        |
   processo 1: agente          processo 2: filmscan     |
     ponte/agente.py             interfaccia web  <-----+---- tablet
        |  ctypes                ordini, pipeline,      |
        v                        inversione, export     |
     scn_dll.dll (SDK Kodak)          ^                 |
        |                             |                 |
     EKframe.sys                cartella locale         |
        |                       C:\MasterflexD\ponte
     scheda PCI -> scanner
```

**Perche' due processi e non uno.** Nello stesso programma ci starebbero: Python
a 32 bit carica l'SDK senza problemi. Ma quell'SDK e' C++ del 2001 chiamato con
trampolini fatti a mano, e comanda motori. Se inciampa, si porta dietro il
processo che lo ospita — e se e' lo stesso che serve l'interfaccia, il tablet in
mano all'operatore muore a meta' rullo, con l'ordine aperto.

Separati, invece: l'agente cade, l'interfaccia resta viva, lo dice, e si riparte
senza perdere niente. Su una macchina che deve lavorare tutto il giorno vale
molto piu' dei pochi millisecondi che costa il passaggio.

**Perche' una cartella e non una socket.** Sulla stessa macchina si poteva usare
una porta locale. Una cartella pero' non ha porte da aprire, non litiga con il
firewall, sopravvive al riavvio di uno dei due processi e lascia sul disco i
grezzi che stiamo per elaborare — che e' comodo anche quando si va a capire
perche' un fotogramma e' venuto storto.

    C:\MasterflexD\ponte\cmd\<token>     l'interfaccia scrive un comando
    C:\MasterflexD\ponte\ack\<token>     l'agente risponde
    C:\MasterflexD\ponte\raw\<nome>      l'agente deposita i dati grezzi

Ogni file grezzo ha accanto un `.json` con canale, dimensioni, esposizione e
tempo impiegato. I dati sono `uint16` little endian, riga per riga: nessun
formato, nessuna compressione, niente da decodificare.

**Perche' proprio Windows 7 a 32 bit.** Non e' nostalgia. Windows a 64 bit
pretende driver firmati dal costruttore; `EKframe.sys` e' del 2001 e firmato non
e'. La versione a 32 bit non lo pretende, e i servizi kernel che quel driver usa
— `HalGetBusData`, `HalAssignSlotResources`, il DMA via adapter object — ci sono
ancora. E' l'ultimo sistema su cui questa macchina puo' vivere, ed e' anche
l'ultimo su cui gira Python 3.8. Le due cose combaciano.

## Come parla, in concreto

| Comando | Effetto sulla macchina |
|---|---|
| `ping` | l'agente e' vivo |
| `stato` | tipo di scanner, matricola, carrello montato |
| `sensori` | stato degli ingressi digitali |
| `formato_0` | sceglie il formato (0 = 135, 1 = APS, 4 = 110) |
| `alimenta_on` / `alimenta_off` | trascinamento |
| `avanza` / `avanza_mezzo` / `prossimo` | movimento a fotogrammi |
| `regola_<passi>` | movimento fine, un passo = 0,01 mm |
| `fuoco` | autofocus |
| `grab_G_0_1.500` | acquisisce il canale G a piena risoluzione, esposizione 1,5x |
| `flash_backup` | salva il Parameter Flash in `raw/` |
| `offsetxy_0` | legge gli offset XY dal flash |

Dal lato nostro non si scrivono comandi a mano: c'e' `hal.PonteSdkBackend`, che
espone la stessa interfaccia di sempre. Pipeline, ordini ed export non si
accorgono di niente.

    cfg.driver = "sdk"
    cfg.bridge_path = r"\\pc-scanner\ponte"

## Come si chiama l'SDK da Python senza compilatore

`scn_dll.dll` e' C++ compilato con Visual C++ a 32 bit. I metodi usano la
convenzione `__thiscall`: argomenti sullo stack, puntatore all'oggetto in ECX.
ctypes non la conosce.

Si genera a runtime un trampolino di sei byte:

    58        pop eax     ; indirizzo di ritorno
    59        pop ecx     ; this -> in ECX, dove il metodo lo cerca
    50        push eax    ; rimetto l'indirizzo di ritorno
    E9 rel32  jmp metodo

e si dichiara il metodo a ctypes come `__stdcall` con `this` primo argomento.
I conti sullo stack tornano da soli: il metodo pulisce i suoi argomenti, il
trampolino ha gia' tolto `this`.

Le firme non sono indovinate. I nomi C++ esportati contengono la firma completa,
e sono state ricostruite tutte e 310 (vedi
[api-sdk.md](../software-originale/api-sdk.md)). Anche i due numeri che di solito
si sbagliano sono misurati e non stimati: `ExposureTime` occupa **392 byte**
(`scan_raw` fa `ret 0x1ac` e `sub esp, 0x188`), e le risoluzioni vengono dalla
tabella dentro il DLL.

Nessun compilatore, nessun wrapper C, nessuna modifica al software originale.
Su quel PC noi **leggiamo** soltanto.

## L'interfaccia sul tablet

L'operatore sta in piedi davanti alla macchina, con la pellicola in mano. Il
monitor del PC e' scomodo e a volte lontano; il tablet no.

    MASTERFLEX.bat, voce 1

stampa l'indirizzo vero da aprire, non "localhost":

    http://192.168.1.42:8080/?t=Xk3p9wQ

Il token nell'indirizzo e' la sola chiave, e cambia a ogni avvio. `MASTERFLEX.bat`
prova anche ad aprire la porta 8080 sul firewall di Windows, che e' la ragione
per cui nove volte su dieci il tablet non si collega; per riuscirci vuole
"Esegui come amministratore" la prima volta.

L'interfaccia e' stata rivista per l'uso col dito: dove il puntatore e'
grossolano i bersagli crescono a 48-52 px e i comandi che muovono la macchina
stanno larghi e distanziati. Sotto i 44 px si sbaglia bersaglio, e qui sbagliare
bersaglio vuol dire comandare un motore che tira pellicola. Al desktop non
cambia niente: sono regole sotto `@media(pointer:coarse)`.

**Rete locale fidata soltanto.** Niente cifratura, nessun utente, e dall'altra
parte c'e' un motore. Mai esposta su internet.

## Le tre strade, e perche' questa

**1. Chiamare l'SDK (`scn_dll.dll`) — scelta.**
E' il livello giusto: sotto le interfacce, sopra il driver. Firme complete e
verificabili. Da' `scan_raw`, cioe' il canale grezzo con l'esposizione che
vogliamo — che e' l'intero scopo del progetto.

**2. Pilotare i server COM (`PhxScanner`, `GateControl`).**
Funzionerebbe, ma quelle interfacce non hanno type library: le firme andrebbero
ricostruite dai proxy/stub, con meno certezza e un livello di distanza in piu'
dalla macchina. Resta la seconda strada se la prima trovasse un muro.

**3. `DMLCmdTransfer.exe` da riga di comando.**
Il piu' semplice in assoluto: un `.exe` che apre il device e manda comandi,
compresi `scan roll of film` e `set XY offsets`. Poco controllo fine, ma **e' il
modo migliore per la primissima prova**: se risponde, la catena
PC-driver-scheda-macchina e' viva, e lo si sa in cinque minuti senza scrivere
niente.

## Cosa e' gia' pronto e cosa no

Pronto e collaudato in simulazione, dall'inizio alla fine:

- `ponte/sdk.py` — collegamento all'SDK: 40+ metodi, `scan_raw` con esposizione,
  backup del Parameter Flash, lettura degli ingressi digitali
- `ponte/agente.py` — l'agente per il PC originale, senza dipendenze, con
  modalita' `--simula` per provare tutto senza la macchina
- `filmscan/hal.py` — `PonteSdkBackend`: il lato nostro
- `config.py` — geometria ottica corretta sui valori veri

La catena e' stata provata per intero con l'agente simulato: comando, risposta,
fotogramma 2100x1400 a 16 bit consegnato alla pipeline, blocchi ridotti per la
calibrazione. Quello che manca e' la macchina.

Da scoprire sulla macchina, e sono tutte cose che si vedono guardandola:

- i valori numerici di `ColorChannel` (R, G, B, e se l'IR sia il 3)
- quale numero di `diag_input` e' il sensore d'ingresso e quale quello d'uscita
- segno e scala di `regola_<passi>`: che +100 passi siano davvero +1 mm nel verso
  giusto si controlla in un minuto, guardando la pellicola

Finche' i sensori non sono mappati, il backend **dichiara che non sono
disponibili** invece di restituire due booleani inventati, e l'espulsione
automatica resta disattivata. E' voluto: meglio una funzione che manca di una
che mente.

## Una nota sull'infrarosso

`N_CHANNELS` nell'SDK vale 3: il percorso dati principale e' R, G, B. L'IR sulla
macchina c'e' — in `Scanner\Bin` siedono `DMLDICELib.dll` e `AIDToolkit.dll`,
cioe' Digital ICE, che dell'infrarosso vive. Quindi il canale esiste ma passa da
un'altra parte.

Prima cosa da provare: `ColorChannel = 3` su `scan_raw`. Se risponde, abbiamo
l'IR grezzo e la nostra pulizia difetti funziona come progettata. Se non
risponde, l'IR va cercato dentro `DMLDICELib.dll`, ed e' un capitolo a parte.

## Prima di collegare tutto

L'ordine giusto per la prima volta, senza scorciatoie:

1. **Percorso pellicola vuoto.** Il manuale di servizio lo ripete in maiuscolo:
   i test muovono i motori e una pellicola dentro viene distrutta.
2. **`DMLCmdTransfer 1`** (timer tick test): la catena e' viva?
3. **`python agente.py --condivisa C:\ponte --simula`**: il protocollo gira?
4. Solo allora l'agente vero, e come primo comando `flash_backup`.

Il backup del Parameter Flash prima di ogni altra cosa. Dentro ci sono la length
calibration, gli offset XY e le posizioni di fuoco dei tre formati: tarature
fatte una volta sola da un tecnico, con pellicole di prova che oggi non si
comprano piu'. Se si perdono, non si rifanno.
