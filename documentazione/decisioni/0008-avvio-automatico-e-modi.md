# 0008 — Avvio da amministratore, chiusura del programma originale, due modi di lavoro

- Data: 2026-10-26
- Stato: Proposta (scritto e provato fuori dalla macchina, non ancora sulla
  riserva)

## Contesto

Alessandro ha chiesto che all'accensione:

1. il programma parta già con i diritti di amministratore, senza la
   richiesta di Windows;
2. si chiudano da soli tutti i programmi Gretag e del programma originale,
   che si aprono da soli all'accensione e tengono scheda e carrello;
3. partano agente e interfaccia, e l'interfaccia si connetta appena lo
   scanner è pronto (stato 0 dell'SDK).

Poi ha aggiunto due cose:

- serve anche il **man in the middle**, dove i programmi originali servono
  tutti e il nostro agente non deve partire, altrimenti il programma
  originale non si collega allo scanner;
- il programma deve ripartire con l'ultimo modo usato.

## Decisione

- **Diritti.** Un'attività dell'Utilità di pianificazione, all'accesso
  dell'utente, "con i privilegi più elevati". La si crea da un XML
  (`manutenzione.py avvio on`), non con le opzioni di `schtasks`, che
  lascerebbero il limite di 3 giorni e la priorità "sotto il normale". Per
  crearla serve l'amministratore, una volta: lo script si rilancia da solo
  con la richiesta di Windows (`ShellExecute "runas"`).
- **Quali processi.** Tutti quelli con l'eseguibile sotto
  `C:\MasterflexD` (`processi.py`). Lì sta tutto il software Gretag e Kodak
  e nient'altro, quindi Windows, TeamViewer, AnyDesk, Chrome e il nostro
  Python sono fuori per costruzione, senza elenchi di eccezioni. Se il
  percorso non si legge, si riconosce dal nome, solo per i programmi
  originali noti. Si chiudono prima i padri, e si continua finché per
  qualche secondo non ne ricompare nessuno.
- **Quando.** Solo prima di avviare l'agente, mai dopo: l'SDK dell'agente
  usa a sua volta il GateControl di `C:\MasterflexD` per il carrello.
  `manutenzione.py termina` si rifiuta se l'agente è attivo.
- **Connessione.** `run.py --connetti`: l'interfaccia riprova finché
  l'agente non risponde con lo scanner pronto.
- **Modi.** Completo (chiude, avvia agente e interfaccia) e man in the
  middle (non chiude, non avvia l'agente, aspetta `PhxScanner.exe`, avvia
  osservatore e interfaccia sul collegamento "programma originale").
  L'ultimo usato sta in `software/modo-avvio.txt`.

## Conseguenze

- Il browser, all'accensione, parte con i diritti di amministratore come
  il resto. Chrome 109 e Firefox ESR lo accettano.
- Se l'attività viene tolta dall'Utilità di pianificazione a mano, il menu
  (voce 9) la dà come non attiva.
- Da provare sulla riserva: l'elenco di cosa viene chiuso (`python
  processi.py` lo mostra senza chiudere), e se il programma originale si
  riapre dopo la chiusura (lo dice il registro `avvio-AAAAMMGG.log`).
