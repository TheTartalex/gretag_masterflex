# Masterflex D1008 — la macchina che fa tutto

> Guida per chi usa la macchina tutti i giorni. Era `LEGGIMI-WINDOWS.txt`
> del pacchetto filmscan (settembre 2026). I percorsi `windows\...` sono
> relativi alla cartella [`software/`](../../software/) del repository.
> Aggiornata a ottobre 2026 su driver e numpy.

Una macchina sola: il PC con Windows 7 a 32 bit. Dentro c'è la scheda
originale dello scanner, e sopra gira tutto: l'agente che parla alla macchina
e l'interfaccia web che si comanda dal tablet.

Windows 7 x86 non è un ripiego. Windows a 64 bit accetta solo driver firmati,
e né il driver Kodak del 2001 né il nostro EKframePnP lo sono. La versione a
32 bit non lo pretende.

## Cosa serve

1. **Python 3.8.10**, *Windows x86 executable installer*, a 32 bit:
   <https://www.python.org/downloads/release/python-3810/>.
   Spuntare **Add Python 3.8 to PATH** e **tcl/tk and IDLE**.
   I 32 bit sono obbligatori: l'SDK dello scanner è a 32 bit e un processo a
   64 bit non può caricarlo.
2. Il software originale copiato in `C:\MasterflexD\`. Serve almeno la
   cartella `Scanner` (SDK, IniFiles, Firmware).
3. Il driver della scheda installato e in esecuzione (servizio `EKFrame`):
   - sulla **macchina di produzione** resta l'originale, che si installa con
     `INSTALL.BAT` del software originale, cartella
     `InstallService\KodakFirmware\Driver`, da amministratore;
   - sulla **macchina di riserva** l'originale non funziona e si usa
     [EKframePnP](../../driver/EKframePnP/).

## Installazione

```
windows\installa.bat
```

Installa le librerie, controlla l'ambiente e dice cosa manca. Alla fine
controlla anche il driver e l'SDK, e lo scrive chiaro.

## Tutti i giorni

```
windows\avvia.bat        <- questo, e nient'altro
```

Fa partire due cose:

1. l'agente, che parla allo scanner con l'SDK originale;
2. l'interfaccia web, pubblicata sulla rete locale.

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

1. `windows\collaudo.bat` — controlla ambiente ed elaborazione. Non tocca la
   macchina.
2. `windows\avvia-simulazione.bat` — fa partire tutto con uno scanner finto.
   Serve a collegare il tablet e prendere confidenza **senza muovere un
   motore**. Farlo davvero.
3. Percorso pellicola **vuoto**, poi `windows\avvia.bat`. Primo comando
   dall'interfaccia: il backup del Parameter Flash. Dentro ci sono le
   tarature fatte una volta sola da un tecnico, con pellicole di prova che
   oggi non si comprano più.

> **Ottobre 2026:** il passo 3 è sospeso. L'autotest all'avvio porta i
> motori delle guide a fine corsa e continua a spingere. Finché non è
> corretto si usa solo la simulazione. Vedi
> [stato.md](../stato.md#problemi-aperti-in-ordine-di-priorità).

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

- Nove volte su dieci è il firewall. `avvia.bat` prova ad aprire la porta
  8080 da solo, ma la prima volta va lanciato con il tasto destro, *Esegui
  come amministratore*.
- Tablet e PC devono stare sulla stessa rete. Il wifi per gli ospiti spesso
  isola i dispositivi fra loro: usare la rete normale.
- L'indirizzo giusto è quello stampato all'avvio, non `localhost`.

## Avvio automatico

```
windows\avvio-automatico.bat
```

Mette `avvia.bat` fra i programmi che partono all'accensione. Si rilancia
per toglierlo.

## Il programma originale

A ogni avvio `avvia.bat` scrive una pagina di collegamento dentro l'aiuto
del programma originale (`C:\MasterflexD\PhxHelp\Data`), con l'indirizzo e
il PIN del momento. Dal programma originale si apre con il pulsante Aiuto.

Per spegnere stampa e densitometro nel programma originale:

```
windows\originale.bat
```

Non toglie componenti e non tocca eseguibili: cambia due voci di
configurazione. Di ogni file tiene una copia intatta con il suffisso
`.prima-di-noi`. Dallo stesso menu si riaccende tutto.

> **Attenzione:** i due programmi non vanno usati insieme. Comandano la
> stessa scheda, e il driver non lo impedisce. Prima si chiude uno, poi si
> apre l'altro.

Il perché di ogni scelta sta in
[programma-originale.md](../software-originale/programma-originale.md).

## Tutti i file

| File | Cosa fa |
|---|---|
| `avvia.bat` | tutto: agente + interfaccia in rete |
| `avvia-simulazione.bat` | come sopra, ma con scanner finto |
| `avvia-web.bat` | solo interfaccia (agente già avviato) |
| `finestra.bat` | interfaccia sul monitor della macchina |
| `collaudo.bat` | controllo di ambiente ed elaborazione |
| `installa.bat` | installazione |
| `installa-offline.bat` | installazione senza internet |
| `scarica-offline.bat` | prepara le wheel su un PC con internet |
| `avvio-automatico.bat` | attiva/disattiva l'avvio all'accensione |
| `originale.bat` | spegne stampa e densitometro nell'originale |
| `_python.bat` | interno: trova Python |

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
`installa.bat` lo fa già.

Se il messaggio esce lo stesso, qualcuno ha alzato una versione in
`requirements.txt` oltre l'ultima disponibile come wheel a 32 bit. Le
versioni da usare sono:

| Libreria | Versione | Perché |
|---|---|---|
| numpy | **1.22.4** | la 1.24.4 ha la wheel ma usa AVX senza controllare la CPU: sulle macchine senza AVX Python si chiude con `c000001d` ([decisione 0003](../decisioni/0003-numpy-1.22.4-cpu-senza-avx.md)) |
| scipy | 1.9.1 | la 1.9.2 smette di pubblicare wheel a 32 bit |
| Pillow | 10.4.0 | |
| tifffile | 2023.7.10 | pura Python |

Sono scritte in `requirements.txt` con il perché di ciascuna. La cartella
`.\pacchetti\` le contiene già tutte: se `installa.bat` la trova, installa
da lì senza usare la rete.

## Senza internet

Le wheel sono già in `.\pacchetti\` e `installa.bat` le usa da solo. Per
rifarle basta **qualsiasi** PC con Python e connessione, anche moderno e a 64
bit, perché le wheel si chiedono per l'altra macchina:

```
windows\scarica-offline.bat
```

Poi si copia l'intera cartella sulla macchina e si lancia:

```
windows\installa-offline.bat
```

## Avvertenze

- L'interfaccia web non ha cifratura e comanda un motore che tira pellicola.
  Solo su una rete locale fidata, mai su internet.
- Durante i test il percorso pellicola va **vuoto**: il manuale di servizio
  lo ripete in maiuscolo, e ha ragione.
- Prima di toccare l'hardware: immagine bit a bit del disco. Se si perde,
  non si rifà.
