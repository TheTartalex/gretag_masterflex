# Scheda dati macchina — da compilare

Compilare man mano che i dati emergono dal manuale cartaceo o dall'ispezione.
Ogni voce compilata sblocca una parte del software.

Parte delle voci e' stata compilata il 3 settembre 2026 leggendo il software
originale (`APP ORIGINALE\`) e il manuale di servizio. Le fonti di ogni dato sono
in [scheda-acquisizione.md](scheda-acquisizione.md); l'ordine dei lavori in
[piano-lavori.md](../piano-lavori.md).

## Scheda di acquisizione

Ricavata dal software originale il 3 settembre 2026. Dettaglio completo e fonti
in [scheda-acquisizione.md](scheda-acquisizione.md); protocollo e registri,
verificati a ottobre, in [scheda-iaq.md](scheda-iaq.md).

- Costruttore e modello: **Eastman Kodak - "IAQ Adapter for Kodak film scanners"**
- Bus: **PCI**, bus 0 device 9 funzione 0
- Hardware ID: **`PCI\VEN_11B2&DEV_0008&SUBSYS_908010B5&REV_00`**, class code `040000`
- Chip di interfaccia: **PLX PCI 9080** (dal subsystem `908010B5`) - **data book pubblico**
- Indirizzo base: memoria **`0xDE000000`** (256 B) e I/O **`0xA800`** (256 B) - stessi registri, doppia mappatura
- Finestra bus locale: memoria **`0xDD800000`**, **2 MB** - e' li' che compaiono i dati del CCD
- IRQ: **9**, condivisa
- DMA: **bus-master**, due buffer (`N_DMA_Buffer=2`), adapter object NT
- Driver: **`EKframe.sys`**, servizio `EKFrame`, aperto come **`\\.\EKFrameDev`** via `DeviceIoControl`
- SDK sopra il driver: **`scn_dll.dll`** (classi `DMLScanner` / `DMLScannerAdv`)
- Mappa dei registri: **ricostruita** (ottobre 2026), vedi [scheda-iaq.md](scheda-iaq.md):
  PLX 9080 + FPGA Kodak + controller ARCNET COM20020 + DMA scatter/gather.
  IOCTL noti.
- Driver su Windows 7: l'originale non funziona sulla riserva; si usa
  [EKframePnP](../../driver/EKframePnP/)
- Formato del buffer immagine: da determinare (fase 5.2/5.3 del piano)

> Sblocca: backend `native` in hal.py - ma vedi la raccomandazione in
> scheda-acquisizione.md sezione 8: la strada breve non e' il driver nativo.

## Sensore

- Tipo (CCD lineare mono/tri-lineare): ..........
- Pixel per riga: ..........        → config.sensor_pixels
- Profondità ADC (bit): ..........
- Risoluzione ottica effettiva (dpi): ..........  → config.optical_dpi

## Illuminazione

- Sorgenti separate R/G/B/IR oppure lampada bianca + filtri: ..........
- Lunghezza d'onda del canale IR: ..........
- Tempo di stabilizzazione: ..........

> Sblocca: tempi di attesa corretti fra i passaggi

## Trasporto pellicola

- Tipo di motore: ..........
- Passo minimo: 0,01 mm (dichiarato)   → config.transport_step_mm
- Backlash misurato: ..........
- Comandi di posizionamento: ..........

## Autofocus

- Passo minimo attuatore: ..........   → config.focus_step_mm
- Ripetibilità: ..........
- Movimento puramente assiale? ..........

> Sblocca: la decisione sulla super-risoluzione.
> Se il passo è ben sotto i 10 micron E il movimento non è solo assiale,
> la super-risoluzione vera diventa praticabile.

## Calibrazione

- Sequenza prevista dal manuale: ..........
- Target di riferimento disponibili: ..........

## Formati pellicola supportati dal carrello

- ..........
- Pitch reale misurato (per formato): ..........

> I valori in config.FORMATS sono nominali e vanno corretti qui.

## PC di controllo

- Sistema: Windows 7 x86 (32 bit)
- RAM installata: 4 GB
- Python installato: ..........  (deve essere 3.8.x, ultimo supportato)
- Indirizzo IP in rete locale: ..........
- Porta interfaccia web: 8080

> Verificare con `python run.py --controllo`.
> Il limite di ~1,8 GB per processo non dipende dalla RAM: è architetturale.

## Rete

- L'interfaccia web va usata solo su rete locale fidata.
- Con `--rete` viene generato un token: annotarlo qui se serve
  ai colleghi: ..........

## Sensori di presenza pellicola

La macchina ha sensori fisici in ingresso e in uscita del percorso di
trascinamento, che governano il ciclo automatico: la scansione parte quando il
sensore d'ingresso rileva la pellicola e la coda della striscia si riconosce
quando lo stesso sensore torna libero.

- Tipo di sensore (ottico a forcella, riflessione, microswitch): ..........
- Logica di lettura: **NON booleana.** La macchina legge una **tensione** e la
  confronta con una finestra di validita'. Dal software originale
  (`APP ORIGINALE\Scanner\Bin\Ggc.ini`):

      [DX Sensor Limits]      ok 3.20 - 4.10   tolleranza -0.20 / +0.05
      [Frame Sensor Limits]   ok 0.90 - 3.70   tolleranza -0.10 / +0.10

  Il programma di servizio (`Sensor Test`, manuale cap. 7.2) mostra questi valori
  come verde / giallo / rosso. Un sensore sporco non da' un falso booleano: da'
  un valore fuori finestra, che e' un'informazione migliore e va conservata.
  Da annotare qui, misurati sulla macchina: sensore libero .......... V,
  sensore coperto .......... V
- Come si leggono dalla scheda: **non dalla scheda PCI.** Passano dal canale
  seriale **COM5** verso `GateControl.exe`. Protocollo da osservare (fase 4)

### Distanze del percorso — le due misure che servono davvero

- **Sensore d'ingresso -> finestra di scansione: .......... mm**
  → `config.sensore_ingresso_finestra_mm`

  È il dato più importante di questa sezione. Quando il sensore d'ingresso si
  libera, la pellicola NON è finita: la coda è al sensore, ma davanti alla
  finestra c'è ancora pellicola, e quei fotogrammi vanno letti.
  Senza questo valore si perdono gli ultimi fotogrammi di ogni striscia.
  Il software avvisa se manca, ma non può indovinarlo.

- Finestra di scansione -> sensore di uscita: .......... mm
  → `config.finestra_sensore_uscita_mm`

  Serve a sapere quando l'espulsione è davvero completa.

- Corsa totale del percorso, ingresso -> uscita: .......... mm
  → utile per tarare `config.eject_max_mm` (ora 400 mm)

### Espulsione

- Esiste un comando di espulsione dedicato? ..........
  Se sì: sintassi del comando ..........
  Se no: si espelle avanzando a passi finché il sensore di uscita si libera
  (già implementato, `service.espelli()`).
