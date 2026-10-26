# Gretag Masterflex D1008 — pacchetto di progetto

> **Documento storico.** Era il `LEGGIMI.md` del pacchetto filmscan
> (settembre 2026) e resta per i dati di fondo: preset, memoria, i tre dati
> mancanti, il ponte. Per installare e usare la macchina oggi:
>
> - l'installer unico `INSTALLA.bat`, che fa sia l'installazione nuova sia
>   l'aggiornamento ([strumenti/](../../strumenti/));
> - la [guida per l'operatore](guida-windows.md).
>
> Sono superati il "Ciclo automatico" a sensori (ora il carrello porta i
> fotogrammi ed espelle la striscia da solo) e i numeri delle voci del menu.
> Sulle CPU senza AVX numpy resta alla 1.22.4
> ([decisione 0003](../decisioni/0003-numpy-1.22.4-cpu-senza-avx.md)), e
> sulla macchina di riserva il driver è [EKframePnP](../../driver/EKframePnP/).

## Installazione su Windows 7 x86

Tutto passa da `software\MASTERFLEX.bat` (la prima volta: tasto destro,
*Esegui come amministratore*):

1. voce **7**, *Installa / ripara*: scarica e installa Python 3.8.10 a 32 bit
   se manca, le librerie alle versioni giuste, le cartelle e il firewall;
2. voce **5**: collaudo di tutta la catena su uno scanner simulato, senza la
   macchina.

Senza internet: voce **11** su un altro PC, poi voce 7 sulla macchina.
Dettagli nella [guida per l'operatore](guida-windows.md).

## Uso quotidiano

Voce **1** (macchina vera), **2** (motori bloccati) o **3** (simulazione).
Si comanda dal tablet con l'indirizzo che compare all'avvio.

Flusso: crea ordine cliente → aggiungi rullo → scansiona.
**Non esporre l'interfaccia web su internet.**

## Ciclo automatico

1. sensore d'ingresso coperto → parte la scansione
2. sensore d'ingresso libero → la coda è arrivata al sensore
3. si continua per la distanza sensore→finestra
4. striscia finita → espulsione dal retro
5. sensore d'uscita libero → espulsione completa

Il passo 3 dipende da **`sensore_ingresso_finestra_mm`**, ancora da misurare.
Finché manca, il software lo segnala fra gli errori invece di perdere in
silenzio gli ultimi fotogrammi di ogni striscia.

## Modalità pellicola

| Preset | Canali | Note |
|---|---|---|
| `provino` | R,G,B | Anteprima rapida |
| `standard` | R,G,B,IR | Uso quotidiano |
| `archivio` | R,G,B,IR | Doppia esposizione, letture mediate |
| `bn` | **G soltanto** | Tre letture mediate, IR spento |
| `diapositiva` | R,G,B,IR | Doppia esposizione per le ombre dense |

L'esposizione è misurata per canale, una volta per rullo: il blu chiede quasi
quattro volte il rosso, perché la maschera arancione lo assorbe.

## Memoria

Su Windows 7 a 32 bit ogni processo dispone di **circa 1,8 GB**, a prescindere
dai 4 GB installati: limite architetturale, non di RAM.

Da settembre 2026 pero' questo non e' piu' un problema. La risoluzione vera
della macchina e' **2100x1400** (letta dentro l'SDK originale, non stimata): un
fotogramma a quattro canali e 16 bit occupa 24 MB. Il vincolo di memoria che ci
preoccupava valeva per i 4000 dpi che avevamo ipotizzato, e a quella
risoluzione la macchina non arriva.


## I tre dati che sbloccano il resto

*Rivisti il 3 settembre 2026, dopo aver letto il software originale e il manuale
di servizio in `APP ORIGINALE\`.*

1. **Distanza sensore d'ingresso -> finestra di scansione** -> ultimi fotogrammi
   di ogni striscia.
   **Sappiamo dove sta:** non e' una quota da misurare, e' una taratura per
   macchina in passi motore, salvata nel Parameter Flash. Si legge dal programma
   di servizio (manuale cap. 7.3.5, *Length calibration*).
2. **Passo dell'attuatore di autofocus** -> super-risoluzione.
   Ancora ignoto. Il manuale non lo dichiara: si ricava muovendo il fuoco a passi
   noti con `Actor Test`.
3. **Mappa dei registri della scheda** -> driver nativo.
   **In gran parte trovata.** La scheda e' PCI, Kodak "IAQ Adapter", costruita su
   un **PLX PCI 9080** con data book pubblico; indirizzi, IRQ e modo di
   trasferimento sono documentati in [scheda-acquisizione.md](../hardware/scheda-acquisizione.md)
   e, aggiornati a ottobre, in [scheda-iaq.md](../hardware/scheda-iaq.md).
   Restano ignoti solo i codici IOCTL e il protocollo verso l'EPLD Gretag.

Ed e' emerso un quarto fatto, che nessuno dei tre diceva: **la meccanica non
passa dalla scheda PCI.** Trasporto, sensori, gate, fuoco e lettura DX viaggiano
su **seriale COM5**. Due canali separati, difficolta' molto diverse.

## Il ponte con la macchina originale

Della macchina originale teniamo **driver e comandi**, che funzionano e che
nessuno rifara' meglio. Dell'interfaccia del 2001 — quella che salva su floppy e
butta via i pixel — non teniamo niente.

Sul PC della macchina gira `software/ponte/agente.py`, senza dipendenze, che carica
l'SDK Kodak (`scn_dll.dll`) e obbedisce ai comandi lasciati in una cartella
condivisa. Interfaccia, ordini, pipeline ed export girano dove ci pare.

    python agente.py --condivisa C:\ponte --simula     prova senza macchina
    python agente.py --condivisa C:\ponte              con la macchina

e dal lato nostro basta `cfg.driver = "sdk"`.

Il perche' e il come stanno in [ponte.md](ponte.md); i comandi originali,
tutti e 310, in [api-sdk.md](../software-originale/api-sdk.md).

**Risoluzione vera: 2100x1400**, circa 1570 dpi sul 135 — letta dalle costanti
dell'SDK. Non i 4000 dpi che speravamo, ma comunque dodici volte i pixel dei
600x400 che salvava l'originale. Ed e' un numero onesto.

## Da dove si comincia

L'ordine dei lavori, con stato e riferimenti al manuale, sta in
**[piano-lavori.md](../piano-lavori.md)**.

Il percorso critico in una riga: salvare il Parameter Flash -> macchina viva e
sensori misurati -> leggere dal flash i numeri mancanti -> chiamare `scan_raw`
sotto il nostro comando -> primo rullo vero.


## Prima di toccare l'hardware

Fare un'immagine bit a bit del disco del PC originale. È irripetibile.
