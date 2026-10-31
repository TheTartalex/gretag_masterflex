# 0009 — La pellicola dal codice DX corregge il rullo da sola

- Data: 2026-10-31
- Stato: Proposta (scritto e provato col simulatore, non ancora sulla
  riserva: manca la conferma che il DX arrivi da `get_media_info`)

## Contesto

Alessandro ha chiesto di riconoscere la pellicola dal DX "non per
rafforzarla, ma per sapere già cosa aspettarsi" (tipo, ISO, grana,
densità), di scriverla nei metadati, di aggiornare da soli i dati
dell'ordine quando è diversa da quella scelta a mano, e di avvisare e
trattare a parte le strisce di pellicole diverse inserite nello stesso
rullo.

## Decisione

- **Fonte**: l'elenco I3A "DX Codes for 135-Size Film", gennaio 2009
  (ultima edizione). Solo i numeri che l'elenco assegna; le pellicole
  uscite dopo o mai dichiarate si dicono "non in elenco", con il
  fabbricante dalla parte 1. Nessun numero inventato.
- **Cosa cambia da solo**: il profilo di inversione quando il *tipo* è
  diverso (diapositiva invertita come negativo, IR su un B/N all'argento
  sono errori). Una scelta a mano "bianco e nero" su una pellicola a colori
  si rispetta: può essere il cliente a volerla così. Il cambio va in
  archivio, nella nota dell'ordine e fra gli avvisi del lavoro.
- **Cosa si usa come attesa**: pendenza della curva (0,60 C-41, 0,62 B/N
  all'argento, ~+0,09 per stop di forzatura sul B/N), grana dalla
  sensibilità effettiva e dalla tecnologia dei cristalli. Sono valori tipici
  di laboratorio: le statistiche del rullo restano quelle che misurano.
- **Strisce diverse**: un gruppo per pellicola (statistiche, memoria,
  esposizione rimisurata); stesso rullo in archivio, avviso e nota
  nell'ordine. Non si crea un rullo nuovo: il cliente ha consegnato un
  rullo, e dividere numeri e cartelle a metà lettura è più rischioso del
  beneficio.
- **Spegnibile**: `pellicola_da_dx = False` nelle impostazioni.

## Conseguenze

- Se il DX letto è sbagliato (posizione nella struttura non confermata),
  il rullo prenderebbe un profilo sbagliato: per questo `PonteSdkBackend`
  accetta solo risposte sensate e la sonda del collaudo (voce 6) va fatta
  prima di fidarsi.
- I rulli vecchi senza DX restano come sono.
