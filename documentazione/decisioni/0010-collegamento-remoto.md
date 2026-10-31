# 0010 — Collegamento remoto attraverso GitHub

- Data: 2026-10-31
- Stato: Proposta (scritto e provato fuori dalla macchina: controllo della
  versione e scarico dello zip contro GitHub vero, installazione su una
  copia; manca la prova sulla riserva)

## Contesto

Fra la riserva (Windows 7 a 32 bit, in rete) e chi la segue, registri e
pacchetti viaggiavano su una chiavetta, attraverso AnyDesk. Alessandro ha
chiesto che la macchina salvi da sola i registri in un posto da cui si
possano leggere, e che gli aggiornamenti arrivino da soli.

## Decisione

- **Un solo canale, GitHub**, con la libreria standard di Python 3.8:
  nessun programma in più su Windows 7, e GitHub è dove il codice sta già.
- **Registri in un repository privato a parte**
  (`gretag_masterflex_registri`), mai in quello del codice (regola 4). Prima
  di partire si tolgono nomi, telefoni ed email dei clienti e il PIN: per
  capire la macchina non servono.
- **Aggiornamenti dal ramo `main`** del repository del codice, che è
  pubblico: per scaricarli non serve il token. Su `main` va solo il codice
  pronto per la macchina, come prima i pacchetti.
- **Quando si installa**: all'avvio automatico da solo (configurabile),
  oppure dal tasto dell'interfaccia; mai durante un lavoro. Il codice
  nuovo vale dal riavvio di MASTERFLEX.
- **Token** fine-grained con il solo permesso di scrittura sul repository
  dei registri, salvato in `software/remoto.json` sulla macchina e mai nel
  repository.

## Conseguenze

- Un push sbagliato su `main` arriva alla riserva al prossimo avvio. Il
  backup del codice in `software/backup/codice-<data>` resta la strada per
  tornare indietro.
- Sulla produzione il collegamento si configura solo dopo che la riserva
  l'ha provato (regola 2), eventualmente con un ramo suo.
- Windows 7 potrebbe non avere i certificati radice aggiornati: se GitHub
  non risponde per il certificato, il messaggio lo dice.
