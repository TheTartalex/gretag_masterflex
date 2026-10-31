# CLAUDE.md — contesto del progetto

Questo file si carica da solo all'inizio di ogni sessione nel repository.
Prende il posto del vecchio `01-CONTESTO-PER-NUOVA-SESSIONE.md` che si
allegava a mano. **Lo stato del lavoro non sta qui**: sta in
[documentazione/stato.md](documentazione/stato.md), e va letto subito dopo
questo file.

## Chi e cosa

Alessandro sta sostituendo il software di un minilab **Kodak/Gretag
Masterflex D1008** (scanner pellicola). Scrive in italiano e vuole risposte
**in italiano**. Anche i documenti, i commenti nel codice e i messaggi di
commit sono in italiano.

Le parti del progetto:

- **filmscan** ([`software/`](software/)): Python 3.8, Windows 7 x86. Un
  agente che parla all'SDK Kodak `scn_dll.dll` via ctypes, più
  un'interfaccia web per il tablet. Sulla macchina si usa solo
  `software/MASTERFLEX.bat`. Le regole del codice stanno in
  [`software/CLAUDE.md`](software/CLAUDE.md).
- **Carrello Capri** (Gretag, COM5): guide, trasporto, lente, CCD, sensori.
  Non lo comandiamo direttamente; riferimento in
  [carrello-capri.md](documentazione/hardware/carrello-capri.md).
- **EKframePnP** ([`driver/EKframePnP/`](driver/EKframePnP/)): driver nuovo
  per la scheda di acquisizione, perché il vecchio `EKframe.sys` non funziona
  sulla macchina di riserva.

## Registri: dove guardare quando c'è un problema

Tutto in `software\log` sulla macchina (mai in `C:\MasterflexD`, che è del
programma originale): `programma\` (diario di filmscan, errori con
traceback, comunicazione col ponte), `agente\` (comandi e risposte,
crash), `sdk\` (copie di `SDKCacheDir\log_*.txt`), `collaudi\`,
`diagnosi\`, `da-mandare\` (gli zip). Alessandro lo zip lo prende da
MASTERFLEX.bat voce 6 o da Macchina → Scarica i registri. Quando manda uno
zip `registri-*.zip`, si parte da lì. Codice: `software/filmscan/registri.py`.

**Collegamento remoto** (`software/remoto.py`, decisione 0010): la
macchina manda da sola i registri, senza i dati dei clienti, al repository
privato `TheTartalex/gretag_masterflex_registri` (`<macchina>/<AAAA-MM>/`).
Per leggerli: `add_repo` su quel repository, poi clone. Gli aggiornamenti la
macchina li prende dal ramo `main` di questo repository: **quello che va su
`main` arriva alla riserva al suo prossimo avvio**. Su `main` va solo il
codice pronto per la macchina, come prima i pacchetti.

## Macchine

- **Produzione**: non si tocca finché non c'è un pacchetto completo provato.
- **Riserva**: è il banco di prova. Fisicamente identica alla produzione:
  server IBM del 2007, Win7 SP1 x86, 4 GB, CPU **senza AVX**, scheda IAQ e
  scanner. Si comanda da AnyDesk: Alessandro esegue, Claude prepara.
- **PC di sviluppo**: Windows a 64 bit, senza WDK 7.1. Il driver si compila
  nel container Linux.

## Regole di lavoro

1. **Fonti di verità.** Valgono il disassemblato dell'originale e le
   verifiche in emulazione, non le deduzioni a occhio. Ogni affermazione sul
   protocollo ha nei commenti dei sorgenti l'indirizzo dell'originale
   (`0x1xxxx`).
2. **Niente sulla produzione** finché la riserva non ha provato tutto,
   immagini comprese.
3. **Motori in modalità reale**: per il 135 sì, il problema delle guide è
   risolto (28/10/2026, firmware del carrello 1.15: con la 1.13 torna). Per APS e 110 non ancora provati: la prima volta
   con la mano sull'interruttore.
4. **Mai committare file Kodak/Gretag** (driver originale, SDK, firmware,
   pacchetto CapriFirmware, backup del Parameter Flash, manuali), log delle
   macchine o dati di clienti. `.gitignore` li esclude; non forzarlo.
5. **A fine sessione** aggiornare [stato.md](documentazione/stato.md) e,
   se si è deciso qualcosa, aggiungere una scheda in
   [decisioni/](documentazione/decisioni/). Le regole sui documenti sono in
   [documentazione/README.md](documentazione/README.md).
6. Il pacchetto in `driver/*/pacchetto/` è quello **collaudato**. Si
   sostituisce solo dopo una prova sulla macchina, aggiornando versione,
   impronte e [CHANGELOG](CHANGELOG.md).

## Errori già commessi (da non ripetere)

- **Agosto.** Si era dato per scontato che `ReadFile`/`WriteFile` non
  fossero usati, e si erano interpretati male gli IOCTL
  `A410`/`A414`/`A418`/`A440`/`A444`. Le analisi di agosto sono superate: il
  riferimento è [scheda-iaq.md](documentazione/hardware/scheda-iaq.md).
- **Agosto.** Si era proposto di provare il driver originale su Win7 "così
  com'è". Sulla riserva non funziona, a causa dello zombie.
- **Settembre.** I messaggi "ARCNet" in `EKframe.sys` erano stati liquidati
  come residui di un driver di esempio. Invece ARCNET è il canale comandi
  verso la testa di scansione.
- **Settembre.** Si era indicato numpy 1.24.4 perché la wheel esisteva, ma
  usa AVX senza controllare la CPU. Una wheel che esiste non è detto che
  giri.
- **Settembre.** filmscan passava al fotogramma successivo con la
  regolazione fine (`regola_3800`) e calibrava a una risoluzione diversa da
  quella di scansione. Errori del genere si trovano solo provando il
  percorso SDK con l'agente simulato: farlo a ogni modifica di `hal.py`,
  `acquire.py` o `service.py` (vedi `software/README.md`).
- **Ottobre.** Il problema delle guide era attribuito all'"autotest di
  filmscan", ma le guide le muove il carrello Capri quando si inizializza.
  Prima di cercare la causa nel nostro codice, chiedersi quale pezzo
  della macchina fa davvero quel movimento.
- **Ottobre.** Il firmware 1.13 era stato messo come prima causa delle guide;
  poi, decodificati i backup e disassemblato il firmware, la si era
  declassata a "improbabile" perché il formato del flash era lo stesso. Il
  28/10 è stata proprio la 1.15 a risolvere. Un formato compatibile dice
  che un firmware *può* lavorare con quel flash, non che si comporti come
  l'altro. Quando una prova sulla macchina costa poco (rimettere un
  firmware), la si fa prima di declassare un'ipotesi a tavolino. Il
  firmware del carrello (H8/300H) si legge con `binutils-h8300-hms`.

## La scheda IAQ in breve

Dettagli in [scheda-iaq.md](documentazione/hardware/scheda-iaq.md).

- PCI `VEN_11B2&DEV_0008&SUBSYS_908010B5`, bridge **PLX PCI 9080**: registri
  da 256 B (`0xDE000000`), finestra locale da 2 MB (`0xDD800000`), IRQ 9.
- Nella finestra: FPGA Kodak (`0x4004`–`0x4014`), controller **ARCNET SMC
  COM20020** a `0x8000` (canale comandi: PC nodo `0x55`, scanner `0xAA`),
  descrittori DMA a `0x100000`/`0x140000`, FIFO dei pixel a `0xC0000000`
  (locale).
- Le applicazioni aprono `\\.\EKFrameDev`: 13 IOCTL (device type `0x9C40`),
  `ReadFile`/`WriteFile` per i messaggi ARCNET, 5 eventi.

## Ricostruire l'ambiente nel container

```sh
apt-get install -y gcc-mingw-w64-i686
pip install pefile capstone unicorn --break-system-packages
cd driver/EKframePnP/sorgenti && sh build.sh       # EKframePnP.sys + ektest.exe
python3 confronta.py EKframePnP.sys ../pacchetto/EKframePnP.sys
```

Per filmscan: Python 3.8 con `uv python install 3.8` e le librerie senza
impronte (vedi `software/README.md`), poi `python selftest.py` e la prova con
l'agente simulato.

La verifica di equivalenza ha bisogno del driver originale (in locale,
mai nel repository) e del banco di prova C, che non è ancora stato
importato: vedi
[driver/EKframePnP/verifica/README.md](driver/EKframePnP/verifica/README.md).
