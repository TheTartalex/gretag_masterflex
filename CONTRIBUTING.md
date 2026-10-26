# Come si lavora su questo repository

## Rami

- `main` contiene solo cose provate o documentazione rivista.
- Il lavoro si fa su un ramo (`driver/...`, `filmscan/...`, `doc/...`, o
  quello assegnato alla sessione di Claude) e entra in `main` con una pull
  request.
- Mai riscrivere la storia di `main`.

## Messaggi di commit

In italiano, con il componente davanti:

```
driver: corregge il troncamento dell'ultimo descrittore
filmscan: limita i passi dell'autotest delle guide
doc: aggiorna lo stato dopo la prova di ScanDiag
strumenti: aggiunge raccogli-diagnosi.bat
```

Nel corpo va il **perché**. Se il commit dipende da una prova sulla
macchina, si scrive quale macchina e che esito ha dato.

## Versioni

| Componente | Dove sta la versione | Etichetta git |
|---|---|---|
| Driver EKframePnP | `DriverVer` nell'`.inf`, `EK_DRIVER_VERSION_STRING` in `ekframe.h` | `ekframepnp-v2.1` |
| filmscan | da decidere quando arriva il codice | `filmscan-vX.Y` |

Una versione nuova del driver va nel pacchetto solo dopo il collaudo sulla
riserva. Allora si aggiornano insieme: `.inf`, `ekframe.h`, il README del
driver (versione e impronte SHA-256), il [CHANGELOG](CHANGELOG.md), e si
mette l'etichetta.

## Prima di ogni push

- Driver: `sh build.sh` senza avvisi nuovi; se si è toccato `arcnet.c`,
  `dma.c` o `hw.c`, la verifica di equivalenza (105/105, 7/7).
- filmscan: `python selftest.py` (gli spostamenti dei canali **non** devono
  essere tutti zero) e, se possibile, `windows\collaudo.bat`.
- Documentazione: link controllati, `stato.md` aggiornato.

## Cosa non va mai nel repository

- File Kodak/Gretag: `EKframe.sys`, `scn_dll.dll`, eseguibili e DLL del
  programma originale, firmware (`.wrp`, `.rbf`, `.bin`), manuale di servizio.
- Log delle macchine (`C:\MasterflexD\ponte`, `SDKCacheDir`), esiti di
  `ektest` e delle diagnosi: si allegano alla sessione o a un problema, non
  si committano.
- Fotografie, grezzi, database degli ordini: dati dei clienti.
- Le wheel di `pacchetti/`: si rifanno con `scarica-offline.bat`.

## Problemi e richieste

Un problema (issue) su GitHub per ogni difetto osservato sulla macchina,
con macchina, data, cosa si è fatto, cosa si è visto, e l'output di
`ektest` o della diagnosi. Quando un problema è risolto si aggiorna
[stato.md](documentazione/stato.md).
