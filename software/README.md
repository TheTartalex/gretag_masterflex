# filmscan — software di acquisizione

Qui va il software che sostituisce l'interfaccia originale: un **agente** che
parla all'SDK Kodak `scn_dll.dll` e un'interfaccia web per il tablet, con
ordini, archivio, pipeline colore ed export. Python 3.8 a 32 bit, Windows 7
x86.

> **Il codice non è ancora nel repository.** Sta sulla macchina di riserva,
> cartella `software` sul desktop. Va copiato qui così com'è, poi controllato
> con `windows\collaudo.bat` e `python selftest.py`.

## Struttura attesa

Ricostruita dalla documentazione di settembre. Va corretta quando arriva il
codice vero.

```
software/
  CLAUDE.md            regole del codice: leggere prima di modificare
  requirements.txt     versioni bloccate per cp38-win32 (numpy 1.22.4!)
  run.py               avvio; "python run.py --controllo" controlla la macchina
  selftest.py          collaudo su scanner simulato
  config.py            geometria ottica, formati, distanze
  filmscan/            pacchetto principale
    hal.py             l'unico modulo che sa dell'hardware (PonteSdkBackend)
    acquire.py         acquisizione grezza
    pipeline.py        elaborazione: allineamento, IR, inversione, export
    service.py         tutta la logica di scansione
    webui.py           interfaccia web (solo traduzione HTTP <-> service)
    ui.py              interfaccia tkinter (debito: deve passare a service)
  ponte/
    sdk.py             collegamento a scn_dll.dll via ctypes
    agente.py          agente per il PC della macchina, senza dipendenze
  strumenti/
    originale.py       spegne stampa e densitometro nel programma originale
  windows/             .bat per installare, avviare, collaudare
```

Le wheel di `pacchetti/` **non** vanno nel repository (sono escluse da
`.gitignore`): si rifanno con `windows\scarica-offline.bat`.

## Documentazione

- [CLAUDE.md](CLAUDE.md) — architettura, trappole già incontrate, vincoli di
  dominio
- [guida-windows.md](../documentazione/filmscan/guida-windows.md) — uso
  quotidiano
- [installazione.md](../documentazione/filmscan/installazione.md) —
  installazione, preset, ciclo automatico
- [ponte.md](../documentazione/filmscan/ponte.md) — come l'agente comanda la
  macchina
- [archivio.md](../documentazione/filmscan/archivio.md) — ordini, consegna,
  conservazione

## Problemi aperti su questo codice

Vedi [stato.md](../documentazione/stato.md). In breve: l'autotest dei
motori va oltre il fine corsa (**non usare la modalità macchina vera**), le
immagini non arrivano giuste, e il messaggio d'errore dell'agente stampa un
puntatore al posto del codice d'errore.
