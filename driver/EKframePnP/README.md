# EKframePnP — driver per la scheda di acquisizione IAQ

Driver WDM Plug and Play per la scheda **Kodak/Gretag IAQ**
(`PCI\VEN_11B2&DEV_0008&SUBSYS_908010B5`), per **Windows 7 a 32 bit**.
Sostituisce `EKframe.sys` (Eastman Kodak, 2000), che su Windows 7 non parte
più. Verso le applicazioni l'interfaccia è identica: stesso `\\.\EKFrameDev`,
stessi 13 IOCTL, stessi `ReadFile`/`WriteFile`, stessi 5 eventi. Così
`scn_dll.dll`, `DMLCmdTransfer.exe`, ScanDiag e GretagEMEval lo usano senza
accorgersi della differenza.

| | |
|---|---|
| Versione | **2.1** (25 ottobre 2026) |
| Stato | **funziona sulla macchina di riserva**: la scheda si avvia e il software la riconosce. Non si sa ancora se le immagini arrivano giuste (vedi [stato del progetto](../../documentazione/stato.md)). |
| Macchina di produzione | **non installarlo**: lì resta l'originale finché non c'è un pacchetto completo provato |
| Riferimento tecnico | [scheda-iaq.md](../../documentazione/hardware/scheda-iaq.md) |
| Decisione | [0002 — driver PnP sostitutivo](../../documentazione/decisioni/0002-driver-pnp-sostitutivo.md) |

## Cartelle

```
pacchetto/      quello che si copia sulla macchina: pronto, già compilato
  EKframePnP.sys / .inf          driver e file di installazione
  INSTALLA.bat                   installa e toglie sospensione/risparmio energia
  rimuovi-driver-vecchio.bat     toglie il vecchio ekframe.sys ("zombie")
  RIPRISTINA_Alimentazione.bat   annulla le modifiche di alimentazione
  ektest.exe                     collaudo (doppio clic: salva ektest-esito.txt)
  LEGGIMI.txt                    istruzioni per chi installa
sorgenti/       codice C del driver e di ektest, script di compilazione
verifica/       confronto con l'originale in emulazione
```

## Installare

Le istruzioni per chi sta davanti alla macchina sono in
[`pacchetto/LEGGIMI.txt`](pacchetto/LEGGIMI.txt). In breve:

1. copiare **tutta** la cartella `pacchetto/` sul PC (non lanciare da uno zip);
2. clic destro su `INSTALLA.bat` → *Esegui come amministratore*; all'avviso
   "Impossibile verificare l'autore" scegliere *Installa comunque*;
3. se `INSTALLA.bat` trova il vecchio `ekframe.sys` lancia
   `rimuovi-driver-vecchio.bat` e si ferma: **riavviare** e rilanciare
   `INSTALLA.bat`;
4. collaudo: `ektest`, `ektest arcnet` (scanner acceso), `ektest dma`;
5. prova vera: `DMLCmdTransfer 1`, poi filmscan.

> **Attenzione.** Non lanciare `ektest` con filmscan o l'agente aperti. Alla
> chiusura dell'handle il driver spegne interrupt e ARCNET, come faceva
> l'originale, e l'altro programma resta senza scheda.

## Compilare

Si compila su Linux con MinGW-w64: WDK e Visual Studio non servono.

```sh
apt-get install -y gcc-mingw-w64-i686
pip install pefile
cd sorgenti && sh build.sh            # produce EKframePnP.sys ed ektest.exe
python3 confronta.py EKframePnP.sys ../pacchetto/EKframePnP.sys
```

Opzioni da non togliere, e perché:

| Opzione | Motivo |
|---|---|
| `-march=i586 -mno-sse -mno-mmx` | la CPU della macchina non ha AVX; il binario resta a istruzioni Pentium di base (niente SSE/MMX/`cmov`) |
| `--entry,_DriverEntry@8`, `--subsystem,native` | è un driver kernel, non una DLL |
| `--image-base,0x10000` | come l'originale |
| `--exclude-all-symbols` | nessuna esportazione |
| `--no-insert-timestamp` | compilazione riproducibile: due compilazioni danno lo stesso file |
| flag `WDM_DRIVER` + checksum PE | li imposta `build.sh` dopo il link, con pefile |

`build.sh` produce sempre lo stesso file dagli stessi sorgenti.
`confronta.py` controlla che il binario del pacchetto venga da questi
sorgenti, ignorando data e checksum. Per la 2.1 il controllo è stato fatto il
26/10/2026 ed è risultato **uguale**.

Si compila anche con il WDK 7.1 (`sources` e `makefile`, *x86 Free Build
Environment*, `build -cZ`), ma la strada provata è quella di MinGW.

### Impronte del pacchetto 2.1

```
d881eafa5f8629945f9fa949a15117c889b1e3c1217b7c91b0ed7bd970caa8f0  EKframePnP.sys
e2d27fedef0ce295f651ede31715e38c0cc6bb0e64464f65076abcc88049a4fd  ektest.exe
```

## Sorgenti

| File | Contenuto |
|---|---|
| `ekframe.h` | definizioni, strutture, IOCTL (con `C_ASSERT` sui valori), prototipi |
| `driver.c` | `DriverEntry`, coda seriale delle richieste, alimentazione, chiusura |
| `pnp.c` | Plug and Play, risorse, adattatori DMA |
| `ioctl.c` | gestori delle richieste |
| `hw.c` | registri, ISR, DPC |
| `arcnet.c` | controller ARCNET COM20020 (canale comandi verso la testa) |
| `dma.c` | catene scatter/gather |
| `ektest.c` | strumento di collaudo |

Ogni affermazione sul protocollo ha nei commenti l'indirizzo della routine
corrispondente nell'originale (`0x1xxxx`). Chi cambia il comportamento deve
partire da lì e dal disassemblato, non da deduzioni.

## Nomi

- Servizio **`EKFrame`**, lo stesso nome dell'originale, perché
  `installa.bat` di filmscan controlla quel servizio.
- File `EKframePnP.sys`, per non confonderlo con il driver del 2000.
- Dispositivo `\Device\EKframe0`, link `\DosDevices\EKFrameDev`.
- Classe `MiniLabAdapter`, GUID `{533245A7-2867-11d5-BE3E-00902731AC5D}`, la
  stessa dell'originale.

`IOCTL_EK_DIAG` (`CTL_CODE(0x9C40, 0xA00, METHOD_BUFFERED, FILE_READ_ACCESS)`)
è in più rispetto all'originale. Restituisce lo stato interno (risorse, map
register, contatori) e sta fuori dallo spazio di codici usato dalle
applicazioni Kodak.

## Verifica di equivalenza

Il codice macchina dell'originale gira in emulazione (unicorn) accanto alla
traduzione in C, contro lo stesso modello simulato dell'hardware. Poi si
confrontano gli accessi ai registri uno per uno.

| Parte | Esito |
|---|---|
| ARCNET | 105 scenari su 105 identici |
| DMA | 7 su 7, comprese le catene della scansione piena 2100×1400×16 bit (1436 descrittori) |

Il banco si accorge anche della modifica di un singolo bit. Dettagli e
limiti attuali in [verifica/README.md](verifica/README.md).

## Differenze volute rispetto all'originale

Sono tutte correzioni di difetti dell'originale:

1. accesso ai registri a cavallo del limite della finestra;
2. `ReadFile` senza controllo della dimensione del buffer;
3. DMA lasciato attivo su pagine già sbloccate alla chiusura dell'handle;
4. troncamento nell'ultimo descrittore: l'originale lasciava la lunghezza
   sbagliata e toglieva il fine catena;
5. annullamento di `WriteFile` che bloccava la coda per sempre;
6. handle evento referenziato senza controllo di tipo e permessi;
7. anello dei messaggi sovrascritto quando pieno;
8. sospensione rifiutata mentre il dispositivo è aperto;
9. permessi del dispositivo aperti a tutti tramite l'INF (l'originale su Win7
   aveva la scrittura solo per gli amministratori).

## Limiti noti

- Il driver inoltra gli IRP di alimentazione ma al risveglio **non**
  riprogramma il PLX 9080. Per questo `INSTALLA.bat` disattiva la sospensione
  S3, l'ibernazione e lo spegnimento della scheda per risparmio energia.
- Non firmato: si carica solo su Windows a 32 bit.

## Se qualcosa non va

Mandare l'output completo di `ektest` (o il file `ektest-esito.txt`), con
filmscan chiuso.
