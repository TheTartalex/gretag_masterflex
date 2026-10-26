# -*- coding: utf-8 -*-
"""
HAL - Hardware Abstraction Layer.

Questo e' l'UNICO modulo che sa come si parla alla macchina. Tutto il resto del
programma vede solo l'interfaccia ScannerBackend. Quando avremo la mappa dei
registri della scheda di acquisizione, si scrive un nuovo backend e non si tocca
una riga del resto.

Tre implementazioni previste:

  MockBackend    - genera dati sintetici. Serve a sviluppare e collaudare tutta
                   la pipeline SENZA la macchina accesa. Da usare subito.
  BridgeBackend  - il PC d'epoca fa da ponte: esegue l'acquisizione e deposita i
                   grezzi in una cartella condivisa; noi li leggiamo. E' la strada
                   piu' rapida per arrivare a scansionare davvero.
  NativeBackend  - driver nostro che parla direttamente alla scheda. Da scrivere
                   quando avremo la documentazione. Lo scheletro e' predisposto.
"""

from __future__ import annotations

import abc
import glob
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np


CHANNELS = ("R", "G", "B", "IR")


# --------------------------------------------------------------------------

class ScannerError(RuntimeError):
    pass


@dataclass
class FilmSensors:
    """Lettura dei sensori di presenza pellicola a bordo macchina.

    La macchina ha sensori fisici in ingresso e in uscita del percorso di
    trascinamento. Sono un dato oggettivo e vanno preferiti sempre alla stima
    ottica dalla trasmittanza: quella e' un ripiego per i backend che non li
    espongono.

    Logica del ciclo automatico, come funziona sulla macchina:
      - pellicola inserita  -> sensore INGRESSO coperto -> parte la scansione
      - coda della striscia -> sensore INGRESSO libero  -> la pellicola sta
        finendo, ma NON e' finita: fra il sensore d'ingresso e la finestra di
        scansione c'e' ancora pellicola, e quei fotogrammi vanno letti
      - espulsione completa -> sensore USCITA libero
    """
    ingresso: bool = False       # coperto dalla pellicola
    uscita: bool = False
    disponibili: bool = False    # False se il backend non li espone

    # I sensori della macchina vera NON sono booleani: danno una tensione, che
    # il software originale confronta con una finestra di validita' (Ggc.ini:
    # frame detection 0.90-3.70 V, DX 3.20-4.10 V). Un sensore sporco non da' un
    # falso booleano, da' un valore fuori finestra - ed e' un'informazione utile
    # che va conservata invece di appiattirla su vero/falso.
    # Campi aggiunti in coda: i backend che non li riempiono restano validi.
    livello_ingresso: Optional[float] = None   # volt, se il backend li espone
    livello_uscita: Optional[float] = None
    fuori_finestra: bool = False               # True = sensore da pulire


@dataclass
class ScannerStatus:
    connected: bool = False
    film_loaded: bool = False
    position_mm: float = 0.0
    focus_mm: float = 0.0
    lamp_ready: bool = False
    message: str = ""


# --------------------------------------------------------------------------

class ScannerBackend(abc.ABC):
    """Interfaccia minima. Volutamente povera: meno superficie, meno da riscrivere."""

    # --- ciclo di vita ---
    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def disconnect(self) -> None: ...

    @abc.abstractmethod
    def status(self) -> ScannerStatus: ...

    # --- movimento ---
    @abc.abstractmethod
    def advance(self, mm: float) -> None:
        """Avanza la pellicola. Valori negativi = indietro (attenzione al backlash)."""

    @abc.abstractmethod
    def set_focus(self, mm: float) -> None:
        """Posiziona l'attuatore di fuoco in valore assoluto."""

    # --- acquisizione ---
    @abc.abstractmethod
    def capture_line_block(self, channel: str, lines: int,
                           exposure: float = 1.0) -> np.ndarray:
        """Acquisisce un blocco di righe per un singolo canale.

        Ritorna un array 2D (lines, sensor_pixels) in unita' GREZZE dell'ADC,
        senza alcuna correzione. Nessuna conversione, nessun clipping.
        """

    # --- opzionale ---
    def lamp(self, channel: str, on: bool = True) -> None:
        pass

    def home(self) -> None:
        pass

    # --- sensori ---
    def read_sensors(self) -> FilmSensors:
        """Stato dei sensori di presenza pellicola.

        L'implementazione di base dichiara che non sono disponibili, cosi' i
        livelli superiori sanno di dover ripiegare sulla stima ottica invece di
        credere a due valori falsi.
        """
        return FilmSensors(disponibili=False)

    # --- espulsione ---
    supports_eject: bool = False

    def eject(self) -> None:
        """Espelle la pellicola dal retro, se la macchina ha un comando dedicato.

        L'implementazione di base non fa nulla: chi non ha un comando di
        espulsione viene gestito a monte facendo avanzare il trasporto finche'
        il percorso ottico non risulta libero. Vedi service.espelli().

        Non si comanda un avanzamento fisso: con strisce di lunghezza variabile
        non si sa quanta pellicola resti, e sbagliare per eccesso significa
        continuare a tirare a vuoto un motore del 1995.
        """
        raise NotImplementedError("questo backend non ha un comando di espulsione")


# --------------------------------------------------------------------------

class MockBackend(ScannerBackend):
    """Scanner finto. Sintetizza un negativo plausibile: base arancione, immagine
    invertita, granulosita', qualche graffio visibile nel canale IR, disallineamento
    sub-pixel tra i canali e un po' di vignettatura.

    Serve a verificare che allineamento, inversione e pulizia IR facciano il loro
    lavoro prima ancora di toccare la macchina.
    """

    def __init__(self, sensor_pixels: int = 5600, dpi: int = 4000,
                 bit_depth: int = 16, seed: int = 7):
        self.sensor_pixels = sensor_pixels
        self.dpi = dpi
        self.full_scale = (1 << bit_depth) - 1
        self._rng = np.random.default_rng(seed)
        self._pos = 0.0
        self._focus = 0.0
        self._connected = False
        # disallineamento sub-pixel fisso per canale, come nella macchina vera
        self._offsets = {"R": 0.0, "G": 0.7, "B": -0.4, "IR": 1.3}
        self._scratches = self._rng.integers(0, sensor_pixels, size=6)
        # stato simulato: senza questi la calibrazione non avrebbe senso, perche'
        # dark e flat verrebbero identici alla scansione vera
        self._lamp_on = False
        self.film_present = True

        # Geometria simulata del percorso, in mm dal sensore d'ingresso.
        # I valori reali vanno ricavati dal manuale: vedi NOTE-MACCHINA.md.
        self.sensore_ingresso_mm = 0.0
        self.finestra_mm = 45.0        # sensore ingresso -> finestra di scansione
        self.sensore_uscita_mm = 95.0
        self.strip_length_mm = 190.0   # striscia da 5 fotogrammi circa
        self._dark_offset = 0.008          # corrente di buio + offset ADC
        self._field = None

    def connect(self) -> None:
        time.sleep(0.05)
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def status(self) -> ScannerStatus:
        return ScannerStatus(connected=self._connected, film_loaded=self._connected,
                             position_mm=self._pos, focus_mm=self._focus,
                             lamp_ready=self._connected, message="mock")

    def advance(self, mm: float) -> None:
        self._pos += mm

    def set_focus(self, mm: float) -> None:
        self._focus = mm

    def home(self) -> None:
        self._pos = 0.0

    def lamp(self, channel: str, on: bool = True) -> None:
        self._lamp_on = bool(on)

    supports_eject = True

    def read_sensors(self) -> FilmSensors:
        """Sensori simulati a partire dalla posizione della striscia.

        La striscia occupa il tratto fra la coda e la testa; un sensore risulta
        coperto se si trova dentro quel tratto. E' lo stesso modello della
        macchina vera, e serve a collaudare il ciclo automatico senza pellicola.
        """
        if not self.film_present:
            return FilmSensors(False, False, True)
        testa = self._pos
        coda = testa - self.strip_length_mm

        def coperto(c: float) -> bool:
            return coda <= c <= testa

        return FilmSensors(coperto(self.sensore_ingresso_mm),
                           coperto(self.sensore_uscita_mm), True)

    def eject(self) -> None:
        """Simula l'espulsione: la pellicola esce e il percorso resta libero."""
        self.film_present = False
        self._pos = 0.0

    # --- generazione della scena sintetica -----------------------------

    def _scene_field(self) -> np.ndarray:
        """Campo casuale liscio che fa da 'soggetto fotografato'.

        Si usa rumore filtrato invece di funzioni analitiche perche' i pattern
        periodici producono picchi di correlazione ambigui: l'allineamento dei
        canali sembrerebbe fallire quando in realta' e' il banco di prova a
        essere mal costruito. Un campo casuale ha un solo picco, quindi misura
        davvero cio' che vogliamo misurare.
        """
        if getattr(self, "_field", None) is not None:
            return self._field

        h, w = 2048, self.sensor_pixels
        noise = self._rng.normal(0, 1, (h, w))
        fy = np.fft.fftfreq(h)[:, None]
        fx = np.fft.fftfreq(w)[None, :]
        # spettro a legge di potenza: struttura a tutte le scale, come una scena reale
        radius = np.sqrt(fy ** 2 + fx ** 2)
        radius[0, 0] = 1e-6
        spectrum = np.fft.fft2(noise) / (radius ** 1.1)
        field = np.real(np.fft.ifft2(spectrum))
        field = (field - field.mean()) / (field.std() + 1e-9)
        self._field = np.clip(0.5 + 0.16 * field, 0.02, 0.98)
        return self._field

    def _scene_block(self, row0: int, lines: int, dx: float) -> np.ndarray:
        """Estrae un blocco di righe traslato di 'dx' pixel in orizzontale.

        La traslazione e' fatta nel dominio di Fourier, quindi e' esatta anche
        per valori frazionari: e' cosi' che si simula il disallineamento
        sub-pixel fra i quattro passaggi della macchina.
        """
        field = self._scene_field()
        h = field.shape[0]
        idx = (np.arange(row0, row0 + lines) % h)
        block = field[idx, :]
        if abs(dx) < 1e-9:
            return block
        fx = np.fft.fftfreq(block.shape[1])[None, :]
        shifted = np.fft.ifft(np.fft.fft(block, axis=1) *
                              np.exp(-2j * np.pi * fx * dx), axis=1)
        return np.clip(np.real(shifted), 0.02, 0.98)

    def capture_line_block(self, channel: str, lines: int,
                           exposure: float = 1.0) -> np.ndarray:
        if not self._connected:
            raise ScannerError("scanner non connesso")
        if channel not in CHANNELS:
            raise ScannerError("canale sconosciuto: %s" % channel)

        w = self.sensor_pixels

        # vignettatura dell'illuminazione: presente sempre, e' cio' che il flat
        # field deve misurare e correggere
        vign = 1.0 - 0.12 * ((np.arange(w) - w / 2) / (w / 2)) ** 2

        # lampada spenta -> solo corrente di buio: questo diventa il dark frame
        if not self._lamp_on:
            noise = self._rng.normal(0, 0.0008, (lines, w))
            raw = np.clip(self._dark_offset + noise, 0, 1) * self.full_scale
            return raw.astype(np.uint16 if self.full_scale > 255 else np.uint8)

        # lampada accesa senza pellicola -> solo vignettatura: e' il flat field
        if not self.film_present:
            signal = vign[None, :] * exposure
            signal = signal + self._dark_offset + self._rng.normal(0, 0.001, (lines, w))
            raw = np.clip(signal, 0, 1) * self.full_scale
            return raw.astype(np.uint16 if self.full_scale > 255 else np.uint8)

        row0 = int(round(self._pos / 25.4 * self.dpi))
        scene = self._scene_block(row0, lines, self._offsets[channel])

        # densita' del negativo: immagine invertita + maschera arancione per canale
        base = {"R": 0.16, "G": 0.42, "B": 0.72, "IR": 0.04}[channel]
        # l'IR non e' del tutto cieco all'immagine: un residuo di densita' dei
        # coloranti passa sempre, ed e' cio' che rende possibile allinearlo
        gamma = {"R": 0.60, "G": 0.62, "B": 0.65, "IR": 0.03}[channel]
        density = base + gamma * (1.0 - scene)

        transmit = 10.0 ** (-density)

        transmit = transmit * vign[None, :]

        # difetti fisici: opachi in tutti i canali, IR compreso
        for s in self._scratches:
            lo, hi = max(0, s - 1), min(w, s + 2)
            transmit[:, lo:hi] *= 0.25

        # acquisire consuma pellicola: la posizione avanza, come sulla macchina vera
        self._pos += lines * 25.4 / self.dpi

        signal = transmit * exposure + self._dark_offset
        signal = signal + self._rng.normal(0, 0.0015, signal.shape)  # rumore
        # esporre troppo satura, come sulla macchina vera: e' il motivo per cui
        # measure_channel_exposure punta all'80 per cento della scala e non al 100
        raw = np.clip(signal, 0, 1) * self.full_scale
        return raw.astype(np.uint16 if self.full_scale > 255 else np.uint8)


# --------------------------------------------------------------------------

class BridgeBackend(ScannerBackend):
    """SUPERATO - tenuto solo per chi avesse un ponte scritto a mano.

    Non e' il ponte verso lo scanner: quello e' PonteSdkBackend, driver "sdk".
    Questo qui parla un protocollo diverso (comandi in inglese: status,
    sensors, advance_N) che l'agente non conosce, e sceglierlo per sbaglio
    riempie la finestra dell'agente di "comando sconosciuto". Per questo non
    compare piu' nel menu dell'interfaccia.

    Il PC d'epoca acquisisce e scrive i grezzi in una cartella condivisa.

    Protocollo minimo, volutamente stupido, basato su file:

      <bridge>/cmd/     noi scriviamo i comandi   (advance_2.50, focus_0.030, grab_R_1200_1.0)
      <bridge>/ack/     il ponte conferma
      <bridge>/raw/     il ponte deposita i .npy o .raw acquisiti

    E' primitivo ma non richiede rete, driver o librerie sul lato antico: basta
    saper leggere e scrivere file su una share SMB. Da adattare a quello che il
    PC d'epoca sara' effettivamente in grado di fare.
    """

    def __init__(self, root: str, sensor_pixels: int = 5600, timeout: float = 60.0):
        self.root = root
        self.sensor_pixels = sensor_pixels
        self.timeout = timeout
        for sub in ("cmd", "ack", "raw"):
            os.makedirs(os.path.join(root, sub), exist_ok=True)
        self._pos = 0.0
        self._focus = 0.0

    # --- utilita' ---
    def _send(self, command: str) -> str:
        token = "%d_%s" % (int(time.time() * 1000), command)
        with open(os.path.join(self.root, "cmd", token), "w") as f:
            f.write(command)
        deadline = time.time() + self.timeout
        ack = os.path.join(self.root, "ack", token)
        while time.time() < deadline:
            if os.path.exists(ack):
                with open(ack) as f:
                    return f.read().strip()
            time.sleep(0.05)
        raise ScannerError("timeout sul comando: %s" % command)

    # --- interfaccia ---
    def connect(self) -> None:
        self._send("ping")

    def disconnect(self) -> None:
        pass

    def status(self) -> ScannerStatus:
        try:
            msg = self._send("status")
            return ScannerStatus(True, True, self._pos, self._focus, True, msg)
        except ScannerError as e:
            return ScannerStatus(False, message=str(e))

    def advance(self, mm: float) -> None:
        self._send("advance_%.4f" % mm)
        self._pos += mm

    def set_focus(self, mm: float) -> None:
        self._send("focus_%.4f" % mm)
        self._focus = mm

    def read_sensors(self) -> FilmSensors:
        try:
            r = self._send("sensors")            # atteso: "1,0" oppure "0,1"
            a, b = r.split(",")[:2]
            return FilmSensors(a.strip() == "1", b.strip() == "1", True)
        except (ScannerError, ValueError):
            return FilmSensors(disponibili=False)

    supports_eject = True

    def eject(self) -> None:
        self._send("eject")
        self._pos = 0.0

    def capture_line_block(self, channel: str, lines: int,
                           exposure: float = 1.0) -> np.ndarray:
        name = self._send("grab_%s_%d_%.3f" % (channel, lines, exposure))
        path = os.path.join(self.root, "raw", name)
        deadline = time.time() + self.timeout
        while time.time() < deadline and not os.path.exists(path):
            time.sleep(0.05)
        if not os.path.exists(path):
            raise ScannerError("il ponte non ha prodotto %s" % name)
        if path.endswith(".npy"):
            return np.load(path)
        data = np.fromfile(path, dtype="<u2")
        return data.reshape(-1, self.sensor_pixels)


# --------------------------------------------------------------------------

class PonteSdkBackend(ScannerBackend):
    """La macchina vera, comandata attraverso l'SDK originale.

    E' la strada scelta: della macchina originale teniamo i driver e i comandi
    - che funzionano, e che nessuno rifara' mai meglio - e buttiamo solo
    l'interfaccia del 2001 che salvava su floppy.

    Sul PC originale gira `ponte/agente.py`, che carica scn_dll.dll e obbedisce
    ai comandi che gli lasciamo in una cartella condivisa. Qui c'e' il lato
    nostro: stessa interfaccia ScannerBackend di sempre, quindi pipeline,
    ordini ed export non si accorgono di niente.

    Due differenze rispetto al Mock, che vanno sapute e non nascoste:

    1. **L'acquisizione e' per fotogrammi, non per righe.** L'SDK restituisce
       un'immagine intera alla risoluzione scelta (2100x1400 a piena). Quando
       la pipeline chiede N righe, questo backend acquisisce il fotogramma e ne
       restituisce le prime N. Per la calibrazione, che chiede blocchi piccoli,
       si usa una risoluzione ridotta: e' piu' veloce e serve allo stesso scopo.

    2. **I sensori non sono ancora mappati.** L'SDK li espone come ingressi
       digitali numerati (`diag_input`), ma quale numero sia il sensore
       d'ingresso e quale quello d'uscita si scopre solo sulla macchina,
       provandoli. Finche' `ingressi` non e' configurato, questo backend
       dichiara onestamente che i sensori non sono disponibili invece di
       inventarsi due booleani.
    """

    # canale -> risoluzione SDK usata per i blocchi piccoli di calibrazione
    RIS_PIENA = 0
    RIS_RIDOTTA = 2

    # Un comando alla meccanica risponde in un attimo o non risponde affatto:
    # aspettarlo tre minuti significa solo che il browser molla prima e
    # l'operatore resta a guardare una pagina bloccata. Un'acquisizione invece
    # dura davvero, e li' serve pazienza.
    ATTESA_COMANDO = 15.0
    ATTESA_ACQUISIZIONE = 180.0

    def __init__(self, root: str, timeout: float = ATTESA_ACQUISIZIONE,
                 risoluzione: int = 0, passo_mm: float = 0.01,
                 ingressi: Optional[Dict[str, int]] = None):
        self.root = root
        self.timeout = timeout
        self.risoluzione = risoluzione
        self.passo_mm = passo_mm
        # esempio, una volta scoperti: {"ingresso": 3, "uscita": 5}
        self.ingressi = ingressi or {}
        for sub in ("cmd", "ack", "raw"):
            os.makedirs(os.path.join(root, sub), exist_ok=True)
        self._pos = 0.0
        self._focus = 0.0
        self._connesso = False

    # --- protocollo -------------------------------------------------------

    def _send(self, comando: str, attesa: Optional[float] = None) -> str:
        token = "%d_%s" % (int(time.time() * 1000), comando.split("_")[0])
        percorso = os.path.join(self.root, "cmd", token)
        with open(percorso, "w") as f:
            f.write(comando)
        ack = os.path.join(self.root, "ack", token)
        scadenza = time.time() + (attesa if attesa is not None
                                  else self.ATTESA_COMANDO)
        while time.time() < scadenza:
            if os.path.exists(ack):
                with open(ack) as f:
                    risposta = f.read().strip()
                try:
                    os.remove(ack)
                except OSError:
                    pass
                if risposta.startswith("errore"):
                    raise ScannerError("l'agente ha risposto: %s" % risposta)
                return risposta[3:].strip() if risposta.startswith("ok") else risposta
            time.sleep(0.05)

        # Il comando non e' stato raccolto da nessuno: si toglie di mezzo,
        # altrimenti l'agente lo eseguirebbe quando riparte - e un motore che
        # si muove da solo mezz'ora dopo e' peggio del comando perso.
        try:
            os.remove(percorso)
        except OSError:
            pass
        raise ScannerError(
            "l'agente dello scanner non risponde (comando: %s).\n"
            "Controllare la finestra 'Agente scanner': se si e' chiusa, "
            "l'SDK non e' riuscito ad aprire la macchina." % comando)

    # --- ciclo di vita ----------------------------------------------------

    def connect(self) -> None:
        """Verifica che dall'altra parte ci sia qualcuno, e lo dice subito.

        Meglio saperlo qui, con la pellicola ancora in mano, che al primo
        comando dato con il rullo dentro.
        """
        try:
            self._send("ping", attesa=5.0)
        except ScannerError:
            raise ScannerError(
                "l'agente dello scanner non risponde su %s.\n"
                "Aprire la finestra 'Agente scanner': se non c'e', avviarla con "
                "avvia.bat; se si e' chiusa subito, l'SDK non ha trovato la "
                "scheda (vedi Scanner\\SDKCacheDir\\log_0000.txt)." % self.root)
        self._connesso = True

    def disconnect(self) -> None:
        self._connesso = False

    def status(self) -> ScannerStatus:
        try:
            msg = self._send("stato")
            return ScannerStatus(True, True, self._pos, self._focus, True, msg)
        except ScannerError as e:
            return ScannerStatus(False, message=str(e))

    # --- movimento --------------------------------------------------------

    def advance(self, mm: float) -> None:
        """Avanza in millimetri, convertiti in passi motore da 0,01 mm.

        Passa dalla regolazione fine del rilevamento fotogrammi, che e' la
        stessa che il programma di servizio usa nella Length Calibration.
        Segno e scala vanno verificati sulla macchina la prima volta.
        """
        passi = int(round(mm / self.passo_mm))
        if passi:
            self._send("regola_%d" % passi)
        self._pos += mm

    def avanza_fotogramma(self, mezzo: bool = False) -> None:
        self._send("avanza_mezzo" if mezzo else "avanza")

    def set_focus(self, mm: float) -> None:
        """L'SDK espone l'autofocus, non il posizionamento assoluto del fuoco.

        Finche' non sappiamo il passo dell'attuatore (vedi NOTE-MACCHINA.md)
        questa e' la cosa giusta da fare: chiedere alla macchina di mettere a
        fuoco da sola, che e' cio' che sa fare bene.
        """
        self._send("fuoco")
        self._focus = mm

    def home(self) -> None:
        pass

    # --- sensori ----------------------------------------------------------

    def read_sensors(self) -> FilmSensors:
        if not self.ingressi:
            return FilmSensors(disponibili=False)
        try:
            import json
            stato = json.loads(self._send("sensori"))
        except (ScannerError, ValueError):
            return FilmSensors(disponibili=False)
        ing = bool(stato.get(str(self.ingressi.get("ingresso")), False))
        usc = bool(stato.get(str(self.ingressi.get("uscita")), False))
        return FilmSensors(ing, usc, True)

    # --- acquisizione -----------------------------------------------------

    def capture_line_block(self, channel: str, lines: int,
                           exposure: float = 1.0) -> np.ndarray:
        if channel not in CHANNELS:
            raise ScannerError("canale sconosciuto: %s" % channel)
        # blocchi piccoli = calibrazione o misura: non serve la piena risoluzione
        ris = self.risoluzione if lines > 256 else self.RIS_RIDOTTA
        nome = self._send("grab_%s_%d_%.3f" % (channel, ris, exposure),
                          attesa=self.ATTESA_ACQUISIZIONE)
        percorso = os.path.join(self.root, "raw", nome)
        scadenza = time.time() + self.timeout
        while time.time() < scadenza and not os.path.exists(percorso):
            time.sleep(0.05)
        if not os.path.exists(percorso):
            raise ScannerError("l'agente non ha depositato %s" % nome)

        import json
        with open(percorso[:-4] + ".json") as f:
            meta = json.load(f)
        w, h = int(meta["larghezza"]), int(meta["altezza"])
        dati = np.fromfile(percorso, dtype="<u2").reshape(h, w)
        return dati[:lines] if lines < h else dati

    # --- espulsione -------------------------------------------------------

    supports_eject = False   # diventa True quando i sensori sono mappati

    def eject(self) -> None:
        raise NotImplementedError(
            "espulsione non ancora disponibile su questo backend: prima vanno "
            "mappati gli ingressi digitali dei sensori (vedi PONTE.md)")


# --------------------------------------------------------------------------

class NativeBackend(ScannerBackend):
    """Scheletro per il driver nativo.

    AGGIORNATO 3/9/2026 - dalla lettura del software originale sappiamo molto di
    piu' di quando questo scheletro e' stato scritto. Dossier completo con le
    fonti in documentazione/SCHEDA-ACQUISIZIONE.md.

    La macchina parla su DUE canali indipendenti, e la cosa cambia la struttura
    di questo backend: non e' una scheda sola.

    A) MECCANICA - seriale COM5 verso GateControl (trasporto, sensori, gate,
       fuoco, lettura DX/MOF). Protocollo da osservare con uno sniffer seriale.
       Alimenta advance(), set_focus(), read_sensors(), eject(), home().
       Stati della macchina, estratti dai simboli di GateControl.exe:
         Unloaded, Initialize, MediaInsert, Dx, FrameInPosition, Transport,
         Eject, AdjustFocus, ChangeFilmFormat, Mof, CleanMOF, Reset, Service,
         FatalError

    B) IMMAGINE - scheda PCI "IAQ Adapter for Kodak film scanners", Eastman
       Kodak. Alimenta solo capture_line_block().
         Hardware ID  PCI\VEN_11B2&DEV_0008&SUBSYS_908010B5&REV_00
         Chip         PLX PCI 9080  (dal subsystem 908010B5) - data book pubblico
         Registri     mem 0xDE000000 (256 B)  e  I/O 0xA800 (256 B), doppia
                      mappatura degli stessi registri: firma classica del 9080
         Bus locale   mem 0xDD800000, 2 MB - qui compaiono i dati del CCD
         IRQ 9 condivisa, DMA bus-master, due buffer
         Driver       EKframe.sys, servizio EKFrame, aperto come \\.\EKFrameDev
                      e comandato con DeviceIoControl
         SDK          scn_dll.dll, classi DMLScanner / DMLScannerAdv, con
                      scan_raw(canale, esposizione, risoluzione, ...) - proprio
                      la funzione che ci serve

    Ignoti, e sono i soli veri buchi: i codici IOCTL di EKframe.sys e il
    protocollo sui mailbox del 9080 verso l'EPLD Gretag.

    RACCOMANDAZIONE (SCHEDA-ACQUISIZIONE.md §8): riscrivere questo driver e'
    lavoro da mesi con rischio reale di non arrivare in fondo. Il PC originale,
    che il driver e l'SDK ce li ha gia', usato come motore di acquisizione via
    BridgeBackend, da' scansioni vere in settimane. Il valore del progetto non e'
    mai stato nel driver: e' nel salvare la piena risoluzione che il sensore gia'
    produce, e per farlo basta poter chiamare scan_raw.
    """

    def __init__(self, **kwargs):
        raise NotImplementedError(
            "Driver nativo non ancora implementato.\n"
            "Servono: mappa registri della scheda, modalita' di trasferimento "
            "(DMA o polling), protocollo di comando del motore.\n"
            "Nel frattempo usare MockBackend per sviluppare o BridgeBackend per "
            "acquisire davvero tramite il PC originale."
        )


# --------------------------------------------------------------------------

def make_backend(cfg) -> ScannerBackend:
    """Fabbrica: sceglie il backend in base alla configurazione."""
    if cfg.driver == "mock":
        return MockBackend(sensor_pixels=cfg.sensor_pixels, dpi=cfg.optical_dpi)
    if cfg.driver == "bridge":
        if not cfg.bridge_path:
            raise ScannerError("driver 'bridge' selezionato ma bridge_path e' vuoto")
        return BridgeBackend(cfg.bridge_path, sensor_pixels=cfg.sensor_pixels)
    if cfg.driver == "sdk":
        if not cfg.bridge_path:
            raise ScannerError("driver 'sdk' selezionato ma bridge_path e' vuoto")
        return PonteSdkBackend(cfg.bridge_path,
                               risoluzione=getattr(cfg, "sdk_resolution", 0),
                               passo_mm=cfg.transport_step_mm)
    if cfg.driver == "native":
        return NativeBackend()
    raise ScannerError("driver sconosciuto: %s" % cfg.driver)
