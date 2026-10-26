# 0005 — Un solo script, MASTERFLEX.bat, per tutto quello che si fa sulla macchina

- Data: 2026-10-26
- Stato: Accettata

## Contesto

A settembre filmscan aveva dodici `.bat` in `windows\` (installa, avvia,
avvia-web, avvia-simulazione, collaudo, finestra, installa-offline,
scarica-offline, avvio-automatico, originale, _python...), più gli script
separati per correggere numpy e raccogliere la diagnosi. Sulla riserva:

- `installa.bat` installava numpy 1.24.4, che fa cadere Python sulle CPU
  senza AVX ([0003](0003-numpy-1.22.4-cpu-senza-avx.md));
- il collegamento di avvio automatico puntava a `windows\avvia.bat`;
- Python andava scaricato e installato a mano.

## Decisione

Un solo file da lanciare, `software\MASTERFLEX.bat`, con un menu e gli
stessi comandi anche da riga di comando (per l'avvio automatico). In batch
resta solo quello che serve prima che Python esista: trovare o installare
Python 3.8.10 a 32 bit, con l'impronta controllata. Tutto il resto sta in
`manutenzione.py`, scritto con la sola libreria standard perché deve
funzionare anche con le librerie sbagliate o assenti.

Le versioni delle librerie sono bloccate in `requirements.txt` con le
impronte SHA-256 delle wheel cp38-win32: pip installa quei file o si ferma.

## Conseguenze

- Installare, aggiornare, correggere numpy, preparare il pacchetto senza
  rete, diagnosi e avvio automatico sono voci dello stesso menu.
- L'aggiornamento sostituisce solo il codice (elenco `CODICE` in
  `manutenzione.py`) e non tocca ordini, scansioni, calibrazioni e
  impostazioni. Il codice vecchio va in `backup\`.
- Un aggiornamento da una versione di settembre toglie `windows\`,
  `strumenti\`, `filmscan\ui.py` e il vecchio collegamento di avvio.
- L'interfaccia tkinter (`ui.py`) non c'è più: l'interfaccia web si apre
  anche sul monitor della macchina.
