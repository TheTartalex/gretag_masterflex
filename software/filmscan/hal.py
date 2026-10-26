# -*- coding: utf-8 -*-
"""
HAL - Hardware Abstraction Layer.

Questo e' l'UNICO modulo che sa come si parla alla macchina. Tutto il resto del
programma vede solo l'interfaccia ScannerBackend. Quando avremo la mappa dei
registri della scheda di acquisizione, si scrive un nuovo backend e non si tocca
una riga del resto.

Due implementazioni:

  MockBackend      - genera dati sintetici. Serve a sviluppare e collaudare
                     tutta la pipeline SENZA la macchina accesa.
  PonteSdkBackend  - la macchina vera, attraverso ponte/agente.py e l'SDK
                     originale (driver "sdk").

BridgeBackend (protocollo in inglese che l'agente non capiva) e lo scheletro
NativeBackend sono stati tolti a ottobre 2026: il primo era gia' segnato come
superato, il secondo non serve piu' da quando la scheda ha il suo driver
nuovo sotto lo stesso SDK (vedi documentazione/decisioni/0002).
"""

from __future__ import annotations

import abc
import glob
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np


CHANNELS = ("R", "G", "B", "IR")


# --------------------------------------------------------------------------

class ScannerError(RuntimeError):
    pass


# Il file dello STOP EMERGENZA, nella cartella del ponte. Lo scrive
# ScanService.emergenza(), lo leggono il ponte e l'agente, lo toglie solo il
# tecnico (ScanService.sblocca_emergenza). Sta su disco apposta: resta anche
# se si riavvia tutto.
FILE_EMERGENZA = "EMERGENZA.json"


class Emergenza(ScannerError):
    """STOP EMERGENZA attivo: nessun comando passa finche' il tecnico non da'
    l'ok."""


class PressoreBloccato(ScannerError):
    """Il pressore non si e' rialzato: la pellicola non si muove piu'."""


class MotoriBloccati(ScannerError):
    """L'agente e' partito con --senza-motori e rifiuta i movimenti."""


class FinePellicola(ScannerError):
    """Il carrello non trova un altro fotogramma: la striscia e' finita.
    Non e' un guasto, e chi scansiona un rullo non deve leggerlo come tale."""


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
    # False = il backend non sa spegnere la lampada. La calibrazione allora non
    # puo' leggere un dark frame vero e non deve fingere di averlo fatto: con
    # l'SDK "lampada spenta" e "lampada accesa" davano lo stesso fotogramma,
    # flat - dark veniva quasi zero e la divisione per il flat rovinava ogni
    # immagine.
    controlla_lampada: bool = True

    # Canali che il backend sa acquisire davvero. L'IR sulla macchina vera
    # passa da Digital ICE e il suo numero di canale nell'SDK non e' verificato.
    canali_disponibili = CHANNELS

    def lamp(self, channel: str, on: bool = True) -> None:
        pass

    def seleziona_formato(self, chiave_formato: str) -> None:
        """Dice alla macchina che formato c'e' nel carrello. Di serie, niente."""

    def prepara_carico(self, chiave_formato: str) -> None:
        """Porta il carrello al formato e lo mette in attesa della pellicola.
        Di serie, niente: il simulato ha sempre la pellicola dentro."""
        self.seleziona_formato(chiave_formato)

    def prossimo_fotogramma(self, fmt) -> None:
        """Porta in finestra il fotogramma successivo.

        Di serie avanza del passo del formato. La macchina vera ha un comando
        suo, che sa il passo meglio di noi: vedi PonteSdkBackend.
        """
        self.advance(fmt.pitch)

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

    # --- esposizione, pressore, Digital ICE, temperatura ---
    # Di serie non ci sono: chi li ha (la macchina vera) li ridefinisce.
    supports_pressore: bool = False

    def esposizione_auto(self, auto: bool) -> bool:
        """Base dell'esposizione: True = tempi calibrati della macchina,
        False = valori di fabbrica. False se il backend non distingue."""
        return False

    def calibra_macchina(self) -> bool:
        """La calibrazione della macchina stessa (LED, tempi). False se non
        c'e'."""
        return False

    def pressore(self, giu: bool) -> None:
        pass

    def digital_ice(self, acceso: bool) -> bool:
        return False

    def temperatura(self) -> Optional[Dict[str, float]]:
        return None

    def tensioni(self) -> Optional[List[float]]:
        return None

    def numero_bordo(self) -> Optional[str]:
        """Il numero stampato sul bordo del fotogramma in finestra, se la
        macchina lo sa leggere. Mai bloccante: None se non si sa."""
        return None

    def anteprima_veloce(self) -> np.ndarray:
        """Un'immagine piccola e veloce del verde, per il posizionamento a
        mano. Di serie: un blocco del canale G ridotto."""
        blocco = self.capture_line_block("G", 600).astype(np.float32)
        return blocco[::4, ::8]

    # --- azzeramento ---
    def azzera_motori(self) -> bool:
        """Porta a zero i motori della macchina. False se il backend non ha
        motori da azzerare (simulatore, programma originale)."""
        return False

    def eject(self, verso: str = "davanti") -> None:
        """Espelle la pellicola, se la macchina ha un comando dedicato.
        'verso' e' "davanti" (da dove e' entrata) o "retro".

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
        # I valori reali vanno ricavati dal manuale: vedi documentazione/hardware/note-macchina.md.
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

    def eject(self, verso: str = "davanti") -> None:
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

class PonteSdkBackend(ScannerBackend):
    """La macchina vera, comandata attraverso l'SDK originale.

    Sul PC della macchina gira `ponte/agente.py`, che carica scn_dll.dll e
    obbedisce ai comandi che gli lasciamo in una cartella. Qui c'e' il lato
    nostro: stessa interfaccia ScannerBackend di sempre, quindi pipeline,
    ordini ed export non si accorgono di niente.

    Cose da sapere, e non da nascondere:

    1. **L'acquisizione e' per fotogrammi, non per righe.** L'SDK restituisce
       un'immagine intera (2100x1400 a piena risoluzione). Si acquisisce
       SEMPRE alla stessa risoluzione: prima i blocchi piccoli della
       calibrazione si leggevano a 524 pixel e le scansioni a 2100, e dopo
       ogni calibrazione tutte le scansioni fallivano con "la calibrazione e'
       larga 524 pixel".

    2. **Niente controllo della lampada.** L'SDK non la espone: la
       calibrazione legge solo il flat (vedi controlla_lampada).

    3. **I movimenti grandi passano dai comandi della macchina.** Per il
       fotogramma successivo si usa "avanza" (advance_one_frame dell'SDK),
       che conosce il passo del formato. La regolazione fine (regola_N, cioe'
       frame_detect_fine_adjust) resta per i ritocchi di pochi millimetri:
       prima filmscan la usava per 38 mm alla volta.

    4. **I sensori non sono ancora mappati** e l'IR non e' verificato: finche'
       non lo sono, il backend lo dichiara invece di inventare.
    """

    # Un comando alla meccanica risponde in un attimo o non risponde affatto:
    # aspettarlo tre minuti significa solo che il browser molla prima e
    # l'operatore resta a guardare una pagina bloccata. Un'acquisizione invece
    # dura davvero, e li' serve pazienza.
    ATTESA_COMANDO = 15.0
    # quanto aspettare che l'SDK finisca di collegare lo scanner (riserva: ~40 s)
    ATTESA_PRONTO = 120.0
    ATTESA_MOVIMENTO = 60.0
    ATTESA_ACQUISIZIONE = 180.0

    # FilmFormat dell'SDK: 0 = 135, 1 = APS, 4 = 110 (confermati dai .bat di
    # servizio originali). Il carrello del D1008 non porta il medio formato.
    FORMATI_SDK = {"135": 0, "aps": 1, "110": 4}

    controlla_lampada = False

    def __init__(self, root: str, timeout: float = ATTESA_ACQUISIZIONE,
                 risoluzione: int = 0, passo_mm: float = 0.01,
                 ingressi: Optional[Dict[str, int]] = None, ir: bool = False,
                 max_regola_mm: float = 3.0):
        self.root = root
        self.timeout = timeout
        self.risoluzione = risoluzione
        self.passo_mm = passo_mm
        self.max_regola_mm = max_regola_mm
        # esempio, una volta scoperti: {"ingresso": 3, "uscita": 5}
        self.ingressi = ingressi or {}
        self.canali_disponibili = CHANNELS if ir else ("R", "G", "B")
        for sub in ("cmd", "ack", "raw"):
            os.makedirs(os.path.join(root, sub), exist_ok=True)
        self._pos = 0.0
        self._focus = 0.0
        self._connesso = False
        self._formato = None
        self._info = {}
        self._contatore = 0

    # --- protocollo -------------------------------------------------------

    def _emergenza(self) -> bool:
        return os.path.exists(os.path.join(self.root, FILE_EMERGENZA))

    def _send(self, comando: str, attesa: Optional[float] = None) -> str:
        if self._emergenza() and comando not in ("ping", "stato", "pressore_su"):
            raise Emergenza("STOP EMERGENZA attivo: '%s' non inviato" % comando)
        # Il contatore evita che due comandi uguali nello stesso millisecondo
        # (due tablet aperti) finiscano sullo stesso file di risposta.
        self._contatore += 1
        token = "%d_%04d_%s" % (int(time.time() * 1000), self._contatore % 10000,
                                comando.split("_")[0])
        percorso = os.path.join(self.root, "cmd", token)
        # Scrittura in due tempi: l'agente vede il comando solo quando e'
        # completo. Prima poteva leggerlo ancora vuoto.
        with open(percorso + ".tmp", "w") as f:
            f.write(comando)
        os.replace(percorso + ".tmp", percorso)
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
            if comando not in ("ping", "stato", "pressore_su") and self._emergenza():
                # Non si aspetta la fine del comando: chi ha premuto
                # l'emergenza vuole che il programma smetta adesso. Se il
                # comando non e' ancora partito si ritira; quello in corso
                # nell'SDK non si puo' interrompere da qui.
                try:
                    os.remove(percorso)
                except OSError:
                    pass
                raise Emergenza("STOP EMERGENZA durante '%s'" % comando)
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
                "MASTERFLEX.bat; se si e' chiusa subito, l'SDK non ha trovato la "
                "scheda (vedi Scanner\\SDKCacheDir\\log_0000.txt)." % self.root)
        import json
        # Appena aperto, l'SDK collega lo scanner in sottofondo (sulla
        # riserva circa 40 s) e intanto rifiuta i comandi: si aspetta qui,
        # invece di far fallire la prima calibrazione o il primo collaudo.
        scadenza = time.time() + self.ATTESA_PRONTO
        while True:
            try:
                self._info = json.loads(self._send("stato"))
            except (ScannerError, ValueError):
                self._info = {}
            if self._info.get("pronto", True) or time.time() > scadenza:
                break
            time.sleep(2.0)
        if self._info.get("pronto") is False:
            raise ScannerError(
                "lo scanner non e' pronto dopo %.0f s: l'SDK e' ancora nello "
                "stato %s (10 = scanner non collegato). Guardare la finestra "
                "dell'agente e Scanner\\SDKCacheDir\\log_0000.txt."
                % (self.ATTESA_PRONTO, self._info.get("stato_sdk")))
        self._connesso = True

    def disconnect(self) -> None:
        self._connesso = False

    def status(self) -> ScannerStatus:
        """Stato locale, senza disturbare la macchina.

        L'interfaccia chiede lo stato ogni secondo. Prima ogni richiesta
        diventava un comando "stato" all'agente, cioe' tre chiamate all'SDK al
        secondo, in coda anche dietro a un'acquisizione di tre minuti: la
        pagina restava appesa e dava la macchina per scollegata a meta' rullo.
        """
        if not self._connesso:
            return ScannerStatus(False, message="non connesso")
        parti = []
        if self._info.get("simulato"):
            parti.append("SIMULAZIONE: l'agente non sta comandando la macchina")
        if self._info.get("motori") is False:
            parti.append("motori bloccati")
        if self._info.get("id"):
            parti.append("scanner %s" % self._info["id"])
        return ScannerStatus(True, True, self._pos, self._focus, True,
                             " - ".join(parti) or "connesso")

    # --- movimento --------------------------------------------------------

    def seleziona_formato(self, chiave_formato: str) -> None:
        famiglia = chiave_formato.split("-")[0]
        if famiglia not in self.FORMATI_SDK:
            raise ScannerError(
                "il formato %s non e' supportato dal carrello di questa macchina "
                "(solo 135, APS e 110)" % chiave_formato)
        codice = self.FORMATI_SDK[famiglia]
        if self._info.get("motori") is False:
            # agente con i motori bloccati: il cambio di formato muove le
            # guide, quindi non si chiede. Si lavora sul formato gia' montato.
            return
        if self._formato != codice:
            self._send("formato_%d" % codice, attesa=self.ATTESA_MOVIMENTO)
            self._formato = codice

    def prepara_carico(self, chiave_formato: str) -> None:
        """Formato e carrello in attesa della pellicola (comando "carica"
        dell'agente): senza, il carrello non accetta la pellicola."""
        if self._info.get("motori") is False:
            raise ScannerError("agente con i motori bloccati: preparare il carico "
                               "muove le guide. Avviare con i motori attivi.")
        self.seleziona_formato(chiave_formato)
        self._send("carica_1", attesa=self.ATTESA_MOVIMENTO)

    def advance(self, mm: float) -> None:
        """Regolazione fine, in millimetri convertiti in passi da 0,01 mm.

        Passa da frame_detect_fine_adjust, la stessa correzione che il
        programma di servizio usa con le frecce nella Length Calibration: va
        bene per pochi millimetri. Oltre max_regola_mm si rifiuta qui, prima
        ancora di arrivare all'agente (che ha comunque il suo limite).
        Segno e scala vanno verificati sulla macchina con la prova guidata.
        """
        if abs(mm) > self.max_regola_mm:
            raise ScannerError(
                "movimento di %.1f mm rifiutato: la regolazione fine arriva a "
                "%.1f mm. Per il fotogramma successivo si usa 'avanza'."
                % (mm, self.max_regola_mm))
        passi = int(round(mm / self.passo_mm))
        if passi:
            self._send("regola_%d" % passi, attesa=self.ATTESA_MOVIMENTO)
        self._pos += mm

    # L'espulsione c'e' nei due versi. Davanti: cancel_scan, che col
    # carrello Gretag manda il comando di espulsione (il verso lo fissa il
    # flag 5, a 0 sulla riserva). Retro: fotogramma successivo finche' il
    # carrello rifiuta. Vedi ponte/sdk.py, espelli ed espelli_retro.
    supports_eject = True
    ATTESA_ESPULSIONE_RETRO = 200.0
    # La fine della striscia la dice il carrello (ERR_MOVE sul fotogramma
    # successivo): la stima ottica non serve, costa una lettura a fotogramma
    # e scambierebbe i fotogrammi non esposti per pellicola finita.
    fine_striscia_dal_carrello = True   # l'agente si ferma da solo a 180 s

    ATTESA_AZZERAMENTO = 120.0

    def azzera_motori(self) -> bool:
        """Gretag Reset: il carrello si reinizializza e azzera tamburo, guide,
        lente e CCD. Con l'agente a motori bloccati l'agente lo rifiuta, e qui
        diventa MotoriBloccati: chi calibra lo annota e prosegue."""
        try:
            self._send("azzera", attesa=self.ATTESA_AZZERAMENTO)
        except ScannerError as exc:
            if "motori bloccati" in str(exc):
                raise MotoriBloccati(str(exc))
            raise
        self._pos = 0.0
        return True

    def eject(self, verso: str = "davanti") -> Optional[str]:
        if verso == "retro":
            risposta = self._send("espelli_retro",
                                  attesa=self.ATTESA_ESPULSIONE_RETRO)
        else:
            risposta = self._send("espelli", attesa=self.ATTESA_MOVIMENTO)
        self._pos = 0.0
        # la risposta dell'agente dice dove si e' fermato il carrello: va
        # nel diario cosi' com'e'
        return risposta

    def prossimo_fotogramma(self, fmt) -> None:
        # gretag_NextFrame, come il programma originale: e' il carrello a
        # cercare il fotogramma. Prima si usava advance_one_frame, che e' dei
        # carrelli Kodak.
        try:
            self._send("prossimo", attesa=self.ATTESA_MOVIMENTO)
        except ScannerError as exc:
            # Dopo l'ultimo fotogramma il carrello risponde ERR_MOVE (31):
            # visto sulla riserva il 26/10/2026, alla fine di una striscia
            # da 20 fotogrammi.
            if ("ERR_MOVE" in str(exc) or "codice 31 " in str(exc)
                    or "ERR_END_OF_FILM" in str(exc) or "codice 32 " in str(exc)):
                raise FinePellicola(str(exc))
            raise
        self._pos += fmt.pitch

    def fotogramma_precedente(self, fmt) -> None:
        self._send("precedente", attesa=self.ATTESA_MOVIMENTO)
        self._pos -= fmt.pitch

    def avanza_fotogramma(self, mezzo: bool = False) -> None:
        self._send("avanza_mezzo" if mezzo else "avanza",
                   attesa=self.ATTESA_MOVIMENTO)

    def set_focus(self, mm: float) -> None:
        """L'SDK espone l'autofocus, non il posizionamento assoluto del fuoco.

        Finche' non sappiamo il passo dell'attuatore (vedi note-macchina.md)
        questa e' la cosa giusta da fare: chiedere alla macchina di mettere a
        fuoco da sola, che e' cio' che sa fare bene.
        """
        self._send("fuoco", attesa=self.ATTESA_MOVIMENTO)
        self._focus = mm

    def home(self) -> None:
        pass

    # --- sensori ----------------------------------------------------------

    def pellicola_sdk(self) -> Optional[bool]:
        """Pellicola inserita secondo l'SDK (evento EV_FILM_INSERTED), letta
        dal stato.json dell'agente: con il carrello Gretag e' il carrello a
        vedere la pellicola, non servono gli ingressi mappati a mano.
        None = non si sa (agente vecchio, o mai armato con Carica)."""
        import json
        try:
            with open(os.path.join(self.root, "stato.json")) as f:
                return json.load(f).get("pellicola_inserita")
        except (IOError, OSError, ValueError):
            return None

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

    # Tempo di esposizione massimo, come multiplo di quello di fabbrica.
    # Non e' documentato: il 26/10/2026 sulla riserva x1 andava e x12 no
    # (ERR_SW_SCANNER_IFC + ERR_FW_ERR_NO 1 + ERR_GENERAL_FW, codici 46/47/42:
    # lo rifiuta il firmware della testa, l'SDK da solo taglia a 30000 unita'
    # per R, G e B, 0x1807C710). Si impara alla prima risposta negativa,
    # dimezzando, e da li' in poi non si chiede di piu'.
    max_esposizione: Optional[float] = None
    ultima_esposizione: Optional[float] = None

    # --- esposizione, pressore, Digital ICE, temperatura -------------------

    supports_pressore = True
    _esposizione_auto: Optional[bool] = None

    def esposizione_auto(self, auto: bool) -> bool:
        # si manda solo quando cambia: e' una riga per comando, non serve
        # ripeterla a ogni lettura
        if self._esposizione_auto is not bool(auto):
            self._send("esposizione_%s" % ("auto" if auto else "fabbrica"))
            self._esposizione_auto = bool(auto)
            self.max_esposizione = None     # il tetto dipende dalla base
        return True

    ATTESA_CALIBRAZIONE_SDK = 600.0

    def calibra_macchina(self) -> bool:
        self._send("calibrasdk", attesa=self.ATTESA_CALIBRAZIONE_SDK)
        return True

    def pressore(self, giu: bool) -> None:
        self._send("pressore_%s" % ("giu" if giu else "su"),
                   attesa=self.ATTESA_MOVIMENTO)

    def digital_ice(self, acceso: bool) -> bool:
        self._send("ice_%s" % ("on" if acceso else "off"))
        return True

    def temperatura(self) -> Optional[Dict[str, float]]:
        import json
        return json.loads(self._send("temperatura"))

    def tensioni(self) -> Optional[List[float]]:
        import json
        return json.loads(self._send("tensioni"))

    RISOLUZIONE_ANTEPRIMA = 3          # 262x175: un quarto di secondo o poco piu'

    def anteprima_veloce(self) -> np.ndarray:
        return self._leggi(self._send(
            "grab_G_%d_1.000" % self.RISOLUZIONE_ANTEPRIMA,
            attesa=self.ATTESA_ACQUISIZIONE)).astype(np.float32)

    def capture_line_block(self, channel: str, lines: int,
                           exposure: float = 1.0) -> np.ndarray:
        if channel not in self.canali_disponibili:
            raise ScannerError("canale %s non disponibile su questa macchina"
                               % channel)
        if self.max_esposizione is not None:
            exposure = min(exposure, self.max_esposizione)
        while True:
            try:
                nome = self._send("grab_%s_%d_%.3f"
                                  % (channel, self.risoluzione, exposure),
                                  attesa=self.ATTESA_ACQUISIZIONE)
                break
            except ScannerError as exc:
                # Solo il rifiuto del firmware (codice 47) e solo sopra x1: a
                # x1 lo stesso errore vuol dire altro (finestra sbagliata) e
                # va riportato com'e'.
                if "codice 47" not in str(exc) or exposure <= 1.0:
                    raise
                exposure = max(1.0, exposure / 2.0)
                self.max_esposizione = exposure
        self.ultima_esposizione = exposure
        # Si restituisce sempre il fotogramma intero, anche quando se ne
        # chiedono poche righe: la lettura e' gia' stata pagata, e le prime 16
        # righe (il bordo del fotogramma) erano un pessimo campione per dire
        # se c'e' ancora pellicola.
        return self._leggi(nome)

    def _leggi(self, nome: str) -> np.ndarray:
        """Il grezzo che l'agente ha depositato in <ponte>/raw, poi via."""
        percorso = os.path.join(self.root, "raw", nome)
        scadenza = time.time() + self.timeout
        while time.time() < scadenza and not os.path.exists(percorso):
            time.sleep(0.05)
        if not os.path.exists(percorso):
            raise ScannerError("l'agente non ha depositato %s" % nome)

        import json
        meta_path = percorso[:-4] + ".json"
        with open(meta_path) as f:
            meta = json.load(f)
        w, h = int(meta["larghezza"]), int(meta["altezza"])
        dati = np.fromfile(percorso, dtype="<u2").reshape(h, w)
        # I grezzi del ponte sono gia' stati letti: tenerli li' riempirebbe il
        # disco di 6 MB per lettura, calibrazioni comprese.
        for p in (percorso, meta_path):
            try:
                os.remove(p)
            except OSError:
                pass
        return dati


# --------------------------------------------------------------------------

class OriginaleBackend(ScannerBackend):
    """Collegamento "programma originale": la macchina la comanda
    PhxScanner.exe, e ponte/osservatore.py copia le immagini grezze che riceve
    dallo scanner in <ponte>/mitm/. Qui non si muove niente e non si
    acquisisce: si leggono quelle immagini, che il servizio elabora come se le
    avesse acquisite filmscan (ScanService.ricevi_da_originale).
    """

    controlla_lampada = False

    def __init__(self, bridge_path: str):
        self.cartella = os.path.join(bridge_path, "mitm")
        self._connesso = False

    def connect(self) -> None:
        os.makedirs(self.cartella, exist_ok=True)
        self._connesso = True

    def disconnect(self) -> None:
        self._connesso = False

    def status(self) -> ScannerStatus:
        return ScannerStatus(connected=self._connesso, film_loaded=False,
                             message="programma originale")

    def _rifiuta(self, *_a, **_k):
        raise ScannerError("con il programma originale la macchina la comanda "
                           "lui: movimenti e acquisizioni si fanno da li'")

    advance = set_focus = _rifiuta

    def capture_line_block(self, channel: str, lines: int,
                           exposure: float = 1.0) -> np.ndarray:
        self._rifiuta()

    def nuove(self, dopo: float) -> List[str]:
        """Le immagini copiate dall'osservatore dopo l'istante 'dopo'
        (secondi dall'epoca), dalla piu' vecchia."""
        trovate = []
        for nome in os.listdir(self.cartella):
            if nome.endswith(".raw"):
                p = os.path.join(self.cartella, nome)
                if os.path.getmtime(p) > dopo and os.path.exists(p[:-4] + ".json"):
                    trovate.append(p)
        return sorted(trovate)

    @staticmethod
    def leggi(percorso: str) -> Tuple[Dict[str, np.ndarray], Dict[str, object]]:
        """I piani di un'immagine copiata: {canale: array 2D uint16}.

        Si assume che i piani stiano uno dopo l'altro nell'ordine R, G, B
        (e IR se ce n'e' un quarto), come li chiede capture() con la maschera
        7 o 0xF. E' da verificare con la prima immagine vera: se i colori
        escono scambiati, l'ordine e' un altro.
        """
        import json
        with open(percorso[:-4] + ".json") as f:
            info = json.load(f)
        w, h = int(info["larghezza"]), int(info["altezza"])
        dati = np.fromfile(percorso, dtype="<u2")
        piani = min(int(info.get("piani", 3)), dati.size // (w * h))
        if piani < 1:
            raise ScannerError("%s: meno di un piano (%d campioni per %dx%d)"
                               % (os.path.basename(percorso), dati.size, w, h))
        nomi = ["R", "G", "B", "IR"]
        return ({nomi[i]: dati[i * w * h:(i + 1) * w * h].reshape(h, w)
                 for i in range(min(piani, 4))}, info)


def make_backend(cfg) -> ScannerBackend:
    """Fabbrica: sceglie il backend in base alla configurazione."""
    if cfg.driver == "mock":
        return MockBackend(sensor_pixels=cfg.sensor_pixels, dpi=cfg.optical_dpi)
    if cfg.driver == "sdk":
        if not cfg.bridge_path:
            raise ScannerError("driver 'sdk' selezionato ma bridge_path e' vuoto")
        return PonteSdkBackend(cfg.bridge_path,
                               risoluzione=getattr(cfg, "sdk_resolution", 0),
                               passo_mm=cfg.transport_step_mm,
                               ingressi=getattr(cfg, "sdk_ingressi", None),
                               ir=getattr(cfg, "sdk_ir_verificato", False),
                               max_regola_mm=getattr(cfg, "max_regola_mm", 3.0))
    if cfg.driver == "originale":
        if not cfg.bridge_path:
            raise ScannerError("driver 'originale' selezionato ma bridge_path e' vuoto")
        return OriginaleBackend(cfg.bridge_path)
    raise ScannerError("driver sconosciuto: %s" % cfg.driver)
