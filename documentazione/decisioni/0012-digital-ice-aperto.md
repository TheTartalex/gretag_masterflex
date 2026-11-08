# 0012 — Digital ICE: openICE, nkscan e la versione nostra

- Data: 2026-11-08
- Stato: Proposta (provato sul banco di prova con difetti noti; manca il
  confronto su un negativo vero: collaudo IR, MASTERFLEX.bat → 4 → 3)

## Contesto

Il Digital ICE della Kodak (DMLDICELib.dll) gira solo sul PC della
macchina, a 32 bit, e non si può dare agli elaboratori della rete
(licenza, regola 4). Nel rullo del 7/11 non ha mai funzionato davvero, e
la nostra pulizia di allora (soglia sull'IR e diffusione) era semplice.
Esistono due ricostruzioni aperte del Digital ICE di Nikon Scan 4
(Applied Science Fiction): openICE (C/C#, GPL-3.0, bit per bit) e nkscan
(Rust, MIT/Apache-2.0, lo stesso algoritmo).

## Decisione

- **openICE integrato com'è**: i sorgenti C in `software/terzi/openice/`
  con la licenza, compilati per Windows a 32 bit (macchina, PC Windows) e
  per Linux (Unraid), con `-m32 -mfpmath=387` come chiede openICE per il
  risultato bit per bit. Motore "openice" (`ice_openice.py`).
- **nkscan tradotto in numpy** (`ice_aperto.py`, motore "aperto"): stessi
  passi e costanti. Sul banco rende come openICE.
- **La versione nostra** (motore "filmscan", di serie): la stessa base,
  più quello che sul banco ha aiutato davvero:
  1. l'IR allineato al rosso prima del confronto;
  2. i difetti densi (oltre 0,30 D di perdita nell'IR, cioè polvere quasi
     opaca, dove sotto non è rimasta luce da recuperare) ricostruiti dai
     pixel attorno, con metà della grana misurata intorno.
  Scartate perché non aiutavano o peggioravano: aloni chiari come difetto,
  minimo su 3×3, ripiego dove ICE rinuncia, grana al posto del rumore di
  Nikon.
- I tre motori girano **nella pipeline, dopo l'allineamento dei canali**
  (i canali si leggono uno per volta), quindi anche sugli elaboratori, con
  lo stesso risultato. Quello della Kodak resta sulla macchina, e se non
  riesce si passa a "filmscan". Se un motore non riesce (IR saturo, oltre
  il 15% dell'immagine cambiata) resta la pulizia semplice.

## Banco di prova (8/11/2026)

Fotogramma vero (campione 10), 1,3% di pixel con polvere, peli e graffio
aggiunti con attenuazione uguale nel visibile e nell'IR, IR con il passaggio
del rosso (c = 0,06), sfocato e spostato. Errore sui difetti visibili
(oltre 0,1 D):

| motore | errore medio | errori oltre 0,15 D |
|---|---|---|
| openICE | 0,0516 D | 2,36% |
| aperto (nkscan in numpy) | 0,0515 D | 2,26% |
| **filmscan** | **0,0487 D** | **1,70%** |

Il banco è sintetico: la scelta vera la fa il confronto affiancato su un
negativo della macchina (`confronto_ice.png` del collaudo IR, o
`python -m filmscan.confronto_ice <grezzo.npz>`).

## Conseguenze

- openICE è GPL: i sorgenti stanno accanto ai binari e vanno con loro.
  Finché il programma non si distribuisce fuori dal negozio non cambia
  niente; se un giorno si distribuisse, il programma andrebbe dato con i
  sorgenti.
- Il Digital ICE non è più un collo di bottiglia della macchina quando ci
  sono gli elaboratori.
