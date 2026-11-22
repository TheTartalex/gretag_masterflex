# Strumenti

Script di servizio che non appartengono né al driver né a filmscan. Una
sottocartella per strumento, ognuna con il suo `LEGGIMI.txt` per chi lo lancia
sulla macchina.

| Strumento | Cosa fa |
|---|---|
| [`aggiornamento/`](aggiornamento/) | prepara l'installer per la macchina (`prepara.sh`), con l'`INSTALLA.bat` che si lancia sul posto: installa su una macchina pulita o aggiorna quella che c'è |

Per preparare l'installer, dal repository e con tutto committato:

```sh
sh strumenti/aggiornamento/prepara.sh dist             # con Python e librerie (~75 MB)
sh strumenti/aggiornamento/prepara.sh dist --leggero   # solo il programma
```

Python e librerie si tengono in `dist/cache-pacchetti` e si scaricano solo
la prima volta.

Dopo l'installazione la macchina si aggiorna da sola dal ramo `main` di
questo repository, se il collegamento remoto è configurato (voce 6 di
`MASTERFLEX.bat`, [decisione 0010](../documentazione/decisioni/0010-collegamento-remoto.md)).

Gli strumenti originali Gretag del carrello (Compad, SensorTest,
SelfTestInterface) restano nel pacchetto proprietario `CapriFirmware`, fuori
dal repository: `MASTERFLEX.bat` li lancia dalla voce 4 → 5.

I registri delle macchine non vanno in questo repository: contengono
percorsi e nomi delle macchine. Il collegamento remoto li manda a un
repository privato a parte.
