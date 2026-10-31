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
MASTERFLEX.bat        il menu: installa, aggiorna, avvia, collaudi, diagnosi...
manutenzione.py       il lavoro dietro al menu (solo libreria standard)
processi.py           chiude i programmi originali (C:\MasterflexD) prima dell'agente
collaudo_scanner.py   collaudi dello scanner vero: colori, intensita', trascinamento
run.py                interfaccia web (python run.py --controllo = diagnosi ambiente)
selftest.py           collaudo della catena di elaborazione su scanner simulato
requirements.txt      versioni bloccate con impronta, per cp38-win32
CLAUDE.md             regole del codice: leggere prima di modificare
filmscan/
  hal.py              l'unico modulo che sa dell'hardware (MockBackend, PonteSdkBackend)
  acquire.py          calibrazione e acquisizione grezza
  pipeline.py         dal grezzo al file finale
  lut.py              LUT .cube, .lut (Discreet 1D), .3dl: lettura e applicazione
  service.py          tutta la logica di scansione (un lavoro alla volta)
  webui.py            interfaccia web: traduce HTTP <-> service
  config.py           geometria, formati, preset, impostazioni
  orders.py, consegne.py   archivio ordini, registro, consegne
  appunti.py          appunti di Windows e WhatsApp sul PC della macchina
  rullo.py            equilibrio di rullo, classi di esposizione, memoria per pellicola
  pellicole_dx.py     dal codice DX alla pellicola (elenco I3A 2009) e cosa aspettarsi
  bianconero.py       toni e viraggi del bianco e nero
  registration.py, invert.py, irclean.py, colore.py, enhance.py,
  framing.py, orient.py    elaborazione
  originale.py        convivenza con il programma originale
  compat.py           controlli dell'ambiente (memoria, versioni)
ponte/
  agente.py           l'agente sul PC della macchina (solo libreria standard)
  sdk.py              collegamento a scn_dll.dll via ctypes
  osservatore.py      copia le immagini grezze del programma originale (voce 14)
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
