# filmscan — software di acquisizione

Sostituisce l'interfaccia originale dello scanner: un **agente** che parla
all'SDK Kodak `scn_dll.dll`, e un'interfaccia web per il tablet con ordini,
archivio, pipeline colore ed export. Python 3.8 a 32 bit, Windows 7 x86, CPU
senza AVX.

**Si usa da un solo file: `MASTERFLEX.bat`** (doppio clic, menu). La guida
per chi sta alla macchina è
[guida-windows.md](../documentazione/filmscan/guida-windows.md).

## Struttura

```
MASTERFLEX.bat        il menu: avvio, collaudi, collegamento remoto, installa, avvio automatico
manutenzione.py       il lavoro dietro al menu (solo libreria standard)
processi.py           chiude i programmi originali (C:\MasterflexD) prima dell'agente
run.py                interfaccia web (python run.py --controllo = diagnosi ambiente)
selftest.py           collaudo della catena di elaborazione su scanner simulato
collaudo_auto.py      collaudi automatici: senza pellicola, con pellicola, IR, DX
collaudo_scanner.py   misure usate dai collaudi, e prove manuali da riga di comando
remoto.py             collegamento remoto: registri senza dati dei clienti, aggiornamenti da main
riavvia.py            riavvio di interfaccia e agente con la versione installata
elaboratore.py        elaboratore di rete (altri PC, Docker su Unraid)
grezzo_in_tiff.py     un grezzo in TIFF, per guardarlo fuori dal programma
requirements.txt      versioni bloccate con impronta, per cp38-win32
CLAUDE.md             regole del codice: leggere prima di modificare
certificati/          certificati radice di Mozilla per GitHub (Windows 7 non si aggiorna piu')
terzi/                openICE (GPL-3.0) e la licenza di nkscan
filmscan/
  hal.py              l'unico modulo che sa dell'hardware (MockBackend, PonteSdkBackend)
  acquire.py          calibrazione e acquisizione grezza
  pipeline.py         dal grezzo al file finale
  service.py          tutta la logica di scansione (un lavoro alla volta)
  webui.py            interfaccia web: traduce HTTP <-> service
  config.py           geometria, formati, preset, impostazioni
  rullo.py            equilibrio di rullo, classi di esposizione, memoria per pellicola
  pellicole_dx.py     dal codice DX alla pellicola (elenco I3A 2009)
  registration.py, invert.py, colore.py, enhance.py, framing.py, orient.py,
  geometria.py, bianconero.py, lut.py      elaborazione
  irclean.py, ice_aperto.py, ice_openice.py, ice_kodak.py, confronto_ice.py
                      Digital ICE: i cinque motori e il loro confronto
  orders.py, consegne.py, archivio_rete.py   archivio ordini, consegne, cartella di rete
  remoti.py           elaboratori in rete: coda, codice, risultati
  appunti.py          appunti di Windows e WhatsApp sul PC della macchina
  registri.py         la cartella log: programma, agente, SDK, collaudi, zip da mandare
  desktop.py          desktop di Windows nascosto dal programma originale
  originale.py        convivenza con il programma originale
  compat.py           controlli dell'ambiente (memoria, versioni)
ponte/
  agente.py           l'agente sul PC della macchina (solo libreria standard)
  sdk.py              collegamento a scn_dll.dll via ctypes
  dice.py             la libreria Digital ICE della Kodak, in un processo a parte
  osservatore.py      copia le immagini grezze del programma originale (voce 3)
```

Creati durante il lavoro e fuori da git: `ordini.db`, `impostazioni.json`,
`modo-avvio.txt`, `lut/`, `scansioni/`, `calibrazione/`, `collaudi/`,
`diagnosi/`, `backup/`, `pacchetti/`.

## Sviluppo e prove senza la macchina

Su Linux o su un PC a 64 bit le impronte di `requirements.txt` non
corrispondono, perché valgono per le wheel win32. Si installano le stesse
versioni senza impronte:

```sh
uv venv -p 3.8 .venv && . .venv/bin/activate
uv pip install numpy==1.22.4 scipy==1.9.1 Pillow==10.4.0 tifffile==2023.7.10 openpyxl==3.1.5
python selftest.py                      # spostamenti canali NON tutti a zero
python ponte/agente.py --condivisa /tmp/ponte --simula &
python run.py --driver sdk --ponte /tmp/ponte
python collaudo_scanner.py colori --ponte /tmp/ponte
```

## Problemi aperti su questo codice

Vedi [stato.md](../documentazione/stato.md). Il codice comanda i movimenti
con limiti, ma il nulling delle guide all'avvio lo fa il carrello, non
filmscan: vedi [carrello-capri.md](../documentazione/hardware/carrello-capri.md).
