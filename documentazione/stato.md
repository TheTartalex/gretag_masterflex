# Stato del progetto

**Aggiornato al 26 ottobre 2026.** Questo è il documento da aggiornare a fine
di ogni sessione di lavoro: stato, problemi aperti e prossimi passi stanno
qui, e solo qui. Il piano a lungo termine sta in [piano-lavori.md](piano-lavori.md).

## Macchine

| Macchina | Ruolo | Note |
|---|---|---|
| Server di produzione | **non si tocca** finché non c'è un pacchetto completo provato | Win7 x86, driver originale `EKframe.sys` |
| Macchina di riserva | **banco di prova** | fisicamente identica alla produzione: server IBM del 2007, Win7 SP1 x86 (build 7601), 4 GB, CPU **senza AVX**, con la sua scheda IAQ e il suo scanner. Python 3.8 a 32 bit. Si comanda da AnyDesk. |
| PC di sviluppo | sviluppo | Windows a 64 bit, nessun WDK 7.1: il driver si compila su Linux (vedi [decisione 0004](decisioni/0004-compilazione-mingw-linux.md)) |

## Componenti

| Componente | Dove | Stato |
|---|---|---|
| Driver **EKframePnP 2.1** | [`driver/EKframePnP/`](../driver/EKframePnP/) | **funziona sulla riserva**: la scheda si avvia e il software la riconosce |
| Software **filmscan** (agente + interfaccia web) | [`software/`](../software/) | **non ancora nel repository**: sta sulla riserva, cartella `software` sul desktop |
| Correzione numpy (1.22.4) | `strumenti/` (da importare) | **risolta e verificata** sulla riserva |
| Raccolta diagnosi | `strumenti/` (da importare) | pronta, non ancora nel repository |

## Risolto di recente

- **La riserva non vedeva lo scanner dal 31/12/2025.** Su Windows 7 il
  vecchio `ekframe.sys` lascia un dispositivo "zombie" che blocca tutto.
  Rimedio: `rimuovi-driver-vecchio.bat` + riavvio, poi EKframePnP.
  Dettagli in [scheda-iaq.md §7](hardware/scheda-iaq.md#7-il-difetto-che-blocca-windows-7).
- **Crash di Python all'installazione** (`c000001d`). La causa era numpy
  1.24.4, che usa istruzioni AVX non disponibili su quella CPU. Soluzione:
  numpy 1.22.4. Vedi [decisione 0003](decisioni/0003-numpy-1.22.4-cpu-senza-avx.md).

## Problemi aperti, in ordine di priorità

1. **Motori delle guide.** L'autotest all'avvio di filmscan porta i motori a
   fine corsa e continua a spingere senza tornare indietro. **Rischio
   meccanico: non usare filmscan in modalità "macchina vera"** finché non è
   corretto. `avvia-simulazione.bat` si può usare.
   Sospetti: verso del movimento invertito, oppure un sensore di posizione
   letto sull'ingresso sbagliato, senza limite di tempo o di passi. La mappa
   ingressi/sensori e il segno e la scala del movimento fine erano già "da
   misurare" (piano 3.12, 3.13). La correzione minima è aggiungere limiti
   (passi o tempo massimo, stop su corrente o stallo se l'SDK li espone) e
   verificare il verso con un movimento breve. *Serve il codice di filmscan.*
2. **Le immagini non arrivano correttamente.** Il sintomo non è ancora
   descritto. La prova che distingue i due casi: ScanDiag e GretagEMEval sulla
   riserva. Se le immagini dei programmi Kodak sono giuste, il problema è in
   filmscan; se sono sbagliate anche lì, è nel driver (percorso DMA o ISR).
3. **Messaggio d'errore dell'agente.** `agente.py` stampa `ErrStat 0x...`,
   cioè l'indirizzo dell'oggetto, invece del suo contenuto. Va letto il codice
   d'errore dentro l'oggetto `ErrStat` dell'SDK.
4. Il codice di filmscan non è ancora stato riletto per cercare funzioni di
   numpy più recenti della 1.22. Sul campo lo controlla `collaudo.bat`.

## Materiale chiesto e non ancora arrivato

- [ ] la cartella `software` dal desktop della riserva → `software/`
- [ ] i log di `C:\MasterflexD\ponte` e `C:\MasterflexD\Scanner\SDKCacheDir`
      (non vanno nel repository: possono contenere dati dei clienti)
- [ ] una foto delle immagini difettose (nere, a righe, spostate, colori
      sbagliati, tagliate: ogni difetto indica una causa diversa)
- [ ] l'esito di ScanDiag e GretagEMEval sulla riserva
- [ ] l'output di `raccogli-diagnosi.bat` e di `ektest`, con filmscan chiuso
- [ ] dalla cartella di lavoro locale: `verifica-equivalenza/esegui.sh` con il
      banco di prova C ([dettagli](../driver/EKframePnP/verifica/README.md)),
      `diagnostica/raccogli-diagnosi.bat`, `numpy/correggi-numpy/` (script,
      senza le wheel), le analisi di agosto (`storico-agosto/`)

## Prossimi passi

1. Copiare in `software/` la cartella `software` della riserva: senza il
   codice di filmscan non si possono correggere i problemi 1–3.
2. Provare ScanDiag e GretagEMEval sulla riserva (problema 2).
3. Correggere l'autotest dei motori, con limiti di passi o di tempo, prima di
   qualunque prova con la macchina vera.

## Avvertenze operative

- Non lanciare `ektest` mentre filmscan o l'agente sono aperti: alla
  chiusura dell'handle il driver spegne la parte logica della scheda, come
  faceva l'originale.
- Sulla produzione, il giorno in cui si installerà EKframePnP, `INSTALLA.bat`
  si fermerà finché il vecchio `ekframe.sys` non è tolto. Serve un riavvio.
- Su quella CPU: numpy non oltre la 1.22.4, scipy fermo alla 1.9.1.
