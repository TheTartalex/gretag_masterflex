# Documentazione

## Da dove cominciare

| Se vuoi… | Apri |
|---|---|
| sapere a che punto siamo e cosa resta da fare | [stato.md](stato.md) |
| il percorso a lungo termine, fase per fase | [piano-lavori.md](piano-lavori.md) |
| capire perché si è scelta una strada | [decisioni/](decisioni/) |
| come funziona la scheda di acquisizione | [hardware/scheda-iaq.md](hardware/scheda-iaq.md) |
| installare il driver | [driver/EKframePnP](../driver/EKframePnP/) |
| usare la macchina tutti i giorni | [filmscan/guida-windows.md](filmscan/guida-windows.md) |

## Indice

### Progetto

| Documento | Contenuto | Aggiornato |
|---|---|---|
| [stato.md](stato.md) | macchine, componenti, problemi aperti, prossimi passi | 2026-10-26 |
| [piano-lavori.md](piano-lavori.md) | le fasi dal software originale alla macchina che scansiona | 2026-09-03, note di ottobre |
| [decisioni/](decisioni/) | le scelte che orientano il progetto, una per file | 2026-10-25 |

### Hardware

| Documento | Contenuto | Aggiornato |
|---|---|---|
| [hardware/scheda-iaq.md](hardware/scheda-iaq.md) | **riferimento** della scheda: PLX 9080, FPGA, ARCNET, IOCTL, DMA, difetto su Win7 | 2026-10-26 |
| [hardware/scheda-acquisizione.md](hardware/scheda-acquisizione.md) | i due canali (COM5 e PCI), soglie dei sensori, offset, firmware, manuale di servizio. Superato dove contraddice `scheda-iaq.md` | 2026-09-03 |
| [hardware/note-macchina.md](hardware/note-macchina.md) | scheda dati della macchina, da compilare man mano | 2026-09-03 |

### Software originale (Kodak / Gretag)

| Documento | Contenuto | Aggiornato |
|---|---|---|
| [software-originale/api-sdk.md](software-originale/api-sdk.md) | i 310 metodi di `scn_dll.dll`, costanti ottiche, `DMLCmdTransfer` | 2026-09-03 |
| [software-originale/programma-originale.md](software-originale/programma-originale.md) | convivere con MasterPlus, spegnere stampa e densitometro | 2026-09-04 |

### filmscan (il nostro software)

| Documento | Contenuto | Aggiornato |
|---|---|---|
| [filmscan/installazione.md](filmscan/installazione.md) | installazione, preset, ciclo automatico, i dati che mancano | 2026-09-03 |
| [filmscan/guida-windows.md](filmscan/guida-windows.md) | guida per l'operatore | 2026-10-26 |
| [filmscan/ponte.md](filmscan/ponte.md) | l'agente che comanda la macchina attraverso l'SDK | 2026-09-03 |
| [filmscan/archivio.md](filmscan/archivio.md) | ordini, consegna, conservazione, WhatsApp | 2026-09-07 |

Le regole del codice stanno in [`software/CLAUDE.md`](../software/CLAUDE.md),
accanto al codice.

### Ricerca

| Documento | Contenuto |
|---|---|
| [ricerca/dossier-ricerca-2026-09.pdf](ricerca/dossier-ricerca-2026-09.pdf) | ricerca preliminare: storia della macchina, fonti, architettura proposta |

## Regole per i documenti

1. **Una sola fonte per ogni fatto.** Lo stato sta in `stato.md`, il
   protocollo della scheda in `hardware/scheda-iaq.md`, le scelte in
   `decisioni/`. Gli altri documenti rimandano lì con un link, non
   ricopiano.
2. **Ogni affermazione tecnica ha la sua fonte**: il file dell'originale,
   la pagina del manuale, l'indirizzo nel disassemblato. Ci si fida del
   disassemblato e delle verifiche in emulazione, non delle deduzioni a
   occhio.
3. **Un documento superato non si cancella.** Si aggiunge in cima un
   riquadro *Stato del documento* che dice cosa è superato e da cosa. Le
   frasi sbagliate si barrano (`~~...~~`) con accanto la correzione datata.
   Se un documento è superato per intero va in `storico/`.
4. **Le date sono assolute** (AAAA-MM-GG o "26 ottobre 2026"), mai "ieri" o
   "la settimana scorsa".
5. **Nomi dei file**: minuscolo, parole separate da trattini, in italiano
   (`scheda-iaq.md`). Fanno eccezione i file con un nome convenzionale
   (`README.md`, `CLAUDE.md`, `CHANGELOG.md`) e quelli che girano su Windows
   per chi sta alla macchina (`LEGGIMI.txt`, `INSTALLA.bat`).
6. **Mai nel repository**: i file proprietari Kodak/Gretag (driver, SDK,
   firmware, manuale di servizio), i log delle macchine, i dati dei clienti.
   Il software originale si cita, non si copia.
7. Quando si aggiunge un documento lo si mette nell'indice qui sopra.
