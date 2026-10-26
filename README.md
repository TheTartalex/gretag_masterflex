# Gretag Masterflex D1008 — scanner pellicola

Rimettere in servizio lo scanner pellicola di un minilab **Kodak/Gretag
Masterflex D1008**: il software originale (MasterPlus, 2001) legge
2100×1400 pixel e ne salva 600×400. Questo progetto tiene della macchina
quello che funziona — SDK e firmware Kodak — e sostituisce il resto per
salvare tutto quello che il sensore vede.

Il progetto ha due parti:

- **EKframePnP** — un driver nuovo per la scheda di acquisizione IAQ (PCI,
  PLX 9080), perché su Windows 7 il driver Kodak del 2000 non parte più.
  Interfaccia identica all'originale: l'SDK Kodak lo usa senza accorgersene.
- **filmscan** — il software di acquisizione: un agente che comanda la
  macchina attraverso l'SDK Kodak, più un'interfaccia web per il tablet con
  ordini, archivio, pipeline colore e pulizia a infrarosso.

## Stato

| Componente | Stato |
|---|---|
| [Driver EKframePnP 2.1](driver/EKframePnP/) | funziona sulla macchina di riserva; correttezza delle immagini da confermare |
| [filmscan](software/) | codice ancora da importare nel repository |

Problemi aperti e prossimi passi: **[documentazione/stato.md](documentazione/stato.md)**.

> ⚠️ **Non usare filmscan in modalità "macchina vera"**: l'autotest all'avvio
> spinge i motori delle guide oltre il fine corsa. La simulazione si può
> usare.

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
  hardware/             scheda IAQ, canali di comando, dati della macchina
  software-originale/   SDK Kodak e programma originale
  filmscan/             guide e progetto del nostro software
  ricerca/              dossier di ricerca

driver/                 un driver per cartella
  EKframePnP/           pacchetto/ (pronto) · sorgenti/ · verifica/

software/               filmscan (da importare) e le sue regole in CLAUDE.md
strumenti/              script di servizio: diagnosi, correzione numpy
```

## Piattaforma

Windows 7 **a 32 bit**, Python 3.8 a 32 bit, su PC con CPU **senza AVX**.
I vincoli non sono negoziabili, e il perché sta in
[decisioni/](documentazione/decisioni/):

- driver non firmati → solo Windows a 32 bit;
- SDK Kodak a 32 bit → Python a 32 bit;
- Python 3.8 è l'ultimo per Windows 7;
- niente AVX → numpy 1.22.4, scipy 1.9.1.

## File proprietari

Il repository **non contiene** software Kodak/Gretag: niente driver
originale, SDK, firmware, manuali o programmi di servizio. La documentazione
li cita con percorsi e indirizzi, per chi ne ha una copia legittima. Il
codice in questo repository è sotto licenza [MIT](LICENSE).

Il driver è stato ricostruito dal binario originale per interoperabilità con
l'hardware posseduto, ed è verificato in emulazione contro l'originale.
