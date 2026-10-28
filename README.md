# Gretag Masterflex D1008 — scanner pellicola

Rimettere in servizio lo scanner pellicola di un minilab **Kodak/Gretag
Masterflex D1008**: il software originale (MasterPlus, 2001) legge
2100×1400 pixel e ne salva 600×400. Questo progetto tiene della macchina
quello che funziona, cioè SDK e firmware Kodak, e sostituisce il resto per
salvare tutto quello che il sensore vede.

Il progetto ha due parti:

- **EKframePnP**: un driver nuovo per la scheda di acquisizione IAQ (PCI,
  PLX 9080), perché sulla macchina di riserva il driver Kodak del 2000 non
  parte più. Interfaccia identica all'originale: l'SDK Kodak lo usa senza
  accorgersene.
- **filmscan**: il software di acquisizione. Un agente comanda la macchina
  attraverso l'SDK Kodak `scn_dll.dll`, e un'interfaccia web per il tablet
  gestisce ordini, archivio, colore ed export.

## Cosa fa filmscan

- **Rulli interi, anche a strisce**: si inserisce la striscia, il carrello
  la porta avanti fotogramma per fotogramma, la espelle (davanti o dal
  retro) e aspetta la successiva. I fotogrammi vuoti o non esposti si
  riconoscono e si saltano.
- **Calibrazione quotidiana obbligatoria**: azzeramento dei motori,
  calibrazione della macchina (tempi di esposizione dell'SDK), poi dark e
  flat.
- **Impostazioni della macchina**, che restano memorizzate:
  - esposizione automatica;
  - pressore automatico (giù per leggere, su per muovere);
  - avanzamento automatico;
  - autofocus a scelta, di serie quello del carrello (`gi_focus`).
- **Posizionamento a mano con il video della macchina**, a passi da 1 e
  20 mm. Si apre da solo se il carrello non trova il fotogramma.
- **Digital ICE** scelto per rullo; numeri dal bordo con riordino a fine
  rullo, quando la macchina li passa.
- **Colore**: inversione del negativo, bilanciamento, curve, ritocco.
  In fondo una **LUT** (`.cube`, `.lut`, `.3dl`) che si applica da sola a
  tutti gli ordini.
- **Fine rullo** (chiusura ordinata) e **STOP EMERGENZA**: ferma tutto
  subito e aspetta l'OK di un tecnico.
- **Avvio automatico** all'accensione, senza richieste di Windows, in due
  modi:
  - *completo*: chiude i programmi originali in `C:\MasterflexD` e avvia
    agente e interfaccia;
  - *man in the middle*: lascia lavorare il programma originale e ne
    raccoglie le immagini.
- **Archivio ordini** con consegne, conservazione e registro.

L'interfaccia funziona su monitor 4:3 e 16:9, su tablet (orizzontale e
verticale) e su telefono.

## Stato

| Componente | Stato |
|---|---|
| [Driver EKframePnP 2.1](driver/EKframePnP/) | funziona sulla macchina di riserva; correttezza delle immagini da confermare |
| [filmscan](software/) | provato con l'agente simulato; **da provare sulla riserva** |
| Carrello Capri (COM5) | firmware 1.15 sulla riserva: guide a posto col 135 dal 28/10/2026 ([carrello-capri.md](documentazione/hardware/carrello-capri.md)) |

Problemi aperti e prossimi passi: **[documentazione/stato.md](documentazione/stato.md)**.

> ✅ **Guide del carrello: problema risolto per il 135** (28/10/2026)
> riportando il carrello al **firmware 1.15**. Se il programma originale
> riscarica la 1.13 il problema torna: la voce 13 di `MASTERFLEX.bat` lo
> controlla. Con APS e 110 la modalità "macchina vera" non è ancora
> provata: la prima volta, mano sull'interruttore. Storia in
> [decisione 0006](documentazione/decisioni/0006-firmware-carrello-1.15.md#esito-del-28102026).

## Installazione

L'installer è uno solo, per una macchina pulita e per l'aggiornamento:

1. sul PC di sviluppo, con tutto committato:
   `sh strumenti/aggiornamento/prepara.sh dist`, che crea
   `masterflex-installer-<data>.zip` con Python e librerie dentro;
2. sulla macchina: estrarre lo zip e fare doppio clic su `INSTALLA.bat`.

Da lì in poi si usa solo `software\MASTERFLEX.bat`. Dettagli in
[strumenti/](strumenti/) e nella
[guida per l'operatore](documentazione/filmscan/guida-windows.md).

## Struttura del repository

```
README.md               questo file
CLAUDE.md               contesto per le sessioni con Claude (anche per chi è nuovo)
CHANGELOG.md            cosa cambia, per componente e versione
CONTRIBUTING.md         come si lavora: rami, commit, versioni, cosa non pubblicare

documentazione/         tutta la documentazione, con indice e regole
  stato.md              stato attuale: l'unico posto dove si aggiorna
  piano-lavori.md       il percorso a lungo termine
  decisioni/            una scheda per ogni scelta importante
  hardware/             scheda IAQ, carrello Capri, dati della macchina
  software-originale/   SDK Kodak (tutti i comandi) e programma originale
  filmscan/             guida per l'operatore, ponte, archivio
  ricerca/              dossier di ricerca

driver/                 un driver per cartella
  EKframePnP/           pacchetto/ (collaudato) · sorgenti/ · verifica/

software/               filmscan
  MASTERFLEX.bat        l'unico file da lanciare sulla macchina (menu)
  filmscan/             interfaccia web, servizio, elaborazione, LUT
  ponte/                l'agente che parla all'SDK Kodak

strumenti/
  aggiornamento/        prepara.sh (crea l'installer) · INSTALLA.bat
```

## Piattaforma

Windows 7 **a 32 bit**, Python 3.8 a 32 bit, su PC con CPU **senza AVX**.
I vincoli non sono negoziabili, e il perché sta in
[decisioni/](documentazione/decisioni/):

- driver non firmati → solo Windows a 32 bit;
- SDK Kodak a 32 bit → Python a 32 bit;
- Python 3.8 è l'ultimo per Windows 7;
- niente AVX → numpy 1.22.4, scipy 1.9.1.

L'interfaccia web va su Chrome 109 o Firefox ESR, gli ultimi per Windows 7.

## File proprietari

Il repository **non contiene** software Kodak/Gretag: niente driver
originale, SDK, firmware, pacchetto CapriFirmware, backup del Parameter
Flash, manuali o programmi di servizio. La documentazione
li cita con percorsi e indirizzi, per chi ne ha una copia legittima. Il
codice in questo repository è sotto licenza [MIT](LICENSE).

Il driver è stato ricostruito dal binario originale per interoperabilità con
l'hardware posseduto, ed è verificato in emulazione contro l'originale.
