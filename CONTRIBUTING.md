# Come si lavora su questo repository

## Rami

- `main` contiene solo codice pronto per la macchina e documentazione
  rivista. **Quello che va su `main` arriva alla macchina di riserva al suo
  prossimo avvio** (collegamento remoto, [decisione 0010](documentazione/decisioni/0010-collegamento-remoto.md)).
- Il lavoro si fa su un ramo (`driver/...`, `filmscan/...`, `doc/...`, o
  quello assegnato alla sessione di Claude) e entra in `main` quando è
  provato.
- Mai riscrivere la storia di `main`.

## Messaggi di commit

In italiano, con il componente davanti:

```
driver: corregge il troncamento dell'ultimo descrittore
filmscan: limita i passi dell'autotest delle guide
doc: aggiorna lo stato dopo la prova di ScanDiag
strumenti: l'installer porta anche openICE
```

Nel corpo va il **perché**. Se il commit dipende da una prova sulla
macchina, si scrive quale macchina e che esito ha dato.

## Versioni

| Componente | Dove sta la versione | Etichetta git |
|---|---|---|
| Driver EKframePnP | `DriverVer` nell'`.inf`, `EK_DRIVER_VERSION_STRING` in `ekframe.h` | `ekframepnp-v2.1` |
| filmscan | i commit di `main` (la macchina si aggiorna da lì); per gli installer distribuiti un'etichetta | `filmscan-vX.Y` |

Una versione nuova del driver va nel pacchetto solo dopo il collaudo sulla
riserva. Allora si aggiornano insieme: `.inf`, `ekframe.h`, il README del
driver (versione e impronte SHA-256), il [CHANGELOG](CHANGELOG.md), e si
mette l'etichetta.

## Prima di ogni push

- Driver: `sh build.sh` senza avvisi nuovi; se si è toccato `arcnet.c`,
  `dma.c` o `hw.c`, la verifica di equivalenza (105/105, 7/7).
- filmscan: `python selftest.py` (gli spostamenti dei canali **non** devono
  essere tutti zero) e, se si è toccato `hal.py`, `acquire.py` o
  `service.py`, un rullo con l'agente simulato (`software/README.md`).
- Documentazione: link controllati, `stato.md` aggiornato.

## Cosa non va mai nel repository

- File Kodak/Gretag: `EKframe.sys`, `scn_dll.dll`, eseguibili e DLL del
  programma originale, firmware (`.wrp`, `.rbf`, `.bin`), manuale di servizio.
- Log delle macchine (`C:\MasterflexD\ponte`, `SDKCacheDir`), esiti di
  `ektest` e delle diagnosi: si allegano alla sessione o a un problema, non
  si committano.
- Fotografie, grezzi, database degli ordini: dati dei clienti.
- Le wheel di `pacchetti/`: le scarica `strumenti/aggiornamento/prepara.sh`.

## Problemi e richieste

Un problema (issue) su GitHub per ogni difetto osservato sulla macchina,
con macchina, data, cosa si è fatto e cosa si è visto. I registri della
macchina (Macchina → Scarica i registri) non si allegano a un problema
pubblico: possono contenere nomi di clienti e percorsi. Quando un problema è risolto si aggiorna
[stato.md](documentazione/stato.md).
