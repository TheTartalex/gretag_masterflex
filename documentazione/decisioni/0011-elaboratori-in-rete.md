# 0011 — L'elaborazione anche sugli altri PC della rete

- Data: 2026-11-07
- Stato: Proposta (provato con l'agente simulato: quattro fotogrammi fatti
  da due processi elaboratori; elaboratore spento a metà rullo, i
  fotogrammi rifatti dalla macchina senza perderne nessuno; manca la prova
  con un PC vero nella rete della riserva)

## Contesto

Sul PC della macchina (IBM del 2007) l'elaborazione completa costava ~23 s
a fotogramma (riserva, 7/11/2026): un rullo da 36 si leggeva in pochi
minuti e si elaborava in un quarto d'ora. Alessandro ha in negozio un
server con RAID e PC Windows molto più veloci, nella stessa rete, e ha
chiesto di poterli usare: con un eseguibile da lanciare, indirizzo della
macchina o ricerca automatica.

## Decisione

- **La macchina resta il centro.** Gli elaboratori chiedono lavoro alla
  macchina (richiesta che resta aperta 20 s) attraverso il server web che
  c'è già, sulla stessa porta (8080, già aperta nel firewall per il tablet).
  Nessun servizio nuovo da aprire sulla macchina, niente UDP.
- **Chiave condivisa** al posto del PIN: la genera la macchina, si scarica
  con il pacchetto dal pannello Macchina. Prima di scaricare ed eseguire
  il codice, l'elaboratore chiede alla macchina di dimostrare di conoscere
  la chiave (HMAC di una sfida): un PC qualunque della rete non può
  spacciarsi per la macchina.
- **Il codice lo dà la macchina**: l'elaboratore scarica filmscan dalla
  macchina e riparte da solo quando cambia. Niente da aggiornare sui PC, e
  stessa versione sempre.
- **Mai pickle dagli elaboratori**: la risposta è uno zip con JSON, file
  finiti e array numpy senza oggetti (allow_pickle=False). I nomi dei file
  li decide la macchina.
- **Nessun fotogramma si perde**: se l'elaboratore non prende il lavoro,
  smette di dare segni di vita (30 s) o risponde con un errore, il
  fotogramma lo elabora la macchina.
- **Il Digital ICE della Kodak resta sulla macchina**: la DLL dell'SDK non
  esce di lì (regola 4). La macchina pulisce il grezzo e manda quello.
- Windows: `ELABORATORE.bat` installa Python 3.8 per l'utente e le
  librerie; server: `python3 elaboratore.py`. Niente eseguibile compilato
  per ora: il .bat fa lo stesso senza un passaggio di compilazione da
  mantenere.

## Conseguenze

- Sul PC della macchina restano lettura, salvataggio dei grezzi, Digital
  ICE Kodak e registro; tutto il resto può andare fuori.
- Il grezzo viaggia in rete (~35-50 MB a fotogramma): su una rete a 100
  Mbit sono 3-4 s, su una gigabit meno di mezzo secondo.
- Acceso di serie: senza elaboratori collegati non cambia niente.

## Aggiornamento dell'8/11/2026

- I PC disponibili includono un Threadripper con 64 GB e un server Unraid
  sempre acceso. L'elaboratore conta da solo i processi (metà dei core, 2 GB
  l'uno, massimo 16) e la macchina ne usa fino a `elaboratori_massimo` (16).
- Su Unraid gira in un container (`docker-compose.yml` nel pacchetto,
  `restart: unless-stopped`, rete dell'host): con la macchina spenta aspetta,
  e la ritrova quando si riaccende.
- Il limite vero è la memoria del PC della macchina (32 bit): il pacco resta
  solo in coda finché un elaboratore non lo prende, e i grezzi già su disco
  non si caricano se vanno in rete. I positivi, che tornano come base a
  35 MB, al massimo 4 alla volta.
- La GPU (GTX 1080) non si usa: il processore basta a stare dietro allo
  scanner. Se in futuro servisse, solo sugli elaboratori, con lo stesso
  risultato della macchina.
