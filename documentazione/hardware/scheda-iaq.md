# Scheda IAQ (Kodak/Gretag) — riferimento tecnico

Ricostruito dal binario originale `EKframe.sys` (Eastman Kodak, 2000, 17.079
byte, ImageBase `0x10000`) e confermato in `DMLCmdTransfer.exe`. Gli indirizzi
`0x1xxxx` si riferiscono all'originale. Le parti ARCNET e DMA sono verificate
per equivalenza in emulazione (vedi
[`driver/EKframePnP/verifica/`](../../driver/EKframePnP/verifica/)).

**È il riferimento valido per la scheda.** Sostituisce le analisi di agosto,
che contengono errori, e corregge [scheda-acquisizione.md](scheda-acquisizione.md)
(settembre) dove i due documenti non coincidono.

*Aggiornato al 26 ottobre 2026.*

## 1. Identità e risorse

- PCI `VEN_11B2&DEV_0008&SUBSYS_908010B5` (vendor Gretag Imaging).
- Bridge PLX PCI 9080.

| Risorsa | Valore sulla macchina | Uso |
|---|---|---|
| Memoria 256 B | `0xDE000000` | registri operativi PLX ("spazio 0") |
| Memoria 2 MB | `0xDD800000` | finestra locale ("spazio 1") |
| I/O 256 B | `0xA800` | copia dei registri PLX, non usata |
| IRQ | 9, condiviso, a livello | |

L'originale distingue i due BAR di memoria dalla lunghezza (`0x1112A`):
oltre `0x100` byte è la finestra locale, altrimenti sono i registri PLX.

## 2. Registri PLX 9080 usati

| Offset | Registro | Valore / uso |
|---|---|---|
| `0x18` | LBRD0 | `0x40000043` |
| `0x68` | INTCSR | abilitazione: OR `0x00040800` (bit 11 interrupt locale, bit 18 interrupt DMA ch0); bit 21 = DMA ch0 attivo |
| `0x80` | DMAMODE0 | `0x00001F43` (32 bit, burst, chaining, interrupt di fine) |
| `0x90` | DMADPR0 | puntatore alla catena: `0xC0100000 + canale*0x40000` |
| `0xA8` | DMACSR0 | scritto come ULONG; bit0 abilita, bit1 avvia, bit2 annulla, bit3 azzera interrupt |

## 3. Finestra locale (2 MB, l'offset 0 sta a `0xC0000000` sul bus locale)

| Offset | Contenuto |
|---|---|
| `0x0000` | FIFO dei pixel (indirizzo locale `0xC0000000` nei descrittori) |
| `0x4004` | reset FIFO: scrivi 8, aspetta 1 µs, scrivi 0, rileggi offset 0 (`0x125E8`) |
| `0x4008` | maschera interrupt FPGA (6 = DMA + ARCNET dopo l'apertura, 0 = chiuso) |
| `0x400C` | stato interrupt, da filtrare con la maschera; bit1 DMA, bit2 ARCNET, bit3 errore FIFO |
| `0x4014` | modo: bit0 messaggi ARCNET in attesa, bit1 trasmissione ARCNET in corso |
| `0x8000` | controller ARCNET SMC COM20020, registri byte a passo 4 |
| `0x100000` | descrittori DMA del canale 0 (256 KB = 16.384 descrittori) |
| `0x140000` | descrittori DMA del canale 1 |

## 4. ARCNET (COM20020) — il canale comandi

**Nodi.** Il PC è il nodo `0x55` e la testa di scansione il nodo `0xAA`.
I valori arrivano con l'IOCTL di apertura.

**Registri COM20020:**

| Registro | Lettura | Scrittura |
|---|---|---|
| 0 | stato | maschera interrupt |
| 1 | stato diagnostico | comando |
| 2 | puntatore indirizzo alto (bit7 lettura, bit6 autoincremento) | idem |
| 3 | puntatore indirizzo basso | idem |
| 4 | dato | dato |
| 6 | configurazione (bit7 reset, bit5 TXEN, bit0–1 selezione sottoregistro) | idem |
| 7 | sottoregistro: 1 = ID nodo, 3 = ID nodo successivo | idem |

Dopo ogni accesso c'è un'attesa di 2 µs.

**Inizializzazione** (`0x102C0`):
1. reset;
2. controllo della firma: `0xD1` all'indirizzo 0 e l'ID nodo all'indirizzo 1;
3. test della RAM di 2 KB con gli schemi `0xAA`, `0x55`, `0x00`;
4. ricezione abilitata nella pagina 0 (comando `0x84`), maschera interrupt
   `0x86`.

**Pacchetti** in formato ARCNET standard:
- corti fino a 253 byte;
- lunghi da 257 a 507 byte;
- le lunghezze 254–256 vengono portate a 257 con riempimento a zero;
- trasmissione dalla pagina 1 (`0x200`), comando `0x0B`.

**Parola di stato** (`0x10658`): `((reg0|0x40)<<8) | reg1`, con il bit
`0x2000` forzato se la pagina di ricezione è piena. Poi viene scritto il
comando `0x1E`.

| Bit | Significato | Azione |
|---|---|---|
| `0x0002` | rete pronta | evento 0 |
| `0x0400` | riconfigurazione | evento 1 |
| `0x8000` / `0x2000` | messaggio ricevuto | anello, evento 2 |
| `0x0100` | trasmissione conclusa | `0x0200` indica la conferma dello scanner |

## 5. Interfaccia verso le applicazioni

Dispositivo `\\.\EKFrameDev` (`\Device\EKframe0`), device type `0x9C40`,
`DO_BUFFERED_IO`.

| IOCTL | Nome | Buffer | In coda? | Cosa fa |
|---|---|---|---|---|
| `9C406400` | GET_REGISTER | 12 B `{spazio, offset, valore}` | sì | legge un ULONG; spazio 0 = PLX, 1 = locale |
| `9C40A404` | SET_REGISTER | 12 B | sì | scrive un ULONG |
| `9C406408` | GET_PCI_INFO | 44 B | sì | DeviceId (prima di VendorId!), classe, IRQ, BAR... |
| `9C40E40C` | OPEN | 16 B `{reinit, nodo, destinazione, stato}` | sì | programma il PLX, abilita gli interrupt (maschera 6), inizializza ARCNET; stato bit0 = ARCNET fallito |
| `9C40A410` | REGISTER_EVENT | 8 B `{indice<5, HANDLE}` | no | registra un evento utente |
| `9C40A414` | RELEASE_EVENT | 8 B `{indice}` | no | |
| `9C40A418` | SIGNAL_EVENT | 8 B `{indice}` | sì | segnala l'evento |
| `9C40A421` | DMA_ARM_0 | MDL (IN_DIRECT) | no, resta pendente | aggancia il buffer immagine al canale 0 |
| `9C40A425` | DMA_ARM_1 | MDL | no, resta pendente | canale 1 |
| `9C40A430` | DMA_RELEASE_0 | — | sì | sgancia il buffer e completa l'IRP di aggancio |
| `9C40A434` | DMA_RELEASE_1 | — | sì | |
| `9C40A440` | DMA_START | 8 B `{canale, byte}` | sì | tronca la catena a quella lunghezza, reset FIFO, avvio |
| `9C40A444` | DMA_STOP | 8 B `{canale, uscita}` | sì | ferma il DMA; dà errore se non è attivo |

**ReadFile** legge il messaggio ARCNET più vecchio dall'anello di 64 voci,
e riceve errore se l'anello è vuoto. I messaggi entrano nell'anello solo se
l'evento 2 è registrato.

**WriteFile** spedisce un messaggio allo scanner. Resta pendente finché lo
scanner non conferma; senza conferma riceve `STATUS_IO_DEVICE_ERROR`.

**Eventi:**

| Indice | Significato |
|---|---|
| 0 | rete ARCNET pronta |
| 1 | rete persa |
| 2 | messaggio arrivato |
| 3 | DMA concluso |
| 4 | previsto, mai segnalato |

## 6. DMA

- Due canali logici, usati a turno sul canale DMA 0 del PLX. Dimensione
  massima del buffer: `0x3105560` byte (circa 49 MB).
- Descrittori nella memoria della scheda, 16 byte ciascuno:

```
+0x00  indirizzo fisico PCI
+0x04  0xC0000000 (FIFO)
+0x08  byte (al massimo 4 MB per descrittore)
+0x0C  indirizzo locale del descrittore successivo | flag
```

- Flag sui descrittori intermedi: `0x8` (direzione locale → PCI). Flag
  sull'ultimo: `0xE` (fine catena, interrupt, direzione).
- Sequenza tipica: aggancio (una volta sola) → per ogni trasferimento
  START(lunghezza) → attesa dell'evento 3 → eventuale STOP → alla fine
  RELEASE.

## 7. Il difetto che blocca Windows 7

Il vecchio `EKframe.sys`:

1. crea il dispositivo e il link (`0x1104A`);
2. chiama `HalAssignSlotResources` (`0x110C4`), che su Windows 7 fallisce;
3. esce dal ramo d'errore (`0x110D3`) **senza cancellare** dispositivo e link;
4. avrebbe registrato i gestori IRP solo più avanti (`0x11283`).

Risultato: un dispositivo zombie che risponde "funzione non valida" (errore
1 in apertura) e occupa il nome, per cui il driver nuovo va in Codice 31.
Rimedio: togliere il servizio e il file vecchi, poi riavviare.
