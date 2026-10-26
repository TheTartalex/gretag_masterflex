# CLAUDE.md — riferimento per il codice di filmscan

Contesto permanente per chi (persona o agente) lavora sul codice di questa
cartella. Leggere prima di modificare qualsiasi cosa. Il contesto generale
del progetto (macchine, driver, stato) sta nel [CLAUDE.md](../CLAUDE.md) alla
radice del repository.

> Scritto a settembre 2026. **Aggiornato a ottobre 2026** su due punti:
> numpy va tenuto alla **1.22.4**, non alla 1.24.4 (vedi *Trappole*), e la
> scheda di acquisizione ora è documentata del tutto in
> [scheda-iaq.md](../documentazione/hardware/scheda-iaq.md).

---

## Piattaforma di destinazione — vincolo primario

**Windows 7 x86, 4 GB di RAM.** Da tenere presente prima di ogni modifica:

- **Python 3.8** è l'ultima versione che gira su Windows 7. Niente sintassi più
  recente: no `dict | dict`, no `list[str]` senza `from __future__ import
  annotations`, no `match`.
- Un processo a 32 bit ha **~1,8 GB** di spazio indirizzabile, a prescindere dai
  4 GB installati. Aggiungere RAM non sposta il limite.
- Lo spazio si **frammenta**: un'allocazione contigua da 400 MB può fallire con
  900 MB nominalmente liberi. Meglio molte allocazioni piccole che una grande.
- Dipendenze bloccate a versioni con wheel win32 per il 3.8 (`requirements.txt`).
  Ogni dipendenza nuova va verificata su quel target prima di aggiungerla.

`python run.py --controllo` stampa il verdetto sulla macchina reale.

## Cos'è questo progetto

Software di acquisizione per lo scanner a pellicola di un **Gretag Masterflex
D1008** del 1995 — un minilab digitale completo, di cui a noi interessa solo il
modulo scanner. Sostituisce il software originale (MasterPlus), che faceva anche
densitometro, stampa e gestione ordini, e che salvava a **600×400 pixel** perché
era il limite del PC dell'epoca, non del sensore.

**L'obiettivo non è replicare il software originale. È fare una cosa sola, bene:
tirare fuori dal sensore tutto quello che il sensore vede.**

## L'hardware

| Elemento | Stato |
|---|---|
| Scanner Masterflex D1008 | presente, da rimettere in funzione |
| PC originale, cablaggi, manuale cartaceo, software | presenti |
| Driver della scheda | originale sulla produzione; [EKframePnP](../driver/EKframePnP/) sulla riserva |
| Scheda di acquisizione | protocollo ricostruito: [scheda-iaq.md](../documentazione/hardware/scheda-iaq.md) |

Capacità della macchina, note dal manuale e dall'osservazione:

- Letture separate in sequenza: **R, G, B** con spostamento del sensore
- Quarta lettura **infrarossa** per graffi e sporco
- **Lente mobile** per autofocus
- **Trascinamento pellicola** per scansione automatica dell'intero negativo
- Passo del motore di trascinamento: **0,01 mm** (10 micron)

## I due dati che mancano

*Aggiornato il 3 settembre 2026 dopo la lettura del software originale. Il
secondo punto e' in gran parte risolto; il dettaglio con le fonti sta in
[scheda-acquisizione.md](../documentazione/hardware/scheda-acquisizione.md), l'ordine dei lavori in
[piano-lavori.md](../documentazione/piano-lavori.md).*

1. **`focus_step_mm`** - passo minimo dell'attuatore di autofocus. Decide se la
   super-risoluzione e' praticabile. In `config.py` e' a `None`.
   Va verificato anche se il movimento sia puramente assiale: in quel caso
   cambierebbe il fuoco, non la posizione nel piano, e non servirebbe allo scopo.
   Il manuale di servizio non lo dichiara. Si ricava muovendo il fuoco a passi
   noti con `Actor Test` (cap. 7.2) o leggendo `focus_adv` / `nvm_focus_mag_pos`
   in `scn_dll.dll`.

2. **Mappa dei registri della scheda** - **in gran parte trovata.**
   La scheda e' una **PCI** Eastman Kodak "IAQ Adapter"
   (`VEN_11B2&DEV_0008&SUBSYS_908010B5`), costruita attorno a un
   **PLX PCI 9080**: quindi la meta' PCI - motore DMA, mailbox, doorbell - e'
   un componente standard con data book pubblico, non logica proprietaria.
   Risorse osservate: memoria `0xDE000000` (256 B) e I/O `0xA800` (256 B) per i
   registri, memoria `0xDD800000` (2 MB) come finestra sul bus locale, IRQ 9,
   DMA bus-master a due buffer.
   Driver `EKframe.sys`, aperto come `\\.\EKFrameDev` via `DeviceIoControl`;
   sopra c'e' l'SDK `scn_dll.dll` (`DMLScanner`), che espone gia' `scan_raw` per
   canale con esposizione esplicita.
   ~~Restano ignoti i codici IOCTL e il protocollo sui mailbox verso l'EPLD
   Gretag.~~ **Ottobre 2026:** noti entrambi. Il canale comandi verso la
   testa è un controller ARCNET sulla scheda, non i mailbox. Vedi
   [scheda-iaq.md](../documentazione/hardware/scheda-iaq.md).

### La terza cosa, che nessuno dei due punti diceva

La meccanica **non passa dalla scheda PCI.** Trasporto, sensori, gate, fuoco e
lettura DX/MOF viaggiano su un canale del tutto separato: **seriale COM5**, verso
`GateControl.exe`. Due canali, due protocolli, due livelli di difficolta'.

Conseguenza sul piano di lavoro: il pezzo difficile (l'immagine) e' anche l'unico
che possiamo lasciare al software originale, mentre il resto lo comandiamo noi.
Il `BridgeBackend` gia' presente in `hal.py` non e' un ripiego: alla luce di
questi dati e' la scelta tecnicamente giusta. Vedi
[scheda-acquisizione.md](../documentazione/hardware/scheda-acquisizione.md) §8.

## Il conto sulla super-risoluzione

Step del trasporto 10 micron. A 2000 dpi un pixel è ~12,7 micron; a 4000 dpi
~6,4 micron. **Il trasporto non offre controllo sub-pixel affidabile**, e va
aggiunto il backlash di una meccanica del 1995.

Quindi, in ordine di solidità:

- **Realistico:** più profondità di bit e gamma dinamica combinando esposizioni
  diverse; meno rumore mediando letture ripetute. Già implementato.
- **Da verificare:** super-risoluzione vera via micro-spostamenti del fuoco.
  Dipende dal dato mancante.
- **Da non confondere:** l'upscaling algoritmico non aggiunge informazione.
  Legittimo come passo finale opzionale, mai come sostituto.

Il vero salto rispetto al 1995 non è la super-risoluzione: è salvare la piena
risoluzione nativa che la macchina già produce.

---

## Architettura — le regole che non vanno rotte

### 1. L'hardware sta solo in `hal.py`

Nessun altro modulo sa che esiste una scheda. Il giorno in cui arriva la
documentazione si scrive un backend nuovo e non si tocca una riga altrove.
Se ti trovi a importare `hal` fuori da `acquire.py` e `pipeline.py`, fermati.

### 2. Acquisizione ed elaborazione sono separate

Da `acquire.py` esce il **grezzo**: un piano per canale e per esposizione, più i
frame di calibrazione e i metadati. Nessuna conversione, nessuna compressione,
nessun ritaglio.

Motivo: finché ci sono i raw, l'intero archivio si rielabora quando la pipeline
migliora, **senza rimettere la pellicola nella macchina**. Non barattare questa
proprietà per comodità o spazio disco.

### 3. L'ordine della pipeline non è arbitrario

```
1. calibrazione (dark, flat)      — in acquisizione
2. fusione esposizioni multiple
3. allineamento canali            ← PRIMA di tutto il resto
4. maschera difetti da IR         ← sul negativo, dove i difetti sono netti
5. inversione in spazio densità
6. bilanciamento e curve
7. inpainting difetti             ← dopo l'inversione, sui valori finali
8. raddrizzamento, ritaglio, export
```

Il 3 va per primo perché tutto il resto presuppone canali sovrapposti.
Il 4 e il 7 sono separati apposta: la maschera si costruisce meglio prima
dell'inversione, la correzione si applica meglio dopo.

---

## Trappole già incontrate

Sono errori reali, già commessi e corretti in questo codice. Non reintrodurli.


### Alzare una versione in requirements.txt rompe l'installazione, e l'errore mente

Su cp38-win32 le wheel gia' compilate finiscono presto. Oltre l'ultima
disponibile, pip scarica i sorgenti e prova a costruirli: con scipy si finisce
contro

    mesonpy requires at least Python 3.10

che sembra un problema di versione di Python. Non lo e': e' il sistema di
compilazione di scipy. Su Windows 7 il 3.8 e' l'ultimo possibile, quindi la
strada dell'aggiornamento non esiste, e comunque servirebbe un compilatore C e
Fortran che su quella macchina non c'e'.

Regola: **installare sempre con `--only-binary=:all:`**, cosi' pip dice subito
"la wheel non esiste" invece di tentare di costruirla. Gli script in `windows/`
lo fanno gia'.

Versioni da usare: **numpy 1.22.4** · scipy 1.9.1 (la 1.9.2 smette di
pubblicare wheel a 32 bit) · Pillow 10.4.0 · tifffile 2023.7.10 (pura Python).

### Una wheel che esiste non e' detto che giri: numpy e AVX

*Aggiunto a ottobre 2026.* A settembre la scelta era numpy 1.24.4, l'ultima
con wheel cp38-win32. Sulla macchina di riserva, che ha una CPU **senza AVX**,
Python moriva con `c000001d` in `_multiarray_umath.cp38-win32.pyd`
(istruzione `vmovss`). numpy 1.24.x per win32 usa AVX senza controllare la
CPU: 43 funzioni nella 1.24.x, 14 nella 1.23.5, nessuna dalla 1.22.4 in giu'.
scipy 1.9.1 e Pillow 10.4.0 sono puliti.

Regole: **numpy non oltre la 1.22.4**; niente API di numpy piu' recenti della
1.22; prima di alzare una libreria compilata va controllato il binario, non
solo che la wheel esista. Vedi
[decisione 0003](../documentazione/decisioni/0003-numpy-1.22.4-cpu-senza-avx.md).

Prima di alzare una di queste versioni, verificare che la wheel esista davvero:

    pip download <pacchetto>==<versione> -d . --only-binary=:all: \
        --platform win32 --python-version 3.8 --implementation cp --abi cp38

Funziona da qualunque PC, anche a 64 bit: e' cosi' che si e' riempita la
cartella `pacchetti/`.

### La raffinatura parabolica sulla correlazione di fase non funziona

Il picco della correlazione di fase è quasi una delta: i campioni adiacenti
valgono circa zero e la parabola restituisce **sistematicamente 0,0**.
Il risultato è un allineamento apparentemente perfetto che in realtà non esiste.

Si usa la **DFT sovracampionata** (`_refine_upsampled`, metodo di
Guizar-Sicairos). Errore misurato su spostamenti noti: **0,05 px**.

Attenzione al segno: l'esponente deve essere **positivo**, coerente con la
`ifft2` usata per la stima grossolana. Con il coniugato lo spostamento esce
invertito.

### I canali non differiscono solo per una traslazione

Hanno base e gamma diversi, perché i tre strati di coloranti rispondono
diversamente. Correlarli grezzi dà spostamenti inventati.
Vanno prima portati in densità (log), passati in passa-alto e normalizzati in
varianza — è quel che fa `_prep()`.

### Il canale IR non si può allineare per correlazione

È quasi privo di contenuto d'immagine — ed è esattamente per questo che serve a
trovare i difetti. La correlazione non si aggancia.
Si misura l'**affidabilità** del picco e, sotto soglia, si applica lo spostamento
medio dei canali visibili: il disallineamento nasce dalla meccanica, che è la
stessa per tutti i passaggi.

### Riavvolgere di una quantità fissa fa scivolare i canali

Acquisire consuma pellicola, e passaggi diversi ne consumano quantità
leggermente diverse. Ci si riposiziona **in assoluto** rispetto alla posizione
misurata (`rewind()` in `acquire.py`), mai con un `advance(-frame_w)`.

### PIL non scrive TIFF né PNG RGB a 16 bit

`Image.fromarray` fallisce con `uint16` a 3 canali. Si usa `tifffile`
(o `imageio` come ripiego). Vale per `tiff16` e `png`.

### Le FFT a piena risoluzione fanno esplodere il processo a 32 bit

A 4000 dpi un fotogramma è 5669×3779. La sua FFT complessa occupa 343 MB in
complex64 — **686 MB in complex128, che è il default di numpy** — e la
correlazione di fase ne richiede più di una alla volta. Su Windows 7 x86 questo
basta a far fallire tutto, e falliva già a 2000 dpi.

Due correzioni, entrambe necessarie:

- **l'allineamento si stima su un ritaglio centrale** (`_center_tile`, 1024 px).
  Uno spostamento rigido è lo stesso ovunque nell'immagine: stimarlo su un
  riquadro non perde nulla, è semplicemente il modo corretto di farlo.
- **la traslazione si applica con `ndimage.shift`**, non nel dominio di Fourier.
  Spline cubica: qualità indistinguibile per spostamenti di pochi pixel, senza
  allocare due array complessi grandi quanto l'immagine.

Misurato dopo: precisione 0,10 px, picco di memoria 113 MB invece di gigabyte.

Regola generale: **prima di introdurre una FFT su un array a piena risoluzione,
fermarsi.** Quasi sempre esiste un modo che lavora su un ritaglio o per tile.

### La specchiatura non si deduce dal contenuto

Un'immagine specchiata è un'immagine perfettamente plausibile. Nessuna proprietà
statistica dice da che parte sta il mondo: un volto girato a destra è verosimile
quanto lo stesso volto girato a sinistra. Chi promette di rilevarlo dal
contenuto sta indovinando.

Il segnale vero sono le **scritte sul bordo pellicola** (numeri di fotogramma,
codice DX): se si leggono al rovescio, la pellicola è girata. È un dato
oggettivo. Ed è una proprietà del **rullo**, non del fotogramma: la pellicola si
carica una volta sola, quindi decidere fotogramma per fotogramma
moltiplicherebbe soltanto le occasioni di sbagliare.

Non reintrodurre euristiche di specchiatura basate sul contenuto.

### Un simulatore troppo docile mente

La prima scena sintetica era fatta di sinusoidi lisce: dopo il passa-alto non
restava nulla su cui agganciarsi, e l'allineamento sembrava fallire quando era
il banco di prova a essere mal costruito. La seconda versione, con pattern
quasi-periodici, creava picchi di correlazione ambigui.

Ora si usa un **campo casuale con spettro a legge di potenza**: un solo picco,
struttura a tutte le scale. Il mock riproduce anche stato lampada, presenza
pellicola, dark offset, vignettatura, graffi e disallineamento sub-pixel noto.
Se modifichi il mock, verifica che `selftest.py` continui a **misurare** qualcosa,
non a confermarti che va tutto bene.

---

## Vincoli di dominio

### L'esposizione non è uguale per tutti i canali

La maschera arancione assorbe molto il blu. Con esposizione uniforme il canale B
usa una frazione ridotta della scala dell'ADC e arriva rumoroso, e dopo
l'inversione quel rumore si vede. `measure_channel_exposure` fa una pre-scansione
e calcola il fattore per canale, puntando all'**80%** della scala: il margine
serve perché il fotogramma vero può essere più chiaro del punto campionato, e
saturare perde informazione in modo irreversibile.

Si misura **una volta per rullo**. Rimisurarla a ogni fotogramma farebbe
oscillare il colore lungo la striscia.

### Il sensore d'ingresso libero NON significa pellicola finita

Errore facile e costoso. Quando il sensore d'ingresso si libera, la coda della
striscia è **a quel sensore**, non alla finestra di scansione: fra i due c'è
ancora pellicola con fotogrammi da leggere. Fermarsi lì significa perdere gli
ultimi scatti di ogni striscia, in silenzio.

La distanza sensore→finestra è `config.sensore_ingresso_finestra_mm`, ed è
**ancora da misurare** (vedi [note-macchina.md](../documentazione/hardware/note-macchina.md)). Finché è `None` il codice lo
segnala fra gli errori del lavoro. Non sostituire quell'avviso con un valore
inventato di default: un numero plausibile ma sbagliato è peggio di un avviso,
perché nessuno lo verificherà più.

### Sensori prima, ottica dopo

I sensori di bordo misurano un fatto fisico. `film_present()` (livello e
varianza della trasmittanza) è un ripiego per i backend che non li espongono.
Ordine di preferenza mai invertito: se `read_sensors().disponibili` è vero, si
usano quelli.

### Espulsione: mai una corsa fissa

Con strisce di lunghezza variabile non si sa quanta pellicola resti nel
percorso. Si usa il comando dedicato se esiste, altrimenti si avanza a passi
finché il sensore d'uscita si libera, con un tetto (`eject_max_mm`). Superato
il tetto è inceppamento: ci si ferma e lo si dichiara, invece di continuare a
tirare.

### Le strisce hanno lunghezza variabile

Niente telai, niente diapositive intelaiate: solo pellicola sfusa di lunghezza
non nota in anticipo. Il conteggio fotogrammi è un massimo. `film_present`
controlla livello **e** varianza: il solo livello confonderebbe un fotogramma
sovraesposto con l'assenza di pellicola.

### Bianco e nero: un canale, non tre

Le B/N a base argento non portano informazione di colore. Il preset `bn` usa
`color_channels=["G"]`: tre passaggi colore sarebbero lavoro triplicato per lo
stesso risultato. Il codice a valle gestisce il caso a canale singolo
(`_stack_rgb` lo replica sui tre) — non rompere quel percorso assumendo che
R, G e B esistano sempre.

### Diapositive: campo esplicito, non il nome del profilo

`FilmProfile.positive` è un booleano. La versione precedente deduceva il
comportamento da `profile.name.startswith("Diapositiva")`, che si rompe al primo
profilo personalizzato chiamato "Velvia".

Le diapositive non sono "negativi senza inversione": nessuna maschera arancione
da normalizzare, gamma di densità molto più ampia, ombre profonde dominate dal
rumore. Si normalizza sul percentile 99,5 (il bianco), non sul minimo.

### Inversione del negativo

Non è `1 - x`. Un negativo a colori è fatto di tre strati di coloranti
sovrapposti su base arancione, con risposte spettrali che si sovrappongono.
Si lavora in **spazio densità**: campiona la base non esposta → normalizza →
matrice 3×3 pre-log → logaritmo → matrice 3×3 post-log → curva di tono.
Riferimento: modulo `negadoctor` di darktable, derivato dal sistema Cineon.

Nostro vantaggio: con illuminazione a bande separate R/G/B il crosstalk è minore
che fotografando il negativo con una reflex, e le matrici sono più semplici da
calibrare. Partono dall'identità: **vanno calibrate con un target fotografato su
pellicola**, a occhio non si ottiene nulla di ripetibile.

### Pulizia infrarossa

I coloranti a colori sono trasparenti all'IR, polvere e graffi no.

**Le pellicole B/N a base argento sono opache all'infrarosso**: l'IR va tenuto a
zero, altrimenti la maschera copre tutta l'immagine. Il preset `bn` lo fa già e
il programma avvisa oltre l'8% di copertura. Le B/N cromogeniche (C-41) vanno
invece trattate come le negative a colori.

Non usare la divisione diretta per il canale IR: variazioni di contrasto, fuoco
e apertura lasciano residui. Si costruisce una **maschera binaria** e si fa
inpainting sui pixel mascherati.

L'inpainting funziona bene su aree omogenee e male su dettagli strutturati —
**sui volti produce artefatti riconoscibili**. Da qui il controllo di intensità
e l'export opzionale della maschera come file separato.

### Ritaglio automatico

Bordi striscia e stacchi interfotogramma sono **geometria** e vanno risolti con
precisione: guidano l'avanzamento automatico.
Il ritaglio sul soggetto è una **scelta estetica** e resta sempre una proposta da
confermare: un ritaglio automatico sbagliato butta via informazione che non si
recupera.

---

## Miglioramento immagine — cosa è misura e cosa è stima

Distinzione da tenere ferma:

- **canale IR** → misura un fatto fisico (opacità all'infrarosso). Affidabile.
- **rilevamento graffi senza IR** → euristica geometrica. Non sa distinguere un
  graffio da un palo o un cavo verticale nella scena. Parte basso, la maschera
  va guardata.
- **rotazione automatica** → euristica sul contenuto. Buona sui paesaggi, debole
  su interni e primi piani. Ha una soglia di confidenza, e sotto quella soglia
  **non tocca niente** e dice perché.

Quando una stima non è affidabile il codice lo dichiara nei `warnings`. Non
sostituire un avviso con un'azione silenziosa: è il contrario di quello che
serve a chi scansiona duecento fotogrammi.

## Interfacce e stato condiviso

`service.py` contiene **tutta** la logica di scansione: connessione, calibrazione,
anteprima, scansione rullo, stato del lavoro. Le interfacce sono solo traduttori.

- `webui.py` traduce fra HTTP e `ScanService`. Nessuna logica dentro.
- `ui.py` (tkinter) **ha ancora una propria copia della logica**. È debito noto,
  non una scelta: va portato su `ScanService` come `webui.py`. Finché resta così,
  una funzione nuova aggiunta solo in una delle due interfacce non esiste
  nell'altra.

Se serve una funzione nuova, va in `service.py`.

Solo `http.server` e `json` dalla libreria standard per il web. Nessun Flask: su
Windows 7 a 32 bit ogni dipendenza in più è una wheel che potrebbe non esistere.

Un solo lavoro alla volta, protetto da lock: due scansioni in parallelo
significherebbero due comandi di movimento contemporanei sullo stesso motore,
cioè pellicola inceppata.

### Flusso corretto

    cliente → ordine → rullo → scansione

La scansione parte da un **rullo registrato in archivio**, non da parametri
sciolti. Il formato, il preset, il profilo e la specchiatura sono proprietà del
rullo: così i file finiscono nella cartella giusta, ogni fotogramma viene
registrato con maschera e rotazione, e la specchiatura non può cambiare a metà
rullo.

Default su 127.0.0.1. Con `--rete` si espone sulla LAN e viene generato un
token. **Non deve finire su internet**: niente cifratura, niente utenti, e
comanda un motore che tira pellicola.

## Ordini

`cliente → ordine → rullo → fotogramma`, su SQLite. Aperto con `timeout=15` e
`journal_mode=WAL`: l'interfaccia web e quella locale possono scrivere insieme,
e senza quelle due impostazioni ci si prende un `database is locked` nel mezzo
di una scansione.

Il nome cliente va ripulito prima di usarlo come nome di cartella. Non è
pignoleria: i caratteri `\ / : * ? " < > |` fanno fallire il salvataggio a metà
rullo su Windows.

## Stile del codice

- Commenti in italiano, che spiegano **perché**, non cosa
- Solo numpy, scipy, Pillow, tifffile — niente dipendenze pesanti
- scipy è opzionale ovunque: c'è sempre un ripiego (`HAS_SCIPY`)
- Le operazioni lunghe accettano un callback `progress(msg, frac)`
- Gli errori su un fotogramma non devono far perdere l'intero rullo
- Quando una stima non è affidabile, **dirlo** (`warnings`), non fingere che vada

## Come si verifica una modifica

```bash
python selftest.py
```

Deve arrivare in fondo e mostrare spostamenti canali **non nulli** (il mock ne
inserisce di noti) e una maschera IR di pochi punti percentuali.
Se gli spostamenti sono tutti a zero, l'allineamento è rotto — non perfetto.

---

## Ordine di lavoro consigliato

1. Immagine bit a bit del disco del PC originale, **prima di ogni altra cosa**
2. Esplorare il disco F: e gli altri volumi: la documentazione di terze parti
   segnala che i manuali potevano stare lì
3. Scansionare il manuale cartaceo, priorità alla scheda di acquisizione
4. Identificare fisicamente la scheda: fotografie di chip e serigrafie
5. Verificare il passo dell'attuatore di autofocus
6. Primo traguardo software: **un passaggio, un canale, salvato grezzo**
7. Solo dopo: pipeline colore, IR, interfaccia

## Contesto storico utile

Gretag Imaging è fallita nel dicembre 2002; gli asset minilab sono passati a
San Marco Imaging (Italia), quelli dei laboratori all'ingrosso a KIS Photo-Me.
Non esiste più un titolare unico degli archivi tecnici — è il motivo per cui la
documentazione online è praticamente assente.

Canali di ricerca ancora aperti: il forum minilabhelp.com (sezione Gretag/San
Marco), San Marco Imaging direttamente, tecnici minilab in pensione.
Dettagli nel [dossier di ricerca](../documentazione/ricerca/dossier-ricerca-2026-09.pdf).

---

## Tema chiaro e tema scuro (webui.py)

Le tre pagine (scansione, archivio, PIN) non hanno una propria tavolozza: nel
loro `<style>` c'e' il segnaposto `/*TEMA*/`, e nell'intestazione `/*ICONA*/`.
La funzione `_vesti()`, subito dopo le tre costanti, ci infila la tavolozza
(`TEMA`), il CSS dell'iconcina, lo script (`SCRIPT_TEMA`) e il tasto
(`ICONA_TEMA`). Regola pratica: **un colore nuovo si scrive in `TEMA`, mai
dentro una pagina**. Un colore scritto a mano in una pagina sola e' come si
finisce, dopo il terzo ritocco, con un'interfaccia scucita.

Lo script del tema va nel `<head>`, prima del corpo. Se lo si sposta in fondo
alla pagina, all'apertura si vede per un istante il tema scuro che poi sbianca:
su un tablet al banco quel lampo e' l'unica cosa che l'occhio nota.

La scelta si ricorda nel `localStorage` del browser, non nella
configurazione del server: il monitor della macchina sta in penombra e vuole
lo scuro, il tablet in mano sotto le luci del banco vuole il chiaro. E' una
proprieta' dello schermo, non dell'operatore.

Due tele si disegnano a mano e **non ereditano niente dal foglio di stile**:
l'istogramma e l'editor delle curve. Percio':

- i colori se li vanno a prendere con `tinta('--nome')`;
- `window.dopoTema()` le ridisegna a ogni cambio, altrimenti restano coi
  colori di prima e si vede;
- l'istogramma cambia proprio modo di comporre: al buio somma i tre canali
  (`lighter`, sovrapposto = bianco), alla luce li moltiplica (`multiply`,
  sovrapposto = nero). E' lo stesso istogramma letto nel verso giusto per il
  fondo su cui sta; lasciare `lighter` sul bianco lo farebbe sparire.

Il contorno dell'immagine resta **grigio neutro in tutti e due i temi**
(`--neutro`), non bianco: un bianco pieno attorno a una fotografia la fa
sembrare piu' scura e piu' contrastata di quello che e', e quell'immagine si
guarda proprio per giudicare colore e densita'.

---

## Lo STOP

Non e' un freno di emergenza, e il codice non finge che lo sia. La macchina
tira pellicola dentro un percorso ottico: strapparla a meta' passaggio la
lascia in una posizione che nessuno conosce, e sulla meccanica del 1995 questo
si paga.

Quello che fa: chiede di fermarsi al primo punto in cui fermarsi non rompe
niente. I punti sono dentro gli anelli, non solo fra un fotogramma e l'altro -
fra un passaggio di canale e il successivo, fra una ripetizione e l'altra
della calibrazione - percio' l'attesa massima e' qualche secondo invece di un
rullo intero. Prima il controllo stava solo in cima al ciclo dei fotogrammi:
premere STOP a meta' di un fotogramma a quattro canali voleva dire aspettare
il fotogramma intero, e sembrava che il tasto non funzionasse.

Il meccanismo e' `acquire.Annullato`, sollevata da `_controlla(annulla)` dove
fermarsi e' sicuro. E' un'eccezione e non un flag perche' l'acquisizione e'
fatta di anelli annidati - canali, esposizioni, passaggi - e uscirne coi flag
vorrebbe dire tre controlli per anello e uno dimenticato. Nel ciclo del rullo
va intercettata PRIMA del `except Exception` che salva il rullo quando un
fotogramma fallisce: li' verrebbe scambiata per un fotogramma perso e la
scansione ripartirebbe col successivo.

Dopo l'arresto: lampade spente e pellicola dov'e'. Non si espelle da sola -
se si preme STOP di solito e' perche' qualcosa non va nel percorso, e tirare
ancora peggiora. Il riposo si esegue nel thread del lavoro, mai in quello
della richiesta HTTP: due thread che parlano insieme alla stessa macchina e'
il modo migliore per lasciare un comando a meta'.

Nell'interfaccia il tasto diventa "ARRESTO..." finche' il lavoro non e' finito
davvero. Un tasto che torna subito disponibile mentre la macchina sta ancora
girando e' peggio di uno lento: fa premere di nuovo.

E fermato non e' rotto. `Annullato` non finisce fra gli errori del lavoro e il
diario scrive che il rullo e' rimasto a meta' per scelta di chi guardava.
