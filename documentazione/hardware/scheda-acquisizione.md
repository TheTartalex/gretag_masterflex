# La scheda di acquisizione e il canale di comando

> **Stato del documento.** Scritto il 3 settembre 2026 leggendo il software
> originale e il manuale di servizio. Le fonti sui file `.ini`, il manuale e
> il firmware restano valide. **Sulla scheda PCI e sul driver è superato da
> [scheda-iaq.md](scheda-iaq.md)** (ottobre 2026), ricavato dal disassemblato
> e verificato in emulazione. In particolare:
>
> - i codici IOCTL e il protocollo verso la scheda ora sono noti (§7 punti 1–2
>   qui sotto: risolti);
> - i messaggi "ARCNet" **non** sono residui: ARCNET è il canale comandi (§3);
> - su Windows 7 il driver originale **non** funziona sulla macchina di
>   riserva: lascia un dispositivo "zombie". Si usa il driver nuovo
>   [EKframePnP](../../driver/EKframePnP/), che è Plug and Play
>   ([decisione 0002](../decisioni/0002-driver-pnp-sostitutivo.md)).
>
> Resta da chiarire come si dividono il lavoro il canale seriale COM5 (§2) e
> il canale ARCNET della scheda.

Dati ricavati dal software originale e dal manuale di servizio, non da supposizioni.
Ogni voce porta la fonte esatta: il file da cui viene, così che chiunque possa
riaprirlo e verificare.

Le fonti stanno in `APP ORIGINALE\`, cartella affiancata a questo progetto.

---

## 1. La macchina parla su DUE canali distinti

È il fatto strutturale più importante emerso, e non era ovvio: il software
originale non usa una sola interfaccia proprietaria, ne usa due, indipendenti fra
loro e di difficoltà radicalmente diversa.

| Canale | Cosa porta | Come | Difficoltà per noi |
|---|---|---|---|
| **Seriale COM5** | trasporto pellicola, sensori, gate, autofocus, lettura DX/MOF | RS-232 | media — protocollo osservabile |
| **Scheda PCI** | i dati d'immagine del CCD | DMA + registri | alta — protocollo interno |

Il canale seriale è quello che governa tutta la meccanica: è la parte che nel
nostro software corrisponde ad `advance()`, `set_focus()`, `read_sensors()`,
`eject()`. Il canale PCI serve solo a `capture_line_block()`.

Questa separazione è una notizia buona: significa che il pezzo più difficile
(l'immagine) è anche l'unico che possiamo lasciare al software originale mentre
il resto lo controlliamo noi.

---

## 2. Il canale seriale — trasporto, sensori, fuoco

**COM5**, connettore con pallino **verde** sul retro del PC, su scheda multiseriale
RS-232.

> Fonte: manuale di servizio, cap. 4 "PC-rack, printer electronics, SSR",
> pag. 81, tabella `Interface connectors PC 2nd Generation`:
> `COM5   green   Film Scanner`.
> Confermato da `APP ORIGINALE\Scanner\Bin\Ggc.ini`: `[SerialInterface] SerialPort=5`.

Il programma che lo pilota è `GateControl.exe`
(`APP ORIGINALE\Scanner\Bin\`), esposto come oggetto COM
`GretagImaging.GateControl.1`.

### La macchina a stati del gate

Estratta dai simboli C++ di `GateControl.exe`. È la logica reale del ciclo
automatico, non una ricostruzione:

```
Unloaded → Initialize → MediaInsert → Dx → FrameInPosition → Transport → Eject
                     ↘ ChangeFilmFormat  ↘ AdjustFocus  ↘ Mof / CleanMOF
                     ↘ Reset  ↘ Service  ↘ FatalError
```

Corrisponde quasi uno a uno al ciclo descritto in
[installazione.md](../filmscan/installazione.md). Due stati che noi
non avevamo previsto e che vanno considerati: **`Dx`** (lettura del codice DX
prima di scansionare) e **`Mof` / `CleanMOF`** (lettura del codice MOF, e
pulizia dei relativi sensori con striscia apposita).

### Soglie dei sensori, così come le usa la macchina originale

Da `APP ORIGINALE\Scanner\Bin\Ggc.ini` — sono **letture analogiche**, non
digitali on/off:

```
[DX Sensor Limits]        RangeDxOkLow=3.20   RangeDxOkHigh=4.10
                          RangeDxTolLow=0.20  RangeDxTolUp=0.05
[Frame Sensor Limits]     RangeFrameDetOkLow=0.90  RangeFrameDetOkHigh=3.70
                          RangeFrameDetTolLow=0.10 RangeFrameDetTolUp=0.10
[Default Flag Settings]   DefaultFlags=0xFE57
```

Questo cambia un'assunzione del nostro `hal.FilmSensors`, che tratta i sensori
come booleani. La macchina legge **tensioni** e le confronta con una finestra di
validità; il manuale di servizio (cap. 7, `Sensor Test`) mostra a schermo
"green / yellow / red range" proprio su questi valori. Un sensore sporco non dà
un falso booleano: dà un valore fuori finestra, che è un'informazione migliore.

### Offset del formato — già disponibili, non vanno rimisurati

Da `APP ORIGINALE\IniFiles\Scanner_Customer.ini`:

```
[Offsets]
Offset135 = 47 76
OffsetAPS = 70 165
Offset110 = 388 226
```

Sono le posizioni X,Y della proiezione del fotogramma sul CCD per i tre formati.
Il manuale (cap. 7.3.6, pag. 273) chiarisce che il 135 è regolato
**meccanicamente** e che APS e 110 derivano da quello via software; i valori
vivono nel flash della macchina e vengono **riscritti in questo .ini**. Sono
quindi la fotografia della taratura reale di *questa* macchina, numero di serie
**60210** (da `ClientServer_Customer.ini`).

---

## 3. Il canale immagine — la scheda PCI

### Identità

| Voce | Valore |
|---|---|
| Nome | `IAQ Adapter for Kodak film scanners` (Image AcQuisition) |
| Costruttore | Eastman Kodak |
| Classe Windows | `MiniLab adapters`, GUID `{533245A7-2867-11d5-BE3E-00902731AC5D}` |
| Hardware ID | `PCI\VEN_11B2&DEV_0008&SUBSYS_908010B5&REV_00` |
| Class code PCI | `CC_040000` — multimedia video controller |
| Posizione | PCI bus 0, device 9, function 0 |

> Fonti: `APP ORIGINALE\InstallService\KodakFirmware\Driver\KodakIAQ.inf` e
> il ramo `ControlSet001\Enum\PCI\VEN_11B2&DEV_0008&SUBSYS_908010B5&REV_00`
> dell'hive di registro `APP ORIGINALE\Registry\Backup\system`.

### Il dettaglio che vale più di tutti gli altri

Il subsystem ID è **`908010B5`**: subsystem vendor `10B5` = **PLX Technology**,
subsystem device `9080` = **PLX PCI 9080**.

La scheda è costruita attorno a un **PLX PCI 9080**, il ponte PCI↔bus locale più
diffuso degli anni '90. **Il suo data book è pubblico.** Motore DMA, mailbox,
doorbell, mappatura degli spazi locali: tutto documentato da PLX.

Questo ridimensiona la voce "mappa dei registri della scheda" che in
[`software/CLAUDE.md`](../../software/CLAUDE.md)
figurava come dato mancante. La metà PCI della scheda non è proprietaria: è un
componente standard e documentato. Resta proprietaria solo la logica **a valle**
del ponte — l'EPLD Gretag e il firmware dello scanner — che si comanda passando
messaggi attraverso i mailbox del 9080.

### Risorse assegnate

Decodificate dal `BootConfig` (struttura `CM_RESOURCE_LIST`) della chiave PCI
dell'hive di registro. Sono le risorse realmente in uso all'ultimo avvio
registrato:

| Risorsa | Indirizzo | Ampiezza | Interpretazione |
|---|---|---|---|
| Memoria | `0xDE000000` | `0x100` (256 B) | registri runtime del PLX 9080 |
| I/O port | `0xA800` | `0x100` (256 B) | gli stessi registri, in spazio I/O |
| Memoria | `0xDD800000` | `0x200000` (2 MB) | finestra sul bus locale — buffer immagine |
| IRQ | linea 9 | condivisa | interrupt di fine trasferimento |

I 256 byte in doppia mappatura (memoria + I/O) sono la firma classica del PLX
9080, che espone lo stesso file di registri in entrambi gli spazi. La finestra da
**2 MB** è il lato locale: è lì che compaiono i dati del CCD.

Vanno riconfermate in Gestione Dispositivi sulla macchina vera: il BIOS può
assegnare indirizzi diversi. La *forma* però non cambia — due finestre da 256
byte e una da 2 MB, più un IRQ.

### Il driver

`EKframe.sys` (17 KB), servizio `EKFrame`, tipo kernel driver, avvio automatico.
Si installa copiandolo in `system32\drivers` ed eseguendo `regini ekframe.ini`.
Le applicazioni lo aprono come **`\\.\EKFrameDev`** e ci parlano con
`DeviceIoControl`.

> Fonti: `Driver\INSTALL.BAT`, `Driver\EKframe.ini`, `Driver\GretagSDK.reg`,
> e le stringhe di `EKframe.sys`, `scn_dll.dll`, `DMLCmdTransfer.exe`.

Dalle chiamate importate si legge esattamente come lavora:

- `HalGetBusData`, `HalAssignSlotResources`, `HalTranslateBusAddress` — trova la
  scheda scandendo la configurazione PCI (non è PnP: l'`.inf` installa di
  proposito un `Null_device` senza servizio)
- `MmMapIoSpace` + `READ_REGISTER_ULONG` / `WRITE_REGISTER_ULONG` — accede ai
  registri a 32 bit sulla finestra mappata
- `IoConnectInterrupt`, `KeInsertQueueDpc` — interrupt e DPC
- `IoAllocateAdapterChannel`, `IoMapTransfer`, `IoFreeAdapterChannel` — DMA
  bus-master via adapter object

Parametri del driver, da `GretagSDK.reg`
(`HKLM\SOFTWARE\Kodak\MiniLab\ScannerSDK`):

```
N_DMA_Buffer    = 2      due buffer DMA
Scanner Type    = 3      identifica il modello
LockDownMemory  = 0
Log / FW_Diag_Print_Level = 0    diagnostica disattivata (si può alzare!)
```

~~Nota: fra le stringhe di `EKframe.sys` compaiono messaggi ARCNet
("ARCNet failed ram test", ecc.). Sono residui del driver di esempio da cui
è stato derivato, non funzioni reali. Da ignorare.~~

> **Corretto a ottobre 2026: era sbagliato.** I messaggi ARCNet non sono
> residui: sulla scheda c'è davvero un controller ARCNET SMC COM20020, ed è
> il canale comandi verso la testa di scansione (PC nodo `0x55`, scanner
> nodo `0xAA`). Vedi [scheda-iaq.md §4](scheda-iaq.md#4-arcnet-com20020--il-canale-comandi).

### Perche' la macchina deve restare a 32 bit

Il driver e' del 2001 e non e' firmato. Windows a **64 bit** pretende driver
firmati dal costruttore e non ne carica altri: la, questa scheda non parte, e non
c'e' modo di aggirarlo se non disabilitando protezioni che e' meglio lasciare
dove sono.

Windows **7 a 32 bit** non lo pretende, e i servizi kernel che quel driver usa -
`HalGetBusData`, `HalAssignSlotResources`, il DMA via adapter object - ci sono
ancora. E' anche l'ultimo Windows su cui gira Python 3.8, che e' l'ultimo Python
che ci gira. Le due cose combaciano, e non per caso: siamo all'ultimo anello
della catena in cui questa macchina puo' ancora vivere.

Serve inoltre uno **slot PCI vero** sulla scheda madre: nel 2009 ce n'erano
ancora, spesso uno o due accanto ai PCIe. Un ponte PCIe-PCI di solito funziona,
ma va provato.

Il driver si installa come servizio legacy (non PnP): `INSTALL.BAT` del software
originale copia `ekframe.sys` in `system32\drivers`, esegue `regini ekframe.ini`
e fa `net start EKFrame`. Da amministratore. Se in Gestione Dispositivi la
scheda non compare, e' normale: l'`.inf` installa di proposito un dispositivo
nullo, e il driver trova la scheda scandendo il bus PCI da solo.

### Sopra il driver

`scn_dll.dll` (876 KB) è l'SDK vero e proprio, classi `DMLScanner` e
`DMLScannerAdv`. Espone, fra gli altri: `capture`, `scan_raw`, `focus_adv`,
`frame_detect_scan`, `preview_jog_scan`, `set_magnification`,
`gate_sensor_cal`, `nvm_focus_mag_pos`, `get_image_column_defects`.

Due firme meritano attenzione perché descrivono capacità che il software
originale non sfruttava a fondo:

- `scan_raw(buffer, ColorChannel, ExposureTime, bool, int, int, int, int, ImageResolution, int)`
  — **acquisizione grezza per singolo canale con tempo di esposizione esplicito.**
  È esattamente ciò che serve al nostro modello a quattro passaggi con
  esposizione per canale.
- `get_image_column_defects(ImageResolution, GateType, ManGateQual, ColDefectList&)`
  — la macchina conosce le colonne difettose del proprio CCD. Vale la pena
  leggerle e tenerne conto invece di scoprirle a posteriori.

`PhxScanner.exe` è solo il guscio COM (`GretagImaging.PhxScanner.1`) attorno a
`scn_dll.dll`.

---

## 4. Firmware

| File | Versione | Ruolo |
|---|---|---|
| `pappl.wrp` | v2.3.7 | firmware applicativo dello scanner |
| `pboot.wrp` | v2.0.1 | firmware di boot |
| `control_gretag001.wrp` | v72 | codice EPLD (logica programmabile della scheda) |

> `APP ORIGINALE\Scanner\Firmware\` + `firmware_versions.txt`, `readme.txt`.

In `InstallService\KodakFirmware\` ci sono anche `EPLD_v68.rbf` (bitstream Altera
"raw binary file", versione più vecchia) e i binari `pappl_2_1_6.bin`,
`pboot_2_0_1.bin`.

**Conservare questi file come oro.** Il firmware si scarica sulla macchina e una
macchina con firmware perso senza questi file è ferma per sempre.

---

## 5. Gli strumenti diagnostici originali — sottovalutati

In `APP ORIGINALE\InstallService\KodakFirmware\` ci sono tre programmi che
comandano la macchina direttamente, scavalcando l'applicazione:

- **`GretagEMEval.exe`** (2,4 MB) — programma di valutazione Gretag
- **`Photon.exe`** (2,2 MB)
- **`DMLCmdTransfer.exe`** — livello di trasferimento comandi; apre
  `\\.\EKFrameDev` e invia comandi a slot con coda e timeout

Più i batch `set_35_XY_offset.bat`, `set_APS_XY_offset.bat`,
`set_110_XY_offset.bat`, che scrivono gli offset senza passare dall'applicazione.

Sono il banco di prova migliore che abbiamo: permettono di far muovere la
macchina e osservare il traffico **senza scrivere una riga di driver**.

---

## 6. Cosa il manuale di servizio aggiunge

Capitolo 7, "Film scanner", pagg. 235-284. Il programma di servizio ha:
`Self Test`, `Flash & Flags`, `Sensor Test`, `Actor Test`,
`APS Cartridge Handling`, `DX/MOF Test`, `Adjust Film Guides`,
`Length Calibration`.

Due sezioni contano più delle altre per noi:

**7.3.5 Length calibration** (pag. 271) — regola *"the transport distance from
frame detection to the scan window"* per 135, APS e 110. È **il nostro
`sensore_ingresso_finestra_mm`**. Il manuale spiega che non è una costante
nominale ma una taratura per macchina, espressa in **passi motore** e salvata nel
Parameter Flash: le tolleranze dei rulli e la superficie della pellicola fanno sì
che lo stesso numero di passi non copra sempre la stessa distanza.

Conseguenza pratica: quel valore **non va misurato con un calibro, va letto dalla
macchina** — o rifatto con la procedura del manuale, che richiede una striscia
135 lunga (≥ 20 fotogrammi) con bordi ben leggibili e passo regolare.

**7.3.7 Autofocus** (pag. 275) — l'autofocus è per formato e il risultato finisce
nel Parameter Flash. Pellicole di prova Gretag: 135 P/N 34.91.51, APS P/N
34.91.87, 110 P/N 34.91.10. Il manuale **non** dichiara il passo minimo
dell'attuatore: resta il dato mancante per la decisione sulla super-risoluzione,
e va ricavato osservando `focus_adv` / `nvm_focus_mag_pos` o muovendo il fuoco a
passi noti con `Actor Test`.

---

## 7. Cosa resta davvero ignoto

Dopo questa lettura, la lista dei buchi si è accorciata parecchio:

1. **I codici IOCTL di `EKframe.sys`** — sono costanti numeriche nel codice, non
   stringhe: servono un disassemblatore e mezza giornata.
2. **Il protocollo sui mailbox del 9080** — cosa l'EPLD Gretag si aspetta di
   ricevere. Osservabile facendo lavorare `DMLCmdTransfer.exe` e guardando i
   registri.
3. **Il protocollo seriale del gate** — i comandi ASCII/binari su COM5.
   Osservabile con uno sniffer seriale mentre il programma di servizio muove la
   macchina.
4. **Il passo dell'attuatore di fuoco** — vedi sopra.

Nessuno di questi quattro è un muro. Tre su quattro si risolvono guardando la
macchina lavorare, non aprendola.

---

## 8. La conclusione che cambia il piano

Riscrivere da zero il driver di una scheda PCI del 2001, con DMA bus-master e un
protocollo interno non documentato, per farlo girare su Windows 7 a 32 bit, è
un lavoro da mesi con un rischio reale di non arrivare mai in fondo.

Tenere il PC originale come **motore di acquisizione** — driver e SDK originali
che fanno quello che già sanno fare — e metterci sopra il nostro software per
tutto il resto costa settimane e funziona da subito. È esattamente il
`BridgeBackend` che è già in `hal.py`, e questa ricerca dice che è la scelta
giusta, non un ripiego.

Il valore del nostro progetto non è mai stato nel driver. È nel salvare la piena
risoluzione che il sensore già produce, invece dei 600×400 pixel del 1995.
Per farlo basta poter chiamare `scan_raw` con il canale e l'esposizione che
vogliamo — e quella funzione c'è già, dentro `scn_dll.dll`.
