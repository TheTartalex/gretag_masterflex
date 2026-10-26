# I comandi originali della macchina

Catalogo di cosa il software originale sa dire allo scanner, ricostruito dai
binari in `APP ORIGINALE\Scanner\`. Serve a una cosa sola: comandare la macchina
senza la sua interfaccia.

**Come e' stato ricavato.** I metodi C++ esportati da un DLL Microsoft portano
la firma completa dentro il nome (name mangling): tipo di ritorno, classe,
argomenti. `scn_dll.dll` ne esporta **310**. Non e' stato indovinato niente: e'
stato letto.

---

## 1. L'SDK — `scn_dll.dll`, classe `DMLScanner`

Versione 27.4.0.0 (dal log in `Scanner\SDKCacheDir\log_0000.txt`).
`DMLScannerAdv` estende `DMLScanner` con le funzioni di servizio.

Ogni metodo torna `ErrStat*`: **puntatore nullo = tutto bene**.

### Apertura e identita'

| Metodo | Cosa fa |
|---|---|
| costruttore `DMLScannerAdv(...)` | apre la macchina — 3 percorsi, 2 callback, 2 flag |
| `ResetScanner()` | reset |
| `get_attached_scanner_type()` | tipo di scanner collegato |
| `gretag_GetScannerID(uint*)` | matricola |
| `SetFirmwarePath(const wchar*)` | dove stanno i `.wrp` |
| `get_gate_type(GateType*)` | quale carrello e' montato |
| `get_media_info(MediaInfo**)` | dati del rullo — DX compreso |
| `get_device_status(...)` | stato macchina |

L'apertura avviene **nel costruttore**: e' li' che l'SDK apre `\\.\EKFrameDev`.
Quando fallisce scrive `Can't open scanner` nel proprio log — ed e' esattamente
quello che si legge nel log del 2 agosto 2025.

### Trasporto pellicola

| Metodo | Cosa fa |
|---|---|
| `gate_feed_on()` / `gate_feed_off()` | trascinamento |
| `advance_one_frame()` | avanza di un fotogramma |
| `advance_one_half_frame()` | mezzo fotogramma |
| `gretag_NextFrame()` / `gretag_PreviousFrame()` | fotogramma successivo / precedente |
| `gate_test_advance(int*)` / `gate_post_test_advance(int)` | avanzamenti di prova |
| `gate_sensor_cal()` | calibrazione dei sensori del gate |
| `set_gate_mode(GateMode)` / `get_gate_mode(GateMode*)` | modo normale o servizio |

### Rilevamento fotogrammi

| Metodo | Cosa fa |
|---|---|
| `frame_detect_scan(ImageResolution, uchar*, FilmType)` | cerca i fotogrammi |
| `frame_detect_adjust(int)` | correzione grossolana |
| `frame_detect_fine_adjust(long)` | **correzione fine, in passi motore da 0,01 mm** |
| `frame_detect_done()` | chiude la sessione |

`frame_detect_fine_adjust` e' la stessa funzione che il programma di servizio usa
con le frecce nella Length Calibration (manuale cap. 7.3.5). E' anche il nostro
unico modo di spostare la pellicola di una quantita' arbitraria.

### Fuoco

| Metodo | Cosa fa |
|---|---|
| `focus(FocusType)` | autofocus |
| `focus_adv(FocusMode, FocusResult*, FocusAlg, FocusMotorMode)` | autofocus con controllo di algoritmo e modo motore |
| `setup_focus_scan_defaults(...)` | politiche di default |
| `nvm_focus_mag_pos(FocusMagPos*, GateType, ManGateQual, uint, bool)` | **legge/scrive dal flash le posizioni di fuoco e ingrandimento** |

`nvm_focus_mag_pos` in lettura e' la via per arrivare al passo dell'attuatore di
fuoco senza smontare niente.

### Acquisizione — la parte che ci interessa davvero

| Metodo | Cosa fa |
|---|---|
| `scan_raw(ushort*, ColorChannel, ExposureTime, bool, int x0, int y0, int w, int h, ImageResolution, int)` | **lettura grezza di un canale, con esposizione e regione esplicite** |
| `capture(bool, ImageResolution, bool)` | acquisizione normale |
| `add_image_buffer(ImageResolution, ushort*)` | consegna un buffer all'SDK |
| `get_image_buffer_size(ImageResolution, bool)` | quanto deve essere grande (statica) |
| `get_scan_data(ScanData**)` | risultati |
| `cancel_scan()` | annulla |
| `preview_jog_scan(...)`, `perform_video_mode_scan(ushort*)` | anteprime |

`scan_raw` e' il motivo per cui questo progetto ha senso: campioni a 16 bit come
escono dall'ADC, un canale alla volta, con l'esposizione che scegliamo noi.
Il software originale aveva tutto questo e salvava 600x400.

### Calibrazione, colore, diagnostica

| Metodo | Cosa fa |
|---|---|
| `calibrate(...)`, `calibrate_all(...)`, `get_calibration_status(...)` | calibrazione |
| `select_datapath_negative(...)` / `..._reversal(...)` | percorso dati negativo / diapositiva |
| `load_matrix_var(Matrix_ID, Matrix)`, `nvm_matrix(...)` | matrici colore |
| `set_dmin_removal_values(double, double, double)` | rimozione della base |
| `diag_input(int, bool*, ScanCallback*)` | **legge un ingresso digitale — i sensori** |
| `diag_output(int, bool, ScanCallback*)` | comanda un'uscita |
| `diag_GetCCDInfo(CCD_Region*, ScanCallback*)` | geometria reale del CCD |
| `get_image_column_defects(...)` | colonne difettose del sensore |
| `readIAQMemory(ulong, ulong, ulong*)` / `writeIAQMemory(...)` | accesso diretto alla memoria della scheda |
| `nvm_raw(NVMemRaw*, bool)` | **l'intero Parameter Flash, in lettura e scrittura** |
| `nvm_xy_offset(...)`, `nvm_serial_number(...)`, `nvm_defect_col_list(...)` | singole aree del flash |

`nvm_raw` in lettura e' il backup del Parameter Flash in una chiamata sola: il
lavoro piu' urgente di tutto il progetto, quello che mette al sicuro la taratura
fatta da un tecnico con pellicole di prova che oggi non esistono piu'.

### `ExposureTime`

Oggetto passato **per valore** a `scan_raw`. Occupa **392 byte**: non e' una
stima, `scan_raw` termina con `ret 0x1ac` (428 byte di argomenti) e gli altri
argomenti ne fanno 36; nel corpo si vede `sub esp, 0x188` = 392.

| Metodo | Cosa fa |
|---|---|
| `ExposureTime()` | costruttore |
| `set_to_default_texp()` | valori di fabbrica |
| `operator*=(double)` | scala l'esposizione |
| `is_positive()` | verifica |
| `read_file(...)` / `write_file(...)` | persistenza |

Bastano questi quattro per fare quello che ci serve: partire dai tempi di
fabbrica e moltiplicarli per canale — che e' esattamente il modello di
esposizione per canale del nostro software.

---

## 2. Le costanti ottiche — e la rettifica che portano

Sono variabili globali inizializzate all'avvio del DLL, diverse per tipo di
scanner. Il nostro e' il **tipo 3** (`GretagSDK.reg`: `"Scanner Type"=dword:3`,
confermato da `auto_update.bat`, che esegue `DMLCmdTransfer 12 3` = *set scanner
type 3*).

| Costante | Tipo 3 (il nostro) | Tipi 0 e 1 |
|---|---|---|
| `PIXEL_SIZE` | **6,8 um** | 9 um |
| `MAG_35` | **0,420026** | 0,794 |
| `MAG_APS` | **0,523181** | 0,989 |
| `FSTOP_FAC` | 1,81 | 1,81 |
| `N_CHANNELS` | 3 | 3 |

Tabella `IMAGE_RESOLUTION`, cinque varianti:

| Indice | Tipo 3 (il nostro) | Tipi 0 e 1 |
|---|---|---|
| 0 | **2100 x 1400** | 3000 x 2000 |
| 1 | 1052 x 700 | 1500 x 1000 |
| 2 | 524 x 350 | 752 x 500 |
| 3 | 264 x 175 | 376 x 250 |
| 4 | 132 x 88 | 188 x 125 |

**Cosa ne segue, ed e' bene dirlo chiaro.** 6,8 um diviso 0,420026 fa 16,19 um
sul film: circa **1570 dpi** sul 135, non 4000. Il fotogramma pieno e' 2100x1400,
non 5669x3779 come avevamo ipotizzato.

Resta un salto di dodici volte rispetto ai 600x400 del software originale, ed e'
un numero onesto invece di una speranza. Ha anche un lato buono che non va
sottovalutato: a 2100x1400 il problema della memoria su Windows a 32 bit
semplicemente non esiste piu' — un fotogramma a quattro canali sta in 24 MB.

`config.py` e' stato corretto di conseguenza.

---

## 3. `DMLCmdTransfer.exe` — la macchina da riga di comando

Utility Kodak del 2000 che apre `\\.\EKFrameDev` e manda comandi. Il menu, letto
dalle sue stringhe:

| # | Comando |
|---|---|
| 1 | timer tick test |
| 2 | film gate movement test |
| 3 | download application |
| 4 | download EPLD |
| 5 | **scan roll of film** |
| 7 | set clock and clamp |
| 8 | **set XY offsets** |
| 9 / 10 | motor life test #1 / #2 |
| 11 | set mag position |
| 12 | **set scanner type** |
| 13 | Phogenics motor test |
| 14 | download boot code |
| 15 | download NVMEM image |

Uso: `DMLCmdTransfer <n> [argomenti]`.

I `.bat` accanto lo confermano e ci regalano l'enumerazione dei formati:

    set_35_XY_offset.bat   ->  DMLCmdTransfer 8 0 %1 %2     135 = 0
    set_APS_XY_offset.bat  ->  DMLCmdTransfer 8 1 %1 %2     APS = 1
    set_110_XY_offset.bat  ->  DMLCmdTransfer 8 4 %1 %2     110 = 4

E `auto_update.bat` mostra la sequenza di ripristino del firmware — da tenere da
parte, perche' e' la procedura d'emergenza se la macchina perde il firmware.

---

## 4. Eventi e tipi, dalle stringhe

**Eventi asincroni** che la macchina manda al PC:

    DML Scanner connected / disconnected
    Film Inserted.
    Film Upside Down.
    image complete
    Asynchronous Event [%d] is sent by the scanner.

`Film Inserted` merita attenzione: la presenza pellicola arriva come **evento**,
non solo come sensore da interrogare. Il nostro ciclo automatico puo' quindi
essere guidato dagli eventi invece che dal polling — piu' vicino a come la
macchina e' fatta.

**Tipi di carrello** (`GateType`): No gate · Manual gate (strip) · Manual gate
(mount) · NK 35mm · NK APS · Noritsu 135ANM · Noritsu 240ANM · Future 1 · Future 2.

**Formato pellicola** (`FilmFormat`): 0 = 135, 1 = APS, 4 = 110 (confermati);
2 e 3 restano da verificare, verosimilmente i due carrelli manuali.

---

## 5. Le interfacce COM, per completezza

I programmi originali sono server COM registrati:

| ProgID | CLSID | Eseguibile |
|---|---|---|
| `GretagImaging.PhxScanner.1` | `{BFE72872-A580-11d2-9772-00104B36A39C}` | `Scanner\Bin\PhxScanner.exe` |
| `GretagImaging.GateControl.1` | `{82C76FF1-613F-11d3-98B0-00104B36A39C}` | `Scanner\Bin\GateControl.exe` |

Interfacce: `IPhxScanner` (11 metodi) con `IPhxScannerNotification` per gli
eventi; `IGate` (26 metodi) con `IGateNotification`.

Sono interfacce COM **senza type library**: chiamarle da fuori richiede di
ricostruire le firme dai proxy/stub (`PhxScannerps.dll`, `FilmGateps.dll`).
Si puo' fare, ma non conviene: l'SDK sotto ha le firme complete nei nomi
esportati ed e' un livello piu' vicino alla macchina. Restano come seconda
strada se la prima trovasse un muro.

---

## 6. Quello che ancora non sappiamo

- I valori numerici di `ColorChannel`, `FocusType`, `GateMode`, `FilmType`.
  Si scoprono provandoli sulla macchina, uno alla volta, guardando cosa fa.
- Quale numero di `diag_input` corrisponde a quale sensore. Stessa cosa: si
  provano con il percorso libero e poi con la pellicola dentro.
- Il layout interno di `NVMemRaw`, `FocusMagPos`, `MediaInfo`. Per il backup
  non serve: si salvano i byte come sono.

Sono tutte domande che si risolvono **guardando la macchina lavorare**, non
aprendola e non disassemblando altro.

---

## 7. Errori e stati dell'SDK

Ricavati il 26/10/2026 da `scn_dll.dll` (SDK 27.4.0.0, SHA-256
`2519fa07…54e8`), che resta fuori dal repository.

**Codici `ScnError`.** La tabella è nello `switch` di
`ErrStat::formatScnErrorEnum` (`0x18028AE0`, valori 0–0xCE). I più utili:

| Codice | Nome | Codice | Nome |
|---|---|---|---|
| 1 | ERR_SDK_SW | 22 | ERR_POSITION_UNKNOWN |
| 2 | ERR_TIMEOUT | 28 | ERR_FILM_JAM |
| 3 | ERR_ILLEGAL_IN_THIS_STATE | 31 | ERR_MOVE |
| 4 | ERR_NO_SCANNER | 33 | ERR_CANCEL_DETECTED |
| 5 | ERR_COMMAND_PARAMETER | 41 | ERR_NVMEM |
| 6 | ERR_CALIBRATION_FAILED | 43 | ERR_SCANNER_HW |
| 10 | ERR_CANT_OPEN_SCANNER | 48 | ERR_NK_GATE_FIRMWARE |
| 11 | ERR_NOT_CALIBRATED | 200 | EV_FILM_INSERTED |
| 12 | ERR_NO_FILM_LOADED | 202 | EV_GATE_CHANGE |
| 14 | ERR_NO_GATE_INSTALLED | 203 | EV_STATE_CHANGE |
| 16 | ERR_NO_CALIB_DATA | 204 | EV_SCAN_COMPLETE |

I valori da 100 a 143 sono qualificatori (`QUAL_…`) e compaiono come
dettaglio. La lista completa, con i nomi, sta in `ponte/sdk.py`
(`NOMI_ERRORI_SDK`).

**Lo stato interno.** È una variabile globale (`0x180BF92C`), che vale 7 al
caricamento della DLL. Prima di ogni comando `ScnState` (`0x18079E40`) la
confronta con l'identificativo del comando (per esempio `0x16` =
`get_gate_type`):

- **stato 10**: quasi ogni comando risponde `ERR_NO_SCANNER` più
  `ERR_ILLEGAL_IN_THIS_STATE` con dettaglio 10;
- **stato 0**: quasi tutto è ammesso.

Il cambio arriva al callback d'errore come `EV_STATE_CHANGE`, nella forma
`(203, nuovo, vecchio)` (`0x1807AAB2`). Sulla riserva lo scanner passa da 10
a 0 circa 40 s dopo l'apertura. L'agente aspetta quel momento prima di
accettare comandi.

**Come lo usa il programma originale.** `PhxScanner.exe` importa 36 funzioni
di `DMLScanner`:

- per acquisire: `calibrate_all`, `get_calibration_status`,
  `add_image_buffer`, `capture`, `import_image`, `get_device_status`;
- per il carrello: `select_film_format`, `gretag_NextFrame`,
  `frame_detect_*`, `show_gretag_ui`.

Non usa mai `DMLScannerAdv::scan_raw`, che usiamo noi: la sequenza di
`PhxScanner` è il riferimento per capire cosa va fatto prima di
un'acquisizione.

**`scan_raw`, i quattro interi.** Firma: `scan_raw(ushort*, ColorChannel,
ExposureTime, bool, int a, int b, int c, int d, ImageResolution, int)`.

- `a`–`b` sono le colonne e `c`–`d` le righe, estremi compresi, in pixel a
  piena risoluzione sul CCD;
- l'SDK conta `(b−a+1)·(d−c+1)` pixel e li divide per `4^risoluzione`
  (`scnr_ifc`, `0x18078940`);
- al firmware manda il rettangolo `(a, c, b, d)` (`0x18069F50`).

Il programma originale, per il 135, usa colonne 47–2146 e righe 76–1475,
cioè 2100x1400 a partire dall'offset XY del formato (47, 76). Nel log
dell'SDK compare come `set resolution: bin=0 scalar=3 [47,76] to
[2146,1475]`. Valori sbagliati danno `ERR_SW_SCANNER_IFC` +
`ERR_FW_ERR_NO 1` + `ERR_GENERAL_FW` ("set resolution failed, ErrNo = 1").

Canale (`ColorChannel`): 0–3 sono un piano solo (maschere 1, 2, 4, 8: il 3 è
il quarto piano, l'IR); 4 vale tre piani (maschera 7), 5 quattro piani
(0xF).

Nello stato 0 sono vietati i comandi `0x0D`, `0x14`, `0x1C` e `0x1D`.
`0x1D` è quello di tutti i `frame_detect_*`, compresa la regolazione fine:
servono con la pellicola caricata.

**Mettere il carrello Gretag in attesa della pellicola.**
`select_film_format` da solo non basta. Il programma originale (`PhxScanner`,
`0x447820` e `0x447860`) chiama poi `set_scanner_input_mode(modo)`, con modo
1 o 2: il 3 lo rifiuta come non supportato.

Per il carrello Gretag l'SDK lo trasforma nella richiesta 3 al GateControl,
ChangeFilmFormat (`0x18037FA0`), e aspetta la risposta fino a 30 s. Se il
carrello non c'è, risponde `ERR_NO_NK_GATE` (`0x18075B90`). L'arrivo della
pellicola si annuncia con `EV_FILM_INSERTED` (200) al callback.

**Fotogramma successivo, precedente, espulsione.** Con il carrello Gretag
il programma originale usa `gretag_NextFrame` e `gretag_PreviousFrame`
(richiesta 6 al GateControl, `0x180386A0`): è il carrello a cercare il
fotogramma. `advance_one_frame` è dei carrelli Kodak.

L'espulsione passa da `cancel_scan`, cioè `ScanCntr::OnCancelScan`
(`0x18060000`), che per il Gretag manda la richiesta 5, Eject
(`0x18038580`: `push 0; push 5`, poi attesa fino a 30 s). La richiesta
**non porta il verso**, e nemmeno il comando `22` che il GateControl manda
al carrello: il verso lo decide il flag 5 del carrello, *Eject to Back*
([carrello-capri.md §3](../hardware/carrello-capri.md)). Sulla riserva è a
0, quindi la pellicola esce dal davanti. Per farla uscire dal retro senza
toccare il flash, filmscan va avanti a fotogrammi finché il carrello non
rifiuta (`ponte/sdk.py`, `espelli_retro`). Quale errore dia l'SDK a
pellicola uscita va ancora visto sulla macchina.

**Fine striscia.** Dopo l'ultimo fotogramma `gretag_NextFrame` risponde
`ERR_MOVE` (31): visto sulla riserva il 26/10/2026, a 20 fotogrammi.

**Tempo di esposizione.** `ExposureTime` ha, per ogni LED, 12 tempi in
`double` (`0x60` byte a LED da `+8`; il numero di LED è in `0x180B69C4`).
`operator*=` li moltiplica tutti. L'SDK li taglia a un massimo per LED
(`0x180684A0` con `0x1807C710`): 30000 per R, G e B, 5120 per l'IR. Il
firmware della testa però rifiuta già prima: a x12 rispetto ai valori di
fabbrica risponde `ERR_SW_SCANNER_IFC` + `ERR_FW_ERR_NO 1` +
`ERR_GENERAL_FW`, a x1 accetta. Il limite vero non è ancora misurato.

## 8. Cosa l'SDK offre e non usiamo ancora

*Censimento del 26/10/2026 sulla `scn_dll.dll` della riserva: 119 metodi
pubblici di `DMLScanner` e `DMLScannerAdv`, 45 legati in `ponte/sdk.py`.*
Le firme vengono dai nomi decorati. Dove serve sapere cosa fanno davvero,
il disassemblato va guardato prima di usarle.

### Da provare, in quest'ordine

| Metodi | Cosa sono | Perché ci servono |
|---|---|---|
| `get_exp_time`, `calibrate_all`, `get_calibration_status` | tempi di esposizione **calibrati** per LED (la calibrazione dell'SDK, `calib_texp_*`) | noi partiamo da `set_to_default_texp`, i valori di fabbrica. Il programma originale chiama `calibrate_all` all'avvio. Coi tempi calibrati il colore e il limite d'esposizione (x12 rifiutato) potrebbero tornare a posto da soli |
| `gi_clamp` (Adv) | pressore della pellicola: richieste Gretag 19 e 20, *Clamp Down* e *Clamp Up* | pellicola piatta durante la lettura, cioè nitidezza. Da capire se `scan_raw` lo abbassa da solo |
| `get_temperature`, `get_voltage` (Adv) | temperatura e tensioni della testa | diagnosi e controllo prima della calibrazione del giorno. Sola lettura |
| `nvm_ccd_serial_number`, `nvm_usage_log`, `nvm_calib_timer`, `nvm_led_initial_response`, `nvm_led_contribution`, `nvm_defect_col_list`, `get_image_column_defects` (Adv) | memoria non volatile della testa: numero di serie, contatori d'uso, risposta dei LED, **colonne difettose del CCD** | diagnosi; le colonne difettose si possono correggere nella nostra pipeline. **Attenzione:** molte `nvm_*` hanno un booleano finale che probabilmente sceglie lettura o scrittura: va verificato prima |
| `show_gretag_ui`, `move_gretag_ui` | la finestra di servizio del GateControl | un tasto "pannello del carrello" per il tecnico, senza chiudere l'agente |
| `gi_set_asf_mode`, `SetASFImageBufferPtr` | ASF = Applied Science Fiction, cioè **Digital ICE** | la pulizia IR dell'SDK, da confrontare con la nostra |
| `get_processed_frame_info`, `get_processed_leader_info`, `get_is_trailer_first_status`, `assign_frame_number`, `frame_detect_scan` | rilevamento dei fotogrammi dell'SDK e numeri letti dal bordo | i **numeri veri del fotogramma** al posto del contatore, e il verso della striscia |
| `perform_video_mode_scan`, `get_video_mode_size`, `preview_jog_*`, `scan_in_preview_jog_mode` | anteprima veloce a bassa risoluzione e spostamento fine in anteprima | un'anteprima dal vivo per mettere in finestra il fotogramma |
| `gi_focus`, `setup_focus_scan_defaults`, `set_aperture`, `set_magnification` | fuoco e ingrandimento: richieste Gretag 10–13 (*SetupFocus*, *GotoFocusPos*, *StoreFocusPos*, *SetFocusParam*) | autofocus, e il passo del fuoco che manca ancora (`focus_step_mm`) |

### Il percorso immagine dell'SDK: solo come riferimento

`capture`, `import_image`, `select_image_processing_mode`,
`select_datapath_negative`, `select_datapath_reversal`, `load_LUT_var`,
`load_matrix_var`, `set_dmin_removal_values`, `enable_exposure_compensation`,
`load_correction` e `get_correction_filename` sono la catena d'immagine
dell'SDK, quella che usa `PhxScanner.exe`. Noi leggiamo il grezzo con
`scan_raw` e facciamo la nostra. Le matrici e le LUT del programma originale
possono servire come punto di partenza per i profili.

### Da non usare

| Metodi | Perché |
|---|---|
| `download`, `get_available_firmware_version`, `nvmem_program` | scrivono firmware o memoria non volatile della testa: un errore e la testa non riparte |
| `diag_motor` (Adv) | muove i motori senza i nostri limiti |
| `diag_arcnet`, `diag_exposure`, `diag_exposureReadback`, `diag_image`, `diag_mem`, `diag_nk`, `get_diagscan_data` | prove di servizio, utili per l'autotest. Prima va capito cosa muovono, uno per uno |
| `delete_cached_data`, `lock_calibration_data`, `get_calibration_data_filename` | gestione della cache di calibrazione dell'SDK: da toccare solo insieme a `calibrate_all` |
| `gate_enable_anm`, `gate_disable_anm` | significato non chiaro (ANM); da guardare nel disassemblato |

Fra le richieste al GateControl, *Goto Frame* (8) non ha chiamanti
nell'SDK. *NextFrameGap* (4) e *Place Manually* (9) le usano solo funzioni
interne.
