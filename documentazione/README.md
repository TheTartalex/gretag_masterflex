# Documentazione

## Da dove cominciare

| Se vuoi… | Apri |
|---|---|
| usare la macchina tutti i giorni | [filmscan/guida-windows.md](filmscan/guida-windows.md) |
| sapere a che punto siamo e cosa resta da fare | [stato.md](stato.md) |
| capire perché si è scelta una strada | [decisioni/](decisioni/) |
| come funziona la scheda di acquisizione | [hardware/scheda-iaq.md](hardware/scheda-iaq.md) |
| installare il driver | [driver/EKframePnP](../driver/EKframePnP/) |
| il carrello pellicola e il suo firmware | [hardware/carrello-capri.md](hardware/carrello-capri.md) |

## Indice

### Progetto

| Documento | Contenuto |
|---|---|
| [stato.md](stato.md) | macchine, componenti, problemi aperti, prossimi passi |
| [decisioni/](decisioni/) | le scelte che orientano il progetto, una per file |

### filmscan (il nostro software)

| Documento | Contenuto |
|---|---|
| [filmscan/guida-windows.md](filmscan/guida-windows.md) | guida per l'operatore: menu di `MASTERFLEX.bat`, interfaccia, rullo, colore, archivio |
| [filmscan/ponte.md](filmscan/ponte.md) | l'agente che comanda la macchina attraverso l'SDK Kodak |
| [filmscan/archivio.md](filmscan/archivio.md) | ordini, consegna, conservazione, WhatsApp |

Le regole del codice stanno in [`software/CLAUDE.md`](../software/CLAUDE.md),
accanto al codice.

### Hardware

| Documento | Contenuto |
|---|---|
| [hardware/scheda-iaq.md](hardware/scheda-iaq.md) | **riferimento** della scheda: PLX 9080, FPGA, ARCNET, IOCTL, DMA, difetto su Windows 7 |
| [hardware/carrello-capri.md](hardware/carrello-capri.md) | carrello Capri (COM5): motori, firmware 1.15, flag, selftest di servizio, errori, protocollo |
| [hardware/scheda-acquisizione.md](hardware/scheda-acquisizione.md) | i due canali (COM5 e PCI), soglie dei sensori, offset, manuale di servizio; sulla scheda PCI vale `scheda-iaq.md` |
| [hardware/note-macchina.md](hardware/note-macchina.md) | scheda dati della macchina |

### Software originale (Kodak / Gretag)

| Documento | Contenuto |
|---|---|
| [software-originale/api-sdk.md](software-originale/api-sdk.md) | i 310 metodi di `scn_dll.dll`, costanti ottiche, `DMLCmdTransfer` |
| [software-originale/programma-originale.md](software-originale/programma-originale.md) | convivere con il programma originale, spegnere stampa e densitometro |
| [software-originale/elaborazione-originale.md](software-originale/elaborazione-originale.md) | la catena colore dell'originale (EyeTech, Dens2Paper, profili CMM) e cosa ne ha preso filmscan |

### Ricerca

| Documento | Contenuto |
|---|---|
| [ricerca/dossier-ricerca-2026-09.pdf](ricerca/dossier-ricerca-2026-09.pdf) | ricerca preliminare di settembre 2026: storia della macchina, fonti |

### Immagini

[immagini/](immagini/): schermate dell'interfaccia, fatte con lo scanner
simulato.

## Regole per i documenti

1. **Una sola fonte per ogni fatto.** Lo stato sta in `stato.md`, il
   protocollo della scheda in `hardware/scheda-iaq.md`, le scelte in
   `decisioni/`. Gli altri documenti rimandano lì con un link, non
   ricopiano.
2. **Ogni affermazione tecnica ha la sua fonte**: il file dell'originale,
   la pagina del manuale, l'indirizzo nel disassemblato. Ci si fida del
   disassemblato e delle verifiche in emulazione, non delle deduzioni a
   occhio.
3. **Un documento superato si corregge o si toglie.** Le parti sbagliate
   si riscrivono; un documento superato per intero si cancella. La storia
   resta in git e nel [CHANGELOG](../CHANGELOG.md): la documentazione
   descrive com'è oggi.
4. **Le date sono assolute** (AAAA-MM-GG o "26 ottobre 2026"), mai "ieri" o
   "la settimana scorsa".
5. **Nomi dei file**: minuscolo, parole separate da trattini, in italiano
   (`scheda-iaq.md`). Fanno eccezione i file con un nome convenzionale
   (`README.md`, `CLAUDE.md`, `CHANGELOG.md`) e quelli che girano su Windows
   per chi sta alla macchina (`LEGGIMI.txt`, `INSTALLA.bat`).
6. **Mai nel repository**: i file proprietari Kodak/Gretag (driver, SDK,
   firmware, pacchetto CapriFirmware, backup del Parameter Flash, manuale di
   servizio), i log delle macchine, i dati dei clienti.
   Il software originale si cita, non si copia.
7. Quando si aggiunge un documento lo si mette nell'indice qui sopra.
