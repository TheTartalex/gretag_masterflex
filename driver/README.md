# Driver

Una cartella per ogni driver, con la stessa struttura:

```
<NomeDriver>/
  README.md     cos'è, stato, come si installa, come si compila
  pacchetto/    quello che si copia sulla macchina, pronto e collaudato
  sorgenti/     codice e script di compilazione
  verifica/     prove che non richiedono la macchina
```

| Driver | Per | Versione | Stato |
|---|---|---|---|
| [EKframePnP](EKframePnP/) | scheda di acquisizione IAQ (PCI, PLX 9080) | 2.1 | funziona sulla riserva; immagini da verificare |

Il pacchetto in `pacchetto/` è sempre quello **provato su una macchina**. Una
versione nuova si compila in `sorgenti/` e si copia in `pacchetto/` solo dopo
il collaudo. Allora si aggiornano la versione nell'`.inf` e nel README, le
impronte SHA-256 e il [CHANGELOG](../CHANGELOG.md).
