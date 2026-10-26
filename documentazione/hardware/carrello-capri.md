# Carrello pellicola "Capri" (COM5) — riferimento

Ricavato il 26 ottobre 2026 dal pacchetto originale `CapriFirmware`
(Gretag Imaging, 2000–2002): file di configurazione di Compad, istruzioni di
installazione, registro del selftest, tabella degli errori, firmware e 125
backup del Parameter Flash (2001–2025). Il pacchetto è proprietario e **non sta
nel repository**: sulla macchina va in `C:\MasterflexD\CapriFirmware`.

Il carrello è la parte Gretag della macchina: trascinamento, guide della
pellicola, tamburo dei formati (revolver), lente e CCD, sensori di fotogramma
e DX/MOF, gestione delle cartucce APS. Parla con il PC sulla **COM5** (vedi
[scheda-acquisizione.md §2](scheda-acquisizione.md#2-il-canale-seriale--trasporto-sensori-fuoco)),
mentre la testa di scansione Kodak passa dalla scheda IAQ
([scheda-iaq.md](scheda-iaq.md)).

## 1. Le "guide" sono due motori del carrello

I "motori delle guide" del problema aperto sono i **motori di larghezza
sinistro e destro** (`Width L`, `Width R`), che portano le guide della
pellicola alla larghezza del formato (135, APS, 110). Il carrello ha in tutto:

| Motore | Comando di movimento | Lettura posizione |
|---|---|---|
| Guida sinistra (Width L) | `B0 00 03 01 <passi int16>` | `B0 00 02 15 03` |
| Guida destra (Width R) | `B0 00 03 02 <passi int16>` | `B0 00 02 15 04` |
| Lente | `B0 00 03 03 <passi>` | `B0 00 02 15 05` |
| CCD | `B0 00 03 04 <passi>` | `B0 00 02 15 06` |
| Trascinamento | `B0 00 07 07 <int32 passi> <velocità>` | `B0 00 02 15 00` |

Comandi che portano le guide in posizione di riposo: **Crash Remove**
(`A8 00 01 00`) e **GO HOME** (`F5 00 01 08`).

All'accensione e a ogni reset il carrello fa lo **"nulling"** di ogni motore:
lo porta contro il suo riferimento per trovare lo zero. Se una guida non lo
trova, il firmware segnala:

| Errore | Messaggio | Significato |
|---|---|---|
| 06/04 `ER_WIDTH_LEFT` | 37507 | la guida **sinistra** non trova lo zero |
| 06/05 `ER_WIDTH_RIGHT` | 37508 | la guida **destra** non trova lo zero |
| 06/02 `ER_LENS_CCD` | 37505 | CCD o lente non trovano lo zero |
| 06/03 `ER_REVOLVER` | 37506 | il tamburo non trova lo zero |

Il sintomo visto sulla riserva, con le guide che vanno a fine corsa e
continuano a spingere all'avvio, è quello di un nulling che non vede il
riferimento. Dal 26/10/2026 l'agente traduce questi codici nel suo
registro, se l'SDK li riporta.

## 2. Il firmware: deve essere la 1.15

| Data | Fatto | Fonte |
|---|---|---|
| fino al 2006 | carrello alla 1.15 (Para. Flash 7.00, compilata il 28/11/2002) | backup `M1v15 S1v15`, `SelfTest.log` |
| **2/8/2025 14:06** | backup con **1.15** | `ParameterBackup M1v15 S1v15 2-8-2025 14-6-19.bin` |
| **2/8/2025 14:08** | backup con **1.13**: il software originale ha riscaricato la 1.13 | `ParameterBackup M1v13 S1v13 2-8-2025 14-8-12.bin` |
| 2/8/2025 14:24 | un parametro cambiato (byte `0x113–0x114`, da 752 a 744) | backup delle 14:24 |

Perché è successo: `AutomaticUpdate\Capri1FirmwareVersion.ini` chiede
`UpdateToVersion=00000071` (= 1.13) con i file `MasterV113.a37` e
`SlaveV113.a37`. A ogni avvio il programma originale confronta la versione
del carrello con questa e, se è diversa, scarica la sua. Il pacchetto contiene
**solo la 1.13** (`caprikodak.a37` è identico a `MasterV113.a37`, compilato il
3/6/2002).

È lo stesso giorno del primo "Can't open scanner" nel log dell'SDK. Il flash
è rimasto identico byte per byte (scritto dalla 1.15, letto dalla 1.13). Che
la 1.13 legga male le posizioni delle guide scritte dalla 1.15 è
un'**ipotesi da verificare**, non un fatto: è però la prima cosa da escludere.

Controllo: `MASTERFLEX.bat`, voce 13, oppure
`python manutenzione.py firmware`. Riporta la versione dell'ultimo backup e
quella che il programma originale imporrà al prossimo avvio. Con i file della
1.15 (da cercare sulla macchina di produzione) li installa al posto della 1.13
e corregge l'ini (`UpdateToVersion=00000073`), tenendo l'originale come
`.prima-di-noi`. Dal vivo la versione si legge con Compad
(`CP_FirmwareDownload.ini`, *Get Version*).

Dopo ogni cambio di firmware le istruzioni Gretag chiedono di reimpostare i
flag con Compad. L'ini automatico imposta i flag 0, 1, 4, 6, 9 e azzera il 7 e
l'8.

## 3. I flag del carrello

`B0 00 03 0F <n> <0|1>` imposta il flag *n*; `B0 00 01 14` li legge (*Get
Flags*); `B0 00 01 0C` salva nel flash.

| n | Flag | Valore consigliato da Gretag |
|---|---|---|
| 0 | Eject short | 1 |
| 1 | Read MOF | 1 |
| 2 | Check Revolver | 1 se c'è il sensore aggiuntivo del tamburo |
| 3 | APS Handling | 1 se c'è la gestione delle cartucce |
| 4 | **Crash Detect** | **1** |
| 5 | Eject to Back | 0 (1 per espellere dal retro) |
| 6 | **No Hardware Reset** | **1 ("important")** |
| 7 | Special | 0 |
| 8 | Packet Mode (MOF) | 0 |
| 9 | Black Mask Detect | 1 |

## 4. Il selftest di servizio

`SelfTestInterface.exe` (COM5) lancia il selftest del firmware (`;050000`).
Il menu *Automatic test (Service)* prova, con il **percorso pellicola vuoto**:

- master: RAM;
- HOME: guida destra, guida sinistra, CCD, lente, tamburo;
- PRECISION: guida destra, guida sinistra, CCD, lente, tamburo;
- sensori: livelli DX destro e sinistro, livelli rilevamento fotogramma;
- slave: risposta, RAM, EPLD programmata, rumore testine MOF, motore APS;
- fotocellule: aggancio del tamburo, ingresso, uscita.

Nel `SelfTest.log` del pacchetto tutti i test risultano **OPEN**, cioè mai
eseguiti ("No valid Test"). È la prova che risponde alla domanda sulle
guide: HOME e PRECISION delle due guide, passato o fallito.

Gli altri strumenti, da usare con l'agente e il programma originale
**chiusi** (la COM5 la può avere un solo programma):

| Strumento | Per |
|---|---|
| `SensorTest_COM5.exe` | stato delle fotocellule (rosso = coperta) |
| `Compad2_5.exe` + `CP_Calibrate_Size.ini` | posizione e movimento a 5 o 50 passi delle guide |
| `Compad2_5.exe` + `CP_ADC_SensorValues.ini` | livelli analogici dei sensori di fotogramma e DX |
| `Compad2_5.exe` + `CP_Change_Flags*.ini` | lettura e scrittura dei flag |
| `ParaStore.exe` | backup del Parameter Flash |

Da `MASTERFLEX.bat`: voce 4, poi 5.

## 5. Procedura consigliata per il problema delle guide

In quest'ordine, con il percorso pellicola vuoto e la mano sull'interruttore:

1. **Versione del firmware** (voce 13): se è 1.13, procurarsi la 1.15 prima
   di tutto il resto.
2. **Selftest di servizio** (voce 4 → 5 → 1): annotare HOME e PRECISION
   delle due guide.
3. **SensorTest**: le fotocellule cambiano quando si spostano le guide a mano
   (a macchina spenta)?
4. **Compad, CP_Change_Flags2**: *Get Flags*, controllare Crash Detect e No
   Reset.
5. **Compad, CP_Calibrate_Size**: leggere la posizione delle guide, muoverle
   di 5 passi e rileggerla.
6. Solo dopo, filmscan con l'agente a motori attivi.

## 6. Il protocollo, per un uso futuro

Le stringhe di Compad hanno la forma `;<comando esadecimale>:<risposta
attesa>`. Più comandi si separano con `;`, più risposte con `:`. Ricorrono:

- risposta `03` = conferma; `B4` = dato; `82` = versione;
- `;02` = Break, `;01` = Reset, `;04` = versione;
- `;22` = espulsione, `;23` = fotogramma successivo, `;21` = interfotogramma;
- `;A0 00 01 0n` = formato (01 = 110, 02 = 135, 03 = APS).

Impostazioni seriali in Compad: `BaudRate=12`, `FlowControl=2` (indici della
finestra di Compad, da tradurre in valori reali osservando la porta). Un
backend Python per la COM5 (piano dei lavori, fase 4) può partire da qui.
