# -*- coding: utf-8 -*-
"""
Servizio di scansione: lo stato condiviso fra l'interfaccia locale e quella web.

Esiste perche' con due interfacce servirebbero due copie della stessa logica, e
due copie divergono sempre. Qui c'e' una sola macchina, una sola calibrazione,
una sola coda di lavoro; entrambe le interfacce comandano questo oggetto.

Un solo lavoro alla volta, protetto da lock: lo scanner e' fisicamente uno, e
due scansioni contemporanee significherebbero due comandi di trasporto in
conflitto sulla stessa pellicola.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import io
import json
import os
import queue
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np

from .acquire import (Annullato, Calibration, RawScan, ScanMetadata, film_present,
                      fotogramma_vuoto,
                      measure_channel_exposure, run_calibration, scan_frame)
from .compat import check_environment
from .config import AppConfig, FORMATS, PRESETS
from .hal import (FILE_EMERGENZA, Emergenza, FilmSensors, FinePellicola,
                  MotoriBloccati, OriginaleBackend, PressoreBloccato, ScannerBackend, ScannerError,
                  make_backend)
from . import colore
from .invert import PROFILES
from .orders import Archivio
from .orient import RollMirror
from .pipeline import (ProcessOptions, export, export_mask, finiture,
                       process)


@dataclass
class JobState:
    running: bool = False
    kind: str = ""              # "anteprima" | "fotogramma" | "rullo" | "calibrazione"
    message: str = "pronto"
    progress: float = 0.0
    frame: int = 0
    total: int = 0
    errors: List[str] = field(default_factory=list)
    log: List[str] = field(default_factory=list)
    # il rullo aspetta la striscia successiva: l'interfaccia mostra
    # "Fine rullo"
    attesa_striscia: bool = False
    # "Fine rullo" premuto: il rullo si sta chiudendo
    chiusura: bool = False
    # il rullo aspetta che il fotogramma si metta in finestra a mano
    posizionamento: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {"running": self.running, "kind": self.kind,
                "message": self.message, "progress": self.progress,
                "frame": self.frame, "total": self.total,
                "errors": self.errors[-10:], "log": self.log[-40:],
                "attesa_striscia": self.attesa_striscia,
                "chiusura": self.chiusura,
                "posizionamento": self.posizionamento}


# Nella nota della calibrazione: con quale base d'esposizione e' stata fatta
TAG_ESPOSIZIONE = ("[esposizione automatica]", "[esposizione di fabbrica]")


class _FotogrammaVuoto(Exception):
    """Esce dal blocco del fotogramma senza esportare: non e' un errore."""


class _CodaIniziale(Exception):
    """La prima lettura di una striscia e' caduta sulla coda: non e' un
    fotogramma, non prende un numero."""


class ScanService:

    def __init__(self, cfg: AppConfig, archivio: Optional[Archivio] = None):
        self.cfg = cfg
        self.archivio = archivio or Archivio(
            os.path.join(os.path.dirname(os.path.abspath(cfg.out_dir)) or ".",
                         "ordini.db"))
        self.backend: Optional[ScannerBackend] = None
        self.calib: Optional[Calibration] = None
        self.job = JobState()
        self._lut_cache = None
        self._lut_errore = ""
        self.options = ProcessOptions()
        self.preview: Optional[np.ndarray] = None
        # il negativo grezzo dello stesso fotogramma, per il confronto prima/dopo
        self.preview_neg: Optional[np.ndarray] = None
        # L'ultimo grezzo acquisito resta in memoria: cambiare una correzione
        # deve costare un secondo di calcolo, non un nuovo passaggio di
        # pellicola nella macchina. E' la stessa ragione per cui i grezzi si
        # conservano su disco, applicata al fotogramma che si sta guardando.
        self.ultimo_raw = None
        # La base lineare dell'ultimo fotogramma: da qui si ricalcola
        # l'anteprima quando si muove un cursore, senza rifare la catena.
        self.base_lineare: Optional[np.ndarray] = None
        self.preview_ridotta = False
        self._cache_prov: Dict[str, bytes] = {}

        self._lock = threading.Lock()
        self._stop = threading.Event()
        # "Fine rullo": chiude il rullo al primo punto sicuro, e smette di
        # aspettare altre strisce
        self._fine_rullo = threading.Event()
        # STOP EMERGENZA: il blocco sta su disco e si rilegge all'avvio,
        # cosi' un riavvio non lo scavalca
        self.emergenza: Optional[Dict[str, object]] = self._leggi_emergenza()
        # dopo un'emergenza serve una calibrazione nuova (che azzera i motori)
        self._calibra_dopo: float = 0.0
        # posizionamento a mano: i tasti della pagina mettono qui i comandi,
        # il thread del rullo li esegue (la macchina la comanda uno solo)
        self._comandi_pos: "queue.Queue" = queue.Queue()
        self._video: bytes = b""
        # mentre la connessione automatica aspetta lo scanner, cosa aspetta
        self.attesa_connessione: Optional[str] = None
        # Disconnetti ferma anche la connessione automatica: altrimenti si
        # ricollegherebbe da sola cinque secondi dopo
        self._ferma_auto = threading.Event()

    # ------------------------------------------------------------------

    def log(self, msg: str) -> None:
        self.job.log.append("%s  %s" % (time.strftime("%H:%M:%S"), msg))

    def progress(self, msg: str, frac: float) -> None:
        self.job.message = msg
        self.job.progress = float(max(0.0, min(1.0, frac)))

    def stato(self) -> Dict[str, object]:
        st = None
        if self.backend is None and self.attesa_connessione:
            st = {"connesso": False, "messaggio": self.attesa_connessione}
        if self.backend is not None:
            try:
                s = self.backend.status()
                st = {"connesso": s.connected, "posizione_mm": round(s.position_mm, 3),
                      "fuoco_mm": round(s.focus_mm, 4), "messaggio": s.message}
            except Exception as exc:                    # noqa: BLE001
                st = {"connesso": False, "messaggio": str(exc)}

        calib = None
        if self.calib is not None:
            calib = {"eta_giorni": round(self.calib.age_days(), 1),
                     "di_oggi": self.calibrazione_di_oggi()}

        return {"macchina": st, "calibrazione": calib,
                "emergenza": self.emergenza,
                "impostazioni_macchina": self.impostazioni_macchina(),
                "driver": self.cfg.driver, "job": self.job.as_dict(),
                "ambiente": check_environment(dpi=self.cfg.optical_dpi)}

    # ------------------------------------------------------------------

    # --- LUT ------------------------------------------------------------

    @property
    def options(self) -> ProcessOptions:
        return self._options

    @options.setter
    def options(self, valore: ProcessOptions) -> None:
        # La LUT e' una scelta del programma, valida per tutti gli ordini: ogni
        # ProcessOptions che arriva (dalla pagina, da un rullo) la riceve qui.
        valore.lut = self._lut_corrente()
        valore.lut_intensita = self.cfg.lut_intensita
        self._options = valore

    def _lut_corrente(self):
        if not self.cfg.lut:
            return None
        percorso = os.path.join(self.cfg.path("lut"), self.cfg.lut)
        try:
            chiave = (percorso, os.path.getmtime(percorso))
        except OSError:
            if self._lut_errore != percorso:
                self._lut_errore = percorso
                self.log("LUT %s non trovata: si lavora senza." % self.cfg.lut)
            return None
        if self._lut_cache and self._lut_cache[0] == chiave:
            return self._lut_cache[1]
        from . import lut
        try:
            l = lut.carica(percorso)
        except (OSError, ValueError) as exc:
            self.log("LUT %s non leggibile (%s): si lavora senza." % (self.cfg.lut, exc))
            return None
        self._lut_cache = (chiave, l)
        return l

    def elenco_lut(self) -> Dict[str, object]:
        from . import lut
        return {"lut": lut.elenco(self.cfg.path("lut")), "scelta": self.cfg.lut,
                "intensita": self.cfg.lut_intensita,
                "cartella": self.cfg.path("lut")}

    def scegli_lut(self, nome: str, intensita: Optional[float] = None) -> Dict[str, object]:
        """La LUT per tutti gli ordini, da adesso. "" = nessuna."""
        from . import lut
        nome = str(nome or "")
        if nome and nome not in lut.elenco(self.cfg.path("lut")):
            raise ValueError("LUT sconosciuta: %s" % nome)
        self.cfg.lut = nome
        if intensita is not None:
            self.cfg.lut_intensita = float(min(max(float(intensita), 0.0), 1.0))
        self.options = self._options          # rimette la LUT nelle opzioni
        self.log("LUT: %s%s." % (nome or "nessuna",
                                 (" al %d%%" % round(self.cfg.lut_intensita * 100))
                                 if nome else ""))
        if self.base_lineare is not None:
            try:
                self.rielabora()
            except Exception:                           # noqa: BLE001
                pass
        return self.elenco_lut()

    def carica_lut(self, nome: str, testo: str) -> Dict[str, object]:
        """Una LUT nuova dalla pagina: si controlla che sia un .cube valido,
        poi si salva nella cartella del programma."""
        from . import lut
        nome = lut.nome_valido(nome)
        letta = lut.leggi(testo, nome)                  # ValueError se non va
        percorso = os.path.join(self.cfg.path("lut"), nome)
        with open(percorso + ".tmp", "w", encoding="utf-8", newline="\n") as f:
            f.write(testo)
        os.replace(percorso + ".tmp", percorso)
        self.log("LUT %s aggiunta (%s, %s da %d)." % (
            nome, letta.titolo or "cube", letta.tipo.upper(), letta.dimensione))
        esito = self.elenco_lut()
        esito["aggiunta"] = nome
        return esito

    def connetti(self, driver: Optional[str] = None) -> Dict[str, object]:
        if driver:
            self.cfg.driver = driver
        # il backend si tiene solo se la connessione riesce: prima restava
        # anche fallita, e lo stato mostrava una macchina "presente" che
        # non rispondeva
        backend = make_backend(self.cfg)
        backend.connect()
        self.backend = backend
        self.log("Backend '%s' connesso." % self.cfg.driver)
        existing = Calibration.latest(self.cfg.path("calib"))
        if existing is not None:
            self.calib = existing
            self.log("Calibrazione esistente, %.0f giorni." % existing.age_days())
        return self.stato()

    def connetti_automatico(self, durata: float = 900.0,
                            pausa: float = 5.0) -> None:
        """Si connette da solo appena l'agente ha lo scanner pronto.

        All'accensione agente e interfaccia partono insieme, ma l'SDK ci mette
        una quarantina di secondi a collegare lo scanner (stato 10 -> 0). Si
        riprova finche' non riesce: connect() del ponte aspetta gia' lo stato
        0, qui si copre il tempo in cui l'agente non risponde ancora. Se nel
        frattempo qualcuno preme Connetti, si lascia fare a lui.
        """
        def ciclo():
            fine = time.time() + durata
            ultimo = ""
            self.attesa_connessione = "connessione automatica: attendo lo scanner"
            try:
                while (time.time() < fine and self.backend is None
                       and not self._ferma_auto.is_set()):
                    try:
                        self.connetti()
                        self.log("Connessione automatica riuscita.")
                        return
                    except Exception as exc:            # noqa: BLE001
                        ultimo = str(exc).splitlines()[0] if str(exc) else ""
                        self.attesa_connessione = (
                            "connessione automatica: attendo lo scanner (%s)"
                            % ultimo)
                    time.sleep(pausa)
                if self.backend is None and not self._ferma_auto.is_set():
                    self.log("Connessione automatica non riuscita in %.0f "
                             "minuti: %s. Premere Connetti." % (durata / 60, ultimo))
            finally:
                self.attesa_connessione = None

        self._ferma_auto.clear()
        threading.Thread(target=ciclo, name="connessione-automatica",
                         daemon=True).start()

    def fine_rullo(self) -> Dict[str, object]:
        """"Fine rullo", il tasto Scansiona rullo mentre il rullo gira: lo
        chiude finito il fotogramma in corso (o subito, se sta aspettando la
        striscia successiva), espelle la pellicola e lo segna completato.
        E' lo stop del lavoro normale; l'emergenza e' un'altra cosa."""
        if self.job.running and self.job.kind == "rullo":
            self._fine_rullo.set()
            self.job.chiusura = True
            self.log("Fine rullo richiesto: si chiude dopo il fotogramma in "
                     "corso.")
        return self.stato()

    def _prossima_striscia(self, backend, formato: str):
        """Fine di una striscia di un rullo tagliato in piu' pezzi: espelle,
        riarma subito il carrello e aspetta la striscia successiva.

        Restituisce (continua, espulsa). Si smette con "Fine rullo", con
        STOP o dopo cfg.attesa_striscia_s senza una striscia nuova.
        """
        if not (getattr(backend, "supports_eject", False)
                and hasattr(backend, "pellicola_sdk")):
            return False, False
        espulsa = False
        try:
            verso = self.cfg.verso_espulsione_fine_rullo
            esito = backend.eject(verso)
            espulsa = True
            self.log(esito or ("Striscia espulsa dal %s." % verso))
            backend.prepara_carico(formato)
        except Exception as exc:                        # noqa: BLE001
            self.log("Striscia successiva non preparata: %s" % exc)
            return False, espulsa
        self.log("Carrello pronto: inserire la striscia successiva, oppure "
                 "Fine rullo.")
        self.job.attesa_striscia = True
        try:
            scadenza = time.time() + self.cfg.attesa_striscia_s
            while time.time() < scadenza:
                if self._stop.is_set() or self._fine_rullo.is_set():
                    return False, espulsa
                self.job.message = "Inserire la striscia successiva (o Fine rullo)"
                if backend.pellicola_sdk():
                    self.log("Striscia inserita.")
                    return True, False
                time.sleep(0.5)
            self.log("Nessuna striscia nuova in %.0f s: rullo chiuso."
                     % self.cfg.attesa_striscia_s)
            return False, espulsa
        finally:
            self.job.attesa_striscia = False

    # --- posizionamento a mano con il video -------------------------------

    AZIONI_POSIZIONAMENTO = ("muovi", "prossimo", "acquisisci", "striscia", "fine")

    def posiziona(self, azione: str, mm: float = 0.0) -> Dict[str, object]:
        """Un tasto del posizionamento a mano. Lo esegue il thread del rullo."""
        if not self.job.posizionamento:
            raise ScannerError("nessun fotogramma da posizionare adesso")
        if azione not in self.AZIONI_POSIZIONAMENTO:
            raise ValueError("azione sconosciuta: %s" % azione)
        if azione == "muovi" and not (0 < abs(mm) <= 20.0):
            raise ValueError("passo non valido: %s mm" % mm)
        self._comandi_pos.put((azione, float(mm)))
        return self.stato()

    def video_jpeg(self) -> bytes:
        return self._video

    @staticmethod
    def _jpeg_video(img: np.ndarray) -> bytes:
        """Il verde a bassa risoluzione, invertito come un positivo e tirato
        sui percentili: serve a vedere dov'e' il fotogramma, non il colore."""
        from PIL import Image
        x = img.astype(np.float32)
        lo, hi = np.percentile(x, (1.0, 99.0))
        x = np.clip((x - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
        x = (1.0 - x) ** 0.8
        buf = io.BytesIO()
        Image.fromarray((x * 255).astype(np.uint8)).save(buf, "JPEG", quality=75)
        return buf.getvalue()

    def _sposta(self, backend, mm: float) -> None:
        """Uno spostamento a mano, a pezzi entro la regolazione fine (3 mm):
        20 mm sono sette comandi. La scala vera dei passi va ancora misurata
        sulla macchina (carrello-capri.md §7)."""
        passo = float(getattr(backend, "max_regola_mm", 3.0) or 3.0)
        resto = float(mm)
        while abs(resto) > 1e-6:
            d = max(-passo, min(passo, resto))
            backend.advance(d)
            resto -= d

    def _posizionamento_manuale(self, backend, fmt, motivo: str) -> str:
        """Il video e i tasti: il fotogramma si mette in finestra a mano.

        Si apre quando il carrello non trova il fotogramma (negativo
        sbiadito, interfotogramma che non si vede) o quando l'avanzamento
        automatico e' spento. Restituisce "acquisisci", "striscia" (striscia
        finita: espelli e aspetta la prossima) o "fine".
        """
        self.log("Posizionamento a mano: %s." % motivo)
        while not self._comandi_pos.empty():
            self._comandi_pos.get_nowait()
        self.job.posizionamento = motivo
        ultimo = 0.0
        try:
            while True:
                if self.emergenza:
                    raise Emergenza("STOP EMERGENZA")
                if self._stop.is_set():
                    raise Annullato()
                if self._fine_rullo.is_set():
                    return "fine"
                try:
                    azione, mm = self._comandi_pos.get(timeout=0.15)
                except queue.Empty:
                    azione, mm = None, 0.0
                if azione in ("acquisisci", "striscia", "fine"):
                    return azione
                try:
                    if azione == "muovi":
                        self._sposta(backend, mm)
                        ultimo = 0.0
                    elif azione == "prossimo":
                        backend.prossimo_fotogramma(fmt)
                        ultimo = 0.0
                except Emergenza:
                    raise
                except FinePellicola:
                    self.log("Il carrello non trova altri fotogrammi: "
                             "striscia finita?")
                except ScannerError as exc:
                    self.log("Movimento non riuscito: %s" % exc)
                if time.time() - ultimo > 0.8:
                    try:
                        self._video = self._jpeg_video(backend.anteprima_veloce())
                    except Emergenza:
                        raise
                    except Exception as exc:            # noqa: BLE001
                        self.log("Video non disponibile: %s" % exc)
                        time.sleep(1.0)
                    ultimo = time.time()
        finally:
            self.job.posizionamento = ""

    # --- numeri dal bordo e riordino --------------------------------------

    @staticmethod
    def _chiave_bordo(testo: str) -> Optional[float]:
        """"12" -> 12, "12A" -> 12.5 (il mezzo fotogramma dei numeri sul
        bordo). None se non e' un numero."""
        m = re.match(r"^\s*(\d{1,3})\s*(A?)\s*$", str(testo or ""), re.I)
        if not m:
            return None
        return int(m.group(1)) + (0.5 if m.group(2) else 0.0)

    @classmethod
    def ordine_per_bordo(cls, fotogrammi: List[Dict[str, object]]) -> List[Dict[str, object]]:
        """L'ordine dei fotogrammi secondo i numeri letti sul bordo. Chi non
        ha un numero resta attaccato al fotogramma numerato che lo precede
        nella scansione: le strisce si inseriscono in qualunque ordine, ma
        dentro una striscia l'ordine e' gia' giusto."""
        chiavi = []
        precedente = float("-inf")
        for i, f in enumerate(fotogrammi):
            k = cls._chiave_bordo(f.get("numero_bordo") or "")
            if k is not None:
                precedente = k
                chiavi.append((k, 0, i))
            else:
                chiavi.append((precedente, 1, i))
        ordine = sorted(range(len(fotogrammi)), key=lambda i: chiavi[i])
        return [fotogrammi[i] for i in ordine]

    def _riordina_per_bordo(self, rullo_id: int) -> None:
        """A fine rullo: se sul bordo si sono letti dei numeri, i fotogrammi
        si rinumerano (e i file si rinominano) in quell'ordine. Se i numeri
        non ci sono, non cambia niente: e' un di piu', non un requisito."""
        fot = self.archivio.fotogrammi(rullo_id)
        letti = [f for f in fot if self._chiave_bordo(f.get("numero_bordo") or "") is not None]
        if len(letti) < 2:
            return
        nuovo = self.ordine_per_bordo(fot)
        if [f["id"] for f in nuovo] == [f["id"] for f in fot]:
            self.log("Numeri del bordo letti su %d fotogrammi: l'ordine era "
                     "gia' quello giusto." % len(letti))
            return
        # i numeri di prima, nell'ordine: sono quelli che i fotogrammi
        # prendono, uno dopo l'altro
        numeri = [f["numero"] for f in fot]
        piano = []
        for numero, f in zip(numeri, nuovo):
            g = dict(f)
            g["numero"] = numero
            for campo in ("file_raw", "file_out", "file_maschera"):
                g[campo] = self._nome_con_numero(f.get(campo) or "", numero)
            piano.append((f, g))
        # file: prima tutti su un nome provvisorio, poi su quello giusto,
        # cosi' due fotogrammi che si scambiano il numero non si pestano
        spostati = []
        try:
            for f, g in piano:
                for campo in ("file_raw", "file_out", "file_maschera"):
                    for a, b in self._file_compagni(f.get(campo) or "",
                                                    g.get(campo) or ""):
                        if a != b and os.path.exists(a):
                            os.replace(a, a + ".riordino")
                            spostati.append((a + ".riordino", b))
            for a, b in spostati:
                os.replace(a, b)
        except OSError as exc:
            self.log("Riordino per numeri del bordo non riuscito (%s): i "
                     "fotogrammi restano nell'ordine di scansione." % exc)
            for a, b in spostati:
                if os.path.exists(a):
                    os.replace(a, a[:-len(".riordino")])
            return
        self.archivio.fotogrammi_rinumera([g for _, g in piano])
        self.log("Fotogrammi riordinati secondo i numeri del bordo (%d letti "
                 "su %d)." % (len(letti), len(fot)))

    @staticmethod
    def _nome_con_numero(percorso: str, numero: int) -> str:
        if not percorso:
            return percorso
        cartella, nome = os.path.split(percorso)
        nuovo = re.sub(r"_(\d{3})(?=(_maschera)?\.[^.]+$)", "_%03d" % numero, nome)
        return os.path.join(cartella, nuovo)

    @staticmethod
    def _file_compagni(vecchio: str, nuovo: str):
        """Il file e i suoi compagni: il grezzo .npz ha accanto il .json."""
        if not vecchio:
            return []
        coppie = [(vecchio, nuovo)]
        if vecchio.endswith(".npz"):
            coppie.append((vecchio[:-4] + ".json", nuovo[:-4] + ".json"))
        return coppie

    def disconnetti(self) -> Dict[str, object]:
        """Lascia la macchina. Non a lavoro in corso: a meta' di un rullo
        la pellicola resterebbe in una posizione che nessuno conosce. Con
        l'SDK l'agente resta acceso e tiene lo scanner: si stacca solo
        l'interfaccia, e Connetti la riattacca subito."""
        if self.job.running:
            raise ScannerError("c'e' un lavoro in corso: aspettare la fine o "
                               "premere STOP prima di disconnettere")
        self._ferma_auto.set()
        backend, self.backend = self.backend, None
        if backend is not None:
            try:
                backend.disconnect()
            except Exception as exc:                    # noqa: BLE001
                self.log("Disconnessione: %s" % exc)
            self.log("Macchina disconnessa.")
        return self.stato()

    def _require(self) -> ScannerBackend:
        if self.emergenza:
            raise Emergenza("STOP EMERGENZA attivo: la macchina riparte solo "
                            "con l'ok del tecnico")
        if self.backend is None:
            raise ScannerError("macchina non connessa")
        return self.backend

    # --- STOP EMERGENZA -------------------------------------------------

    def _file_emergenza(self) -> str:
        cartella = self.cfg.bridge_path or os.path.dirname(
            os.path.abspath(self.cfg.out_dir))
        return os.path.join(cartella, FILE_EMERGENZA)

    def _leggi_emergenza(self) -> Optional[Dict[str, object]]:
        try:
            with open(self._file_emergenza()) as f:
                return json.load(f)
        except (IOError, OSError, ValueError):
            return None

    def stop_emergenza(self, motivo: str = "") -> Dict[str, object]:
        """STOP EMERGENZA: motori incastrati, pellicola che si strappa,
        rumori strani. Diverso dallo STOP del rullo, che aspetta un punto
        sicuro e rimette le cose a posto: qui non si aspetta e non si rimette
        a posto niente, perche' ogni movimento in piu' puo' peggiorare.

        Cosa blocca, subito: ogni comando nuovo (il file di blocco lo vedono
        il ponte e l'agente), l'attesa di quello in corso, il lavoro.
        Cosa NON puo' fare: interrompere a meta' un comando che l'SDK sta gia'
        eseguendo. Per quello c'e' l'interruttore della macchina.
        """
        dati = {"dalle": time.strftime("%Y-%m-%d %H:%M:%S"),
                "motivo": motivo or "STOP EMERGENZA dall'interfaccia"}
        percorso = self._file_emergenza()
        try:
            os.makedirs(os.path.dirname(percorso), exist_ok=True)
            with open(percorso + ".tmp", "w") as f:
                json.dump(dati, f)
            os.replace(percorso + ".tmp", percorso)
        except (IOError, OSError) as exc:
            self.log("ATTENZIONE: file di blocco non scritto (%s): il blocco "
                     "vale solo per questa interfaccia" % exc)
        self.emergenza = dati
        self._stop.set()
        self._fine_rullo.set()
        self.log("STOP EMERGENZA: tutto bloccato. Riprende solo con l'ok del "
                 "tecnico.")
        return self.stato()

    def sblocca_emergenza(self, nota: str = "") -> Dict[str, object]:
        """L'ok del tecnico: ha controllato motori e percorso pellicola."""
        if self.job.running:
            raise ScannerError("il lavoro interrotto non si e' ancora chiuso: "
                               "riprovare tra qualche secondo")
        try:
            os.remove(self._file_emergenza())
        except OSError:
            pass
        self.emergenza = None
        self._stop.clear()
        self._calibra_dopo = time.time()
        self.log("Emergenza chiusa: ripresa autorizzata dal tecnico%s. Prima "
                 "di lavorare va rifatta la calibrazione (azzera i motori)."
                 % ((" (%s)" % nota) if nota else ""))
        return self.stato()

    def _libera(self) -> ScannerBackend:
        """La macchina, ma solo se nessun lavoro la sta usando.

        I tasti di movimento dell'interfaccia passavano direttamente al
        backend, anche a rullo in corso: due comandi di trasporto sulla stessa
        pellicola, esattamente quello che il lock dei lavori deve impedire.
        """
        if self.job.running:
            raise ScannerError("c'e' un lavoro in corso: aspettare la fine o "
                               "premere STOP prima di muovere la macchina")
        return self._require()

    def muovi(self, mm: float) -> Dict[str, object]:
        self._libera().advance(float(mm))
        return self.stato()

    def fotogramma(self, avanti: bool = True,
                   formato: str = "135-full") -> Dict[str, object]:
        """Il carrello porta in finestra il fotogramma successivo o il
        precedente (per trovare il primo dopo aver inserito la pellicola)."""
        backend = self._libera()
        fmt = FORMATS.get(formato, FORMATS["135-full"])
        if avanti:
            backend.prossimo_fotogramma(fmt)
        elif hasattr(backend, "fotogramma_precedente"):
            backend.fotogramma_precedente(fmt)
        else:
            backend.advance(-fmt.pitch)
        return self.stato()

    def fuoco(self, mm: float) -> Dict[str, object]:
        """Il tasto Fuoco: l'autofocus scelto (con "spento", quello di serie,
        perche' chi preme il tasto lo vuole); senza autofocus nel backend, il
        posizionamento di prima."""
        backend = self._libera()
        metodo = self.cfg.autofocus if self.cfg.autofocus != "spento" else "gi"
        if not backend.autofocus(metodo):
            backend.set_focus(float(mm))
        else:
            self.log("Autofocus (%s) fatto." % metodo)
        return self.stato()

    def ferma(self) -> Dict[str, object]:
        """STOP.

        Non e' un freno di emergenza e non finge di esserlo: la macchina sta
        tirando pellicola dentro un percorso ottico, e strapparla a meta'
        passaggio significa lasciarla in una posizione che nessuno conosce.
        Quello che fa e' chiedere di fermarsi al primo punto in cui fermarsi
        non rompe niente - fra un passaggio e l'altro, non a meta' - e da li'
        spegnere le lampade e rimettere il trasporto dov'era.

        Il tempo di attesa massimo e' quindi un passaggio di canale, qualche
        secondo, non un rullo intero come prima.
        """
        if not self.job.running:
            self._stop.set()      # se un lavoro parte ora, nasce gia' fermato
            return {"ok": True, "fermato": False,
                    "messaggio": "Non c'e' nessun lavoro in corso."}
        self._stop.set()
        self.log("STOP: arresto richiesto dall'operatore.")
        self.job.message = "Arresto richiesto..."
        return {"ok": True, "fermato": True,
                "messaggio": "Arresto richiesto: la macchina si ferma appena "
                             "finisce il passaggio in corso."}

    def annullato(self) -> bool:
        """Il gancio che i cicli lunghi interrogano per sapere se mollare."""
        return self._stop.is_set()

    # ------------------------------------------------------------------

    def _avvia(self, kind: str, fn: Callable, total: int = 0) -> None:
        """Avvia un lavoro in background. Rifiuta se ce n'e' gia' uno in corso:
        la macchina e' una sola."""
        if not self._lock.acquire(blocking=False):
            raise ScannerError("c'e' gia' un lavoro in corso")

        self._stop.clear()
        self.job = JobState(running=True, kind=kind, total=total,
                            log=self.job.log[-40:])

        def worker():
            try:
                fn()
            except Annullato:
                # non e' un errore: e' quello che l'operatore ha chiesto.
                self.log("Fermato dall'operatore.")
                self.job.message = "Fermato dall'operatore"
            except Emergenza:
                self.job.message = "BLOCCATO: STOP EMERGENZA"
                self.log("Lavoro interrotto dallo STOP EMERGENZA: niente "
                         "espulsione, niente riavvolgimento.")
                rullo = getattr(self, "_rullo_in_corso", None)
                if kind == "rullo" and rullo:
                    try:
                        self.archivio.rullo_stato(rullo, "problema")
                    except Exception:                   # noqa: BLE001
                        pass
            except Exception as exc:                    # noqa: BLE001
                self.job.errors.append(str(exc))
                self.log("ERRORE: %s" % exc)
                self.log(traceback.format_exc(limit=2).strip().replace("\n", " | "))
            finally:
                if self._stop.is_set():
                    self._a_riposo()
                self.job.running = False
                self.job.progress = 1.0
                self._lock.release()

        threading.Thread(target=worker, daemon=True).start()

    def _a_riposo(self) -> None:
        """Dopo uno STOP: lampade spente, niente altri movimenti.

        Si esegue nel thread del lavoro, mai in quello della richiesta HTTP:
        due thread che parlano insieme alla stessa macchina del 1995 e' il
        modo migliore per ritrovarsi con un comando a meta'.

        La pellicola NON si espelle da sola: se si preme STOP di solito e'
        perche' qualcosa non va nel percorso, e tirare ancora peggiora.
        """
        b = self.backend
        if b is None:
            return
        for ch in ("R", "G", "B", "IR"):
            try:
                b.lamp(ch, False)
            except Exception:                           # noqa: BLE001
                pass
        self.log("Macchina a riposo: lampade spente, pellicola dove si trova.")

    # ------------------------------------------------------------------
    # Sensori, caricamento, espulsione
    # ------------------------------------------------------------------

    def sensori(self) -> FilmSensors:
        try:
            return self._require().read_sensors()
        except Exception:                               # noqa: BLE001
            return FilmSensors(disponibili=False)

    def attendi_caricamento(self, timeout: float = 300.0) -> bool:
        """Aspetta che l'operatore infili la striscia nel sensore d'ingresso.

        Ritorna False se i sensori non sono disponibili o scade il tempo: in
        quel caso si parte a comando, come prima.
        """
        s = self.sensori()
        if not s.disponibili:
            return False
        scadenza = time.time() + timeout
        while time.time() < scadenza:
            if self._stop.is_set():
                return False
            if self.sensori().ingresso:
                self.log("Pellicola rilevata in ingresso.")
                return True
            self.progress("In attesa della pellicola", 0.0)
            time.sleep(0.4)
        self.log("Nessuna pellicola inserita entro il tempo previsto.")
        return False

    def espelli(self, verso: str = "davanti") -> None:
        """Espelle la pellicola dal davanti (da dove e' entrata) o dal retro.

        Con il carrello Gretag il verso lo decide il carrello stesso (flag 5,
        "Eject to Back"): sulla riserva e' a 0, quindi esce dal davanti.

        Due strade, in ordine di preferenza:

        1. comando di espulsione della macchina, se esiste;
        2. avanzamento a passi finche' il sensore di USCITA non si libera.

        Non si usa mai un avanzamento fisso: con strisce di lunghezza variabile
        non si sa quanta pellicola resti nel percorso, e sbagliare per eccesso
        vuol dire tirare a vuoto un motore del 1995. C'e' comunque un limite di
        corsa: se dopo quello il sensore risulta ancora coperto, la pellicola e'
        inceppata e continuare peggiorerebbe le cose.
        """
        if verso not in ("davanti", "retro"):
            raise ValueError("verso di espulsione sconosciuto: %s" % verso)

        def job():
            backend = self._require()

            if getattr(backend, "supports_eject", False):
                try:
                    self.progress("Espulsione dal %s" % verso, 0.5)
                    esito = backend.eject(verso)
                    self.log(esito or ("Pellicola espulsa dal %s." % verso))
                    self.progress("Espulsione completata", 1.0)
                    return
                except NotImplementedError:
                    pass

            s = backend.read_sensors()
            if not s.disponibili:
                self.log("Sensori non disponibili: espulsione non automatica. "
                         "Avanzare a mano e togliere la pellicola.")
                return

            percorso = 0.0
            passo = min(10.0, getattr(backend, "max_regola_mm", 10.0))
            while percorso < self.cfg.eject_max_mm:
                if self._stop.is_set():
                    self.log("Espulsione interrotta dall'operatore.")
                    return
                if not backend.read_sensors().uscita:
                    self.log("Pellicola espulsa dopo %.0f mm." % percorso)
                    self.progress("Espulsione completata", 1.0)
                    return
                backend.advance(passo)
                percorso += passo
                self.progress("Espulsione: %.0f mm" % percorso,
                              percorso / self.cfg.eject_max_mm)

            self.job.errors.append(
                "Espulsione non riuscita: dopo %.0f mm il sensore di uscita e' "
                "ancora coperto. Probabile inceppamento: verificare a mano "
                "prima di comandare altri movimenti."
                % self.cfg.eject_max_mm)
            self.log(self.job.errors[-1])

        self._avvia("espulsione", job)

    def carica(self, formato: str) -> None:
        """Guide al formato e carrello pronto a ricevere la pellicola."""
        def job():
            if formato not in FORMATS:
                raise ValueError("formato sconosciuto: %s" % formato)
            self.progress("Carrello in preparazione", 0.3)
            self._require().prepara_carico(formato)
            self.log("Carrello pronto: inserire la pellicola (%s)." % formato)
            self.progress("Inserire la pellicola", 1.0)
        self._avvia("carico", job)

    def calibrazione_di_oggi(self) -> bool:
        """La lampada e il sensore cambiano con le ore di accensione e con la
        temperatura: il primo lavoro della giornata si fa con una calibrazione
        fatta oggi, a macchina calda e appena azzerata."""
        if self.calib is None:
            return False
        if self.calib.created < self._calibra_dopo:
            return False          # fatta prima di uno STOP EMERGENZA
        return (datetime.date.fromtimestamp(self.calib.created)
                == datetime.date.today())

    def _serve_calibrazione(self) -> None:
        if isinstance(self.backend, OriginaleBackend):
            return            # col programma originale calibra lui
        if not self.calibrazione_di_oggi():
            raise ScannerError(
                "oggi la macchina non e' ancora stata calibrata: prima di "
                "anteprime e scansioni premere Calibra, con il percorso "
                "pellicola vuoto (azzera anche i motori)")
        # il flat e' stato letto con una certa base d'esposizione: se poi la
        # si cambia, ogni lettura esce scalata di un fattore per canale
        note = self.calib.note or ""
        voluto = self._tag_esposizione()
        altro = [t for t in TAG_ESPOSIZIONE if t != voluto and t in note]
        if altro:
            raise ScannerError(
                "la calibrazione di oggi e' stata fatta con l'esposizione %s: "
                "dopo aver cambiato l'impostazione va rifatta (Calibra)"
                % ("automatica" if altro[0] == TAG_ESPOSIZIONE[0] else "di fabbrica"))

    def _tag_esposizione(self) -> str:
        return TAG_ESPOSIZIONE[0 if self.cfg.esposizione_auto else 1]

    # --- impostazioni della macchina ----------------------------------

    AUTOFOCUS = ("gi", "rapido", "nominale", "spento")

    def impostazioni_macchina(self) -> Dict[str, object]:
        return {"esposizione_auto": self.cfg.esposizione_auto,
                "pressore_auto": self.cfg.pressore_auto,
                "avanzamento_automatico": self.cfg.avanzamento_automatico,
                "autofocus": self.cfg.autofocus}

    def imposta_macchina(self, valori: Dict[str, object]) -> Dict[str, bool]:
        if self.job.running:
            raise ScannerError("c'e' un lavoro in corso: cambiare le "
                               "impostazioni della macchina a lavoro finito")
        for k in ("esposizione_auto", "pressore_auto", "avanzamento_automatico"):
            if k in valori:
                setattr(self.cfg, k, bool(valori[k]))
        if "autofocus" in valori:
            if valori["autofocus"] not in self.AUTOFOCUS:
                raise ValueError("autofocus sconosciuto: %s" % valori["autofocus"])
            self.cfg.autofocus = str(valori["autofocus"])
        return self.impostazioni_macchina()

    def _autofocus(self, backend, metodo: Optional[str] = None) -> bool:
        """L'autofocus scelto nelle impostazioni. Se non trova il fuoco la
        macchina torna alla posizione nominale: si annota e si va avanti."""
        metodo = metodo or self.cfg.autofocus
        if metodo == "spento":
            return False
        try:
            if backend.autofocus(metodo):
                self.log("Autofocus (%s) fatto." % metodo)
                return True
        except (Emergenza, MotoriBloccati):
            raise
        except ScannerError as exc:
            self.log("Autofocus (%s) non riuscito: %s" % (metodo, exc))
        return False

    def finestra_carrello(self, numero: int, mostra: bool) -> Dict[str, object]:
        """Una finestra del GateControl sul monitor del PC, per il tecnico."""
        if numero not in (1, 3, 4, 5):
            raise ValueError("finestra sconosciuta: %s" % numero)
        self._libera().finestra_carrello(numero, mostra)
        self.log("Finestra %d del carrello %s." % (numero, "aperta" if mostra else "chiusa"))
        return self.stato()

    def stato_testa(self) -> Dict[str, object]:
        """Temperature e tensioni della testa: si leggono a richiesta, non a
        ogni aggiornamento della pagina. Una che non risponde non toglie
        l'altra."""
        backend = self._libera()
        esito: Dict[str, object] = {}
        for nome in ("temperatura", "tensioni"):
            try:
                esito[nome] = getattr(backend, nome)()
            except Emergenza:
                raise
            except Exception as exc:                    # noqa: BLE001
                esito[nome + "_errore"] = str(exc)
        return esito

    def alza_pressore(self) -> Dict[str, object]:
        """Il pressore su, a mano: dopo un PressoreBloccato o per liberare la
        pellicola. Vale anche con lo STOP EMERGENZA attivo, perche' non muove
        la pellicola; non a lavoro in corso, quando il pressore e' del lavoro."""
        if self.job.running:
            raise ScannerError("c'e' un lavoro in corso: il pressore lo "
                               "comanda lui")
        if self.backend is None:
            raise ScannerError("macchina non connessa")
        if not getattr(self.backend, "supports_pressore", False):
            raise ScannerError("questo collegamento non comanda il pressore")
        self.backend.pressore(False)
        self.log("Pressore alzato a mano.")
        return self.stato()

    def _esposizione_fissa(self, preset) -> Optional[Dict[str, float]]:
        """Con l'esposizione automatica spenta: x1 su tutti i canali, senza
        misura. Accesa: None, e decide la misura per canale."""
        if self.cfg.esposizione_auto:
            return None
        return dict((c, 1.0) for c in preset.channels)

    @contextlib.contextmanager
    def _sotto_pressore(self, backend):
        """Pressore giu' per la lettura, su appena finita, anche se la lettura
        fallisce. Se non si rialza, la pellicola non si deve muovere piu':
        PressoreBloccato ferma il rullo invece di passare al fotogramma
        successivo."""
        usa = (self.cfg.pressore_auto
               and getattr(backend, "supports_pressore", False))
        if usa:
            backend.pressore(True)
        try:
            yield
        finally:
            if usa:
                try:
                    backend.pressore(False)
                except Emergenza:
                    raise
                except Exception as exc:                # noqa: BLE001
                    raise PressoreBloccato(
                        "il pressore non si e' rialzato (%s): la pellicola "
                        "non si muove finche' non lo si controlla" % exc)

    def calibra(self) -> None:
        def job():
            backend = self._require()
            # Primo passo: i motori a zero. Lo faceva il programma originale
            # all'avvio; senza di lui, e con il flag "No Reset" del carrello,
            # tamburo e guide restano dove si sono fermati, e un tamburo
            # fermo storto davanti alla luce fa leggere il buio.
            self.progress("Azzeramento dei motori", 0.0)
            try:
                if backend.azzera_motori():
                    self.log("Motori azzerati (Gretag Reset).")
            except MotoriBloccati:
                self.log("Azzeramento dei motori saltato: l'agente e' a "
                         "motori bloccati. Tamburo e guide restano dove sono.")
            # Secondo passo, con l'esposizione automatica: la calibrazione
            # della macchina stessa (calibrate_all, come il programma
            # originale all'avvio), che da' i tempi di esposizione per LED.
            tag = ""
            if backend.esposizione_auto(self.cfg.esposizione_auto):
                tag = self._tag_esposizione()
                if self.cfg.esposizione_auto:
                    self.progress("Calibrazione della macchina (LED e tempi)", 0.03)
                    try:
                        backend.calibra_macchina()
                        self.log("Calibrazione della macchina fatta: si usano "
                                 "i suoi tempi di esposizione.")
                    except MotoriBloccati:
                        self.log("Calibrazione della macchina saltata (motori "
                                 "bloccati): tempi di esposizione dell'ultima "
                                 "calibrazione, o di fabbrica.")
                    except Emergenza:
                        raise
                    except ScannerError as exc:
                        self.log("Calibrazione della macchina non riuscita: "
                                 "%s. Tempi dell'ultima calibrazione, o di "
                                 "fabbrica." % exc)
            self.progress("Calibrazione in corso", 0.05)
            calib = run_calibration(backend, progress=self.progress,
                                    annulla=self.annullato)
            if tag:
                calib.note = ("%s %s" % (calib.note or "", tag)).strip()
            path = calib.save(self.cfg.path("calib"))
            self.calib = calib
            self.log("Calibrazione salvata: %s" % os.path.basename(path))
        self._avvia("calibrazione", job)

    def anteprima(self, formato: str) -> None:
        self._serve_calibrazione()

        def job():
            fmt = FORMATS[formato]
            backend = self._require()
            backend.seleziona_formato(formato)
            backend.esposizione_auto(self.cfg.esposizione_auto)
            with self._sotto_pressore(backend):
                raw = scan_frame(backend, self.cfg, PRESETS["provino"], fmt,
                                 self.calib, progress=self.progress,
                                 annulla=self.annullato,
                                 channel_exposure=self._esposizione_fissa(
                                     PRESETS["provino"]))
            # L'anteprima serve a vedere se il fotogramma c'e' e com'e': il
            # ritaglio e il raddrizzamento automatici erano la parte piu'
            # lenta, e li' non servono. Si fanno nella scansione del rullo.
            opts = ProcessOptions(**{k: v for k, v in vars(self.options).items()})
            opts.auto_crop = False
            opts.auto_straighten = False
            res = process(raw, opts, progress=self.progress)
            self.ultimo_raw = raw
            self.preview = res.image
            self.base_lineare = res.base
            self.preview_ridotta = False
            self.preview_neg = res.negativo
            for w in res.warnings:
                self.log("Avviso: " + w)
        self._avvia("anteprima", job)

    # ------------------------------------------------------------------

    def scansiona_rullo(self, rullo_id: int) -> None:
        """Scansiona un rullo registrato in archivio, salvando nella cartella
        del cliente e registrando ogni fotogramma nel database."""
        rullo = self.archivio.rullo(rullo_id)
        if rullo is None:
            raise ValueError("rullo inesistente")
        ordine = self.archivio.ordine(rullo.ordine_id)
        if ordine is None:
            raise ValueError("ordine inesistente")

        fmt = FORMATS[rullo.formato]
        preset = PRESETS[rullo.preset]
        # Digital ICE si sceglie rullo per rullo. Senza: niente lettura IR
        # (un passaggio in meno a fotogramma) e niente pulizia IR.
        if rullo.ice and not preset.use_ir:
            self.log("Digital ICE chiesto, ma il preset %s non usa l'IR "
                     "(bianco e nero all'argento: opaco all'infrarosso)."
                     % rullo.preset)
        preset = dataclasses.replace(preset, use_ir=bool(rullo.ice) and preset.use_ir)
        dest = self.archivio.cartella_ordine(self.cfg.out_dir, ordine.id)
        mirror = RollMirror(flip_h=rullo.specchiato, source="operatore",
                            confirmed=rullo.conf_specchio)

        opts = ProcessOptions(**{k: v for k, v in vars(self.options).items()})
        opts.profile = PROFILES.get(rullo.profilo, PROFILES["c41"])
        opts.roll_mirror = mirror
        if not rullo.ice:
            opts.ir_strength = 0.0

        if isinstance(self.backend, OriginaleBackend):
            return self.ricevi_da_originale(rullo_id)
        self._serve_calibrazione()

        def job():
            backend = self._require()
            backend.seleziona_formato(rullo.formato)
            backend.esposizione_auto(self.cfg.esposizione_auto)
            ice_sdk = False
            if rullo.ice:
                # Due parti: l'ASF dell'SDK (gi_set_asf_mode, sperimentale:
                # su scan_raw l'effetto va visto sulla macchina) e la nostra
                # pulizia dal canale IR, che pero' c'e' solo quando l'IR
                # dell'SDK e' verificato.
                if "IR" not in getattr(backend, "canali_disponibili", ["IR"]):
                    self.log("Digital ICE: il canale IR non e' ancora "
                             "verificato su questa macchina, la nostra "
                             "pulizia IR resta spenta.")
                try:
                    ice_sdk = backend.digital_ice(True)
                    if ice_sdk:
                        self.log("Digital ICE (ASF) acceso nella macchina.")
                except Emergenza:
                    raise
                except Exception as exc:                # noqa: BLE001
                    self.log("Digital ICE (ASF) non acceso: %s" % exc)
            mancanti = [c for c in preset.channels
                        if c not in getattr(backend, "canali_disponibili", preset.channels)]
            if mancanti:
                self.log("Canali non disponibili su questa macchina, saltati: %s"
                         % ", ".join(mancanti))

            if self.cfg.auto_start:
                s = backend.read_sensors()
                if s.disponibili and not s.ingresso:
                    self.progress("In attesa della pellicola", 0.0)
                    scadenza = time.time() + 300.0
                    while time.time() < scadenza and not self._stop.is_set():
                        if backend.read_sensors().ingresso:
                            self.log("Pellicola rilevata: avvio scansione.")
                            break
                        time.sleep(0.4)
                    else:
                        self.log("Nessuna pellicola inserita: annullo.")
                        return

            self.archivio.rullo_stato(rullo_id, "in_corso")
            self._rullo_in_corso = rullo_id
            fatti = 0
            vuoti = 0
            # L'esposizione per canale si misura UNA volta per rullo, non a
            # ogni fotogramma: dipende dall'emulsione e dalla lampada, che nel
            # corso di un rullo non cambiano. Misurarla ogni volta farebbe
            # oscillare il colore da un fotogramma all'altro.
            coda = None      # fotogrammi ancora davanti alla finestra
            gains = self._esposizione_fissa(preset)
            if (gains is None and preset.auto_exposure
                    and preset.channel_exposure is None):
                self.progress("Misura esposizione per canale", 0.0)
                avvisi: List[str] = []
                with self._sotto_pressore(backend):
                    gains = measure_channel_exposure(backend, preset.channels,
                                                     self.calib,
                                                     annulla=self.annullato,
                                                     avvisi=avvisi)
                for a in avvisi:
                    self.log("Attenzione: " + a)
                self.log("Esposizioni: " + ", ".join(
                    "%s x%.2f" % (k, v) for k, v in gains.items()))

            # Quanti fotogrammi ha la striscia non lo sa nessuno prima di
            # leggerla, e chiederlo all'operatore vuol dire farglielo indovinare.
            # Si conta scansionando e ci si ferma quando la pellicola finisce.
            # Il tetto del formato serve solo a non tirare all'infinito se il
            # rilevamento della coda sbaglia: e' una rete, non un obiettivo.
            automatico = not rullo.attesi
            limite = rullo.attesi or fmt.max_fotogrammi

            # Un rullo puo' arrivare tagliato in strisce da 3-4 fotogrammi:
            # a fine striscia si espelle, si riarma il carrello e si aspetta
            # la successiva (_prossima_striscia). I numeri continuano da una
            # striscia all'altra. "i" avanza solo sui fotogrammi veri: la coda
            # iniziale di una striscia non prende un numero.
            self._fine_rullo.clear()
            espulsa = False
            strisce = 1
            inizio_striscia = True
            # l'autofocus si fa sul primo fotogramma vero di ogni striscia
            fuoco_da_fare = True
            i = 0
            giri = 0
            while i < limite and giri < limite + 50:
                giri += 1
                conta = True
                if self.emergenza:
                    raise Emergenza("STOP EMERGENZA")
                if self._stop.is_set():
                    self.log("Interrotto dall'operatore al fotogramma %d." % (i + 1))
                    break

                # Avanzamento automatico spento: ogni fotogramma si mette in
                # finestra a mano, con il video.
                if not self.cfg.avanzamento_automatico:
                    inizio_striscia = False
                    esito = self._posizionamento_manuale(
                        backend, fmt, "mettere in finestra il fotogramma %d"
                        % (i + 1))
                    if esito == "fine":
                        self.log("Rullo chiuso dall'operatore.")
                        break
                    if esito == "striscia":
                        continua, espulsa = self._prossima_striscia(
                            backend, rullo.formato)
                        if not continua:
                            break
                        strisce += 1
                        fuoco_da_fare = True
                        continue

                # Le strisce hanno lunghezza variabile: "attesi" e' un massimo,
                # non una certezza. Si guardano prima i sensori di bordo, che
                # sono un dato oggettivo; la stima ottica e' il ripiego per i
                # backend che non li espongono.
                if i > 0 and coda is None:
                    s = backend.read_sensors()
                    if s.disponibili:
                        if not s.ingresso:
                            # ATTENZIONE: il sensore libero non significa
                            # pellicola finita. La coda e' al sensore
                            # d'ingresso, ma fra quello e la finestra di
                            # scansione c'e' ancora pellicola, e quei
                            # fotogrammi vanno letti.
                            resto = self.cfg.sensore_ingresso_finestra_mm
                            if resto is None:
                                self.job.errors.append(
                                    "sensore_ingresso_finestra_mm non e' "
                                    "configurato: gli ultimi fotogrammi della "
                                    "striscia potrebbero andare persi. "
                                    "Misurare la distanza sensore-finestra e "
                                    "annotarla in documentazione/hardware/note-macchina.md.")
                                self.log(self.job.errors[-1])
                                coda = 0
                            else:
                                coda = max(0, int(resto / fmt.pitch))
                                self.log(
                                    "Coda della striscia al sensore d'ingresso: "
                                    "restano %d fotogrammi davanti alla finestra."
                                    % coda)
                    elif (not getattr(backend, "fine_striscia_dal_carrello", False)
                          and not film_present(backend, self.calib)):
                        self.log("Fine della striscia dopo %d fotogrammi." % i)
                        break

                if coda is not None:
                    if coda <= 0:
                        self.log("Striscia terminata dopo %d fotogrammi." % i)
                        break
                    coda -= 1
                self.job.frame = i + 1
                base = i / float(max(limite, 1))
                # In automatico non si scrive "di 72": sarebbe un numero
                # inventato. Si scrive quello che si sa, cioe' a che fotogramma
                # siamo arrivati.
                self.progress(
                    ("Fotogramma %d" % (i + 1)) if automatico
                    else ("Fotogramma %d di %d" % (i + 1, rullo.attesi)),
                    min(base, 0.97))

                nome = "%s_%s_%03d" % (ordine.codice, rullo.etichetta.replace(" ", "-"), i + 1)
                # Autofocus a inizio striscia, col pressore giu' come per la
                # lettura: la pellicola deve stare dove sara' letta.
                if fuoco_da_fare and self.cfg.autofocus != "spento":
                    with self._sotto_pressore(backend):
                        self._autofocus(backend)

                # il numero stampato sul bordo, se la macchina lo sa dire:
                # mai bloccante, e se manca il rullo va avanti lo stesso
                try:
                    bordo = backend.numero_bordo() or ""
                except Emergenza:
                    raise
                except Exception:                       # noqa: BLE001
                    bordo = ""
                try:
                    with self._sotto_pressore(backend):
                        raw = scan_frame(backend, self.cfg, preset, fmt,
                                         self.calib, frame_index=i,
                                         channel_exposure=gains,
                                         annulla=self.annullato)

                    vuoto, escursione = fotogramma_vuoto(
                        raw, self.cfg.soglia_fotogramma_vuoto_d)
                    primo = inizio_striscia
                    inizio_striscia = False
                    if vuoto and primo:
                        # appena caricata, la finestra sta spesso sulla coda
                        # della striscia: non e' un fotogramma (e il fuoco
                        # si rifa' sul prossimo)
                        raise _CodaIniziale()
                    fuoco_da_fare = False
                    if vuoto:
                        # Non esposto (o tutto opaco): niente export, ma il
                        # grezzo si tiene sempre, cosi' se la soglia ha
                        # sbagliato il fotogramma si rielabora senza
                        # rimettere la pellicola. Il numero resta quello
                        # della striscia, come lo conta il cliente.
                        raw_path = raw.save(self.cfg.path("raw"), nome)
                        self.archivio.fotogramma_registra(
                            rullo_id, i + 1, file_raw=raw_path, stato="vuoto",
                            avvisi="non esposto: escursione %.2f D" % escursione,
                            numero_bordo=bordo)
                        vuoti += 1
                        self.log("Fotogramma %d vuoto (escursione %.2f D): "
                                 "saltato, grezzo conservato." % (i + 1, escursione))
                        raise _FotogrammaVuoto()

                    raw_path = ""
                    if self.cfg.keep_raw:
                        raw_path = raw.save(self.cfg.path("raw"), nome)

                    res = process(raw, opts)
                    ext = {"tiff16": ".tif", "tiff8": ".tif",
                           "png": ".png", "jpeg": ".jpg"}.get(self.cfg.export_format, ".tif")
                    out_path = os.path.join(dest, nome + ext)
                    export(res.image, out_path, self.cfg.export_format,
                           self.cfg.jpeg_quality)

                    mask_path = ""
                    if self.cfg.export_ir_mask and res.mask is not None:
                        mask_path = export_mask(res.mask,
                                                os.path.join(dest, nome + "_maschera.png"))

                    self.archivio.fotogramma_registra(
                        rullo_id, i + 1, file_out=out_path, file_raw=raw_path,
                        file_maschera=mask_path,
                        rotazione=res.orientation.rotation if res.orientation else 0,
                        stato="ok", avvisi=" | ".join(res.warnings),
                        numero_bordo=bordo)
                    self.ultimo_raw = raw
                    self.preview = res.image
                    self.base_lineare = res.base
                    self.preview_ridotta = False
                    self.preview_neg = res.negativo
                    fatti += 1
                    for w in res.warnings:
                        self.log("Fotogramma %d: %s" % (i + 1, w))

                except _FotogrammaVuoto:
                    pass

                except (Emergenza, PressoreBloccato):
                    raise

                except _CodaIniziale:
                    conta = False
                    self.log("Inizio striscia sulla coda (vuota): si passa "
                             "al primo fotogramma.")

                except Annullato:
                    # STOP non e' un fotogramma fallito: non si registra come
                    # errore e soprattutto non si passa al successivo.
                    self.log("Interrotto durante il fotogramma %d." % (i + 1))
                    break

                except Exception as exc:                # noqa: BLE001
                    # un fotogramma perso non deve far perdere il rullo
                    self.job.errors.append("fotogramma %d: %s" % (i + 1, exc))
                    self.log("Fotogramma %d fallito: %s" % (i + 1, exc))
                    self.archivio.fotogramma_registra(
                        rullo_id, i + 1, stato="errore", avvisi=str(exc))

                if self.emergenza:
                    raise Emergenza("STOP EMERGENZA")
                if self._stop.is_set():
                    self.log("Interrotto dall'operatore dopo il fotogramma %d."
                             % (i + 1))
                    break
                if self._fine_rullo.is_set():
                    self.log("Rullo chiuso dall'operatore dopo il fotogramma %d."
                             % (i + (1 if conta else 0)))
                    break
                # Se la macchina ha rifiutato un'esposizione, il backend ha
                # imparato il suo limite: da qui in poi i fotogrammi si
                # chiedono gia' entro quello, e il registro lo dice una volta.
                tetto = getattr(backend, "max_esposizione", None)
                if gains and tetto and any(v > tetto for v in gains.values()):
                    gains = dict((k, min(v, tetto)) for k, v in gains.items())
                    self.log("Esposizione ridotta al limite della macchina "
                             "(x%.2f): %s" % (tetto, ", ".join(
                                 "%s x%.2f" % kv for kv in gains.items())))
                try:
                    if self.cfg.avanzamento_automatico:
                        backend.prossimo_fotogramma(fmt)
                except FinePellicola:
                    self.log("Fine della striscia %d: il carrello non trova "
                             "altri fotogrammi dopo il %d."
                             % (strisce, i + (1 if conta else 0)))
                    continua, espulsa = self._prossima_striscia(
                        backend, rullo.formato)
                    if not continua:
                        break
                    strisce += 1
                    inizio_striscia = True
                    fuoco_da_fare = True
                except Emergenza:
                    raise
                except Exception as exc:                # noqa: BLE001
                    # Il carrello non trova il fotogramma: negativo sbiadito,
                    # interfotogramma invisibile. Non si chiude il rullo: si
                    # apre il video e lo si mette in finestra a mano.
                    esito = self._posizionamento_manuale(
                        backend, fmt, "il carrello non trova il fotogramma "
                        "successivo (%s)" % str(exc).splitlines()[0])
                    if esito == "fine":
                        self.log("Rullo chiuso dall'operatore.")
                        if conta:
                            i += 1
                        break
                    if esito == "striscia":
                        continua, espulsa = self._prossima_striscia(
                            backend, rullo.formato)
                        if not continua:
                            break
                        strisce += 1
                        inizio_striscia = True
                        fuoco_da_fare = True
                if conta:
                    i += 1

            # una striscia piu' corta del previsto non e' un problema: e' la
            # normalita' con pellicole di lunghezza variabile
            stato = "completato" if fatti > 0 else "problema"
            if self._stop.is_set():
                # Interrotto non e' rotto: il rullo e' rimasto a meta' perche'
                # qualcuno l'ha deciso, e chi lo riapre domani deve leggere
                # questo, non "problema".
                stato = "problema"
                self.job.message = ("Fermato dall'operatore dopo %d fotogramm%s"
                                    % (fatti, "a" if fatti == 1 else "i"))
            try:
                self._riordina_per_bordo(rullo_id)
            except Exception as exc:                    # noqa: BLE001
                self.log("Riordino per numeri del bordo saltato: %s" % exc)
            if ice_sdk:
                try:
                    backend.digital_ice(False)
                except Exception as exc:                # noqa: BLE001
                    self.log("Digital ICE (ASF) non spento: %s" % exc)
            if (self.cfg.auto_eject and fatti > 0 and not espulsa
                    and not self._stop.is_set()):
                try:
                    if getattr(backend, "supports_eject", False):
                        verso = self.cfg.verso_espulsione_fine_rullo
                        esito = backend.eject(verso)
                        self.log(esito or ("Pellicola espulsa dal %s." % verso))
                    else:
                        sens = backend.read_sensors()
                        percorso = 0.0
                        passo = min(10.0, getattr(backend, "max_regola_mm", 10.0))
                        while sens.disponibili and sens.uscita and \
                                percorso < self.cfg.eject_max_mm:
                            backend.advance(passo)
                            percorso += passo
                            sens = backend.read_sensors()
                        if percorso:
                            self.log("Pellicola espulsa dopo %.0f mm." % percorso)
                except Exception as exc:                # noqa: BLE001
                    self.log("Espulsione non riuscita: %s" % exc)

            self.archivio.rullo_stato(rullo_id, stato)
            self.archivio.ordine_stato(ordine.id, "in_lavorazione")
            self.aggiorna_registro()
            self.pulizia_scaduti()
            if self._stop.is_set():
                self.log("Rullo lasciato a meta': %d fotogramm%s salvat%s. "
                         "Riaprendolo, la scansione riprende dal successivo."
                         % (fatti, "a" if fatti == 1 else "i",
                            "o" if fatti == 1 else "i"))
            elif automatico:
                self.log("Rullo terminato: %d fotogrammi%s%s." % (
                    fatti, (", %d vuoti" % vuoti) if vuoti else "",
                    (", in %d strisce" % strisce) if strisce > 1 else ""))
                self.job.message = "Rullo terminato: %d fotogrammi" % fatti
                if i >= limite:
                    self.log(
                        "Raggiunto il tetto di %d fotogrammi per il formato %s: "
                        "se la striscia continua, scansionare il resto come "
                        "rullo nuovo." % (limite, rullo.formato))
            else:
                self.log("Rullo terminato: %d fotogrammi su %d%s%s." % (
                    fatti, rullo.attesi, (", %d vuoti" % vuoti) if vuoti else "",
                    (", in %d strisce" % strisce) if strisce > 1 else ""))
                self.job.message = "Rullo terminato: %d fotogrammi" % fatti

        self._avvia("rullo", job,
                    total=rullo.attesi or FORMATS[rullo.formato].max_fotogrammi)

    def ricevi_da_originale(self, rullo_id: int) -> None:
        """Collegamento "programma originale": si scansiona da PhxScanner, e
        ogni immagine che l'osservatore copia diventa un fotogramma di questo
        rullo, elaborato dalla nostra pipeline. Si resta in ascolto finche'
        non si preme STOP.

        Le immagini sono i dati del DMA, prima delle correzioni dell'SDK:
        niente calibrazione nostra (e' fatta per le nostre letture, non per
        le sue), ma la pipeline normale da li' in poi.
        """
        rullo = self.archivio.rullo(rullo_id)
        if rullo is None:
            raise ValueError("rullo inesistente")
        ordine = self.archivio.ordine(rullo.ordine_id)
        if ordine is None:
            raise ValueError("ordine inesistente")
        fmt_key = rullo.formato
        dest = self.archivio.cartella_ordine(self.cfg.out_dir, ordine.id)
        opts = ProcessOptions(**{k: v for k, v in vars(self.options).items()})
        opts.profile = PROFILES.get(rullo.profilo, PROFILES["c41"])
        opts.roll_mirror = RollMirror(flip_h=rullo.specchiato, source="operatore",
                                      confirmed=rullo.conf_specchio)

        def job():
            backend = self._require()
            self.archivio.rullo_stato(rullo_id, "in_corso")
            inizio = time.time()
            visti = set()
            fatti = 0
            self.log("In ascolto del programma originale: scansionare da li'. "
                     "STOP per finire il rullo.")
            while not self._stop.is_set():
                nuove = [p for p in backend.nuove(inizio) if p not in visti]
                if not nuove:
                    self.progress("In attesa del programma originale: %d fotogramm%s"
                                  % (fatti, "a" if fatti == 1 else "i"), 0.5)
                    time.sleep(0.5)
                    continue
                for percorso in nuove:
                    visti.add(percorso)
                    i = fatti
                    nome = "%s_%s_%03d" % (ordine.codice,
                                           rullo.etichetta.replace(" ", "-"), i + 1)
                    try:
                        piani, info = backend.leggi(percorso)
                        h, w = next(iter(piani.values())).shape
                        meta = ScanMetadata(
                            timestamp=time.time(), format_key=fmt_key,
                            preset_name="originale", dpi=self.cfg.optical_dpi,
                            bit_depth=16, channels=list(piani),
                            exposures=[1.0], passes_per_channel=1,
                            frame_index=i, position_mm=0.0, focus_mm=0.0,
                            operator_note="dal programma originale: %s"
                                          % os.path.basename(percorso))
                        raw = RawScan(meta)
                        for ch, p in piani.items():
                            raw.add(ch, p.astype(np.float32))
                        raw_path = raw.save(self.cfg.path("raw"), nome) \
                            if self.cfg.keep_raw else ""
                        res = process(raw, opts)
                        ext = {"tiff16": ".tif", "tiff8": ".tif", "png": ".png",
                               "jpeg": ".jpg"}.get(self.cfg.export_format, ".tif")
                        out_path = os.path.join(dest, nome + ext)
                        export(res.image, out_path, self.cfg.export_format,
                               self.cfg.jpeg_quality)
                        self.archivio.fotogramma_registra(
                            rullo_id, i + 1, file_out=out_path, file_raw=raw_path,
                            rotazione=res.orientation.rotation if res.orientation else 0,
                            stato="ok", avvisi=" | ".join(res.warnings))
                        self.ultimo_raw = raw
                        self.preview = res.image
                        self.base_lineare = res.base
                        self.preview_ridotta = False
                        self.preview_neg = res.negativo
                        fatti += 1
                        self.job.frame = fatti
                        self.log("Fotogramma %d dal programma originale (%dx%d, %s)."
                                 % (fatti, w, h, "".join(piani)))
                    except Exception as exc:                # noqa: BLE001
                        self.job.errors.append("%s: %s" % (os.path.basename(percorso), exc))
                        self.log("Immagine %s non elaborata: %s"
                                 % (os.path.basename(percorso), exc))
            self.archivio.rullo_stato(rullo_id, "completato" if fatti else "problema")
            self.archivio.ordine_stato(ordine.id, "in_lavorazione")
            self.aggiorna_registro()
            self.log("Rullo chiuso: %d fotogramm%s dal programma originale."
                     % (fatti, "a" if fatti == 1 else "i"))

        self._avvia("rullo", job, total=0)

    # ------------------------------------------------------------------

    def rielabora(self, opzioni=None, veloce: bool = True,
                  lato_max: int = 1000) -> Dict[str, object]:
        """Rifa' l'elaborazione sull'ultimo grezzo, senza toccare la macchina.

        E' il cuore del lavoro di correzione: si sposta un cursore, si guarda,
        si sposta ancora. Rimettere la pellicola nella macchina a ogni ritocco
        sarebbe assurdo, e su una meccanica del 1995 anche dannoso.
        """
        if opzioni is not None:
            self.options = opzioni

        # Strada veloce: la base c'e' gia' e serve solo l'anteprima. Si lavora
        # su una copia ridotta - quella che il browser mostra comunque - e si
        # rifanno solo i passi per-pixel. Su un PC del 2009 sono decimi di
        # secondo invece di decine di secondi.
        if veloce and self.base_lineare is not None:
            base = self.base_lineare
            h, w = base.shape[:2]
            passo = max(1, int(max(h, w) / float(lato_max)))
            img, _ = finiture(base[::passo, ::passo], self.options)
            self.preview = img
            self.preview_ridotta = passo > 1
            return {"avvisi": [], "ridotta": self.preview_ridotta}

        if self.ultimo_raw is None:
            raise ValueError("nessun fotogramma da rielaborare: fare prima "
                             "un'anteprima o una scansione")
        res = process(self.ultimo_raw, self.options)
        self.preview = res.image
        self.preview_neg = res.negativo
        self.base_lineare = res.base
        self.preview_ridotta = False
        return {"avvisi": res.warnings, "maschera_pct": round(res.mask_coverage, 2)}

    def piena_risoluzione(self) -> None:
        """Rifa' l'anteprima a grandezza naturale, se adesso e' una ridotta.

        Serve quando si chiede la vista 1:1: li' la copia ridotta non basta,
        ed e' proprio guardando i pixel veri che si decide se una scansione va
        rifatta. Costa qualche secondo, ma solo quando lo si chiede davvero.
        """
        if not self.preview_ridotta or self.base_lineare is None:
            return
        img, _ = finiture(self.base_lineare, self.options)
        self.preview = img
        self.preview_ridotta = False

    def campiona_neutro(self, x: float, y: float) -> Dict[str, object]:
        """Prende un punto che deve essere grigio e ci bilancia sopra l'immagine.

        E' il modo piu' affidabile di bilanciare una pellicola: invece di
        indovinare la temperatura, si indica qualcosa che nella realta' era
        neutro - un muro, una camicia bianca, l'asfalto - e si lascia che sia
        l'immagine a dire di quanto e' fuori.

        Il campionamento si fa sull'immagine SENZA le correzioni manuali gia'
        applicate, altrimenti si bilancerebbe sopra un bilanciamento e i
        guadagni si moltiplicherebbero fra loro a ogni clic.
        """
        if self.ultimo_raw is None and self.base_lineare is None:
            raise ValueError("nessun fotogramma su cui campionare")

        from copy import copy
        neutre = copy(self.options)
        neutre.lut = None       # il grigio si cerca prima del look
        neutre.wb_guadagni = None
        neutre.temperatura_k = 0.0
        neutre.tinta_vm = 0.0
        if self.base_lineare is not None:
            pulita, _ = finiture(self.base_lineare, neutre)
        else:
            pulita = process(self.ultimo_raw, neutre).image

        guad = colore.guadagni_da_neutro(pulita, x, y)
        self.options.wb_guadagni = [float(v) for v in guad]
        self.options.temperatura_k = 0.0
        self.rielabora()
        self.log("Bilanciato sul punto campionato: R %.2f G %.2f B %.2f"
                 % tuple(guad))
        return {"guadagni": self.options.wb_guadagni}

    def aggiorna_registro(self) -> str:
        """Riscrive il registro degli ordini nella cartella d'archivio.

        Si chiama a ogni cambiamento - ordine nuovo, rullo finito, rinomina -
        perche' un registro che si aggiorna solo quando qualcuno se lo ricorda
        e' peggio di non averlo: si finisce per fidarsene quando e' vecchio.

        Se fallisce non blocca niente: e' una comodita', non un archivio. Il
        database vero e' un altro.
        """
        try:
            from . import consegne
            percorso = os.path.join(self.cfg.path("archivio"),
                                    self.cfg.registro_nome)
            return consegne.scrivi_registro(self.archivio, percorso)
        except Exception as exc:                        # noqa: BLE001
            self.log("Registro non aggiornato: %s" % exc)
            return ""

    def pulizia_scaduti(self) -> Dict[str, object]:
        """Applica la politica di conservazione. Di serie non cancella niente.

        Si chiama all'avvio del server e alla fine di ogni rullo: sono i due
        momenti in cui lo spazio serve davvero e in cui l'operatore non sta
        aspettando nessuna risposta.
        """
        from . import consegne
        try:
            esito = consegne.pulizia_automatica(
                self.archivio, self.cfg.conservazione_giorni, self.cfg.pulizia)
        except Exception as exc:                        # noqa: BLE001
            self.log("Pulizia non riuscita: %s" % exc)
            return {"errore": str(exc)}
        if esito.get("tolti"):
            self.log("Conservazione (%s, %d giorni): %d file tolti da %d "
                     "ordini, %.0f MB liberati."
                     % (esito["modo"], esito["giorni"], esito["tolti"],
                        esito["puliti"], esito["liberati"] / 1048576.0))
            self.aggiorna_registro()
        elif esito.get("ordini") and self.cfg.pulizia == "segnala":
            self.log("Conservazione: %d ordini oltre i %d giorni. "
                     "Niente viene cancellato da solo."
                     % (esito["ordini"], esito["giorni"]))
        return esito

    def anteprima_jpeg(self, max_side: int = 900, quality: int = 82,
                       vista: str = "dopo", zoom: bool = False,
                       cx: float = 0.5, cy: float = 0.5) -> bytes:
        """Anteprima corrente come JPEG, per l'interfaccia web.

        vista  "dopo" = il positivo finito; "prima" = il negativo grezzo, com'e'
               uscito dal sensore. Il confronto fra i due e' l'unico modo per
               capire se una scansione storta e' colpa della macchina o della
               nostra elaborazione.

        zoom   Con False si manda il fotogramma intero ridotto. Con True si
               ritaglia una finestra a grandezza naturale (1:1) attorno al punto
               (cx, cy) in coordinate 0..1.

               Serve piu' di quanto sembri: nitidezza, grana e pulizia IR non si
               giudicano su un'immagine rimpicciolita. Ridotta, qualunque
               scansione sembra a fuoco. Questo e' il motivo per cui ogni
               programma serio ha un tasto 1:1.
        """
        from io import BytesIO
        from PIL import Image

        if zoom and vista != "prima":
            self.piena_risoluzione()

        if vista == "prima":
            if self.preview_neg is None:
                raise ValueError("nessun negativo disponibile")
            arr = self.preview_neg
        else:
            if self.preview is None:
                raise ValueError("nessuna anteprima disponibile")
            arr = (np.clip(self.preview, 0, 1) * 255).astype(np.uint8)

        h, w = arr.shape[:2]
        if zoom:
            fw, fh = min(w, max_side), min(h, int(max_side * 0.72))
            x0 = int(np.clip(cx * w - fw / 2, 0, max(0, w - fw)))
            y0 = int(np.clip(cy * h - fh / 2, 0, max(0, h - fh)))
            arr = arr[y0:y0 + fh, x0:x0 + fw]
            pil = Image.fromarray(arr)
        else:
            pil = Image.fromarray(arr)
            pil.thumbnail((max_side, max_side))

        buf = BytesIO()
        pil.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

    def istogramma(self, vista: str = "dopo", bins: int = 128) -> Dict[str, object]:
        """Istogramma per canale dell'anteprima corrente, piu' i tagli.

        Il numero che conta davvero non e' la forma della curva ma quanto sta
        attaccato agli estremi: se il 2% dei pixel e' a zero, quelle ombre sono
        perse e nessuna correzione le riporta indietro. Meglio accorgersene
        adesso, con la pellicola ancora nella macchina, che a rullo finito.
        """
        if vista == "prima":
            src = self.preview_neg
            if src is None:
                return {"disponibile": False}
            arr = src.astype(np.float32) / 255.0
        else:
            if self.preview is None:
                return {"disponibile": False}
            arr = np.clip(self.preview, 0, 1)

        # su un'immagine grande l'istogramma si calcola benissimo su un
        # sottocampione: la forma non cambia e il PC del 2009 ringrazia
        passo = max(1, int(max(arr.shape[:2]) / 700))
        piccola = arr[::passo, ::passo]

        canali = {}
        for i, nome in enumerate(("r", "g", "b")):
            piano = piccola[..., i] if piccola.ndim == 3 else piccola
            conteggi, _ = np.histogram(piano, bins=bins, range=(0.0, 1.0))
            canali[nome] = conteggi.tolist()
            canali[nome + "_basso"] = round(float((piano <= 0.002).mean() * 100), 2)
            canali[nome + "_alto"] = round(float((piano >= 0.998).mean() * 100), 2)

        canali["disponibile"] = True
        canali["massimo"] = int(max(max(canali[c]) for c in ("r", "g", "b")))
        canali["vista"] = vista
        return canali

    def provino_jpeg(self, percorso: str, lato: int = 150) -> bytes:
        """Miniatura di un fotogramma gia' esportato, per la striscia del rullo.

        Con cache: la striscia si ridisegna a ogni aggiornamento, e rileggere
        ogni volta dei TIFF da 16 bit metterebbe in ginocchio la macchina.
        La chiave tiene conto della data del file, cosi' una riscansione la
        aggiorna da sola.
        """
        from io import BytesIO
        from PIL import Image

        if not percorso or not os.path.exists(percorso):
            raise ValueError("file non trovato")
        chiave = "%s|%s|%d" % (percorso, os.path.getmtime(percorso), lato)
        if chiave in self._cache_prov:
            return self._cache_prov[chiave]

        pil = Image.open(percorso)
        pil.thumbnail((lato, lato))
        if pil.mode not in ("RGB", "L"):
            pil = pil.convert("RGB")
        buf = BytesIO()
        pil.save(buf, format="JPEG", quality=78)
        dati = buf.getvalue()

        if len(self._cache_prov) > 200:          # non cresce all'infinito
            self._cache_prov.clear()
        self._cache_prov[chiave] = dati
        return dati
