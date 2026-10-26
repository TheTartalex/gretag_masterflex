# Piano di lavoro — dal software originale alla macchina che scansiona

Stato al **3 settembre 2026**, con le correzioni di ottobre segnate nelle
tabelle e riassunte in fondo. Lo stato giorno per giorno sta in
[stato.md](stato.md); qui c'è il percorso a lungo termine.

Le fasi sono in ordine di dipendenza, non di preferenza: ogni fase usa quello che
la precedente ha prodotto. Le fasi 0, 1 e 6 si possono però portare avanti in
parallelo alle altre, perché non dipendono da niente.

Legenda stato: **[fatto]** · **[in corso]** · **[da fare]** · **[bloccato]**

Riferimenti: `SM` = manuale di servizio
(`APP ORIGINALE\Help\PhxServiceEnglish.pdf`), `AO` = `APP ORIGINALE\`,
`SA` = [scheda-acquisizione.md](hardware/scheda-acquisizione.md),
`NM` = [note-macchina.md](hardware/note-macchina.md),
`IAQ` = [scheda-iaq.md](hardware/scheda-iaq.md).

---

## Fase 0 — Mettere al sicuro l'irripetibile

Prima di toccare qualsiasi cosa. Il software originale esiste in un solo
esemplare al mondo e la taratura di questa macchina è in un chip.

| # | Attività | Stato | Riferimento |
|---|---|---|---|
| 0.1 | Copia della cartella del software originale | **[fatto]** | `AO\` |
| 0.2 | Immagine bit a bit del disco del PC originale | **[da fare]** | [installazione.md](filmscan/installazione.md), ultima sezione |
| 0.3 | Backup del Parameter Flash della macchina su file | **[da fare]** | SM 7.2, pulsante `Flash & Flags` → backup |
| 0.4 | Copia a parte dei firmware `.wrp` e `.rbf` | **[da fare]** | `AO\Scanner\Firmware\`, `AO\InstallService\KodakFirmware\` |
| 0.5 | Trascrizione dei parametri già leggibili nei file `.ini` | **[fatto]** | SA §2, §3 |

Il punto **0.3** è il più urgente e il meno ovvio: nel flash ci sono la length
calibration, gli offset XY e le posizioni di fuoco per i tre formati. Sono il
frutto di una taratura fatta da un tecnico con pellicole di prova che oggi non si
comprano più. Il backup si fa dal programma di servizio, in un minuto, e va
copiato fuori dal PC.

---

## Fase 1 — La macchina che fa tutto

Una macchina sola: il PC del 2009 con **Windows 7 a 32 bit**, con la scheda
originale dentro. Avvia, comanda lo scanner, elabora e pubblica l'interfaccia
sulla rete. Non c'e' un secondo computer da nessuna parte.

Windows 7 a 32 bit non e' un ripiego: e' l'unico sistema su cui `EKframe.sys`,
non firmato e del 2001, puo' ancora caricarsi — a 64 bit i driver non firmati non
partono — ed e' anche l'ultimo su cui gira Python 3.8. Le due cose combaciano.

### 1a — Ferro e driver

| # | Attivita' | Stato | Riferimento |
|---|---|---|---|
| 1.1 | Scheda IAQ in uno slot PCI vero della macchina | **[da fare]** | SA §3 |
| 1.2 | Driver `EKFrame` installato e avviato | **[da fare]** | `INSTALL.BAT` del software originale, da amministratore |
| 1.3 | Verificare le risorse assegnate in Gestione Dispositivi | **[da fare]** | attese: mem `0xDE000000`, I/O `0xA800`, mem 2 MB, IRQ 9 |
| 1.4 | Software originale copiato in `C:\MasterflexD\` | **[da fare]** | serve almeno `Scanner\` (SDK, IniFiles, Firmware) |
| 1.5 | `DMLCmdTransfer 1` — la catena e' viva? | **[da fare]** | cinque minuti, nessun codice da scrivere |

### 1b — Il nostro software sulla macchina

| # | Attivita' | Stato | Riferimento |
|---|---|---|---|
| 1.6 | Python 3.8.10 **a 32 bit**, con PATH e tcl/tk | **[da fare]** | a 64 bit l'SDK non si carica |
| 1.7 | `MASTERFLEX.bat` voce 7, installa / ripara | **[da fare]** | Python, librerie, firewall; verifica driver, SDK e firmware |
| 1.8 | `MASTERFLEX.bat` voce 5, collaudo del programma | **[da fare]** | ambiente ed elaborazione, senza macchina |
| 1.9 | `MASTERFLEX.bat` voce 3, simulazione, e collegarsi dal tablet | **[da fare]** | rete e interfaccia, senza muovere motori |
| 1.10 | Regola firewall per la porta 8080 | **[fatto nel codice]** | `MASTERFLEX.bat` la crea da amministratore |
| 1.11 | `windows\avvio-automatico.bat` | **[da fare]** | la macchina si accende e c'e' tutto |

### 1c — Diagnosi con il programma di servizio originale

| # | Attivita' | Stato | Riferimento |
|---|---|---|---|
| 1.12 | `Self Test` completo | **[da fare]** | SM 7.2 |
| 1.13 | `Sensor Test`: annotare le tensioni a vuoto e con pellicola | **[da fare]** | SM 7.2 · soglie in SA §2 |
| 1.14 | `Actor Test`: motori e solenoidi uno per uno | **[da fare]** | SM 7.2 |
| 1.15 | Confronto versioni firmware | **[da fare]** | SA §4 |

Il **1.13** produce un dato che serve davvero: i sensori della macchina sono
analogici, e sapere che tensione danno liberi e coperti e' cio' che permettera'
di distinguere "sensore libero" da "sensore sporco". Va scritto in `NM`.


## Fase 2 — Farsi dire dalla macchina i numeri che ci mancano

Nessuna di queste attività richiede di aprire la macchina né di scrivere codice.
Sono letture.

| # | Attività | Stato | Riferimento |
|---|---|---|---|
| 2.1 | Leggere la length calibration per 135 / APS / 110 | **[da fare]** | SM 7.3.5 pag. 271 → `config.sensore_ingresso_finestra_mm` |
| 2.2 | Convertire i passi in mm con il passo dichiarato (0,01 mm) | **[da fare]** | `NM`, sezione trasporto |
| 2.3 | Autofocus per formato, poi rileggere le posizioni salvate | **[da fare]** | SM 7.3.7 pag. 275 → `config.focus_step_mm` |
| 2.4 | Muovere il fuoco a passi noti con `Actor Test` e misurare | **[da fare]** | SM 7.2 — è la via più diretta al passo minimo |
| 2.5 | Offset XY per formato | **[fatto]** | `AO\IniFiles\Scanner_Customer.ini` → `135: 47 76 · APS: 70 165 · 110: 388 226` |
| 2.6 | Verificare il pitch reale dei formati sul carrello | **[da fare]** | corregge i valori nominali in `config.FORMATS` |
| 2.7 | Leggere l'elenco delle colonne difettose del CCD | **[da fare]** | `get_image_column_defects` in `scn_dll.dll`, SA §3 |

Il **2.1** merita una nota: `sensore_ingresso_finestra_mm` non è una quota di
progetto da cercare in un disegno. Il manuale è esplicito — è una taratura per
macchina, in passi motore, perché rulli e superficie della pellicola cambiano il
rapporto passi/millimetri. Va quindi **letta dal flash**, non misurata.
Finché manca, il nostro software la segnala fra gli errori: comportamento
corretto, da non cambiare.

---

## Fase 3 — Il ponte: comandi originali, interfaccia nostra

Aggiornata il 3 settembre 2026. Il progetto del ponte e' fatto e il codice c'e';
manca la macchina. Dettaglio in [ponte.md](filmscan/ponte.md), catalogo dei comandi in
[api-sdk.md](software-originale/api-sdk.md).

Della macchina originale teniamo driver e comandi. Dell'interfaccia, niente.

| # | Attivita' | Stato | Riferimento |
|---|---|---|---|
| 3.1 | Ricostruire l'API dell'SDK originale (310 simboli) | **[fatto]** | [api-sdk.md](software-originale/api-sdk.md) |
| 3.2 | Collegamento a `scn_dll.dll` da Python, senza compilatore | **[fatto]** | `software/ponte/sdk.py` |
| 3.3 | Agente per il PC originale, senza dipendenze | **[fatto]** | `software/ponte/agente.py` |
| 3.4 | Lato nostro: `PonteSdkBackend` | **[fatto]** | `filmscan/hal.py` |
| 3.5 | Collaudo dell'intera catena in simulazione | **[fatto]** | `agente.py --simula` |
| 3.6 | Correggere la geometria ottica sui valori veri | **[fatto]** | `config.py` — 2100x1400, 6,8 um |
| 3.7 | Prima prova sulla macchina: `DMLCmdTransfer 1` | **[da fare]** | la catena e' viva? cinque minuti |
| 3.8 | `agente.py --simula` sul PC originale | **[da fare]** | il protocollo gira su quel PC? |
| 3.9 | Aprire l'SDK per davvero (`agente.py` senza `--simula`) | **[da fare]** | errori in `SDKCacheDir\log_0000.txt` |
| 3.10 | **`flash_backup` come primo comando vero** | **[da fare]** | mette al sicuro la taratura — vedi 0.3 |
| 3.11 | Scoprire i valori di `ColorChannel` (e se l'IR e' il 3) | **[da fare]** | [ponte.md](filmscan/ponte.md), nota sull'infrarosso |
| 3.12 | Mappare gli ingressi digitali sui sensori | **[da fare]** | `sensori` con percorso libero e poi con pellicola |
| 3.13 | Verificare segno e scala di `regola_<passi>` | **[da fare]** | 100 passi = 1 mm, nel verso giusto? |
| 3.14 | Primo fotogramma vero con `scan_raw` | **[da fare]** | il cardine di tutto |
| 3.15 | Primo rullo completo con la nostra pipeline | **[da fare]** | `pipeline.py` |
| 3.16 | Confronto: nostro TIFF 2100x1400 vs 600x400 originale | **[da fare]** | dodici volte i pixel |

Il **3.10** viene prima di ogni acquisizione, non dopo. Il **3.14** e' il punto
in cui il progetto smette di essere un progetto.


## Fase 4 — Capire il canale seriale (comando della meccanica)

Da qui in poi si guadagna indipendenza dal PC originale. Non è urgente, ma è ciò
che rende il progetto autonomo.

| # | Attività | Stato | Riferimento |
|---|---|---|---|
| 4.1 | Sniffer seriale su COM5 (Portmon o com0com) | **[da fare]** | SM pag. 81 |
| 4.2 | Registrare il traffico di operazioni note: insert, transport, eject, DX, autofocus | **[da fare]** | stati in SA §2 |
| 4.3 | Mappare comandi ↔ stati `CGgcGateState*` | **[da fare]** | SA §2 |
| 4.4 | Implementare il trasporto in Python con `pyserial` | **[da fare]** | nuovo backend in `hal.py` |
| 4.5 | Aggiungere alla nostra HAL la lettura analogica dei sensori | **[da fare]** | modifica a `hal.FilmSensors` — vedi nota sotto |

Nota sul **4.5**: `FilmSensors` oggi ha due booleani. La macchina vera dà
tensioni con una finestra di validità. Il campo booleano va tenuto (i livelli
superiori lo usano), ma affiancato dal valore grezzo e da un flag "fuori
finestra", che è il modo in cui la macchina segnala un sensore sporco.

---

## Fase 5 — Il canale immagine, se e quando

Solo dopo che la fase 3 ha prodotto scansioni vere. Se la fase 3 funziona bene,
questa fase può anche non farsi mai: sarebbe lavoro per eleganza, non per
risultato.

| # | Attività | Stato | Riferimento |
|---|---|---|---|
| 5.1 | Procurarsi il data book del PLX PCI 9080 | **[da fare]** | SA §3 — è pubblico |
| 5.2 | Disassemblare `EKframe.sys` per i codici IOCTL | **[fatto]** | IAQ §5 — ottobre 2026 |
| 5.3 | Disassemblare `scn_dll.dll` per la sequenza di comandi | **[da fare]** | 876 KB, lavoro serio |
| 5.4 | Osservare i mailbox del 9080 mentre `DMLCmdTransfer` lavora | **[superata]** | il canale comandi è ARCNET, non i mailbox: IAQ §4 |
| 5.5 | Decisione motivata: driver nativo oppure PC originale come motore | **[decisa]** | PC originale via SDK — [decisione 0001](decisioni/0001-ponte-sdk-originale.md) |

---

## Fase 6 — Qualità dell'immagine (indipendente dall'hardware)

Si può fare adesso, senza macchina. È il lavoro che determina se le scansioni
saranno buone o solo grandi.

| # | Attività | Stato | Riferimento |
|---|---|---|---|
| 6.1 | Calibrare le matrici colore con un target fotografato su pellicola | **[da fare]** | oggi partono dall'identità — `software/README.md` |
| 6.2 | Tarare le soglie di stacco e bordo sul materiale vero | **[da fare]** | oggi tarate sul simulatore |
| 6.3 | Verificare il budget di memoria a 4000 dpi sulla macchina reale | **[da fare]** | `MASTERFLEX.bat` voce 5 |
| 6.4 | Togliere il debito noto: `ui.py` deve usare `service.py` | **[superata]** | `ui.py` tolto il 26/10/2026, decisione 0005 |
| 6.5 | Valutare i parametri colore dell'originale come punto di partenza | **[da fare]** | `AO\Scanner\IniFiles\KLTMAT.INI`, `CORMAT.INI`, `KLTPAR.INI` |

Il **6.5** è un'occasione da non perdere: i file KLT/CORMAT contengono le matrici
di trasformazione colore e le statistiche di correzione calibrate su questa
macchina, con questa lampada e questo CCD. Non sono direttamente riusabili — il
nostro spazio di lavoro è diverso — ma sono un punto di partenza molto migliore
della matrice identità, e vanno studiati prima di tarare da zero.

---

## Il percorso critico, in una riga

**0.3 / 3.10** (salvare il Parameter Flash) → **3.7** (la catena e' viva) →
**3.9** (l'SDK si apre) → **3.14** (`scan_raw` sotto il nostro comando) →
**3.15** (primo rullo vero).

Tutto il resto e' miglioramento. Questi cinque passi sono il progetto, e i primi
due si fanno in un pomeriggio.

## Cosa e' cambiato il 3 settembre 2026

Il piano nasceva con la fase 3 come "strada breve da progettare". Oggi la fase 3
e' scritta e collaudata in simulazione: il ponte esiste, parla l'SDK originale e
consegna fotogrammi grezzi alla nostra pipeline. La fase 5 — il driver nativo —
si e' ridotta da obiettivo a curiosita': non serve piu' a niente che ci
interessi.

In compenso e' emersa una correzione scomoda e utile: la macchina legge
2100x1400, non 5669x3779. Meglio saperlo adesso.

## Cosa e' cambiato a ottobre 2026

- **Fase 1a.** Sulla macchina di riserva il driver originale non parte su
  Windows 7: lascia un dispositivo "zombie" e la catena non arriva mai alla
  scheda. Al suo posto c'e' il driver nuovo
  [EKframePnP 2.1](../driver/EKframePnP/), che funziona: la scheda si avvia e
  il software la riconosce. La produzione resta con l'originale.
- **Fase 1b.** `installa.bat` su quella CPU (senza AVX) faceva cadere Python:
  numpy va tenuto alla 1.22.4 ([decisione 0003](decisioni/0003-numpy-1.22.4-cpu-senza-avx.md)).
- **Fase 5.** Senza volerlo e' stata fatta in gran parte: il protocollo della
  scheda e' ricostruito e verificato ([scheda-iaq.md](hardware/scheda-iaq.md)).
  La decisione 5.5 non cambia: l'immagine passa ancora dall'SDK originale.
- **Prima di 3.14** viene il problema dell'autotest dei motori, che spinge
  le guide oltre il fine corsa: vedi [stato.md](stato.md).
- **Fase 1c.** Il pacchetto `CapriFirmware` ha il selftest di servizio del
  carrello (prove HOME e PRECISION di guide, CCD, lente, tamburo) e gli
  strumenti Compad e SensorTest: [carrello-capri.md](hardware/carrello-capri.md).
  Il carrello va riportato al firmware 1.15 ([decisione 0006](decisioni/0006-firmware-carrello-1.15.md)).
- **3.12 e 3.13** hanno uno strumento: `collaudo_scanner.py sensori` e
  `passi` (`MASTERFLEX.bat` voce 4).
- **Fase 4.** Il protocollo COM5 non va piu' osservato da zero: i comandi
  di Compad lo descrivono in buona parte ([carrello-capri.md §7](hardware/carrello-capri.md#7-il-protocollo)).
