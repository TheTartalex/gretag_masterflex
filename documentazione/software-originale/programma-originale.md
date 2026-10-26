# Convivere con il programma originale

Due domande pratiche: come lanciare la nostra interfaccia da dentro il
programma originale, e come togliergli stampa e densitometro.

## Prima di tutto: non vanno usati insieme

Il driver **non** lo impedisce. `EKframe.sys` crea il suo dispositivo con
`Exclusive = FALSE` — si legge nel binario, alla chiamata a `IoCreateDevice`:
il registro passato come sesto argomento e' azzerato con `xor esi, esi` poco
prima. Quindi due processi possono aprire `\\.\EKFrameDev` insieme, e Windows
non dira' niente.

Non e' un permesso, e' solo l'assenza di un divieto. Sotto c'e' un solo motore
DMA e una sola coda di comandi: due padroni che mandano comandi alla stessa
scheda si pestano i piedi a meta' scansione, e il guasto arriva quando la
pellicola e' gia' dentro.

**Prima si chiude uno, poi si apre l'altro.** Il collegamento qui sotto serve a
passare dall'uno all'altro comodamente, non a tenerli accesi insieme.

## Il collegamento dentro il programma originale

Il programma originale e' MFC del 2001: niente estensioni, niente plugin, i
pulsanti stanno nelle risorse di un eseguibile da 8 MB. Toccare quel binario si
puo', ma sarebbe fragile e senza ritorno.

Non serve. C'e' un punto in cui il programma mostra contenuto che possiamo
scrivere noi: **il visualizzatore di aiuto**. `PhxHelp.exe` importa `ole32` e
`OLEAUT32` e nessuna funzione di shell, e nelle sue stringhe si leggono
`C:\MasterflexD\PhxHelp\Data\Hot Topics.htm` e `All Topics.htm`. E' un
controllo Internet Explorer incorporato che apre pagine HTML da una cartella.

Quindi ci mettiamo una pagina: titolo, un tasto grande, e soprattutto
**l'indirizzo scritto in chiaro e in grande**, che e' quello che serve davvero
perche' il lavoro si fa dal tablet.

La pagina si riscrive **a ogni avvio** di `avvia.bat`, con l'indirizzo e il
token del momento: un collegamento con il token di ieri sarebbe peggio di
nessun collegamento.

L'HTML e' volutamente antico — niente CSS moderno, niente JavaScript — perche'
a mostrarlo e' un motore che si comporta come un IE di venticinque anni fa.
Per la stessa ragione **la nostra interfaccia non puo' vivere dentro quella
finestra**: usa griglie CSS, canvas ed eventi che quel motore non conosce.
Sul monitor della macchina serve un browser vero (Firefox ESR 115 o Chrome 109
sono gli ultimi che girano su Windows 7); dal tablet non c'e' problema.

Il tasto usa `target="_blank"`, che nella maggior parte dei casi fa aprire il
browser di sistema. Se non succede, l'indirizzo e' li' da copiare: e' per
questo che e' scritto grande.

## Togliere stampa e densitometro

Non si tolgono componenti. `PhxStartup.exe` cerca i componenti per ProgId e ha
messaggi d'errore gia' pronti — *"Cannot get ProgID of PrintServer"*, *"of
PhxDaily"*: togliere una voce da `SWComponent_Factory.ini` vuol dire rischiare
che il programma non parta piu', e a quel punto non c'e' piu' niente da
alleggerire.

Le due funzioni hanno un interruttore proprio, ed e' reversibile:

| Funzione | File | Cosa si cambia |
|---|---|---|
| Stampa | `Products.ini` | i 24 prodotti hanno tutti `Target=Paper`: sono tutti lavori di stampa. `Defined=1` diventa `Defined=0` e spariscono dall'elenco ordinabile. |
| Densitometro | `Daily_Factory.ini` | `ColorCalibrationEnabled=false` ferma la calibrazione colore giornaliera, cioe' la routine densitometrica. `CalDueCheckInterval` passa da 20 minuti a 24 ore e il programma smette di chiederla. |

    python strumenti\originale.py --stato
    python strumenti\originale.py --alleggerisci
    python strumenti\originale.py --ripristina

oppure `windows\originale.bat`, che fa le stesse cose con un menu.

Di ogni file toccato resta una copia intatta con il suffisso `.prima-di-noi`,
piu' una copia datata a ogni passaggio successivo. Si torna indietro quando si
vuole, e il ripristino e' stato provato: 24 prodotti spenti e riaccesi,
calibrazione spenta e riaccesa.

## Cosa resta al programma originale

Gestione ordini, archivio, masterizzazione e la parte meccanica di servizio —
i test dei motori, i sensori, la Length Calibration. Quest'ultima serve ancora:
e' con il programma originale che si tara la macchina, ed e' il suo Parameter
Flash che va salvato prima di ogni altra cosa (vedi [ponte.md](../filmscan/ponte.md)).
