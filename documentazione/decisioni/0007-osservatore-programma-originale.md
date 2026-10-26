# 0007 — Immagini dal programma originale con un osservatore esterno

- Data: 2026-10-26
- Stato: Proposta (scritto, non ancora provato sulla macchina)

## Contesto

Sulla riserva il programma originale (`PhxScanner.exe`) acquisisce con il
nostro driver EKframePnP: il log dell'SDK delle 17:03 mostra decine di
immagini RGB 2100x1400 complete. Però non riesce a salvarle. Intanto la
nostra acquisizione diretta è ancora in collaudo (carrello, tamburo, bit
del buio).

Alessandro ha chiesto un terzo collegamento, oltre a "macchina vera" e
"simulato": lasciar scansionare il programma originale e prendere noi i
dati grezzi, da elaborare con la nostra pipeline.

Le strade considerate:

1. **Un osservatore esterno**: un debugger che si aggancia a
   `PhxScanner.exe` e copia i buffer delle immagini.
2. **Il driver**: EKframePnP copierebbe ogni DMA in un'area condivisa.
3. **Una DLL finta** al posto di `scn_dll.dll`, che passa le chiamate a
   quella vera e intanto copia.

## Decisione

La prima. `ponte/osservatore.py`, solo libreria standard, mette tre int3:

- `add_image_buffer`: indirizzi dei buffer;
- `remove_all_image_buffer`;
- il registro interno dell'SDK (`0x18002D42`), filtrato su tre messaggi:
  `xsize=`, `total scan size` e `xfer: image complete buffer=N ok=1`.

All'ultimo copia il buffer N in `<ponte>/mitm/`. filmscan, con il
collegamento "programma originale", li raccoglie nel rullo scelto.

Contro le altre due:

- il driver collaudato non si tocca per una funzione accessoria (regola 6 di
  `CLAUDE.md`);
- la DLL finta vuol dire sostituire un file dell'installazione originale, e
  rifare a mano più di 300 esportazioni C++.

## Conseguenze

- **Nessun file dell'originale cambia.** All'uscita gli int3 si tolgono e ci
  si stacca (`DebugActiveProcessStop`). `DebugSetProcessKillOnExit(False)`
  evita che una caduta dell'osservatore chiuda PhxScanner.
- **Gli indirizzi interni valgono solo per questa `scn_dll.dll`**
  (SHA-256 `2519fa07…54e8`). Con un'altra versione l'osservatore si
  rifiuta di agganciarsi.
- **Va avviato prima di aprire un ordine** nell'originale: i buffer si
  registrano lì.
- **Da verificare con le prime immagini:**
  - l'ordine dei piani: si assume R, G, B uno dopo l'altro;
  - che l'indice N del registro corrisponda all'ordine di registrazione
    dei buffer;
  - che i dati del DMA siano già utilizzabili, perché sono quelli prima
    delle correzioni dell'SDK.
- **Un fotogramma si può perdere** se due thread passano dal registro nello
  stesso istante: mentre uno fa il passo singolo, l'altro non vede l'int3.
  Il rischio è basso e si vede, perché manca un numero.
