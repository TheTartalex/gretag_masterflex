# Masterflex D1008 — la macchina che fa tutto

> Guida per chi usa la macchina tutti i giorni. Era `LEGGIMI-WINDOWS.txt`
> del pacchetto filmscan (settembre 2026). Aggiornata il 26/10/2026: i dodici
> `.bat` della cartella `windows\` sono diventati un solo file,
> **`MASTERFLEX.bat`**, nella cartella [`software/`](../../software/).

Una macchina sola: il PC con Windows 7 a 32 bit. Dentro c'è la scheda
originale dello scanner, e sopra gira tutto: l'agente che parla alla macchina
e l'interfaccia web che si comanda dal tablet.

Windows 7 x86 non è un ripiego. Windows a 64 bit accetta solo driver firmati,
e né il driver Kodak del 2001 né il nostro EKframePnP lo sono. La versione a
32 bit non lo pretende.

## Cosa serve

1. **Python 3.8.10 a 32 bit.** Non serve installarlo a mano: la voce
   *Installa / ripara* di `MASTERFLEX.bat` lo scarica da python.org, ne
   controlla l'impronta e lo installa. Se la macchina non riesce a scaricare
   (su Windows 7 capita, per il TLS), si prepara il pacchetto su un altro PC
   (vedi *Senza internet*). I 32 bit sono obbligatori: l'SDK dello scanner è
   a 32 bit e un processo a 64 bit non può caricarlo.
2. Il software originale copiato in `C:\MasterflexD\`. Serve almeno la
   cartella `Scanner` (SDK, IniFiles, Firmware). Per i controlli del carrello,
   anche `C:\MasterflexD\CapriFirmware`.
3. Il driver della scheda installato e in esecuzione (servizio `EKFrame`):
   - sulla **macchina di produzione** resta l'originale, che si installa con
     `INSTALL.BAT` del software originale, cartella
     `InstallService\KodakFirmware\Driver`, da amministratore;
   - sulla **macchina di riserva** l'originale non funziona e si usa
     [EKframePnP](../../driver/EKframePnP/).

## Il menu

Doppio clic su **`MASTERFLEX.bat`** (la prima volta: tasto destro, *Esegui
come amministratore*, per il firewall):

```
 AVVIO      Invio  l'ultimo modo usato
                1  completo, macchina vera (chiude il programma originale)
                2  completo, motori BLOCCATI (idem)
                3  simulazione (nessuna macchina)
               14  man in the middle: programma originale + osservatore
 COLLAUDI       4  scanner: colori, intensita', trascinamento, strumenti Gretag
                5  programma (senza macchina)
                6  diagnosi: un file da inviare
               13  firmware del carrello (richiesta 1.15)
 MANUTENZIONE   7  installa / ripara
                8  aggiorna
                9  avvio automatico
               10  programma originale: stampa e densitometro
               11  pacchetto per installare senza internet
               12  disinstalla
```

## Installazione

Voce **7**. Installa Python se manca, le librerie alle versioni giuste
(numpy 1.22.4: la 1.24.4 fa cadere Python su questa CPU), crea le cartelle,
apre la porta 8080 sul firewall, controlla driver, scheda, SDK e firmware del
carrello, e dice cosa manca.

## Tutti i giorni

Si lavora in uno di due modi, e il programma ricorda l'ultimo usato:
**Invio** nel menu riparte con quello, ed è quello dell'avvio automatico.

- **Completo** (voce **1**, o **2** finché il problema delle guide non è
  risolto). Chiude tutti i programmi che partono da `C:\MasterflexD`
  (il programma originale si apre da solo e terrebbe scheda e carrello),
  poi fa partire:
  1. l'agente, che parla allo scanner con l'SDK originale;
  2. l'interfaccia web, pubblicata sulla rete locale, che **si connette da
     sola** appena l'agente ha lo scanner pronto (circa 40 secondi).

  La chiusura avviene solo prima di avviare l'agente: dopo, alcuni
  programmi di quella cartella (il GateControl del carrello) li usa l'SDK
  dell'agente. TeamViewer, AnyDesk, Chrome e Windows non stanno lì e non si
  toccano.
- **Man in the middle** (voce **14**): il programma originale resta, e
  l'agente non parte. Vedi più sotto.

**La calibrazione di ogni giorno.** Finché oggi non è stata fatta, anteprima
e scansione si rifiutano e il tasto **Calibra** è evidenziato. Calibra
prima porta a zero i motori del carrello (tamburo, guide, lente, CCD), che
senza il programma originale nessuno azzera più, poi legge la luce a
percorso vuoto. Con i motori bloccati l'azzeramento si salta, e il diario lo
dice.

I due processi sono separati di proposito: se l'SDK del 2001 si blocca,
l'interfaccia resta viva, lo segnala, e si riparte senza perdere l'ordine.

All'avvio compare l'indirizzo da aprire sul tablet, per esempio:

```
http://192.168.1.42:8080/?t=90780016
```

Le otto cifre dopo `?t=` sono il PIN. Chi apre l'indirizzo senza PIN se lo
vede chiedere, come al bancomat. Cambia a ogni accensione. Dopo cinque
tentativi sbagliati il server si chiude per mezzo minuto: otto cifre si
indovinerebbero, se nessuno contasse i tentativi.

All'avvio la pagina si apre da sola sul monitor della macchina. Sul desktop
resta una scorciatoia aggiornata all'indirizzo del momento: se si chiude la
finestra, si riapre da lì.

## Chiaro o scuro

In alto a destra, accanto allo STOP, c'è un'iconcina: sole o luna. Cambia il
tema della pagina: chiaro per il banco illuminato e per il tablet, scuro per
il monitor della macchina in penombra.

La scelta vale per quello schermo. Il tablet può stare sul chiaro e il
monitor della macchina sullo scuro nello stesso momento: è una preferenza
dello schermo, non dell'operatore.

Il contorno attorno alla fotografia resta grigio in tutti e due i temi, di
proposito. Un bianco pieno intorno a un'immagine la fa sembrare più scura e
più contrastata di quello che è, e quell'immagine si guarda proprio per
giudicare colore e densità.

## La prima volta, in quest'ordine

1. Voce **5** — collaudo del programma: ambiente ed elaborazione. Non tocca
   la macchina.
2. Voce **3** — tutto con uno scanner finto. Serve a collegare il tablet e
   prendere confidenza **senza muovere un motore**. Farlo davvero.
3. Voce **13** e voce **4 → 5** (selftest di servizio del carrello), con il
   percorso pellicola **vuoto**.
4. Voce **2** (motori bloccati) e voce **4 → 1** (collaudo colori e
   intensità): le immagini arrivano giuste?
5. Solo dopo, voce **1**. Primo comando dall'interfaccia: il backup del
   Parameter Flash. Dentro ci sono le tarature fatte una volta sola da un
   tecnico, con pellicole di prova che oggi non si comprano più.

> **Ottobre 2026:** il passo 5 è sospeso. All'avvio i motori delle guide sono
> andati a fine corsa continuando a spingere. Vedi
> [stato.md](../stato.md#problemi-aperti-in-ordine-di-priorità) e
> [carrello-capri.md](../hardware/carrello-capri.md).

## Il tasto STOP

Non è un fungo di emergenza. La macchina sta tirando pellicola dentro il
percorso ottico: fermarla di colpo la lascia in una posizione che nessuno
conosce.

Quello che fa: si ferma appena finisce il passaggio in corso, quindi qualche
secondo, non il rullo intero. Il tasto mostra `ARRESTO...` finché la macchina
non si è fermata davvero, poi torna `STOP`. Lampade spente e pellicola dov'è:
non viene espulsa, perché se si preme STOP di solito è proprio il percorso
che non va, e tirare ancora peggiora.

I fotogrammi già salvati restano. Riaprendo il rullo la scansione riprende
dal successivo.

Se la pellicola è inceppata: STOP, poi si toglie a mano. Il comando
"Espelli" serve quando la pellicola scorre.

## L'archivio degli ordini

È la seconda pagina, in alto a destra: "Archivio". Serve al banco per
ritrovare un ordine, preparare la copia per il cliente, avvisarlo che è
pronto, fare spazio. Usa lo stesso PIN della scansione.

Il registro degli ordini in Excel si riscrive da solo a ogni cambiamento,
nella cartella che si sceglie dalla pagina stessa.

I giorni di un ordine si contano dall'**ultima** volta che ci si è lavorato,
non da quando è stato aperto: un ordine di gennaio scansionato ieri ha un
giorno.

Di serie gli ordini scaduti vengono **segnalati**, non cancellati: i grezzi
sono l'unica cosa che non si rifà. Nel pannello "Registro e archivio" si può
scegliere di far cancellare in automatico i grezzi, oppure grezzi e foto. La
riga dell'ordine non sparisce mai.

Per cancellare a mano ci sono due comandi diversi:

- "Elimina i grezzi / le foto / tutto" toglie i **file** e lascia l'ordine in
  archivio;
- "Elimina questo rullo / l'ordine / il cliente" toglie le **righe**
  dall'archivio, e chiede a parte se cancellare anche i file.

Se un fotogramma mostra una piastrella grigia con una croce, il suo file non
è più sul disco: è stato cancellato da fuori, oppure il disco dell'archivio
non è collegato.

Dettagli in [archivio.md](archivio.md).

## Se il tablet non si collega

- Nove volte su dieci è il firewall. `MASTERFLEX.bat` prova ad aprire la
  porta 8080 da solo, ma la prima volta va lanciato con il tasto destro,
  *Esegui come amministratore*.
- Tablet e PC devono stare sulla stessa rete. Il wifi per gli ospiti spesso
  isola i dispositivi fra loro: usare la rete normale.
- L'indirizzo giusto è quello stampato all'avvio, non `localhost`.

## Avvio automatico

Voce **9** (oppure alla fine dell'installazione). Si sceglie il modo di
partenza: completo con motori bloccati, completo con motori attivi, man in
the middle o simulazione. All'accensione parte poi sempre **l'ultimo modo
usato**.

All'accensione il programma parte già da amministratore e **senza la
richiesta di Windows**: lo lancia un'attività dell'Utilità di
pianificazione, "Masterflex D1008", con i privilegi più elevati. La
richiesta di Windows compare una volta sola, al momento di attivarla.
L'attività parte 20 secondi dopo l'accesso, non ha limite di durata e gira
a priorità normale. Nel menu c'è anche "prova adesso", che la lancia
subito come all'accensione.

In modo completo, all'accensione, la chiusura dei programmi originali
aspetta che per 15 secondi non se ne apra nessuno nuovo (al massimo 2
minuti), perché il programma originale si apre da solo poco dopo l'accesso.
Cosa è stato chiuso si legge in `C:\MasterflexD\ponte\log\avvio-AAAAMMGG.log`.
In man in the middle si aspetta invece che il programma originale apra
`PhxScanner.exe`, fino a 10 minuti.

Il vecchio metodo (un `.bat` in Esecuzione automatica, senza diritti) viene
tolto quando si riattiva.

## Il programma originale

A ogni avvio l'interfaccia scrive una pagina di collegamento dentro l'aiuto
del programma originale (`C:\MasterflexD\PhxHelp\Data`), con l'indirizzo e
il PIN del momento. Dal programma originale si apre con il pulsante Aiuto.

Per spegnere stampa e densitometro nel programma originale: voce **10**.
Non toglie componenti e non tocca eseguibili: cambia due voci di
configurazione. Di ogni file tiene una copia intatta con il suffisso
`.prima-di-noi`. Dallo stesso menu si riaccende tutto.

> **Attenzione:** i due programmi non vanno usati insieme. Comandano la
> stessa scheda, e il driver non lo impedisce. Prima si chiude uno, poi si
> apre l'altro.

Il perché di ogni scelta sta in
[programma-originale.md](../software-originale/programma-originale.md).

### Scansionare con l'originale e salvare con filmscan (voce 14)

Serve quando il programma originale acquisisce ma non salva. Il programma
originale comanda la macchina, e un osservatore copia le immagini grezze che
riceve. Filmscan le elabora e le mette nel rullo
([decisione 0007](../decisioni/0007-osservatore-programma-originale.md)).

1. Chiudere l'agente, se è aperto: il programma originale non si
   collegherebbe allo scanner.
2. Avviare il programma originale, **senza** aprire ancora un ordine
   (all'accensione si apre da solo).
3. Voce **14**. Si aprono la finestra "Osservatore programma originale" e
   l'interfaccia, già connessa con il collegamento "programma originale".
4. Nell'interfaccia: ordine e rullo, poi **Scansiona rullo**. L'interfaccia
   resta in ascolto.
5. Nel programma originale: aprire l'ordine e scansionare. A ogni immagine,
   nell'osservatore compare "immagine N copiata" e nell'interfaccia un
   fotogramma nuovo.
6. **STOP** nell'interfaccia chiude il rullo. Ctrl+C nella finestra
   dell'osservatore lo stacca, e il programma originale continua da solo.

Le immagini copiate restano anche in `C:\MasterflexD\ponte\mitm\`, una
coppia `.raw` + `.json` per immagine.

## Tutti i file

| File | Cosa fa |
|---|---|
| `MASTERFLEX.bat` | il menu: l'unico file da lanciare |
| `manutenzione.py` | il lavoro dietro al menu (installazione, aggiornamento, diagnosi...) |
| `collaudo_scanner.py` | i collaudi dello scanner vero |
| `processi.py` | chiude i programmi che partono da `C:\MasterflexD`; `python processi.py` li elenca senza chiudere |
| `run.py` | l'interfaccia web |
| `selftest.py` | il collaudo del programma senza macchina |
| `ponte\agente.py` | l'agente che comanda lo scanner |

## Se pip dice "mesonpy requires at least Python 3.10"

Non riguarda il tuo Python e non si risolve aggiornandolo: su Windows 7 il
3.8 è l'ultimo che si può avere.

Vuol dire che per una libreria non esiste la wheel già pronta per
cp38-win32. Allora pip ha scaricato i sorgenti e prova a compilarli, e il
sistema di compilazione di scipy (meson-python) pretende Python 3.10. Anche
soddisfacendolo servirebbe un compilatore C e Fortran, che su quella
macchina non c'è.

La cura è non lasciare che pip compili:

```
pip install --only-binary=:all: -r requirements.txt
```

Così pip dice subito "questa wheel non esiste" invece di provarci.
`MASTERFLEX.bat` lo fa già.

Se il messaggio esce lo stesso, qualcuno ha alzato una versione in
`requirements.txt` oltre l'ultima disponibile come wheel a 32 bit. Le
versioni da usare sono:

| Libreria | Versione | Perché |
|---|---|---|
| numpy | **1.22.4** | la 1.24.4 ha la wheel ma usa AVX senza controllare la CPU: sulle macchine senza AVX Python si chiude con `c000001d` ([decisione 0003](../decisioni/0003-numpy-1.22.4-cpu-senza-avx.md)) |
| scipy | 1.9.1 | la 1.9.2 smette di pubblicare wheel a 32 bit |
| Pillow | 10.4.0 | |
| tifffile | 2023.7.10 | pura Python |

Sono scritte in `requirements.txt` con il perché di ciascuna e con
l'impronta del file: pip installa esattamente quelle wheel o si ferma. Se c'è
la cartella `.\pacchetti\`, *Installa / ripara* installa da lì senza usare
la rete.

## Senza internet

Su **qualsiasi** PC con Python e internet, anche moderno e a 64 bit: copiare
la cartella `software`, lanciare `MASTERFLEX.bat` e scegliere la voce **11**.
Scarica in `pacchetti\` l'installer di Python 3.8.10 e le wheel per Windows a
32 bit, tutti con l'impronta controllata. Poi si copia l'intera cartella sulla
macchina e si sceglie la voce **7**: non servirà la rete.

## Avvertenze

- L'interfaccia web non ha cifratura e comanda un motore che tira pellicola.
  Solo su una rete locale fidata, mai su internet.
- Durante i test il percorso pellicola va **vuoto**: il manuale di servizio
  lo ripete in maiuscolo, e ha ragione.
- Prima di toccare l'hardware: immagine bit a bit del disco. Se si perde,
  non si rifà.
