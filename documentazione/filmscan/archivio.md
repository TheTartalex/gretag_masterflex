# L'archivio degli ordini

La seconda pagina, `/archivio`. Si usa al banco, non davanti alla macchina:
ritrovare un ordine di tre settimane fa, preparare la copia per il cliente,
avvisarlo che e' pronto, e ogni tanto fare spazio.

Stesso PIN della pagina di scansione: senza, non si vede nemmeno il nome di un
cliente. Chi arriva su `/archivio` senza PIN valido viene rimandato alla
richiesta del PIN.

## Cosa si fa

**Sfogliare.** Elenco degli ordini con cliente, data, rulli, fotogrammi, peso
e giorni trascorsi. Ricerca per cliente. Aprendone uno si vedono i rulli, i
provini di ogni fotogramma, e quanto occupa.

**Consegnare.** Un solo file zip con le foto esportate, o con i grezzi, o con
tutto. Lo zip non e' compresso di proposito: i TIFF a 16 bit di un rullo pesano
centinaia di megabyte e comprimerli su un PC del 2009 vuol dire lasciare
l'operatore ad aspettare per guadagnare poco. Serve a fare **un** file da
copiare, non a risparmiare spazio.

**Riaprire in scansione.** Un ordine o un singolo rullo si riaprono nella
pagina di scansione gia' selezionati, per rifare un fotogramma venuto male.

**Correggere i nomi.** Il nome di un cliente si sbaglia a scriverlo piu' spesso
di quanto si creda, specie sotto dettatura al banco. Si corregge la scheda del
cliente - e vale per tutti i suoi ordini - oppure si sposta l'ordine su
un'altra persona scrivendo un nome diverso: se non esiste, viene creata.

I file gia' salvati restano dove sono: rinominare un cliente non sposta
centinaia di megabyte sul disco, e i percorsi nel database sono assoluti,
quindi tutto continua a funzionare.

**Il provino a contatto.** Tutti i fotogrammi dell'ordine in una pagina sola,
generata dai file gia' esportati. E' quello che il cliente guarda per dire
"questa si', questa no", e si salva nella cartella dell'ordine.

## Il registro Excel

Il registro **non e' il database**. Il database resta SQLite, perche' un foglio
di calcolo aperto da qualcuno blocca il file e fa perdere le scritture.

Il registro e' una fotografia leggibile, riscritta a ogni cambiamento - ordine
nuovo, rullo finito, rinomina - con codice, cliente, telefono, data, stato,
rulli, fotogrammi, giorni, peso e note. Si apre, si stampa, si manda al
commercialista. Se si rompe si rifa' con un clic; se si rompesse il database
sarebbe un'altra storia.

Serve `openpyxl` (pura Python, gia' in `pacchetti\`). Senza, il registro esce
in CSV con il punto e virgola come separatore, che l'Excel italiano apre senza
procedure guidate.

La cartella si sceglie dalla pagina: c'e' un elenco da sfogliare, perche' il
browser non puo' aprire il dialogo di Windows per una cartella del server - e
quando si lavora dal tablet il "server" e' un'altra macchina, quindi non
avrebbe nemmeno senso.

## La conservazione a 30 giorni

**I giorni si contano dall'ultima volta che si e' lavorato a quell'ordine**,
non da quando e' stato aperto: il piu' recente fra l'apertura, l'ultimo
fotogramma registrato e l'ultima modifica di un file ancora sul disco. Un
ordine aperto a gennaio e scansionato ieri ha un giorno, non nove mesi.
Contare dall'apertura vorrebbe dire proporre di cancellare il lavoro appena
finito, ed e' esattamente l'errore che rende inutile una scadenza.

Un ordine che non ha piu' file sul disco esce dall'elenco degli scaduti: non
c'e' niente da liberare, e riproporlo ogni giorno sarebbe chiedere una
cancellazione gia' fatta.

Cosa succede agli ordini scaduti lo decide una scelta esplicita, nel pannello
"Registro e archivio", e sono tre:

- **Segnala soltanto** - non tocca niente, li elenca con quanto spazio
  libererebbero. E' il valore di serie e resta tale finche' qualcuno non
  decide altro.
- **Cancella i grezzi, tieni le foto** - libera quasi tutto lo spazio e lascia
  l'ordine consegnabile.
- **Cancella grezzi e foto**.

La pulizia si applica all'accensione del server e alla fine di ogni rullo -
i due momenti in cui lo spazio serve e nessuno sta aspettando una risposta -
e a comando con "Applica adesso".

Il motivo per cui di serie non cancella: i grezzi sono l'unica cosa che non si
rifa'. Per riaverli bisogna rimettere la pellicola nella macchina, ammesso che
il cliente ce l'abbia ancora. Un conto alla rovescia che cancella da solo le
fotografie di qualcuno e' esattamente il genere di comodita' che si paga una
volta sola, e cara.

**In nessun caso sparisce la riga dell'ordine.** Sapere che quell'ordine e'
esistito non costa niente e serve spesso.

## Cancellare a mano

Due gesti diversi, e vanno tenuti diversi.

**Liberare spazio** (pannello "Spazio" di un ordine): cancella i file e lascia
tutto il resto - ordine, rulli, fotogrammi, chi ha portato cosa e quando.

**Eliminare** una riga d'archivio: c'e' un pulsante per il **rullo**, uno per
l'**ordine** e uno per il **cliente** (che si porta dietro tutti i suoi
ordini, e per questo chiede conferma due volte). I file sul disco si
cancellano solo se lo si conferma a parte, con scritto quanti sono: "togli
dall'elenco" e "butta le fotografie" sono due decisioni, e si prendono due
volte.

### Il database non deve mentire sul disco

Quando un file viene cancellato, il suo percorso viene tolto anche dal
database. Sembra un dettaglio ed e' la meta' che conta: finche' l'archivio
continua a indicare file che non esistono, conta fotogrammi che non ci sono,
le miniature si rompono una a una, e chi guarda conclude - a ragione - che il
pulsante "elimina" non ha fatto niente.

Per la stessa ragione i conteggi e i pesi sono **di file che esistono sul
disco**, non di righe del database, e un file sparito per conto suo (cancellato
da fuori, disco scollegato) viene detto a chiare lettere invece di comparire
come una miniatura che non arriva. Al posto della miniatura rotta c'e' una
piastrella che dice "file mancante": l'icona di immagine rotta del browser e'
identica per "file cancellato", "disco staccato" e "errore di rete", e da un
tablet a mezzo metro non si distinguono.

Un grezzo e' due file, il `.npz` con i dati e il `.json` con i parametri di
scansione accanto: si cancellano insieme.

## L'avviso su WhatsApp

Si apre WhatsApp con la conversazione giusta e il messaggio gia' scritto.
L'invio lo fa una persona.

**Non si puo' fare di meglio, e vale la pena spiegare perche'.** WhatsApp non
offre alcun modo lecito a un programma esterno di allegare un file o premere
invio: le sole strade sarebbero l'API a pagamento per aziende, che richiede un
account business e l'approvazione dei modelli di messaggio, oppure pilotare di
nascosto il browser su web.whatsapp.com - che e' contro le condizioni d'uso e
si rompe al primo aggiornamento della pagina.

Quello che si puo' fare, e che si fa: il numero viene normalizzato da solo
(`335 12.34.567`, `+39 348 99 88 776`, `0451234567` diventano tutti numeri che
WhatsApp accetta), il messaggio si compone da un modello con `{cliente}`,
`{codice}`, `{fotogrammi}`, `{rulli}`, `{data}`, e il provino a contatto sta
gia' nella cartella dell'ordine, a un trascinamento di distanza.

Se il cliente non ha un numero in scheda, WhatsApp si apre lo stesso con il
messaggio pronto e il destinatario si sceglie dalla rubrica.
