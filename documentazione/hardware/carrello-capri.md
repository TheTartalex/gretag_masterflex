# Carrello pellicola "Capri" (COM5) — riferimento

Ricavato il 26 ottobre 2026 dal pacchetto originale `CapriFirmware`
(Gretag Imaging, 2000–2002), che contiene:

- i file di configurazione di Compad e le istruzioni di installazione;
- il registro del selftest e la tabella degli errori;
- il firmware 1.13 (master e slave), **disassemblato**;
- 125 backup del Parameter Flash, dal 2001 al 2025, **decodificati**.

Il pacchetto è proprietario e **non sta nel repository**: sulla macchina va in
`C:\MasterflexD\CapriFirmware`. Gli indirizzi `0x…` citati sotto sono del
firmware master 1.13 (`caprikodak.a37` = `MasterV113.a37`). Il processore è
un Hitachi H8/300H; per disassemblarlo servono `binutils-h8300-hms` e
`objdump -b binary -m h8300h`.

Il carrello è la parte Gretag della macchina: trascinamento, guide della
pellicola, tamburo dei formati (revolver), lente e CCD, sensori di fotogramma
e DX/MOF, gestione delle cartucce APS. Parla con il PC sulla **COM5** (vedi
[scheda-acquisizione.md §2](scheda-acquisizione.md#2-il-canale-seriale--trasporto-sensori-fuoco)),
mentre la testa di scansione Kodak passa dalla scheda IAQ
([scheda-iaq.md](scheda-iaq.md)).

## 1. I motori e come trovano lo zero

Il firmware numera otto motori (tabella dei nomi a `0x18a74`):

| N. | Motore | Muovi (Compad) | Posizione | Zero all'avvio |
|---|---|---|---|---|
| 0 | Trasporto | `B0 00 07 07 <int32 passi> <uint16 vel.>` | `B0 00 02 15 00` | — |
| 1 | Onestep | | | — |
| 2 | Tamburo (revolver) | `B0 00 03 06 <formato> 01` | | 410 / 10 passi, 15 passi/s |
| 3 | **Guida sinistra** (Width1) | `B0 00 03 01 <int16 passi>` | `B0 00 02 15 03` | **750 / 200 passi, 500 passi/s** |
| 4 | **Guida destra** (Width2) | `B0 00 03 02 <int16 passi>` | `B0 00 02 15 04` | **750 / 200 passi, 500 passi/s** |
| 5 | Lente | `B0 00 03 03 <int16 passi>` | `B0 00 02 15 05` | 1100 / 150 passi |
| 6 | CCD | `B0 00 03 04 <int16 passi>` | `B0 00 02 15 06` | 2400 / 150 passi |
| 7 | Pulizia MOF | | | — |

Le due guide sono montate a specchio: per la sinistra i passi positivi sono
">>", per la destra "<<" (`CP_Calibrate_Size.ini`).

**Lo zero ("nulling", `GoHomeMotor`, `0x15198`).** Ogni motore ha una
fotocellula di riferimento. Tutte e cinque stanno sulla stessa porta di
ingresso del processore (`0xFFFF51`): guida sinistra bit 2, guida destra
bit 3, tamburo bit 4, CCD bit 5, lente bit 6. I limiti sono nella tabella
scritta a `0x14f4c`. Lo zero va in due fasi:

1. **Step in**: il motore va verso la fotocellula finché la vede. Se fa più
   passi del limite (750 per le guide, 1,5 secondi), si ferma e dà
   `Step in error`.
2. **Step out**: il motore si allontana finché la fotocellula si libera. Se
   la fotocellula risulta già coperta in partenza, salta subito qui. Oltre
   il limite (200 passi per le guide) dà `Step out error`.

Se una fase fallisce, `GoHomeAllMotors` (`0x153e8`) ferma tutti i motori. Il
chiamante (`0x18d0`) traduce l'errore:

| Motore che fallisce | Errore | Messaggio |
|---|---|---|
| lente o CCD | 06/02 `ER_LENS_CCD` | 37505 |
| tamburo | 06/03 `ER_REVOLVER` | 37506 |
| guida sinistra | 06/04 `ER_WIDTH_LEFT` | 37507 |
| guida destra | 06/05 `ER_WIDTH_RIGHT` | 37508 |

Dal 26/10/2026 l'agente traduce questi codici nel suo registro, se l'SDK li
riporta.

**Cosa vuol dire per il problema delle guide.** Il firmware non può
spingere una guida all'infinito: al massimo 750 passi per tentativo, poi si
ferma con l'errore. Il motore però resta alimentato (corrente di
mantenimento, *Stopcur.* nel flash), quindi una guida ferma contro il fine
corsa resta in spinta. Il sintomo visto sulla riserva corrisponde a una
fotocellula che **non si copre mai**: la guida va in fondo, perde passi
contro il fine corsa fino a 750 e lì resta. Se la spinta riparte più volte,
è il PC che ripete l'inizializzazione.

Un guasto che colpisce **tutte e due le guide** insieme fa pensare a
qualcosa di comune: alimentazione delle fotocellule, connettore o cavo verso
la scheda master. Un guasto su **una sola guida** fa pensare alla sua
fotocellula, sporca, spostata o guasta.

## 2. Il firmware: 1.15, ma non è lui la causa probabile

| Data | Fatto | Fonte |
|---|---|---|
| 11/2001–2/2002 | 0.94, poi 1.09 | backup `M0v94`, `M1v09` |
| 14/11/2002 | **1.13** | backup `M1v13` |
| 8/4/2003 19:20 → 19:22 | 1.15 riportata alla **1.13** dal programma originale | backup delle 19:20 e delle 19:22 |
| 12/3/2004 – 9/2006 | 1.15 | backup `M1v15` |
| **2/8/2025 14:06** | backup con **1.15** | `ParameterBackup M1v15 S1v15 2-8-2025 14-6-19.bin` |
| **2/8/2025 14:08** | backup con **1.13**: il programma originale l'ha riscaricata | `ParameterBackup M1v13 S1v13 2-8-2025 14-8-12.bin` |
| 2/8/2025 14:24 | cambiato solo il *limite A/D pellicola* (752 → 744) | backup delle 14:24 |

Perché succede: `AutomaticUpdate\Capri1FirmwareVersion.ini` chiede
`UpdateToVersion=00000071` (= 1.13) con i file `MasterV113.a37` e
`SlaveV113.a37`. A ogni avvio il programma originale confronta la versione
del carrello con questa e, se è diversa, scarica la sua. Il pacchetto
contiene **solo la 1.13**: `caprikodak.a37` è identico a `MasterV113.a37`,
compilato il 3/6/2002.

**Correzione rispetto alla prima analisi.** Si era ipotizzato che la 1.13
leggesse male il flash scritto dalla 1.15. I backup lo escludono quasi del
tutto:

- il flash ha lo stesso formato (versione 7) con la 0.94, la 1.09, la 1.13 e
  la 1.15;
- la 1.13 ha lavorato con questo formato dal novembre 2002 al marzo 2004;
- il declassamento del 2025 era già successo identico nell'aprile 2003.

La 1.15 resta la versione da tenere: è l'ultima di Gretag, e le note della
1.0x elencano correzioni sull'espulsione e sul rilevamento dei fotogrammi.
Però non spiega le guide
([decisione 0006](../decisioni/0006-firmware-carrello-1.15.md)).

Controllo: `MASTERFLEX.bat`, voce 13, oppure `python manutenzione.py
firmware`. Riporta:

- la versione dell'ultimo backup;
- la versione che il programma originale imporrà al prossimo avvio;
- i flag e le posizioni decodificati dal backup (§4).

Con i file della 1.15 (da cercare sulla macchina di produzione) li installa
al posto della 1.13 e corregge l'ini (`UpdateToVersion=00000073`), tenendo
l'originale come `.prima-di-noi`. Dal vivo la versione si legge con Compad
(`CP_FirmwareDownload.ini`, *Get Version*).

## 3. I flag del carrello

`B0 00 03 0F <n> <0|1>` imposta il flag *n*; `B0 00 01 14` li legge (*Get
Flags*); `B0 00 01 0C` salva nel flash. I nomi tra parentesi sono quelli che
il firmware stampa con *Print parameter flash*. Il valore nel 2025 viene dal
backup delle 14:24.

| n | Flag | Consigliato da Gretag | Nel 2025 |
|---|---|---|---|
| 0 | Eject short (*Eject short stripe*) | 1 | 1 |
| 1 | Read MOF (*MOF reading*) | 1 | 1 |
| 2 | Check Revolver (*Drum sensor*) | 1 se c'è il sensore del tamburo | 1 |
| 3 | APS Handling | 1 se c'è la gestione delle cartucce | 0 |
| 4 | **Crash Detect** (*Crash detection*) | **1** | **1** |
| 5 | Eject to Back (*No eject to front*) | 0 | 0 (1 fino al 2006) |
| 6 | **No Reset** (*Power up no motors*) | **1 ("important")** | **1** |
| 7 | Special | 0 | 0 |
| 8 | Packet Mode (MOF) | 0 | 0 |
| 9 | Black Mask Detect | 1 | 1 |

Due conseguenze:

- **Crash Detect era acceso**: l'ipotesi del flag spento è esclusa.
- **"No Reset" vuol dire che all'accensione i motori non si muovono.** Lo
  zero delle guide lo fa partire il PC, con il comando di inizializzazione
  o di reset. Senza nessun programma che apre il carrello, all'accensione le
  guide devono restare ferme.

## 4. Il Parameter Flash

297 byte, salvati dal programma originale a ogni aggiornamento del firmware
in `AutomaticUpdate\ParameterBackup M<v> S<v> <data>.bin`. La struttura
viene dalla funzione *Print parameter flash* (`0x7d62`); nel file i campi in
coda stanno 11 byte più avanti che nella struttura in RAM.

| Offset nel file | Contenuto |
|---|---|
| `0x01` | identificativo del blocco |
| `0x03`–`0x06` | somma di controllo |
| `0x07`–`0x08` | versione del formato (7) |
| `0x09`–`0x0a` | byte coperti dalla somma |
| `0x4b` | 20 record da 8 byte: posizione (int32), velocità (uint16), corrente di mantenimento. Per motore (tamburo, guida sinistra, guida destra, lente, CCD) e per formato (110, 135, APS, servizio) |
| `0x10b` | flag 0–7, un bit ciascuno (vedi `manutenzione.py`, `PF_FLAG`) |
| `0x10d` | formato all'avvio (2 = 135) |
| `0x10f` | distanza rivelatore–fotogramma (uint32) |
| `0x113` | limite A/D pellicola (uint16) |
| `0x115` | matricola (uint32) |
| `0x11b` | flag 8 e 9 |
| `0x11d`, `0x121`, `0x125` | correzioni per cartuccia APS, riordino APS e 110 (int32) |

Posizioni salvate, in passi dallo zero:

| | 110 | 135 | APS | servizio |
|---|---|---|---|---|
| guida sinistra 2001–2006 | 697 | 307 | 523 | 381 |
| guida destra 2001–2006 | 664 | 306 | 522 | 386 |
| guida sinistra 2025 | 711 | 308 | 526 | 381 |
| guida destra 2025 | 651 | 279 | 504 | 386 |

Fino al 2006 la matricola è 0 e le posizioni delle guide non cambiano mai.
Nel 2025 la matricola è 60210, come quella della riserva, e le posizioni sono
diverse. Fra il 2006 e il 2025 il carrello è stato ricalibrato o sostituito;
i backup di quegli anni mancano.

Le posizioni del 110 (711 e 651 passi) stanno sotto il limite di 750 dello
zero, cioè dentro la corsa che il firmware si aspetta.

## 5. Il selftest di servizio

`SelfTestInterface.exe` lancia il selftest del firmware (`;050000`).

**Attenzione alla porta.** Nel pacchetto `selftest.ini` ha `ComPort=0`,
cioè COM1, mentre tutti gli ini di Compad hanno `ComPort=4`, cioè COM5:
l'indice parte da 0. Così com'è il selftest non trova il carrello. Dal
26/10/2026 la voce 4 → 5 del menu lancia prima `manutenzione.py
porta-carrello`, che porta a COM5 gli ini che non lo sono e tiene gli
originali come `.prima-di-noi`.

Uso dei tasti:

- **SelfTest** avvia il programma;
- **Yes** conferma la voce evidenziata;
- **No** passa alla voce successiva.

Il menu *Automatic test (Service)* prova, con il **percorso pellicola vuoto**:

- master: RAM;
- HOME: guida destra, guida sinistra, CCD, lente, tamburo;
- PRECISION: le stesse cinque, con i passi misurati per lo step in e per lo
  step out (`DETAIL INFO PRECISION: Step in: … Step out: …`);
- sensori: livelli DX destro e sinistro, livelli del rilevamento fotogramma;
- slave: risposta, RAM, EPLD programmata, rumore delle testine MOF, motore
  APS;
- fotocellule: aggancio del tamburo.

Il menu *Interactiv test (Service)* chiede in più di coprire tre volte le
fotocellule di ingresso e di uscita.

Nel `SelfTest.log` del pacchetto tutti i test risultano **OPEN** ("No valid
Test"): nessuno li ha mai eseguiti. Quel registro è stato scritto con la 1.15
e la matricola 60210. È la prova che risponde alla domanda sulle guide: HOME
e PRECISION delle due guide, passato o fallito, e con quanti passi.

Gli altri strumenti, da usare con l'agente e il programma originale
**chiusi** (la COM5 la può avere un solo programma):

| Strumento | Per |
|---|---|
| `SensorTest_COM5.exe` | stato delle fotocellule collegate alla CPU master (rosso = coperta) |
| `Compad2_5.exe` + `CP_Calibrate_Size.ini` | posizione e movimento a 5 o 50 passi delle guide |
| `Compad2_5.exe` + `CP_ADC_SensorValues.ini` | livelli analogici dei sensori di fotogramma e DX |
| `Compad2_5.exe` + `CP_Change_Flags*.ini` | lettura e scrittura dei flag |
| `ParaStore.exe` | backup del Parameter Flash |
| `DX_MOF_Test\dxtest2.exe`, `moftest2.exe` | qualità della lettura DX e MOF su una pellicola (`dxtest2 5 14` = COM5, 14 fotogrammi) |

Da `MASTERFLEX.bat`: voce 4, poi 5.

## 6. Procedura consigliata per il problema delle guide

In quest'ordine, con il percorso pellicola vuoto e la mano sull'interruttore:

1. **Accensione senza programmi**: con "No Reset" a 1 le guide devono restare
   ferme. Se si muovono da sole, il flag non è quello del backup, oppure il
   flash è stato riscritto.
2. **Selftest di servizio** (voce 4 → 5 → 1): annotare HOME e PRECISION
   delle due guide, con i passi.
3. **SensorTest**: le fotocellule cambiano quando si spostano le guide a mano
   (a macchina spenta, poi accesa senza programmi)? Una fotocellula che non
   cambia mai è la causa.
4. **Versione del firmware e flag** (voce 13): ora mostra anche i flag del
   backup. Dal vivo: Compad, `CP_Change_Flags2.ini`, *Get Flags*.
5. **Compad, CP_Calibrate_Size**: leggere la posizione delle guide, muoverle
   di 5 passi e rileggerla.
6. Solo dopo, filmscan con l'agente a motori attivi.

## 7. Il protocollo

Le stringhe di Compad hanno la forma `;<byte in esadecimale>:<risposta
attesa>`. Più comandi si separano con `;`, più risposte con `:`.

**Formato di un comando**: un byte di comando, poi la lunghezza su 16 bit
(big endian), poi i dati. Esempio: `B0 0002 15 03` è il comando `B0` con due
byte di dati, `15 03`, cioè "posizione della guida sinistra". I comandi
corti sono un byte solo: `01` reset, `02` break, `04` versione, `21`
interfotogramma, `22` espulsione, `23` fotogramma successivo.

Risposte ricorrenti:

| Byte | Significato |
|---|---|
| `03` | conferma |
| `B4` | dato |
| `82` | versione |
| `80` | errore (tasto *Echo Error* di Compad) |
| `A4` | dopo `23` e `21`: fotogramma raggiunto (dedotto) |
| `90`–`93` | dati DX/MOF del fotogramma (dedotto dal registro di Compad) |
| `F1` | risposta ai comandi `F0` (prove della cartuccia APS) |
| `F4` | valore dell'A/D, risposta a `F3 … 0E` |

Comandi:

- `A0 00 01 0n`: formato (01 = 110, 02 = 135, 03 = APS);
- `A1 00 01 0n`: modo (00 = un fotogramma, 01 = riordino del rullo, 02 =
  cartuccia APS);
- `A8 00 01 00`: Crash Remove;
- `F5 00 01 08`: GO HOME;
- `F3 00 02 0E 0n`: canale A/D *n* (0–3 rilevamento fotogramma, 4–7 DX).

**Trasporto** (`CP_Move_Film.ini`):

- *One Frame* sono **406 passi** a 1900 passi/s. Se è il fotogramma da 38 mm
  del 135, un passo del carrello vale circa 0,094 mm.
- L'SDK Kodak invece dichiara passi da 0,01 mm per `frame_detect_fine_adjust`.
- La scala vera si misura con il collaudo dello scanner, voce 3
  (trascinamento comandato).

**Impostazioni seriali** in Compad: `ComPort=4` (COM5), `BaudRate=12`,
`FlowControl=2`. `BaudRate` e `FlowControl` sono indici della finestra di
Compad, da tradurre in valori reali osservando la porta. Un backend Python
per la COM5 (piano dei lavori, fase 4) può partire da qui.

Il firmware ha anche un menu di servizio testuale, *Advanced motor control*,
con movimento libero di qualunque motore. Le sue stringhe stanno a
`0x191dc`. Non va usato senza sapere a chi arrivano quei comandi.
