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
import logging
import os
import queue
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

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
from .pipeline import (ProcessOptions, anteprima_rapida, export, export_mask,
                       export_negativo, finiture, process)
from . import geometria as geometrie, ice_kodak, pellicole_dx, registri, remoti
from .rullo import MemoriaPellicole, StatisticheRullo, chiave_dx


# il diario su file: software/log/programma (registri.configura_programma)
_registro = logging.getLogger("filmscan.servizio")


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
    # cresce a ogni anteprima nuova (rapida o finita): la pagina aggiorna
    # immagine e striscia anche a rullo in corso
    anteprime: int = 0
    # fotogrammi letti e non ancora elaborati
    in_elaborazione: int = 0
    # la pellicola riconosciuta dal codice DX (striscia in finestra)
    pellicola: str = ""
    # ordine finito (tutti i rulli letti): l'interfaccia propone l'avviso
    avviso_ordine: int = 0
    # fotogrammi lasciati da elaborare perche' l'elaborazione e' stata fermata
    non_elaborati: int = 0

    def as_dict(self) -> Dict[str, object]:
        return {"running": self.running, "kind": self.kind,
                "message": self.message, "progress": self.progress,
                "frame": self.frame, "total": self.total,
                "errors": self.errors[-10:], "log": self.log[-40:],
                "attesa_striscia": self.attesa_striscia,
                "chiusura": self.chiusura,
                "posizionamento": self.posizionamento,
                "anteprime": self.anteprime,
                "in_elaborazione": self.in_elaborazione,
                "pellicola": self.pellicola,
                "avviso_ordine": self.avviso_ordine,
                "non_elaborati": self.non_elaborati}


def _peso(raw) -> int:
    """La memoria che un grezzo tiene occupata finche' non e' elaborato."""
    return int(sum(p.nbytes for piani in raw.planes.values() for p in piani))


class _ElaborazioneInFondo(object):
    """L'elaborazione completa dei fotogrammi in un thread suo, mentre la
    macchina legge il successivo.

    Prima ogni fotogramma veniva letto, elaborato ed esportato prima di
    passare al prossimo: sul PC della macchina l'elaborazione costa piu'
    della lettura, e l'immagine compariva solo alla fine. Ora la lettura va
    avanti e l'anteprima ridotta compare subito.

    La coda non ha un numero fisso di posti ma un tetto di memoria
    (cfg.memoria_elaborazione_mb): un processo a 32 bit ha ~1,8 GB, e un
    grezzo RGB a piena risoluzione ne occupa ~35. Finche' c'e' posto la
    lettura non aspetta mai, e la pellicola corre; l'elaborazione recupera
    durante l'espulsione e il cambio di striscia. Pieno, chi legge aspetta
    (metti). L'elaborazione non tocca la macchina: non c'e' nulla da fermare
    a meta'.
    """

    def __init__(self, lavora: Callable, budget: int = 600 * 1024 * 1024,
                 aiutanti: int = 0, aiuto: Optional[Callable[[], bool]] = None) -> None:
        self._coda: "queue.Queue" = queue.Queue()
        self._lavora = lavora
        self._budget = max(int(budget), 1)
        self._in_memoria = 0
        self._cond = threading.Condition()
        self._chiusa = False
        # la lettura del rullo e' finita: chi aspettava altri fotogrammi per
        # l'equilibrio del rullo non deve piu' aspettare
        self.lettura_finita = threading.Event()
        # "Chiusura..." premuto di nuovo: i fotogrammi rimasti in coda si
        # salvano solo come grezzi (stato "da_elaborare"), senza elaborarli.
        # Il grezzo non si salta mai: e' l'unica copia della pellicola.
        self.solo_grezzi = threading.Event()
        self._thread = threading.Thread(target=self._giro, name="elaborazione",
                                        daemon=True)
        self._thread.start()
        # Gli aiutanti prendono fotogrammi dalla stessa coda solo quando un
        # elaboratore della rete e' in ascolto ('aiuto'), e li mandano a lui
        # (ScanService._esporta): con due PC in rete si elaborano due
        # fotogrammi alla volta mentre la macchina legge. Senza elaboratori
        # restano fermi e lavora solo il thread principale, come prima.
        self._aiuto = aiuto or (lambda: False)
        self._aiutanti = [threading.Thread(target=self._giro_aiutante,
                                           name="elaborazione-%d" % (k + 1), daemon=True)
                          for k in range(max(0, int(aiutanti)))]
        for t in self._aiutanti:
            t.start()

    PESO_SU_DISCO = 1024 * 1024

    def metti(self, compito, peso: int, attesa: Callable[[], None],
              su_disco: Optional[Callable] = None) -> None:
        """In coda un fotogramma letto. Con la memoria piena: se c'e'
        'su_disco', il grezzo si scrive sul disco e in coda va solo il suo
        percorso, e la lettura non si ferma (riserva, 7/11/2026: sui rulli
        lunghi la scansione si fermava dopo una ventina di fotogrammi ad
        aspettare l'elaborazione); senza, si aspetta come prima."""
        with self._cond:
            pieno = bool(self._in_memoria and self._in_memoria + peso > self._budget)
        if pieno and su_disco is not None:
            try:
                compito = su_disco(compito)
                peso = self.PESO_SU_DISCO
            except Exception as exc:                    # noqa: BLE001
                _registro.warning("grezzo su disco non riuscito (%s): aspetto la memoria", exc)
        with self._cond:
            # uno alla volta passa sempre, anche se pesa piu' del tetto
            while self._in_memoria and self._in_memoria + peso > self._budget:
                attesa()
                self._cond.wait(0.5)
            self._in_memoria += peso
        self._coda.put((compito, peso))

    def _giro(self) -> None:
        while True:
            voce = self._coda.get()
            if voce is None:
                self._coda.put(None)        # anche per gli aiutanti
                return
            self._fai(voce)

    def _giro_aiutante(self) -> None:
        while True:
            if not self._aiuto():
                if self._chiusa and not self._thread.is_alive():
                    return
                time.sleep(0.5)
                continue
            try:
                voce = self._coda.get(timeout=1.0)
            except queue.Empty:
                if self._chiusa and not self._thread.is_alive():
                    return
                continue
            if voce is None:
                self._coda.put(None)
                return
            self._fai(voce)

    def _fai(self, voce) -> None:
        compito, peso = voce
        # gli errori li gestisce 'lavora' fotogramma per fotogramma: qui
        # una rete, perche' un thread morto lascerebbe il rullo appeso
        try:
            self._lavora(compito)
        except Exception:                           # noqa: BLE001
            traceback.print_exc()
        finally:
            del compito, voce
            with self._cond:
                self._in_memoria -= peso
                self._cond.notify_all()

    def chiudi(self) -> None:
        """Aspetta che i fotogrammi in coda siano elaborati."""
        if self._chiusa:
            return
        self._chiusa = True
        self.lettura_finita.set()
        self._coda.put(None)
        self._thread.join()
        for t in self._aiutanti:
            t.join()


# Nella nota della calibrazione: con quale base d'esposizione e' stata fatta
TAG_ESPOSIZIONE = ("[esposizione automatica]", "[esposizione di fabbrica]")


class _FotogrammaVuoto(Exception):
    """Esce dal blocco del fotogramma senza esportare: non e' un errore."""


class _ElaborazioneFermata(Exception):
    """"Ferma elaborazione" premuto mentre un fotogramma si elaborava: lo
    si lascia a meta' e resta da elaborare."""


class _CodaIniziale(Exception):
    """La prima lettura di una striscia e' caduta sulla coda: non e' un
    fotogramma, non prende un numero."""


class ScanService:

    def __init__(self, cfg: AppConfig, archivio: Optional[Archivio] = None):
        registri.configura_programma()
        self.cfg = cfg
        # la resa di stampa della Gretag (Negative2Lab_P8_neg.lut), se c'e'
        # sulla macchina, fra le LUT da scegliere
        try:
            from . import lut as _lut
            for n in _lut.porta_profili_gretag(cfg.path("lut")):
                logging.getLogger("filmscan.servizio").info(
                    "profilo di stampa Gretag disponibile fra le LUT: %s", n)
        except Exception:                               # noqa: BLE001
            pass
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
        # dopo un'emergenza Calibra azzera sempre i motori (vedi calibra)
        self._emergenza_alle = time.time() if self.emergenza else 0.0
        # dopo un'emergenza serve una calibrazione nuova (che azzera i motori)
        self._calibra_dopo: float = 0.0
        # posizionamento a mano: i tasti della pagina mettono qui i comandi,
        # il thread del rullo li esegue (la macchina la comanda uno solo)
        self._comandi_pos: "queue.Queue" = queue.Queue()
        self._video: bytes = b""
        # anteprima in diretta: una lettura alla volta, l'ultima si ripete
        # a chi chiede mentre la successiva e' in corso
        self._lock_diretta = threading.Lock()
        self._diretta: bytes = b""
        # elaborazione in sottofondo del rullo in corso, e le miniature
        # rapide dei fotogrammi che aspettano la loro: (rullo, numero) -> jpeg
        self._elab: Optional[_ElaborazioneInFondo] = None
        self._provini_rapidi: Dict[Tuple[int, int], bytes] = {}
        # la stessa anteprima della scansione, piu' grande: se il fotogramma
        # non arriva all'elaborazione (fermata o errore) si salva accanto al
        # grezzo e fa da miniatura e da anteprima (_salva_anteprima_viva)
        self._anteprime_vive: Dict[Tuple[int, int], bytes] = {}
        # un'elaborazione completa alla volta sul PC della macchina (memoria
        # a 32 bit); quelle mandate agli elaboratori della rete non contano
        self._lock_locale = threading.Lock()
        self._lock_ice = threading.Lock()
        self.elaboratori = remoti.Elaboratori(cfg.chiave_elaboratori or "")
        # copia dei rulli nell'archivio di rete, a macchina ferma
        from .archivio_rete import ArchivioRete
        self.archivio_rete = ArchivioRete(self)
        # la geometria del fotogramma aperto (pipeline, ProcessOptions.geometria),
        # con le scelte a mano non ancora salvate
        self._geometria_aperta: Optional[Dict[str, object]] = None
        # nel visore c'e' il fotogramma intero (sezione Geometria, Modifica)
        self._intero_mostrato = False
        # gli spostamenti a mano gia' dentro base_lineare (coordinate della vista)
        self._canali_in_base: Dict[str, Tuple[float, float]] = {}
        self._ultima_rapida: Optional[Tuple[int, int]] = None
        # il fotogramma riaperto dalla striscia (id), per Salva e Applica
        self._aperto: Optional[int] = None
        # mentre la connessione automatica aspetta lo scanner, cosa aspetta
        self.attesa_connessione: Optional[str] = None
        # Disconnetti ferma anche la connessione automatica: altrimenti si
        # ricollegherebbe da sola cinque secondi dopo
        self._ferma_auto = threading.Event()
        # collegamento remoto (software/remoto.py): registri che partono da
        # soli, aggiornamento disponibile. Un thread suo, che non tocca mai
        # la macchina; senza remoto.json non fa niente.
        self.remoto: Dict[str, object] = {}
        threading.Thread(target=self._ciclo_remoto, name="remoto",
                         daemon=True).start()

    # ------------------------------------------------------------------

    def log(self, msg: str) -> None:
        self.job.log.append("%s  %s" % (time.strftime("%H:%M:%S"), msg))
        # e su file (software/log/programma): il diario dell'interfaccia si
        # perde alla chiusura, e per capire un problema e' il pezzo piu' utile
        m = str(msg)
        if m.startswith(("ERRORE", "Attenzione", "ATTENZIONE", "STOP")):
            _registro.warning(m)
        else:
            _registro.info(m)

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
                "remoto": self.remoto,
                "elaboratori": self.stato_elaboratori(),
                "preferenze": self.preferenze(),
                "archivio_rete": self.archivio_rete.stato(),
                "ambiente": check_environment(dpi=self.cfg.optical_dpi)}

    # ------------------------------------------------------------------
    # collegamento remoto

    @staticmethod
    def _modulo_remoto():
        try:
            import remoto                               # software/remoto.py
            return remoto
        except ImportError:
            return None

    def _ciclo_remoto(self) -> None:
        """Ogni ora: c'e' un aggiornamento? Ogni cfg invio_ore: i registri,
        mai a lavoro in corso (a rullo finito ci pensa il giro dopo)."""
        time.sleep(60.0)
        ultimo_invio = 0.0
        while True:
            r = self._modulo_remoto()
            cfg = r.carica() if r else None
            if cfg is None:
                self.remoto = {"configurato": False}
            else:
                stato: Dict[str, object] = {"configurato": True,
                                            "macchina": cfg.get("macchina"),
                                            "ramo": cfg.get("ramo")}
                try:
                    stato["controllo"] = r.controlla(cfg)
                except Exception as exc:                # noqa: BLE001
                    stato["errore"] = r.spiega(exc)
                ore = float(cfg.get("invio_ore") or 0)
                if (ore > 0 and time.time() - ultimo_invio > ore * 3600
                        and not self.job.running):
                    try:
                        r.invia(cfg, 2, "automatico", scrivi=self.log)
                        ultimo_invio = time.time()
                    except Exception as exc:            # noqa: BLE001
                        self.log("Registri non inviati: %s" % r.spiega(exc))
                stato["invio"] = (r.carica() or {}).get("stato", {}).get("invio")
                self.remoto = stato
            time.sleep(3600.0)

    def remoto_invia(self, motivo: str = "") -> str:
        r = self._modulo_remoto()
        cfg = r.carica() if r else None
        if cfg is None:
            raise ScannerError("collegamento remoto non configurato: MASTERFLEX.bat, "
                             "voce Collegamento remoto")
        try:
            dove = r.invia(cfg, 2, motivo or "dall'interfaccia", scrivi=self.log)
        except Exception as exc:                        # noqa: BLE001
            raise ScannerError("registri non inviati: %s" % r.spiega(exc))
        self.remoto = dict(self.remoto, invio={"file": dove,
                                               "quando": time.strftime("%Y-%m-%d %H:%M")})
        return dove

    def riavvia(self) -> None:
        """Riavvia interfaccia e agente con il codice installato: lancia
        riavvia.py staccato, poi questo processo esce con il codice 3 (che
        MASTERFLEX.bat riconosce e chiude la sua finestra). Mai a lavoro in
        corso: l'agente si chiude, e la macchina con lui."""
        if self.job.running:
            raise ScannerError("c'e' un lavoro in corso: riavviare a lavoro finito")
        import subprocess
        import sys as _sys
        software = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ponte = getattr(self.backend, "root", None) or r"C:\MasterflexD\ponte"
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = 0x00000008 | 0x00000200   # staccato, gruppo suo
        subprocess.Popen([_sys.executable, os.path.join(software, "riavvia.py"),
                          "--pid-web", str(os.getpid()), "--ponte", ponte],
                         cwd=software, close_fds=True, **kw)
        self.log("Riavvio con la versione installata: interfaccia e agente "
                 "ripartono fra poco.")

        def esci():
            time.sleep(1.5)                 # la risposta HTTP deve partire
            os._exit(3)
        threading.Thread(target=esci, daemon=True).start()

    def remoto_installa(self) -> str:
        """Installa l'aggiornamento del ramo. Il codice nuovo vale dal
        prossimo avvio di MASTERFLEX."""
        if self.job.running:
            raise ScannerError("c'e' un lavoro in corso: aggiornare a lavoro finito")
        r = self._modulo_remoto()
        cfg = r.carica() if r else None
        if cfg is None:
            raise ScannerError("collegamento remoto non configurato")
        try:
            sha = r.installa(cfg, scrivi=self.log)
        except Exception as exc:                        # noqa: BLE001
            raise ScannerError("aggiornamento non installato: %s" % r.spiega(exc))
        self.log("Aggiornamento %s installato: vale dal prossimo avvio di "
                 "MASTERFLEX (riavviare il PC o chiudere e riaprire)." % sha[:7])
        self.remoto = dict(self.remoto, installato=sha[:7])
        return sha[:7]

    # ------------------------------------------------------------------

    # --- LUT ------------------------------------------------------------

    @property
    def options(self) -> ProcessOptions:
        return self._options

    @options.setter
    def options(self, valore: ProcessOptions) -> None:
        # La LUT e' una scelta del programma, valida per tutti gli ordini: ogni
        # ProcessOptions che arriva (dalla pagina, da un rullo) la riceve qui.
        valore = self.con_preferenze(valore)
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
            elab = self._elab
            if elab is not None and elab.solo_grezzi.is_set():
                # gia' fermata: altri tocchi non aggiungono niente (prima
                # ognuno riscriveva "Fine rullo richiesto" nel diario)
                return self.stato()
            if (self.job.chiusura or (elab is not None and elab.lettura_finita.is_set())) \
                    and elab is not None:
                # secondo tocco, o lettura gia' finita: l'elaborazione si
                # ferma dove e', anche a meta' fotogramma; il resto resta da
                # elaborare (tasto Completa), con l'anteprima della scansione
                elab.solo_grezzi.set()
                self.log("Elaborazione fermata: i fotogrammi rimasti si salvano "
                         "solo come grezzi (Completa li elabora dopo).")
                self.job.message = "Elaborazione fermata: salvo i grezzi rimasti"
                self._fine_rullo.set()
                self.job.chiusura = True
                return self.stato()
            self._fine_rullo.set()
            self.job.chiusura = True
            self.log("Fine rullo richiesto: si chiude dopo il fotogramma in "
                     "corso (toccare di nuovo per fermare anche l'elaborazione).")
        return self.stato()

    def _leggi_dx(self, backend) -> str:
        """La chiave DX della striscia nel carrello ("DX 109-9"), "" se la
        macchina non la sa dire o se la lettura e' spenta. Mai bloccante."""
        if not getattr(self.cfg, "leggi_dx", True):
            return ""
        if self.cfg.acquisizione != "originale":
            # Il DX (get_media_info) porta l'SDK nello stato 1, dove il
            # firmware rifiuta ogni scan_raw (46/47/42 con dettaglio 1, ...)
            # finche' una capture non lo riporta a 0 (riserva, 7/11/2026:
            # rullo fermo alla misura dell'esposizione alle 17:59). Con la
            # lettura un canale per volta quindi non si legge.
            if not getattr(self, "_avviso_dx", False):
                self._avviso_dx = True
                self.log("Codice DX non letto: con l'acquisizione un canale per volta "
                         "bloccherebbe le letture. Si legge con l'acquisizione come il "
                         "programma originale (Macchina).")
            return ""
        try:
            return chiave_dx(backend.codice_dx())
        except Emergenza:
            raise
        except Exception as exc:                        # noqa: BLE001
            self.log("Codice DX non letto: %s" % exc)
            return ""

    def _riparti_esposizione(self, preset):
        """(gains, da_misurare, misure_opache, verifica_unica) come a inizio
        rullo: per una pellicola nuova l'esposizione per canale si rimisura
        (una B/N non ha la maschera arancione di una colore)."""
        gains = self._esposizione_fissa(preset)
        return (gains, gains is None and preset.auto_exposure
                and preset.channel_exposure is None, 0, True)

    def _profilo_da_dx(self, ordine, rullo, chiave: str) -> Dict[str, object]:
        """Il DX della prima striscia contro quel che si e' scelto a mano.
        Se il tipo di pellicola e' diverso, il rullo si corregge da solo
        (profilo in archivio, nota nell'ordine) e si dice. Restituisce i
        cambi per la lettura: {"argento": True} (niente IR), {"preset": ...}.

        Una scelta a mano B/N su una pellicola a colori si rispetta (puo'
        essere il cliente a volerla in bianco e nero); il resto no: una
        diapositiva invertita come negativo, o l'IR su un B/N all'argento,
        sono errori, non scelte."""
        pell = pellicole_dx.da_chiave(chiave)
        if pell is None or not pell.conosciuta:
            if pell is not None:
                self.log("Pellicola: %s." % pell.descrizione())
            return {}
        cambi: Dict[str, object] = {"argento": pell.argento}
        nuovo = pell.profilo
        if (not nuovo or nuovo == rullo.profilo
                or not getattr(self.cfg, "pellicola_da_dx", True)):
            return cambi
        if nuovo == "c41" and rullo.profilo in ("bn", "bn_c41"):
            self.log("Il DX dice %s, il rullo e' registrato in bianco e nero: "
                     "resta in bianco e nero, come scelto." % pell.descrizione())
            return cambi
        prima = PROFILES.get(rullo.profilo)
        prima = prima.name if prima else rullo.profilo
        self.archivio.rullo_profilo(rullo.id, nuovo)
        rullo.profilo = nuovo
        riga = ('"%s": pellicola %s (dal codice %s); a mano era %s, '
                "corretto da solo" % (rullo.etichetta, pell.descrizione(), chiave, prima))
        self.archivio.ordine_aggiungi_nota(ordine.id, riga)
        self.job.errors.append(riga)
        self.log("Attenzione: " + riga + ".")
        if nuovo in ("diapositiva", "bn_diapositiva") and rullo.preset != "diapositiva":
            cambi["preset"] = "diapositiva"
        elif rullo.preset == "bn" and nuovo in ("c41",):
            cambi["preset"] = "standard"
        return cambi

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

    def _attendi_pellicola(self, backend, formato: str) -> bool:
        """Inizio rullo: arma il carrello e aspetta la prima striscia.
        False se non arriva (Fine rullo, STOP, cfg.attesa_striscia_s)."""
        try:
            self.progress("Carrello in preparazione", 0.0)
            backend.prepara_carico(formato)
        except (Emergenza, MotoriBloccati):
            raise
        except ScannerError as exc:
            # motori bloccati o carrello che rifiuta: si scansiona quello che
            # c'e', come prima che Scansiona rullo facesse anche Carica
            self.log("Carrello non preparato (%s): si scansiona quello che "
                     "c'e' in finestra." % str(exc).splitlines()[0])
            return True
        self.log("Carrello pronto: inserire la pellicola, la scansione "
                 "parte da sola.")
        scadenza = time.time() + self.cfg.attesa_striscia_s
        while time.time() < scadenza:
            if self.emergenza:
                raise Emergenza("STOP EMERGENZA")
            if self._stop.is_set() or self._fine_rullo.is_set():
                self.log("Rullo non iniziato: nessuna pellicola inserita.")
                return False
            self.progress("Inserire la pellicola (o Fine rullo)", 0.0)
            if backend.pellicola_sdk():
                self.log("Pellicola inserita: avvio scansione.")
                return True
            time.sleep(0.5)
        self.log("Nessuna pellicola in %.0f s: rullo non iniziato."
                 % self.cfg.attesa_striscia_s)
        return False

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

    def diretta_jpeg(self, positivo: bool = True) -> bytes:
        """L'anteprima in diretta: un'immagine del video della macchina per
        ogni richiesta della pagina. Niente thread: smette da sola quando la
        pagina smette di chiedere. Non durante un lavoro, che ha la macchina
        per se' (nel posizionamento a mano il video c'e' gia')."""
        if self.job.running:
            if self.job.posizionamento:
                return self._video
            raise ScannerError("c'e' un lavoro in corso: la diretta riprende "
                               "a lavoro finito")
        backend = self._require()
        if not self._lock_diretta.acquire(False):
            return self._diretta
        try:
            self._diretta = self._jpeg_video(backend.anteprima_veloce(),
                                             inverti=positivo)
            return self._diretta
        finally:
            self._lock_diretta.release()

    @staticmethod
    def _jpeg_video(img: np.ndarray, inverti: bool = True) -> bytes:
        """Il video a bassa risoluzione, tirato sui percentili e (di serie)
        invertito come un positivo: serve a vedere dov'e' il fotogramma e
        com'e', non a giudicare il colore."""
        from PIL import Image
        x = img.astype(np.float32)
        lo, hi = np.percentile(x, (1.0, 99.0))
        x = np.clip((x - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
        x = ((1.0 - x) if inverti else x) ** 0.8
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
            for campo in ("file_raw", "file_out", "file_maschera", "file_negativo"):
                g[campo] = self._nome_con_numero(f.get(campo) or "", numero)
            piano.append((f, g))
        # file: prima tutti su un nome provvisorio, poi su quello giusto,
        # cosi' due fotogrammi che si scambiano il numero non si pestano
        spostati = []
        try:
            for f, g in piano:
                for campo in ("file_raw", "file_out", "file_maschera", "file_negativo"):
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
        nuovo = re.sub(r"_(\d{3})(?=(_maschera|_negativo)?\.[^.]+$)", "_%03d" % numero, nome)
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
        self._emergenza_alle = time.time()
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
                self.job.log.append(traceback.format_exc(limit=2).strip().replace("\n", " | "))
                # nel file il traceback intero, non le ultime due righe
                _registro.exception("lavoro '%s' fallito", kind)
            finally:
                # i fotogrammi gia' letti si elaborano e si salvano anche
                # dopo uno STOP EMERGENZA: non toccano la macchina
                try:
                    self._chiudi_elaborazione()
                except Exception as exc:                # noqa: BLE001
                    self.log("Elaborazione in sottofondo: %s" % exc)
                if self._stop.is_set():
                    self._a_riposo()
                self.job.running = False
                self.job.progress = 1.0
                self._lock.release()

        threading.Thread(target=worker, daemon=True).start()

    def _chiudi_elaborazione(self) -> None:
        # self._elab resta valorizzato finche' la coda non e' vuota: e' da li'
        # che "Ferma elaborazione" (fine_rullo) la raggiunge. Prima veniva
        # azzerato subito, e il secondo tocco non trovava niente da fermare.
        elab = self._elab
        if elab is None:
            return
        if self.job.in_elaborazione:
            self.progress("Elaborazione degli ultimi %d fotogramm%s (Ferma elaborazione per interromperla)"
                          % (self.job.in_elaborazione,
                             "o" if self.job.in_elaborazione == 1 else "i"), 0.98)
        try:
            elab.chiudi()
        finally:
            self._elab = None

    def _mostra_rapida(self, raw, opts, chiave: Tuple[int, int]) -> None:
        """L'anteprima ridotta di un fotogramma appena letto, nel visore e
        nella striscia, in attesa di quella vera."""
        from PIL import Image
        try:
            img, neg, base = anteprima_rapida(raw, opts)
        except Exception as exc:                        # noqa: BLE001
            self.log("Anteprima rapida non riuscita: %s" % exc)
            return
        self.preview = img
        self.preview_neg = neg
        self.base_lineare = base
        self.preview_ridotta = False
        self.ultimo_raw = raw
        self._ultima_rapida = chiave
        pil = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))
        grande = pil.copy()
        grande.thumbnail((1600, 1600))
        buf = io.BytesIO()
        grande.save(buf, format="JPEG", quality=85)
        self._anteprime_vive[chiave] = buf.getvalue()
        pil.thumbnail((150, 150))
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=78)
        self._provini_rapidi[chiave] = buf.getvalue()
        self.job.anteprime += 1

    @staticmethod
    def file_anteprima_viva(raw_path: str) -> str:
        """Dove sta l'anteprima della scansione di un fotogramma non
        elaborato: accanto al grezzo, stesso nome."""
        return raw_path[:-4] + "_anteprima.jpg" if raw_path.endswith(".npz") else ""

    def _salva_anteprima_viva(self, chiave: Tuple[int, int], raw_path: str) -> None:
        """Il fotogramma non e' stato elaborato (elaborazione fermata,
        errore): la sua anteprima della scansione resta su disco e la si
        mostra al posto del file finito, finche' non lo si elabora
        (Alessandro, 7/11/2026). Senza, la striscia restava vuota."""
        dati = self._anteprime_vive.get(chiave)
        dove = self.file_anteprima_viva(raw_path)
        if not dati or not dove:
            return
        try:
            with open(dove, "wb") as f:
                f.write(dati)
        except (IOError, OSError) as exc:
            _registro.warning("anteprima di %s non salvata: %s", raw_path, exc)

    def provino_rapido(self, fotogramma: Dict[str, object]) -> bytes:
        """La miniatura rapida di un fotogramma ancora in elaborazione, o
        quella salvata di uno non elaborato."""
        dati = self._provini_rapidi.get(
            (int(fotogramma.get("rullo_id") or 0),
             int(fotogramma.get("numero") or 0)), b"")
        if dati:
            return dati
        dove = self.file_anteprima_viva(str(fotogramma.get("file_raw") or ""))
        if dove and os.path.exists(dove):
            try:
                return self.provino_jpeg(dove)
            except (ValueError, OSError):
                pass
        return b""

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
                "autofocus": self.cfg.autofocus,
                "negativo_lineare": self.cfg.negativo_lineare,
                "acquisizione": self.cfg.acquisizione,
                "digital_ice": self.cfg.digital_ice,
                "digital_ice_kodak": ice_kodak.disponibile(),
                "leggi_dx": bool(self.cfg.leggi_dx),
                "pin_fisso": bool(self.cfg.pin_fisso)}

    def imposta_macchina(self, valori: Dict[str, object]) -> Dict[str, bool]:
        if self.job.running:
            raise ScannerError("c'e' un lavoro in corso: cambiare le "
                               "impostazioni della macchina a lavoro finito")
        for k in ("esposizione_auto", "pressore_auto", "avanzamento_automatico",
                  "leggi_dx"):
            if k in valori:
                setattr(self.cfg, k, bool(valori[k]))
        if "autofocus" in valori:
            if valori["autofocus"] not in self.AUTOFOCUS:
                raise ValueError("autofocus sconosciuto: %s" % valori["autofocus"])
            self.cfg.autofocus = str(valori["autofocus"])
        if "digital_ice" in valori:
            if valori["digital_ice"] not in ("kodak", "nostro"):
                raise ValueError("digital_ice: 'kodak' o 'nostro'")
            self.cfg.digital_ice = str(valori["digital_ice"])
        if "acquisizione" in valori:
            if valori["acquisizione"] not in ("canali", "originale"):
                raise ValueError("acquisizione: 'canali' o 'originale'")
            self.cfg.acquisizione = str(valori["acquisizione"])
        if "negativo_lineare" in valori:
            if valori["negativo_lineare"] not in ("", "tiff", "dng"):
                raise ValueError("negativo lineare: '', 'tiff' o 'dng'")
            self.cfg.negativo_lineare = str(valori["negativo_lineare"])
        return self.impostazioni_macchina()

    def _autofocus(self, backend, metodo: Optional[str] = None) -> bool:
        """L'autofocus scelto nelle impostazioni. Se non trova il fuoco la
        macchina torna alla posizione nominale: si annota e si va avanti."""
        scelto = metodo or self.cfg.autofocus
        if scelto == "spento":
            return False
        if metodo is None and getattr(self, "_metodo_fuoco", None):
            # quello che nel rullo ha dato l'immagine piu' nitida (_verifica_fuoco)
            scelto = self._metodo_fuoco
        # Se il metodo scelto non trova il fuoco si provano gli altri: con
        # il fuoco non trovato la lente resta alla posizione nominale, e li'
        # l'immagine e' sfocata (collaudo del 7/11/2026: nitidezza del verde
        # ~5 prima del fuoco, ~22 dopo, con tutti e tre i metodi).
        prove = [scelto] + ([m for m in ("gi", "rapido", "nominale") if m != scelto]
                            if metodo is None else [])
        for m in prove[:3]:
            try:
                if backend.autofocus(m):
                    self.log("Autofocus (%s) fatto." % m)
                    self._ultimo_metodo_fuoco = m
                    return True
            except (Emergenza, MotoriBloccati):
                raise
            except ScannerError as exc:
                self.log("Autofocus (%s) non riuscito: %s" % (m, str(exc).split(": ")[-1]))
        self.job.errors.append("fuoco non trovato su questa striscia: le immagini "
                               "possono essere sfocate")
        self.log("Attenzione: fuoco non trovato con nessun metodo, la striscia "
                 "puo' venire sfocata.")
        return False

    @staticmethod
    def _nitidezza(raw) -> float:
        """Quanto e' a fuoco un grezzo: varianza del laplaciano sul centro
        del verde (meta' risoluzione, normalizzato sul 99° percentile).
        Riserva, 7/11/2026: ~80 un fotogramma a fuoco, ~8 uno sfocato, con
        "Autofocus fatto" in tutti e due i casi. Dipende dal soggetto: si
        usa per confrontare, non come verita' assoluta."""
        canale = "G" if "G" in raw.planes else next(c for c in raw.planes if c != "IR")
        a = np.asarray(raw.planes[canale][0], dtype=np.float32)[::2, ::2]
        h, w = a.shape
        a = a[h // 4:3 * h // 4, w // 4:3 * w // 4]
        a = a / max(float(np.percentile(a, 99)), 1e-6)
        try:
            from scipy import ndimage
            lap = ndimage.laplace(a)
        except ImportError:
            lap = (4 * a[1:-1, 1:-1] - a[:-2, 1:-1] - a[2:, 1:-1]
                   - a[1:-1, :-2] - a[1:-1, 2:])
        return float(lap.var() * 1e4)

    SOGLIA_NITIDEZZA = 15.0

    def _verifica_fuoco(self, backend, raw, rif: List[float], rileggi):
        """Dopo l'autofocus del primo fotogramma vero di una striscia: se il
        fotogramma e' molto meno nitido del dovuto (sotto 15, o sotto un
        terzo del migliore visto nel rullo) si provano gli altri metodi, si
        rilegge, e si tiene la lettura migliore. Il metodo vincente vale per
        le strisce seguenti. Un "Autofocus fatto" non basta: il 7/11/2026
        dopo un nominale riuscito il rullo e' uscito sfocato."""
        s = self._nitidezza(raw)
        usato = getattr(self, "_ultimo_metodo_fuoco", None)
        soglia = max(self.SOGLIA_NITIDEZZA, 0.35 * rif[0]) if rif[0] else self.SOGLIA_NITIDEZZA
        _registro.info("nitidezza dopo il fuoco (%s): %.1f, soglia %.1f", usato, s, soglia)
        if s >= soglia:
            rif[0] = max(rif[0], s)
            return raw
        self.log("Fuoco dubbio (nitidezza %.0f, %s): provo un altro metodo." % (s, usato))
        migliore = (s, raw, usato)
        provati = [usato]
        for m in [x for x in ("rapido", "gi", "nominale") if x != usato][:2]:
            try:
                with self._sotto_pressore(backend):
                    backend.autofocus(m)
            except (Emergenza, MotoriBloccati, PressoreBloccato):
                raise
            except ScannerError as exc:
                self.log("Autofocus (%s) non riuscito: %s" % (m, str(exc).split(": ")[-1]))
                continue
            provati.append(m)
            nuovo = rileggi()
            s2 = self._nitidezza(nuovo)
            _registro.info("nitidezza con il fuoco %s: %.1f", m, s2)
            if s2 > migliore[0]:
                migliore = (s2, nuovo, m)
            if s2 >= soglia:
                break
        s, raw, m = migliore
        if m and m != provati[-1]:
            # la lente e' rimasta all'ultimo provato: si torna al migliore
            try:
                with self._sotto_pressore(backend):
                    backend.autofocus(m)
            except (Emergenza, MotoriBloccati, PressoreBloccato):
                raise
            except ScannerError:
                pass
        if m:
            self._metodo_fuoco = m
            self._ultimo_metodo_fuoco = m
        rif[0] = max(rif[0], s)
        if s < soglia:
            self.job.errors.append("fuoco dubbio anche con gli altri metodi (nitidezza %.0f): "
                                   "la striscia puo' venire sfocata" % s)
            self.log("Attenzione: fuoco dubbio anche con gli altri metodi.")
        else:
            self.log("Fuoco migliore con il metodo %s (nitidezza %.0f): lo uso per il "
                     "resto del rullo." % (m, s))
        return raw

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

    def _misura_esposizione(self, backend, preset,
                            ultima: bool = False) -> Optional[Dict[str, float]]:
        """L'esposizione per canale misurata sul fotogramma in finestra.
        None se nemmeno il canale piu' chiaro passa la soglia
        (cfg.soglia_misura_esposizione): la finestra e' sulla coda o su un
        fotogramma tutto opaco, e una misura li' chiederebbe tempi che la
        macchina rifiuta. All'ultima prova non si rinuncia: x1 su tutto, e
        lo si dice."""
        self.progress("Misura esposizione per canale", 0.0)
        avvisi: List[str] = []
        livelli: Dict[str, float] = {}
        with self._sotto_pressore(backend):
            gains = measure_channel_exposure(backend, preset.channels,
                                             self.calib,
                                             annulla=self.annullato,
                                             avvisi=avvisi, livelli=livelli)
        self._livelli_misura = dict(livelli)
        luce = max(livelli.values()) if livelli else 1.0
        soglia = self.cfg.soglia_misura_esposizione
        if luce < soglia:
            dettaglio = ", ".join("%s %.1f%%" % (k, v * 100)
                                  for k, v in livelli.items())
            if not ultima:
                self.log("Misura dell'esposizione su una zona opaca (%s): "
                         "non vale, si riprova sul fotogramma dopo." % dettaglio)
                return None
            gains = dict((c, 1.0) for c in gains)
            avviso = ("esposizione non misurabile: tre zone opache di fila "
                      "(%s). Si scansiona a x1 su tutti i canali." % dettaglio)
            self.job.errors.append(avviso)
            self.log("Attenzione: " + avviso)
        else:
            for a in avvisi:
                self.log("Attenzione: " + a)
        self.log("Esposizioni: " + ", ".join(
            "%s x%.2f" % (k, v) for k, v in gains.items()))
        return gains

    def _lettura_unica_torna(self, raw) -> bool:
        """La lettura unica ha dato a ogni canale il suo tempo? Si confronta
        il livello di ogni piano con quello misurato a x1 sullo stesso
        fotogramma, per il fattore chiesto. L'ordine dei LED nell'
        ExposureTime (R, G, B, IR) e' supposto, non verificato: se e'
        diverso, i tempi finiscono sui canali sbagliati e qui si vede."""
        scarti = {}
        for c, livello in (self._livelli_misura or {}).items():
            if c not in raw.planes:
                continue
            fattore = float(raw.meta.channel_exposure.get(c, 1.0))
            atteso = livello * fattore
            if not (0.05 < atteso < 0.9):
                continue            # al buio o in saturazione non si giudica
            visto = float(np.percentile(raw.planes[c][0][::4, ::4], 99.0))
            scarti[c] = visto / atteso
        if not scarti:
            return True
        dettaglio = ", ".join("%s %.2f" % kv for kv in sorted(scarti.items()))
        if any(abs(r - 1.0) > 0.35 for r in scarti.values()):
            self.log("Lettura unica disattivata: i livelli non tornano con i "
                     "tempi chiesti (visto/atteso %s). Probabile ordine dei "
                     "LED diverso: si legge un canale per volta." % dettaglio)
            return False
        self.log("Lettura unica verificata (visto/atteso %s)." % dettaglio)
        return True

    # --- esportazione, e rielaborare i fotogrammi gia' scansionati --------

    # --- statistiche di rullo -------------------------------------------

    def _memoria_pellicole(self) -> MemoriaPellicole:
        cartella = os.path.dirname(os.path.abspath(self.archivio.path))
        return MemoriaPellicole(os.path.join(cartella, "pellicole.json"))

    def _file_statistiche(self, rullo_id: int) -> str:
        return os.path.join(self.cfg.path("raw"), "rullo_%d_statistiche.json" % int(rullo_id))

    @staticmethod
    def _opzioni_fotogramma(opts: ProcessOptions, corr) -> ProcessOptions:
        """Le opzioni di un fotogramma con base, equilibrio e classe del
        rullo; senza correzione (pochi dati, fotogramma letto a x1) quelle
        del rullo, cioe' il bilanciamento del singolo fotogramma."""
        if not corr:
            return opts
        if corr.get("profilo"):
            opts = ScanService._con_pellicola(opts, corr)
        if not corr.get("base") or not corr.get("densita"):
            return opts
        if not opts.colore_auto:
            # colore a mano: resta solo la base della pellicola
            return dataclasses.replace(opts, base_rullo=tuple(corr["base"]))
        return dataclasses.replace(
            opts, base_rullo=tuple(corr["base"]),
            # bianco automatico spento: niente equilibrio di colore del rullo
            correzione_densita=(tuple(corr["densita"]) if opts.auto_wb > 0 else None),
            # luce automatica spenta: niente ritocco della classe d'esposizione
            classe_esposizione=(str(corr.get("classe", "")) if opts.auto_levels else ""),
            avvisi_rullo=list(corr.get("avvisi", [])))

    @staticmethod
    def _con_pellicola(opts: ProcessOptions, extra) -> ProcessOptions:
        """Le opzioni con quel che si sa della pellicola (_extra_pellicola):
        profilo di inversione, pendenza attesa, grana attesa, nome e ISO per
        i metadati. Senza pellicola riconosciuta resta il profilo del rullo,
        con la pendenza dello sviluppo scelto."""
        profilo = PROFILES.get(extra.get("profilo") or "", opts.profile)
        if not profilo.positive and extra.get("gamma"):
            profilo = dataclasses.replace(profilo, gamma_negativo=float(extra["gamma"]))
        opts = dataclasses.replace(
            opts, profile=profilo,
            grana_pellicola=float(extra.get("riduzione_grana") or 0.0),
            grana_pellicola_solo_croma=bool(extra.get("solo_croma", True)),
            pellicola=str(extra.get("nome_pellicola") or ""),
            iso_pellicola=int(extra.get("iso") or 0))
        if extra.get("argento"):
            # B/N all'argento: opaco all'infrarosso, la maschera IR
            # coprirebbe l'immagine intera
            opts.ir_strength = 0.0
        return opts

    def _extra_pellicola(self, rullo, chiave: str) -> Dict[str, object]:
        """Pellicola (dal codice DX) e sviluppo del rullo, in un dizionario
        che si salva con le correzioni dei fotogrammi: Riapri e Applica al
        rullo lo ritrovano anche se la striscia era di un'altra pellicola."""
        pell = pellicole_dx.da_chiave(chiave)
        profilo = rullo.profilo
        if (pell is not None and pell.profilo
                and getattr(self.cfg, "pellicola_da_dx", True)):
            profilo = pell.profilo
        sviluppo = int(getattr(rullo, "sviluppo", 0) or 0)
        att = pellicole_dx.attese_pellicola(pell, profilo, sviluppo)
        conosciuta = pell is not None and pell.conosciuta
        return {"pellicola": chiave or "",
                "nome_pellicola": pell.descrizione() if conosciuta else "",
                "tipo": pell.tipo if conosciuta else "",
                "iso": pell.iso if conosciuta else 0,
                "argento": bool(conosciuta and pell.argento),
                "profilo": profilo, "sviluppo": sviluppo,
                "gamma": att["gamma"], "scala_densita": att["scala_densita"],
                "nome_grana": att["nome_grana"],
                "riduzione_grana": att["riduzione_grana"],
                "solo_croma": att["solo_croma"], "note": att["note"]}

    def _chiudi_statistiche(self, rullo_id: int, gruppi: Dict[str, dict],
                            memoria: MemoriaPellicole) -> None:
        """A fine rullo: statistiche salvate (servono a Riapri, Salva e
        Applica al rullo), memoria della pellicola aggiornata, diario. Un
        gruppo per pellicola: le strisce di pellicole diverse hanno ognuna
        il suo equilibrio e la sua memoria."""
        try:
            dati = {"pellicole": {}, "fotogrammi": {}}
            for chiave, g in gruppi.items():
                stat = g["stat"]
                r = stat.riepilogo()
                if not r["fotogrammi"]:
                    continue
                dati["pellicole"][chiave or "?"] = dict(r, pellicola=g["extra"])
                for n in stat.numeri():
                    dati["fotogrammi"][str(n)] = dict(stat.correzione(n), **g["extra"])
                memoria.aggiorna(chiave, stat)
                classi = ", ".join("%s %d" % kv for kv in sorted(r["classi"].items()))
                self.log("%s: %d fotogrammi nell'equilibrio (%s)." % (
                    g["extra"].get("nome_pellicola") or chiave or "Rullo",
                    r["fotogrammi"], classi))
                velo = stat.velo_di_base()
                if velo is not None and velo > 0.08:
                    avviso = ("base della pellicola %.2f D piu' densa del solito per %s: "
                              "pellicola velata o sviluppo diverso" % (velo, chiave))
                    self.job.errors.append(avviso)
                    self.log("Attenzione: " + avviso)
            if not dati["fotogrammi"]:
                return
            percorso = self._file_statistiche(rullo_id)
            with open(percorso + ".tmp", "w", encoding="utf-8") as f:
                json.dump(dati, f, indent=1)
            os.replace(percorso + ".tmp", percorso)
        except Exception as exc:                        # noqa: BLE001
            self.log("Statistiche del rullo non salvate: %s" % exc)

    def _correzioni_salvate(self, rullo_id: int) -> Dict[str, object]:
        try:
            with open(self._file_statistiche(rullo_id), encoding="utf-8") as f:
                return json.load(f).get("fotogrammi", {})
        except (IOError, OSError, ValueError):
            return {}

    def _opzioni_rullo(self, rullo) -> ProcessOptions:
        """Le opzioni di elaborazione per un rullo: quelle correnti (cursori,
        LUT) con profilo, specchiatura e Digital ICE del rullo."""
        opts = ProcessOptions(**{k: v for k, v in vars(self.options).items()})
        opts.profile = PROFILES.get(rullo.profilo, PROFILES["c41"])
        opts.roll_mirror = RollMirror(flip_h=rullo.specchiato, source="operatore",
                                      confirmed=rullo.conf_specchio)
        if not rullo.ice:
            opts.ir_strength = 0.0
        opts.negativo_lineare = self.cfg.negativo_lineare in ("tiff", "dng")
        return self._con_pellicola(opts, self._extra_pellicola(
            rullo, getattr(rullo, "dx", "")))

    @staticmethod
    def _meta_file(ordine, rullo, numero: int, bordo: str, raw,
                   opts: Optional[ProcessOptions] = None) -> Dict[str, object]:
        """Cosa scrivere dentro il file: ordine, rullo, fotogramma, numero
        di bordo, pellicola (quella riconosciuta dal DX se c'e'), ISO, data
        di scansione, dpi veri."""
        parti = ["Ordine %s" % ordine.codice, "rullo %s" % rullo.etichetta,
                 "fotogramma %d" % numero]
        if bordo:
            parti.append("bordo %s" % bordo)
        nome = getattr(opts, "pellicola", "") if opts is not None else ""
        profilo = opts.profile if opts is not None else PROFILES.get(rullo.profilo)
        parole = []
        if nome:
            parti.append("pellicola %s" % nome)
            parole.append(nome.split(",")[0])
        else:
            parti.append("pellicola %s" % (profilo.name if profilo else rullo.profilo))
        if profilo is not None:
            parole.append(profilo.name)
        if getattr(rullo, "dx", ""):
            parti.append(rullo.dx)
            parole.append(rullo.dx)
        sviluppo = int(getattr(rullo, "sviluppo", 0) or 0)
        if sviluppo:
            parti.append("sviluppo %s" % pellicole_dx.SVILUPPI.get(sviluppo, sviluppo))
        parti.append("scansione Gretag Masterflex D1008")
        return {"descrizione": " - ".join(parti),
                "dpi": getattr(raw.meta, "dpi", 0) or 0,
                "data": getattr(raw.meta, "timestamp", 0) or None,
                "iso": getattr(opts, "iso_pellicola", 0) if opts is not None else 0,
                "parole": parole}

    def _esporta(self, raw, opts, dest: str, nome: str, rullo_id: int,
                 numero: int, bordo: str, raw_path: str, ordine_rullo,
                 in_rete: bool = True, ferma: Optional[Callable[[], bool]] = None):
        """Elaborazione completa di un grezzo, file finale (con i
        metadati), maschera e negativo lineare se chiesti, e registro. Lo
        usano il rullo, Salva fotogramma e Applica a tutto il rullo.

        Con un elaboratore della rete in ascolto il lavoro lo fa lui
        (_esporta_in_rete); se non risponde, sparisce o sbaglia, lo si rifa'
        qui, senza perdere il fotogramma. La geometria trovata si salva per
        la prossima volta (geometria.py)."""
        ordine, rullo = ordine_rullo
        t0 = time.time()
        meta = self._meta_file(ordine, rullo, numero, bordo, raw, opts)
        ext = {"tiff16": ".tif", "tiff8": ".tif",
               "png": ".png", "jpeg": ".jpg"}.get(self.cfg.export_format, ".tif")
        out_path = os.path.join(dest, nome + ext)
        mask_path = os.path.join(dest, nome + "_maschera.png") if self.cfg.export_ir_mask else ""
        neg_fmt = self.cfg.negativo_lineare if self.cfg.negativo_lineare in ("tiff", "dng") else ""
        neg_path = os.path.join(dest, nome + "_negativo." + (
            "dng" if neg_fmt == "dng" else "tif")) if neg_fmt else ""

        if ferma is not None and ferma():
            raise _ElaborazioneFermata()
        pulito, opts = self._ice_kodak(raw, opts, numero)
        # Pulito dal Digital ICE Kodak, il grezzo non e' piu' quello del file
        # sul disco: all'elaboratore va il grezzo pulito, non il file (fino
        # all'8/11/2026 partiva il file, cioe' il grezzo NON pulito).
        file_rete = raw_path if pulito is raw else ""
        raw = pulito
        res = None
        if in_rete and self.in_rete():
            try:
                res, mask_path, neg_path = self._esporta_in_rete(
                    raw, opts, file_rete, meta, out_path, mask_path, neg_path, neg_fmt,
                    ferma=ferma)
                _registro.info("fotogramma %d: elaborato da %s in %.1f s",
                               numero, res.tempi.get("elaboratore", "?"), time.time() - t0)
            except Exception as exc:                    # noqa: BLE001
                res = None
                if ferma is not None and ferma():
                    raise _ElaborazioneFermata()
                _registro.warning("fotogramma %d: elaboratore della rete non usato (%s), "
                                  "lo elabora la macchina", numero, exc)
                self.log("Fotogramma %d: elaboratore della rete non disponibile (%s), "
                         "lo elaboro qui." % (numero, exc))
        if res is None:
            def controlla(msg=None, frazione=None):
                if ferma is not None and ferma():
                    raise _ElaborazioneFermata()
            controlla()
            with self._lock_locale:
                controlla()
                t0 = time.time()
                res = process(raw, opts, progress=controlla if ferma is not None else None)
                t_process = time.time() - t0
                export(res.image, out_path, self.cfg.export_format,
                       self.cfg.jpeg_quality, meta=meta)
                # nel registro (non nel diario): dove va il tempo di ogni fotogramma
                lenti = sorted(res.tempi.items(), key=lambda kv: -kv[1])[:5]
                _registro.info("fotogramma %d: elaborazione %.1f s, salvataggio %.1f s (%s)",
                               numero, t_process, time.time() - t0 - t_process,
                               ", ".join("%s %.1f" % kv for kv in lenti))
                if mask_path and res.mask is not None:
                    mask_path = export_mask(res.mask, mask_path)
                else:
                    mask_path = ""
                if neg_path and res.lineare is not None:
                    neg_path = export_negativo(res.lineare, neg_path, neg_fmt, meta=meta)
                else:
                    neg_path = ""
                res.lineare = None          # 35 MB in meno, subito
                if self.archivio_rete.attivo() and res.base is not None:
                    # il negativo invertito per l'archivio di rete: la base
                    # lineare, prima di nitidezza e grana (come Positivo DNG)
                    try:
                        export_negativo(np.clip(np.asarray(res.base, dtype=np.float32), 0.0, 1.0),
                                        os.path.join(dest, nome + "_positivo.dng"), "dng", meta=meta)
                    except Exception as exc:    # noqa: BLE001
                        _registro.warning("positivo DNG del fotogramma %d: %s", numero, exc)

        if res.geometria:
            geometrie.scrivi(self.cfg.path("raw"), rullo_id, numero, res.geometria)
        viva = self.file_anteprima_viva(raw_path)
        if viva and os.path.exists(viva):
            try:
                os.remove(viva)             # adesso c'e' il file vero
            except OSError:
                pass
        self.archivio.fotogramma_registra(
            rullo_id, numero, file_out=out_path, file_raw=raw_path,
            file_maschera=mask_path,
            rotazione=res.orientation.rotation if res.orientation else 0,
            stato="ok", avvisi=" | ".join(res.warnings),
            numero_bordo=bordo, file_negativo=neg_path)
        self.archivio_rete.accoda(rullo_id)
        return res

    def _ice_kodak(self, raw, opts, numero: int):
        """Il Digital ICE della Kodak sul grezzo (ice_kodak.py), se il rullo
        lo chiede e la libreria c'e'. Ritorna (grezzo, opzioni): pulito e
        senza la pulizia nostra, oppure com'era se non si e' potuto."""
        if (self.cfg.digital_ice != "kodak" or opts.ir_strength <= 0
                or "IR" not in raw.planes or not ice_kodak.disponibile()):
            return raw, opts
        try:
            with self._lock_ice:
                nuovo, esito = ice_kodak.pulisci(raw)
        except Exception as exc:                        # noqa: BLE001
            _registro.warning("fotogramma %d: Digital ICE Kodak non usato (%s): "
                              "Digital ICE nostro", numero, exc)
            return raw, opts
        _registro.info("fotogramma %d: Digital ICE Kodak, %s pixel difettosi, %s s, "
                       "cambiati %s%%", numero, esito.get("difettosi"),
                       esito.get("secondi"), esito.get("cambiati_pct"))
        return nuovo, dataclasses.replace(opts, ir_strength=0.0)

    # --- elaboratori della rete (remoti.py, elaboratore.py) ---------------

    def in_rete(self) -> bool:
        """Elaboratori accesi nelle impostazioni e almeno uno collegato."""
        return bool(self.cfg.elaboratori_rete and self.elaboratori.chiave
                    and self.elaboratori.presenti())

    def _quanti_insieme(self, tetto: int = 0) -> int:
        """Quanti fotogrammi lavorare insieme nei lavori in blocco (Completa,
        Applica, Positivi): uno per processo elaboratore collegato, fino a
        cfg.elaboratori_massimo (e a 'tetto', se c'e'). Senza elaboratori
        uno: la macchina li fa uno alla volta, come prima."""
        if not self.in_rete():
            return 1
        n = min(self.elaboratori.processi_presenti(), int(self.cfg.elaboratori_massimo or 1))
        if tetto:
            n = min(n, int(tetto))
        return max(1, n)

    def _in_parallelo(self, elementi, fai, quanti: int, messaggio) -> int:
        """fai(elemento) su tutti, 'quanti' alla volta. Ritorna quanti sono
        riusciti (fai restituisce True). STOP ferma quelli non ancora
        partiti; l'avanzamento conta quelli finiti."""
        import concurrent.futures
        fatti = {"ok": 0, "finiti": 0}
        lock = threading.Lock()
        totale = max(len(elementi), 1)

        def uno(e):
            if self._stop.is_set():
                return
            esito = bool(fai(e))
            with lock:
                fatti["finiti"] += 1
                fatti["ok"] += 1 if esito else 0
                self.progress(messaggio(e, fatti["finiti"], totale),
                              fatti["finiti"] / float(totale))
        if quanti <= 1:
            for e in elementi:
                if self._stop.is_set():
                    break
                uno(e)
            return fatti["ok"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=quanti) as ex:
            for f in [ex.submit(uno, e) for e in elementi]:
                f.result()
        return fatti["ok"]

    def _aiuto_rete(self) -> bool:
        """Per gli aiutanti della coda: un elaboratore e' in ascolto adesso."""
        return bool(self.cfg.elaboratori_rete and self.elaboratori.chiave
                    and self.elaboratori.liberi() > 0)

    def _pacco(self, raw, opts, raw_path: str, lavoro: Dict[str, object]) -> bytes:
        """Il lavoro per un elaboratore: il grezzo (il file gia' salvato se
        c'e', senza ricomprimerlo: sul PC della macchina costerebbe secondi),
        le opzioni, e cosa farne. Zip senza compressione."""
        import pickle
        import zipfile
        from dataclasses import asdict
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
            if raw_path and os.path.exists(raw_path) and os.path.exists(raw_path[:-4] + ".json"):
                z.write(raw_path, "grezzo.npz")
                z.write(raw_path[:-4] + ".json", "grezzo.json")
            else:
                piani = io.BytesIO()
                np.savez(piani, **dict(("%s_%d" % (c, k), p)
                                       for c, pile in raw.planes.items()
                                       for k, p in enumerate(pile)))
                z.writestr("grezzo.npz", piani.getvalue())
                z.writestr("grezzo.json", json.dumps(asdict(raw.meta)))
            z.writestr("opzioni.pkl", pickle.dumps(opts, protocol=2))
            z.writestr("lavoro.json", json.dumps(lavoro))
        return buf.getvalue()

    @staticmethod
    def _esito(dati: bytes):
        """Apre la risposta di un elaboratore. Mai pickle da li': solo JSON,
        immagini e array numpy senza oggetti (allow_pickle=False). Ritorna
        (esito, zip aperto)."""
        import zipfile
        z = zipfile.ZipFile(io.BytesIO(dati))
        esito = json.loads(z.read("esito.json").decode("utf-8"))
        if not isinstance(esito, dict):
            raise RuntimeError("risposta dell'elaboratore non valida")
        return esito, z

    @staticmethod
    def _risultato_da_esito(esito: Dict[str, object], z) -> "object":
        from .orient import Orientation
        from .pipeline import ProcessResult
        img = None
        if "anteprima.jpg" in z.namelist():
            from PIL import Image
            im = Image.open(io.BytesIO(z.read("anteprima.jpg"))).convert("RGB")
            img = np.asarray(im, dtype=np.float32) / 255.0
        base = neg = None
        if "base.npy" in z.namelist():
            base = np.load(io.BytesIO(z.read("base.npy")), allow_pickle=False).astype(np.float32)
        if "negativo.npy" in z.namelist():
            neg = np.load(io.BytesIO(z.read("negativo.npy")), allow_pickle=False)
        rot = int(esito.get("rotazione") or 0)
        geo = esito.get("geometria") if isinstance(esito.get("geometria"), dict) else {}
        tempi = dict((str(k), float(v)) for k, v in (esito.get("tempi") or {}).items())
        tempi["elaboratore"] = str(esito.get("elaboratore") or "?")  # type: ignore
        res = ProcessResult(img, None, None, {}, float(esito.get("maschera_pct") or 0.0),
                            [str(w) for w in (esito.get("avvisi") or [])],
                            Orientation(rot % 360, bool(geo.get("specchio"))),
                            neg, base, None, tempi, geometria=geo)
        return res

    def _esporta_in_rete(self, raw, opts, raw_path, meta, out_path, mask_path,
                         neg_path, neg_fmt, ferma=None):
        lavoro = {"tipo": "esporta", "formato": self.cfg.export_format,
                  "qualita": int(self.cfg.jpeg_quality), "maschera": bool(mask_path),
                  "negativo": neg_fmt, "meta": meta,
                  "positivo": bool(self.archivio_rete.attivo())}
        # il pacco (~40 MB) lo tiene solo la coda degli elaboratori, che lo
        # lascia appena un elaboratore l'ha preso (remoti.prendi)
        esito, z = self._esito(self.elaboratori.elabora(
            lambda: self._pacco(raw, opts, raw_path, lavoro), ferma=ferma))
        nomi = z.namelist()
        if "file/finale" not in nomi:
            raise RuntimeError("l'elaboratore non ha mandato il file finito")
        # i nomi dei file li decide la macchina, non chi risponde
        scritti = []
        try:
            positivo = os.path.splitext(out_path)[0] + "_positivo.dng"
            for interno, dove in (("file/finale", out_path), ("file/maschera", mask_path),
                                  ("file/negativo", neg_path), ("file/positivo", positivo)):
                if dove and interno in nomi:
                    with open(dove + ".tmp", "wb") as f:
                        f.write(z.read(interno))
                    os.replace(dove + ".tmp", dove)
                    scritti.append(dove)
        except Exception:
            for p in scritti:
                if p != out_path:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            raise
        res = self._risultato_da_esito(esito, z)
        return (res, mask_path if mask_path in scritti else "",
                neg_path if neg_path in scritti else "")

    def _processa(self, raw, opts, raw_path: str = ""):
        """L'elaborazione per il visore (Riapri): in rete se c'e' un
        elaboratore, con la base lineare e il negativo per i cursori e il
        confronto; altrimenti qui. Nessun file."""
        pulito, opts = self._ice_kodak(raw, opts, 0)
        file_rete = raw_path if pulito is raw else ""
        raw = pulito
        if self.in_rete():
            try:
                t0 = time.time()
                esito, z = self._esito(self.elaboratori.elabora(
                    lambda: self._pacco(raw, opts, file_rete, {"tipo": "processa"}),
                    timeout=180.0))
                res = self._risultato_da_esito(esito, z)
                if res.base is None:
                    raise RuntimeError("l'elaboratore non ha mandato la base")
                img, _ = finiture(res.base, opts)
                res.image = img
                _registro.info("riapertura elaborata da %s in %.1f s",
                               res.tempi.get("elaboratore"), time.time() - t0)
                return res
            except Exception as exc:                    # noqa: BLE001
                _registro.warning("riapertura: elaboratore non usato (%s)", exc)
        with self._lock_locale:
            return process(raw, opts, progress=self.progress)

    def stato_elaboratori(self) -> Dict[str, object]:
        st = self.elaboratori.stato()
        st["attivi"] = bool(self.cfg.elaboratori_rete)
        st["chiave"] = self.cfg.chiave_elaboratori
        return st

    def imposta_elaboratori(self, attivi: Optional[bool] = None,
                            nuova_chiave: bool = False) -> Dict[str, object]:
        if nuova_chiave or (attivi and not self.cfg.chiave_elaboratori):
            self.cfg.chiave_elaboratori = remoti.nuova_chiave()
            self.elaboratori.chiave = self.cfg.chiave_elaboratori
        if attivi is not None:
            self.cfg.elaboratori_rete = bool(attivi)
        return self.stato_elaboratori()

    # --- geometria salvata ---------------------------------------------

    def _con_geometria(self, opts: ProcessOptions, rullo_id: int,
                       numero: int) -> ProcessOptions:
        """Le opzioni con la geometria salvata del fotogramma: riaprirlo non
        rifa' allineamento, raddrizzamento, ritaglio e orientamento."""
        geo = geometrie.leggi(self.cfg.path("raw"), rullo_id, numero)
        return dataclasses.replace(opts, geometria=geo) if geo else opts

    # --- preferenze ------------------------------------------------------

    PREFERENZE = ("esposizione_auto", "avanzamento_automatico", "pref_ritaglio",
                  "pref_raddrizza", "pref_luce", "pref_bianco", "pref_orientamento")

    def preferenze(self) -> Dict[str, bool]:
        return dict((k, bool(getattr(self.cfg, k))) for k in self.PREFERENZE)

    def imposta_preferenze(self, valori: Dict[str, object]) -> Dict[str, bool]:
        if self.job.running and any(k in valori for k in ("esposizione_auto",
                                                         "avanzamento_automatico")):
            raise ScannerError("c'e' un lavoro in corso: esposizione e avanzamento "
                               "si cambiano a lavoro finito")
        for k in self.PREFERENZE:
            if k in valori:
                setattr(self.cfg, k, bool(valori[k]))
        self.options = self.options        # il setter le rimette
        return self.preferenze()

    def con_preferenze(self, opts: ProcessOptions) -> ProcessOptions:
        """Le opzioni con gli interruttori del pannello Preferenze: sono loro
        a decidere, non i cursori mandati dalla pagina."""
        c = self.cfg
        return dataclasses.replace(
            opts, auto_crop=bool(c.pref_ritaglio), auto_straighten=bool(c.pref_raddrizza),
            auto_levels=bool(c.pref_luce), auto_wb=(1.0 if c.pref_bianco else 0.0),
            auto_rotate=bool(c.pref_orientamento),
            colore_auto=bool(c.pref_luce or c.pref_bianco))

    def _per_la_mano(self) -> Dict[str, object]:
        """Rotazione e canali a mano si fanno sul fotogramma aperto dalla
        striscia, a lavoro finito, e cambiano la sua geometria in memoria:
        diventano il file con Salva fotogramma."""
        if self.job.running:
            raise ScannerError("c'e' un lavoro in corso: a lavoro finito")
        if not self._aperto or self.ultimo_raw is None or self.base_lineare is None:
            raise ValueError("toccare prima il fotogramma nella striscia per aprirlo")
        if self._geometria_aperta is None:
            self._geometria_aperta = {}
        return self._geometria_aperta

    def _esito_mano(self, geo: Dict[str, object]) -> Dict[str, object]:
        self.rielabora(None, veloce=True)
        self.job.anteprime += 1
        return {"rotazione": self._rotazione_di(geo),
                "canali": geo.get("canali") or {},
                "messaggio": "Premere Salva fotogramma per scriverlo nel file."}

    @staticmethod
    def _rotazione_di(geo: Dict[str, object]) -> int:
        if geo.get("rotazione") is not None:
            return int(geo["rotazione"]) % 360
        return int(geo.get("orientamento") or 0) % 360

    def geometria_aperta(self) -> Dict[str, object]:
        geo = self._geometria_aperta or {}
        return {"aperto": bool(self._aperto), "rotazione": self._rotazione_di(geo),
                "canali": geo.get("canali") or {},
                "spostamenti": geo.get("spostamenti") or {},
                "angolo": geo.get("angolo"), "a_mano": geo.get("rotazione") is not None,
                "specchio": bool(geo.get("specchio")),
                "intero": bool(self._intero_mostrato),
                "riquadro_vista": self._riquadro_vista(geo),
                "ritaglio_mano": bool(geo.get("riquadro_mano"))}

    def _riquadro_vista(self, geo: Dict[str, object]) -> List[float]:
        """Il ritaglio di adesso (a mano, o quello automatico se i bordi si
        sono trovati) come lo si vede sul fotogramma intero girato: 0..1,
        [y0, x0, y1, x1]. Da li' parte il riquadro della sezione Geometria."""
        forma = geo.get("forma")
        r = geo.get("riquadro_mano")
        if not r and geo.get("riquadro") and float(geo["riquadro"][4]) >= 0.5 \
                and self.cfg.pref_ritaglio:
            r = geo["riquadro"]
        if not forma or not r:
            return [0.0, 0.0, 1.0, 1.0]
        h, w = float(forma[0]), float(forma[1])
        rot, specchio = self._rotazione_di(geo), bool(geo.get("specchio"))
        punti = [geometrie.punto_nella_vista(y / h, x / w, rot, specchio)
                 for y, x in ((r[0], r[1]), (r[0], r[3]), (r[2], r[1]), (r[2], r[3]))]
        ys, xs = [p[0] for p in punti], [p[1] for p in punti]
        return [round(min(ys), 4), round(min(xs), 4), round(max(ys), 4), round(max(xs), 4)]

    def geometria_conferma(self, angolo_schermo: float = 0.0,
                           riquadro: Optional[Dict[str, float]] = None) -> None:
        """Conferma della sezione Geometria: l'angolo girato a mano sullo
        schermo (gradi, positivo = orario, come lo mostra l'anteprima) e il
        riquadro del ritaglio (0..1 sul fotogramma intero mostrato) entrano
        nella geometria del fotogramma, che si rielabora e si risalva. Il
        grezzo non si tocca: si rielabora sempre da li'."""
        geo = self._per_la_mano()
        a = float(angolo_schermo or 0.0)
        if abs(a) > 45:
            raise ValueError("angolo troppo grande: per i 90 gradi ci sono i tasti di rotazione")
        if abs(a) >= 0.01:
            # schermo orario = antiorario per ndimage; lo specchio lo inverte
            delta = -a * (-1.0 if geo.get("specchio") else 1.0)
            geo["angolo"] = round(float(geo.get("angolo") or 0.0) + delta, 3)
            geo["angolo_mano"] = True
        if riquadro:
            if not self._intero_mostrato:
                raise ValueError("il riquadro si sceglie sul fotogramma intero (Modifica)")
            forma = geo.get("forma")
            if not forma:
                raise ValueError("geometria del fotogramma sconosciuta: riaprirlo")
            y0, y1 = sorted((float(riquadro["y0"]), float(riquadro["y1"])))
            x0, x1 = sorted((float(riquadro["x0"]), float(riquadro["x1"])))
            y0, x0 = max(0.0, y0), max(0.0, x0)
            y1, x1 = min(1.0, y1), min(1.0, x1)
            if y1 - y0 < 0.05 or x1 - x0 < 0.05:
                raise ValueError("riquadro troppo piccolo")
            rot, specchio = self._rotazione_di(geo), bool(geo.get("specchio"))
            punti = [geometrie.punto_dalla_vista(y, x, rot, specchio)
                     for y, x in ((y0, x0), (y0, x1), (y1, x0), (y1, x1))]
            h, w = int(forma[0]), int(forma[1])
            ys, xs = [p[0] for p in punti], [p[1] for p in punti]
            geo["riquadro_mano"] = [int(round(min(ys) * h)), int(round(min(xs) * w)),
                                    int(round(max(ys) * h)), int(round(max(xs) * w))]
        geo.pop("senza_ritaglio", None)
        self._intero_mostrato = False
        self.log("Geometria confermata: rielaboro e salvo il fotogramma (il grezzo resta com'e').")
        self.salva_fotogramma()

    def geometria_annulla(self) -> None:
        """Lascia le modifiche non confermate: torna la geometria salvata."""
        if not self._aperto:
            raise ValueError("nessun fotogramma aperto")
        f, rullo, _ = self._fotogramma_e_rullo(self._aperto)
        self._geometria_aperta = geometrie.leggi(self.cfg.path("raw"), rullo.id,
                                                 int(f["numero"]))
        self._intero_mostrato = False

        def job():
            self.rielabora(None, veloce=False)
            self.log("Modifiche della geometria lasciate.")
        self._avvia("geometria", job)

    def ruota(self, passi: int) -> Dict[str, object]:
        """Gira il fotogramma aperto di 90 gradi per passo (1 = antiorario,
        -1 = orario). Nell'anteprima si gira la base gia' pronta (senza
        rifare niente); nel file entra con Salva fotogramma. Prevale sulla
        rotazione automatica, e resta salvata per le prossime riaperture."""
        geo = self._per_la_mano()
        passi = int(passi) % 4
        if not passi:
            return self._esito_mano(geo)
        geo["rotazione"] = (self._rotazione_di(geo) + 90 * passi) % 360
        # con lo specchio del rullo la rotazione vista sullo schermo e' al
        # contrario (lo specchio viene dopo la rotazione: orient.apply_orientation)
        k = -passi if geo.get("specchio") else passi
        self.base_lineare = np.ascontiguousarray(np.rot90(self.base_lineare, k))
        if self.preview_neg is not None:
            self.preview_neg = np.ascontiguousarray(np.rot90(self.preview_neg, k))
        self.log("Fotogramma ruotato a mano: %d gradi." % geo["rotazione"])
        return self._esito_mano(geo)

    def sposta_canale(self, canale: str, dy: float, dx: float) -> Dict[str, object]:
        """Sposta a mano il rosso o il blu rispetto al verde, di (dy, dx)
        pixel come li si vede sullo schermo. Serve quando l'allineamento
        automatico non basta (bordi colorati). Si somma allo spostamento
        automatico salvato, che non si ricalcola."""
        from .registration import shift_image
        canale = str(canale).upper()
        if canale not in ("R", "B"):
            raise ValueError("si spostano il rosso (R) e il blu (B): il verde e' il riferimento")
        geo = self._per_la_mano()
        if self.base_lineare.ndim != 3 or self.base_lineare.shape[2] < 3 \
                or len([c for c in self.ultimo_raw.planes if c != "IR"]) < 3:
            raise ValueError("fotogramma a un canale solo: non ci sono canali da allineare")
        dy, dx = float(dy), float(dx)
        if abs(dy) > 20 or abs(dx) > 20:
            raise ValueError("spostamento troppo grande")
        rot, specchio = self._rotazione_di(geo), bool(geo.get("specchio"))
        gy, gx = geometrie.dalla_vista(dy, dx, rot, specchio)
        canali = dict(geo.get("canali") or {})
        vy, vx = canali.get(canale, [0.0, 0.0])
        nuovo = [round(vy + gy, 3), round(vx + gx, 3)]
        if abs(nuovo[0]) < 1e-6 and abs(nuovo[1]) < 1e-6:
            canali.pop(canale, None)
        else:
            canali[canale] = nuovo
        if canali:
            geo["canali"] = canali
        else:
            geo.pop("canali", None)
        i = {"R": 0, "B": 2}[canale]
        base = self.base_lineare.copy()
        base[..., i] = shift_image(base[..., i], dy, dx)
        self.base_lineare = base
        return self._esito_mano(geo)

    def azzera_canali(self) -> Dict[str, object]:
        """Toglie gli spostamenti a mano dei canali."""
        geo = self._per_la_mano()
        canali = dict(geo.get("canali") or {})
        rot, specchio = self._rotazione_di(geo), bool(geo.get("specchio"))
        esito = None
        for c, (gy, gx) in canali.items():
            vy, vx = geometrie.nella_vista(-gy, -gx, rot, specchio)
            esito = self.sposta_canale(c, vy, vx)
        geo.pop("canali", None)
        return esito or self._esito_mano(geo)

    def ritaglio_inizia(self) -> None:
        """Ritaglio a mano, primo passo: il fotogramma intero (raddrizzato e
        girato, senza ritaglio) nel visore, per disegnarci il riquadro."""
        geo = self._per_la_mano()
        geo["senza_ritaglio"] = True

        def job():
            self.progress("Fotogramma intero per il ritaglio", 0.1)
            self.rielabora(None, veloce=False)
            # rielabora rimette la geometria del risultato, senza il segno:
            # lo si ripristina, cosi' le rotazioni seguenti restano sull'intero
            if self._geometria_aperta is not None:
                self._geometria_aperta["senza_ritaglio"] = True
            self._intero_mostrato = True
        self._avvia("geometria", job)

    def ritaglio_applica(self, y0: float, x0: float, y1: float, x1: float) -> None:
        """Il riquadro disegnato sullo schermo (0..1 sull'immagine intera
        mostrata) diventa il ritaglio a mano del fotogramma: nelle coordinate
        dell'immagine raddrizzata prima dell'orientamento, come il ritaglio
        automatico. Vale per le riaperture e per Salva."""
        geo = self._per_la_mano()
        y0, y1 = sorted((min(max(float(y0), 0.0), 1.0), min(max(float(y1), 0.0), 1.0)))
        x0, x1 = sorted((min(max(float(x0), 0.0), 1.0), min(max(float(x1), 0.0), 1.0)))
        if y1 - y0 < 0.05 or x1 - x0 < 0.05:
            raise ValueError("riquadro troppo piccolo")
        forma = geo.get("forma")
        if not forma:
            raise ValueError("geometria del fotogramma sconosciuta: riaprirlo")
        rot, specchio = self._rotazione_di(geo), bool(geo.get("specchio"))
        punti = [geometrie.punto_dalla_vista(y, x, rot, specchio)
                 for y, x in ((y0, x0), (y0, x1), (y1, x0), (y1, x1))]
        h, w = int(forma[0]), int(forma[1])
        ys, xs = [p[0] for p in punti], [p[1] for p in punti]
        geo["riquadro_mano"] = [int(round(min(ys) * h)), int(round(min(xs) * w)),
                                int(round(max(ys) * h)), int(round(max(xs) * w))]
        geo.pop("senza_ritaglio", None)

        def job():
            self.progress("Ritaglio a mano", 0.1)
            self.rielabora(None, veloce=False)
            self.log("Ritaglio a mano applicato: Salva fotogramma lo scrive nel file.")
        self._avvia("geometria", job)

    def ritaglio_togli(self) -> None:
        """Toglie il ritaglio a mano: torna quello automatico (o il
        fotogramma intero, se i bordi non si trovano)."""
        geo = self._per_la_mano()
        geo.pop("riquadro_mano", None)
        geo.pop("senza_ritaglio", None)

        def job():
            self.rielabora(None, veloce=False)
            self.log("Ritaglio a mano tolto.")
        self._avvia("geometria", job)

    def rifai_geometria(self, cosa: str = "tutto") -> None:
        """Rifa' le stime da capo sul fotogramma aperto: "canali" solo
        l'allineamento automatico, "tutto" anche raddrizzamento, ritaglio e
        orientamento (le scelte a mano si tolgono). E' l'unico caso, con la
        mano dell'operatore, in cui la geometria salvata si ricalcola."""
        geo = self._per_la_mano()
        if cosa == "canali":
            for k in ("spostamenti", "canali"):
                geo.pop(k, None)
        else:
            geo.clear()
        self._geometria_aperta = geo or None

        def job():
            self.progress("Nuovo calcolo della geometria", 0.1)
            self.rielabora(None, veloce=False)
            self.log("Geometria del fotogramma ricalcolata: Salva fotogramma la "
                     "scrive nel file.")
        self._avvia("geometria", job)

    def _fotogramma_e_rullo(self, fotogramma_id: int):
        righe = self.archivio.fotogrammi_per_id(int(fotogramma_id))
        if not righe:
            raise ValueError("fotogramma inesistente")
        f = righe[0]
        rullo = self.archivio.rullo(int(f["rullo_id"]))
        if rullo is None:
            raise ValueError("rullo inesistente")
        ordine = self.archivio.ordine(rullo.ordine_id)
        if ordine is None:
            raise ValueError("ordine inesistente")
        return f, rullo, ordine

    @staticmethod
    def _nome_file(f: Dict[str, object], ordine, rullo) -> str:
        """Il nome (senza estensione) dei file di un fotogramma: quello del
        file esistente se c'e', cosi' una rielaborazione lo sovrascrive."""
        for campo in ("file_out", "file_raw"):
            p = f.get(campo) or ""
            if p:
                base = os.path.splitext(os.path.basename(p))[0]
                return base
        return "%s_%s_%03d" % (ordine.codice, rullo.etichetta.replace(" ", "-"),
                               int(f["numero"]))

    def apri_fotogramma(self, fotogramma_id: int) -> None:
        """Riapre un fotogramma gia' scansionato dal suo grezzo, senza la
        pellicola: nel visore con le regolazioni correnti, pronto da
        correggere e risalvare."""
        f, rullo, ordine = self._fotogramma_e_rullo(fotogramma_id)
        raw_path = f.get("file_raw") or ""
        if not raw_path or not os.path.exists(raw_path):
            raise ScannerError("il grezzo di questo fotogramma non c'e' piu': "
                               "per rielaborarlo va riscansionato")

        def job():
            self.progress("Apertura del fotogramma %d" % f["numero"], 0.1)
            raw = RawScan.load(raw_path)
            opts = self._con_geometria(self._opzioni_fotogramma(
                self._opzioni_rullo(rullo),
                self._correzioni_salvate(rullo.id).get(str(f["numero"]))),
                rullo.id, int(f["numero"]))
            opts.negativo_lineare = False
            res = self._processa(raw, opts)
            self.ultimo_raw = raw
            self.preview = res.image
            self.base_lineare = res.base
            self.preview_ridotta = False
            self.preview_neg = res.negativo
            self._geometria_aperta = dict(res.geometria or {}) or None
            self._canali_in_base = {}
            self._aperto = int(f["id"])
            self._intero_mostrato = False
            self.log("Fotogramma %d del rullo %s aperto: correggere e premere "
                     "Salva fotogramma, o Applica a tutto il rullo."
                     % (f["numero"], rullo.etichetta))
        self._avvia("apertura", job)

    def mostra_fotogramma(self, fotogramma_id: int) -> Dict[str, object]:
        """Solo guardare, anche a lavoro in corso: il fotogramma si mostra dal
        file gia' esportato, senza il lock dei lavori e senza la macchina.
        Prima, mentre il rullo finiva di elaborare, toccare un fotogramma
        della striscia non mostrava niente (riserva, 7/11/2026). Per
        correggerlo si riapre dal grezzo a lavoro finito (apri_fotogramma)."""
        f, rullo, ordine = self._fotogramma_e_rullo(fotogramma_id)
        percorso = f.get("file_out") or ""
        messaggio = ""
        if not percorso or not os.path.exists(percorso):
            # non ancora elaborato: l'anteprima della scansione, se c'e'
            # (in memoria durante il rullo, su disco se l'elaborazione e'
            # stata fermata)
            chiave = (int(f["rullo_id"]), int(f["numero"]))
            viva = self._anteprime_vive.get(chiave)
            dove = self.file_anteprima_viva(str(f.get("file_raw") or ""))
            if viva is None and dove and os.path.exists(dove):
                with open(dove, "rb") as fh:
                    viva = fh.read()
            if not viva:
                return {"mostrato": False,
                        "messaggio": "fotogramma %d ancora in elaborazione" % f["numero"]}
            from PIL import Image
            im = Image.open(io.BytesIO(viva)).convert("RGB")
            self.preview = np.asarray(im, dtype=np.float32) / 255.0
            self.preview_neg = None
            self.base_lineare = None
            self.ultimo_raw = None
            self.preview_ridotta = True
            self._ultima_rapida = None
            self._aperto = None
            self._geometria_aperta = None
            self.job.anteprime += 1
            stato = f.get("stato") or ""
            messaggio = ("anteprima della scansione: fotogramma non elaborato"
                         if stato in ("da_elaborare", "errore")
                         else "anteprima della scansione: elaborazione in corso")
            return {"mostrato": True, "numero": f["numero"], "messaggio": messaggio,
                    "anteprima_viva": True}
        from .consegne import apri_immagine
        im = apri_immagine(percorso).convert("RGB")
        im.thumbnail((1600, 1600))
        self.preview = np.asarray(im, dtype=np.float32) / 255.0
        self.preview_neg = None
        self.base_lineare = None
        self.ultimo_raw = None          # i cursori non devono toccare un altro grezzo
        self.preview_ridotta = True
        self._ultima_rapida = None      # l'elaborazione in corso non lo copre
        self._aperto = None
        self._geometria_aperta = None
        self.job.anteprime += 1
        return {"mostrato": True, "numero": f["numero"]}

    def invia_campione(self, fotogramma_id: int) -> str:
        """Un fotogramma di PROVA (mai di un cliente) a chi segue la macchina,
        con il collegamento remoto: il grezzo ridotto a meta' lato (npz),
        il file esportato ridotto, le correzioni del rullo e la calibrazione
        in breve. Serve a capire colore ed esposizione senza la chiavetta.
        Lo chiede l'operatore, fotogramma per fotogramma."""
        import zipfile
        f, rullo, ordine = self._fotogramma_e_rullo(fotogramma_id)
        raw_path = f.get("file_raw") or ""
        if not raw_path or not os.path.exists(raw_path):
            raise ScannerError("il grezzo di questo fotogramma non c'e'")
        try:
            import remoto
        except ImportError:
            raise ScannerError("collegamento remoto non installato")
        cfg = remoto.carica()
        if cfg is None or not cfg.get("token"):
            raise ScannerError("collegamento remoto non configurato")
        raw = RawScan.load(raw_path)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            piani = io.BytesIO()
            np.savez_compressed(piani, **dict(
                ("%s_%d" % (c, k), np.ascontiguousarray(p[::2, ::2]))
                for c, pile in raw.planes.items() for k, p in enumerate(pile)))
            z.writestr("grezzo_meta_lato.npz", piani.getvalue())
            z.writestr("meta.json", json.dumps(
                dict(vars(raw.meta), numero=f["numero"], profilo=rullo.profilo,
                     dx=getattr(rullo, "dx", ""), sviluppo=getattr(rullo, "sviluppo", 0),
                     correzione=self._correzioni_salvate(rullo.id).get(str(f["numero"])),
                     opzioni={k: v for k, v in vars(self.options).items()
                              if isinstance(v, (int, float, str, bool))}),
                default=str, indent=1))
            if self.calib is not None:
                z.writestr("calibrazione.json", json.dumps(
                    {"nota": self.calib.note,
                     "flat": dict((c, [float(np.percentile(v, q)) for q in (1, 50, 99)])
                                  for c, v in self.calib.flat.items()),
                     "dark": dict((c, float(np.mean(v))) for c, v in self.calib.dark.items())},
                    indent=1))
            uscita = f.get("file_out") or ""
            if uscita and os.path.exists(uscita):
                from .consegne import jpeg_per_invio
                z.writestr("esportato.jpg", jpeg_per_invio(uscita, 1600))
        nome = "campione-%s-r%d-f%02d.zip" % (time.strftime("%Y%m%d-%H%M%S"),
                                             rullo.id, int(f["numero"]))
        try:
            dove = remoto.carica_file(cfg, nome, buf.getvalue(),
                                      "campione: rullo %d, fotogramma %d" % (rullo.id, f["numero"]))
        except Exception as exc:                        # noqa: BLE001
            raise ScannerError("campione non inviato: %s" % remoto.spiega(exc))
        self.log("Fotogramma %d inviato per l'analisi: %s" % (f["numero"], dove))
        return dove

    def salva_fotogramma(self) -> None:
        """Riesporta il fotogramma aperto con le regolazioni correnti."""
        if not self._aperto:
            raise ValueError("nessun fotogramma aperto: toccarne uno nella "
                             "striscia")
        f, rullo, ordine = self._fotogramma_e_rullo(self._aperto)

        def job():
            self.progress("Salvataggio del fotogramma %d" % f["numero"], 0.2)
            raw = RawScan.load(f["file_raw"])
            dest = self.archivio.cartella_ordine(self.cfg.out_dir, ordine.id)
            opts = self._opzioni_fotogramma(
                self._opzioni_rullo(rullo),
                self._correzioni_salvate(rullo.id).get(str(f["numero"])))
            # la geometria del fotogramma aperto, con rotazione e canali
            # spostati a mano: e' qui che diventano il file
            if self._geometria_aperta:
                opts = dataclasses.replace(opts, geometria=dict(self._geometria_aperta))
            else:
                opts = self._con_geometria(opts, rullo.id, int(f["numero"]))
            res = self._esporta(raw, opts, dest,
                                self._nome_file(f, ordine, rullo), rullo.id,
                                int(f["numero"]), f.get("numero_bordo") or "",
                                f["file_raw"], (ordine, rullo))
            if res.image is not None:
                self.preview = res.image
            if res.base is not None:
                self.base_lineare = res.base
                self._canali_in_base = {}
            if res.negativo is not None:
                self.preview_neg = res.negativo
            if res.geometria:
                self._geometria_aperta = dict(res.geometria)
            self._cache_prov.clear()
            self.log("Fotogramma %d salvato con le nuove regolazioni."
                     % f["numero"])
        self._avvia("salvataggio", job)

    def applica_rullo(self, rullo_id: Optional[int] = None,
                      solo_mancanti: bool = False) -> None:
        """Le regolazioni correnti su tutti i fotogrammi di un rullo: si
        rielaborano dai grezzi e si riesportano, uno dopo l'altro. I vuoti
        restano vuoti; quelli senza grezzo si saltano e lo si dice.
        solo_mancanti: solo quelli non elaborati (elaborazione fermata con
        Chiusura, o andati in errore): "Completa l'elaborazione"."""
        if rullo_id is None:
            if not self._aperto:
                raise ValueError("nessun rullo scelto")
            rullo_id = int(self._fotogramma_e_rullo(self._aperto)[0]["rullo_id"])
        rullo = self.archivio.rullo(int(rullo_id))
        if rullo is None:
            raise ValueError("rullo inesistente")
        ordine = self.archivio.ordine(rullo.ordine_id)

        def job():
            dest = self.archivio.cartella_ordine(self.cfg.out_dir, ordine.id)
            opts = self._opzioni_rullo(rullo)
            correzioni = self._correzioni_salvate(rullo.id)
            fotogrammi = [f for f in self.archivio.fotogrammi(rullo.id)
                          if f.get("stato") != "vuoto"
                          and (not solo_mancanti
                               or f.get("stato") in ("da_elaborare", "errore"))]
            con_grezzo = [f for f in fotogrammi
                          if f.get("file_raw") and os.path.exists(f["file_raw"])]
            senza = len(fotogrammi) - len(con_grezzo)

            def rifai(f):
                raw_path = f["file_raw"]
                try:
                    # pigro: a un elaboratore va il file, i piani li legge
                    # la macchina solo se lo deve fare lei
                    raw = RawScan.load(raw_path, pigro=True)
                    self._esporta(raw, self._con_geometria(self._opzioni_fotogramma(
                                      opts, correzioni.get(str(f["numero"]))),
                                      rullo.id, int(f["numero"])),
                                  dest, self._nome_file(f, ordine, rullo),
                                  rullo.id, int(f["numero"]),
                                  f.get("numero_bordo") or "", raw_path,
                                  (ordine, rullo))
                    return True
                except Exception as exc:                # noqa: BLE001
                    self.job.errors.append("fotogramma %d: %s" % (f["numero"], exc))
                    self.log("Fotogramma %d non rielaborato: %s" % (f["numero"], exc))
                    return False

            # con gli elaboratori della rete, tanti insieme quanti sono i
            # loro processi (un Threadripper: 16); senza, uno alla volta
            insieme = self._quanti_insieme()
            if insieme > 1:
                self.log("Rielaborazione in rete: %d fotogrammi alla volta." % insieme)
            fatti = self._in_parallelo(
                con_grezzo, rifai, insieme,
                lambda f, k, n: "Fotogramma %d (%d di %d)" % (f["numero"], k, n))
            if self._stop.is_set():
                self.log("Applica a tutto il rullo fermato dopo %d fotogrammi." % fatti)
            self._cache_prov.clear()
            self.log("Rullo %s rielaborato: %d fotogrammi%s." % (
                rullo.etichetta, fatti,
                (", %d senza grezzo (saltati)" % senza) if senza else ""))
            self.job.message = "Rullo rielaborato: %d fotogrammi" % fatti
        self._avvia("rielaborazione", job, total=0)

    # --- positivi per altri programmi (Archivio, Scarica i negativi) ------

    def prepara_positivi(self, ordine_id: int, formato: str, rullo_id: int = 0) -> None:
        """I positivi invertiti di un ordine, in uno zip, come lavoro in
        sottofondo: ogni fotogramma si rielabora dal grezzo con le opzioni
        e la geometria del suo rullo (come Salva fotogramma), in rete se c'e'
        un elaboratore, e si salva la base lineare (prima di nitidezza,
        riduzione della grana e curva per lo schermo): in DNG cosi' com'e',
        in TIFF codificata sRGB. La foto finale e i grezzi non si toccano."""
        import zipfile
        from .consegne import FORMATI_POSITIVO
        if formato not in FORMATI_POSITIVO:
            raise ValueError("formato sconosciuto: %s" % formato)
        ordine = self.archivio.ordine(int(ordine_id))
        if ordine is None:
            raise ValueError("ordine inesistente")
        _, ext, tipo = FORMATI_POSITIVO[formato]
        cartella = os.path.join(self.cfg.path("archivio"), "_consegne")
        os.makedirs(cartella, exist_ok=True)
        from .consegne import suffisso_rullo
        dest = os.path.join(cartella, "%s%s-%s.zip" % (
            ordine.codice, suffisso_rullo(self.archivio, rullo_id), formato))
        self._positivi = {"pronto": "", "errore": "", "ordine": int(ordine_id)}

        def job():
            from .invert import to_srgb
            elenco = []
            for rullo in self.archivio.rulli(ordine.id):
                if rullo_id and rullo.id != int(rullo_id):
                    continue            # consegna di un rullo solo
                for f in self.archivio.fotogrammi(rullo.id):
                    npz = f.get("file_raw") or ""
                    if npz and os.path.isfile(npz) and f.get("stato") != "vuoto":
                        elenco.append((rullo, f))
            if not elenco:
                self._positivi["errore"] = "questo ordine non ha grezzi sul disco"
                return
            scrivi_zip = threading.Lock()
            with zipfile.ZipFile(dest + ".tmp", "w", zipfile.ZIP_STORED, allowZip64=True) as z:
                def positivo(voce):
                    rullo, f = voce
                    try:
                        raw = RawScan.load(f["file_raw"], pigro=True)
                        opts = self._con_geometria(self._opzioni_fotogramma(
                            self._opzioni_rullo(rullo),
                            self._correzioni_salvate(rullo.id).get(str(f["numero"]))),
                            rullo.id, int(f["numero"]))
                        opts.negativo_lineare = False
                        res = self._processa(raw, opts, raw_path=f["file_raw"])
                        if res.base is None:
                            raise RuntimeError("base non disponibile")
                        base = np.clip(np.asarray(res.base, dtype=np.float32), 0.0, 1.0)
                        res = None
                        nome = self._nome_file(f, ordine, rullo) + "_positivo" + ext
                        tmp = os.path.join(cartella, nome)
                        meta = self._meta_file(ordine, rullo, int(f["numero"]),
                                               f.get("numero_bordo") or "", raw, opts)
                        if tipo == "dng":
                            export_negativo(base, tmp, "dng", meta=meta)
                        else:
                            export(to_srgb(base), tmp, "tiff16", meta=meta)
                        del base
                        with scrivi_zip:
                            z.write(tmp, os.path.join(ordine.codice, nome))
                        os.remove(tmp)
                        return True
                    except Exception as exc:            # noqa: BLE001
                        self.log("Positivo del fotogramma %d non fatto: %s" % (f["numero"], exc))
                        return False
                # la base di ogni positivo (~35 MB) torna sulla macchina: al
                # massimo 4 alla volta, per la memoria del PC a 32 bit
                fatti = self._in_parallelo(
                    elenco, positivo, self._quanti_insieme(tetto=4),
                    lambda v, k, n: "Positivo %d di %d (fotogramma %d)" % (k, n, v[1]["numero"]))
            os.replace(dest + ".tmp", dest)
            self._positivi["pronto"] = dest
            self.log("Positivi pronti: %d fotogrammi (%s)." % (fatti, os.path.basename(dest)))
            self.job.message = "Positivi pronti: lo scarico parte dall'archivio"
        self._avvia("consegna", job)

    def stato_positivi(self) -> Dict[str, object]:
        p = getattr(self, "_positivi", None) or {}
        lavoro = self.job.running and self.job.kind == "consegna"
        return {"in_corso": bool(lavoro), "progresso": round(float(self.job.progress or 0), 3),
                "messaggio": self.job.message if lavoro else "",
                "pronto": os.path.basename(p.get("pronto") or "") if not lavoro else "",
                "errore": p.get("errore") or ""}

    def file_positivi(self) -> str:
        p = getattr(self, "_positivi", None) or {}
        return p.get("pronto") or ""

    def imposta_archivio_rete(self, valori: Dict[str, object]) -> Dict[str, object]:
        c = self.cfg
        if "attivo" in valori:
            c.rete_attivo = bool(valori["attivo"])
        if "percorso" in valori:
            c.rete_percorso = str(valori["percorso"] or "").strip().rstrip("\\/")
        if "utente" in valori:
            c.rete_utente = str(valori["utente"] or "").strip()
        if valori.get("password"):
            c.rete_password = str(valori["password"])
        if valori.get("togli_password"):
            c.rete_password = ""
        self.archivio_rete._connesso = False
        return self.archivio_rete.stato()

    def vota(self, fotogramma_id: int, voto: int) -> Dict[str, object]:
        """Da stampare (1), scartato (-1), niente (0)."""
        self._fotogramma_e_rullo(fotogramma_id)
        self.archivio.fotogramma_voto(int(fotogramma_id), int(voto))
        return {"ok": True, "voto": voto}

    def _esposizione_fissa(self, preset) -> Optional[Dict[str, float]]:
        """Con l'esposizione automatica spenta: x1 su tutti i canali, senza
        misura. Accesa: None, e decide la misura per canale."""
        if self.cfg.esposizione_auto and self.cfg.acquisizione != "originale":
            return None
        # con la capture dell'originale i tempi li decide l'SDK
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
            # Se lo scanner si e' caricato oggi (e dopo l'ultima emergenza), il
            # GateControl ha gia' azzerato i motori al collegamento: rifarlo
            # costa un altro caricamento dell'SDK (40-90 s) e non aggiunge
            # niente. cfg.azzera_in_calibrazione = "sempre" lo rifa' comunque.
            caricato = backend.azzerato_alle()
            gia_azzerato = bool(
                caricato and self.cfg.azzera_in_calibrazione != "sempre"
                and time.strftime("%Y%m%d", time.localtime(caricato)) == time.strftime("%Y%m%d")
                and caricato > self._emergenza_alle)
            self.progress("Azzeramento dei motori", 0.0)
            try:
                if gia_azzerato:
                    self.log("Motori gia' azzerati al collegamento dello scanner "
                             "(alle %s): non si rifa'." % time.strftime(
                                 "%H:%M", time.localtime(caricato)))
                elif backend.azzera_motori():
                    self.log("Motori azzerati (Gretag Reset).")
                    # dopo il reset l'SDK ricollega lo scanner (~40 s): il
                    # backend aspetta e riprova da solo, qui lo si dice
                    self.progress("Lo scanner si ricollega dopo l'azzeramento "
                                  "(circa 40 s)", 0.01)
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
        opts = self._opzioni_rullo(rullo)

        if isinstance(self.backend, OriginaleBackend):
            return self.ricevi_da_originale(rullo_id)
        self._serve_calibrazione()

        def job():
            nonlocal preset
            backend = self._require()
            backend.seleziona_formato(rullo.formato)
            backend.esposizione_auto(self.cfg.esposizione_auto)
            backend.capture_fallite = 0     # acquire.scan_frame: si riprova a ogni rullo
            ice_sdk = False
            if rullo.ice and self.cfg.acquisizione == "originale":
                # il Digital ICE dell'SDK lo accende e lo spegne la capture,
                # fotogramma per fotogramma (sdk.cattura)
                self.log("Digital ICE dell'SDK nella capture di ogni fotogramma.")
            elif rullo.ice and "IR" not in getattr(backend, "canali_disponibili", ["IR"]):
                # Digital ICE nostro: la pulizia dal canale IR (irclean).
                # L'ASF dell'SDK su scan_raw (prima acceso qui, per prova)
                # non si usa piu': il Digital ICE dell'SDK lavora nella
                # capture (acquisizione "originale").
                self.log("Digital ICE: questa macchina non da' il canale IR, "
                         "la pulizia IR resta spenta.")
            mancanti = [c for c in preset.channels
                        if c not in getattr(backend, "canali_disponibili", preset.channels)]
            if mancanti:
                self.log("Canali non disponibili su questa macchina, saltati: %s"
                         % ", ".join(mancanti))

            # Scansiona rullo fa anche Carica: guide al formato, carrello
            # pronto, e si parte da soli appena il carrello sente la
            # pellicola. Gia' dentro (Carica a mano, poi la striscia): si
            # parte subito. None = l'agente non lo sa dire: come prima.
            if (hasattr(backend, "pellicola_sdk")
                    and backend.pellicola_sdk() is False):
                if not self._attendi_pellicola(backend, rullo.formato):
                    return

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
            elaborati = {"ok": 0}

            # Statistiche di rullo (rullo.py): base ed equilibrio dei grigi
            # su tutti i fotogrammi, partendo da quel che si sa della
            # pellicola (codice DX) se la macchina lo dice. Un gruppo per
            # pellicola: se fra le strisce ne entra una di un'altra
            # pellicola, ha profilo, equilibrio ed esposizione suoi.
            memoria = self._memoria_pellicole()
            gruppi: Dict[str, dict] = {}

            def gruppo(chiave):
                g = gruppi.get(chiave)
                if g is None:
                    extra = self._extra_pellicola(rullo, chiave)
                    nota = memoria.per(chiave)
                    g = gruppi[chiave] = {
                        "stat": StatisticheRullo(nota, extra["scala_densita"]),
                        "extra": extra, "chiave": chiave}
                    self.log("Pellicola: %s%s%s." % (
                        extra["nome_pellicola"] or chiave or "codice DX non letto",
                        (", grana attesa %s" % extra["nome_grana"])
                        if extra["nome_grana"] else "",
                        (", conosciuta da %d rull%s" % (
                            nota.get("rulli", 1),
                            "o" if nota.get("rulli", 1) == 1 else "i"))
                        if nota else ""))
                    for n in extra["note"]:
                        self.log("Pellicola: " + n)
                self.job.pellicola = (g["extra"]["nome_pellicola"]
                                      or g["chiave"] or "")
                return g

            chiave = self._leggi_dx(backend)
            if chiave:
                self.archivio.rullo_dx(rullo_id, chiave)
                rullo.dx = chiave
                cambi = self._profilo_da_dx(ordine, rullo, chiave)
                if cambi.get("argento"):
                    preset = dataclasses.replace(preset, use_ir=False)
                    if ice_sdk:
                        try:
                            backend.digital_ice(False)
                            self.log("Digital ICE (ASF) spento: B/N all'argento.")
                        except Emergenza:
                            raise
                        except Exception as exc:        # noqa: BLE001
                            self.log("Digital ICE (ASF) non spento: %s" % exc)
                        ice_sdk = False
                if cambi.get("preset") and cambi["preset"] in PRESETS:
                    preset = dataclasses.replace(
                        PRESETS[cambi["preset"]],
                        use_ir=preset.use_ir and PRESETS[cambi["preset"]].use_ir
                        and not cambi.get("argento"))
                    self.log("Preset cambiato in %s per questa pellicola."
                             % preset.name)
            corrente = {"g": gruppo(chiave)}

            def nuova_striscia(n: int) -> bool:
                """Striscia n inserita: se il DX dice un'altra pellicola, la
                si elabora a parte e lo si dice. True se e' cambiata."""
                ch = self._leggi_dx(backend)
                prima = corrente["g"]
                if not ch or ch == prima["chiave"]:
                    return False
                g = gruppo(ch)
                corrente["g"] = g
                nome = g["extra"]["nome_pellicola"] or ch
                if not prima["chiave"]:
                    # prima il DX non si era letto: e' probabilmente la
                    # stessa pellicola, non un cambio
                    self.log("Striscia %d: pellicola riconosciuta (%s)." % (n, nome))
                    return True
                avviso = ("striscia %d di un'altra pellicola (%s, prima %s): "
                          "elaborata a parte, con profilo, equilibrio ed "
                          "esposizione suoi" % (
                              n, nome, prima["extra"]["nome_pellicola"] or prima["chiave"]))
                self.job.errors.append(avviso)
                self.log("Attenzione: " + avviso + ".")
                self.archivio.ordine_aggiungi_nota(
                    ordine.id, '"%s", striscia %d: %s (diversa dal resto del '
                    'rullo)' % (rullo.etichetta, n, nome))
                if g["extra"]["argento"] and ice_sdk:
                    try:
                        backend.digital_ice(False)
                        self.log("Digital ICE (ASF) spento: B/N all'argento.")
                    except Emergenza:
                        raise
                    except Exception as exc:            # noqa: BLE001
                        self.log("Digital ICE (ASF) non spento: %s" % exc)
                if preset.monochrome and not g["extra"]["tipo"].startswith("bn"):
                    self.log("Attenzione: il preset del rullo legge un canale solo, "
                             "la striscia %d e' a colori: esce in bianco e nero." % n)
                return True

            def elabora(compito):
                """Un fotogramma letto: elaborazione completa, file, registro.
                Gira nel thread di elaborazione; un errore qui perde il
                fotogramma (il grezzo resta), non il rullo."""
                raw, nome, numero, bordo, vuoto, g = compito
                raw_path = ""
                try:
                    if isinstance(raw, str):
                        # messo su disco dalla lettura con la memoria piena
                        # (su_disco): e' gia' il grezzo definitivo
                        raw_path = raw
                        raw = RawScan.load(raw_path, pigro=True)
                    # Il grezzo si salva qui e non nel ciclo di lettura: la
                    # compressione di ~35 MB costa secondi sul PC della
                    # macchina, e la pellicola intanto va avanti.
                    if not raw_path and (self.cfg.keep_raw or vuoto
                                         or elab.solo_grezzi.is_set()):
                        raw_path = raw.save(self.cfg.path("raw"), nome,
                                            compresso=not elab.solo_grezzi.is_set())
                    if vuoto:
                        self.archivio.fotogramma_registra(
                            rullo_id, numero, file_raw=raw_path, stato="vuoto",
                            avvisi=vuoto, numero_bordo=bordo)
                        return
                    if elab.solo_grezzi.is_set():
                        if not raw_path:
                            raw_path = raw.save(self.cfg.path("raw"), nome)
                        self._salva_anteprima_viva((rullo_id, numero), raw_path)
                        self.archivio.fotogramma_registra(
                            rullo_id, numero, file_raw=raw_path, stato="da_elaborare",
                            avvisi="non elaborato: elaborazione fermata", numero_bordo=bordo)
                        non_elaborati.append(numero)
                        self.archivio_rete.accoda(rullo_id)
                        return
                    # L'equilibrio del rullo ha bisogno di qualche fotogramma:
                    # il primo aspetta che ce ne siano 4 (o che la striscia
                    # sia finita). La lettura intanto va avanti.
                    scadenza = time.time() + 180.0
                    while (len(g["stat"]) < 4 and not elab.lettura_finita.is_set()
                           and time.time() < scadenza):
                        time.sleep(0.2)
                    res = self._esporta(raw, self._opzioni_fotogramma(
                                            opts, dict(g["stat"].correzione(numero),
                                                       **g["extra"])),
                                        dest, nome, rullo_id,
                                        numero, bordo, raw_path,
                                        (ordine, rullo),
                                        ferma=elab.solo_grezzi.is_set)
                    elaborati["ok"] += 1
                    # nel visore la versione finita, se e' ancora l'ultimo
                    # fotogramma letto: uno piu' nuovo non si copre
                    if self._ultima_rapida == (rullo_id, numero):
                        self.ultimo_raw = raw
                        self.preview = res.image
                        self.base_lineare = res.base
                        self.preview_ridotta = False
                        self.preview_neg = res.negativo
                    for w in res.warnings:
                        self.log("Fotogramma %d: %s" % (numero, w))
                except _ElaborazioneFermata:
                    if not raw_path:
                        raw_path = raw.save(self.cfg.path("raw"), nome, compresso=False)
                    self._salva_anteprima_viva((rullo_id, numero), raw_path)
                    self.archivio.fotogramma_registra(
                        rullo_id, numero, file_raw=raw_path, stato="da_elaborare",
                        avvisi="non elaborato: elaborazione fermata", numero_bordo=bordo)
                    non_elaborati.append(numero)
                except Exception as exc:                # noqa: BLE001
                    self.job.errors.append("fotogramma %d: %s" % (numero, exc))
                    self.log("Fotogramma %d non elaborato: %s" % (numero, exc))
                    if vuoto:
                        return
                    self._salva_anteprima_viva((rullo_id, numero), raw_path)
                    self.archivio.fotogramma_registra(
                        rullo_id, numero, file_raw=raw_path, stato="errore",
                        avvisi=str(exc), numero_bordo=bordo)
                finally:
                    self._provini_rapidi.pop((rullo_id, numero), None)
                    self._anteprime_vive.pop((rullo_id, numero), None)
                    self.job.in_elaborazione = max(0, self.job.in_elaborazione - 1)
                    # a lettura finita il messaggio dice quanti ne restano
                    if elab.lettura_finita.is_set() and self.job.in_elaborazione:
                        n = self.job.in_elaborazione
                        self.job.message = (
                            ("Elaborazione fermata: salvo i grezzi rimasti (%d)" % n)
                            if elab.solo_grezzi.is_set() else
                            "Elaborazione degli ultimi %d fotogramm%s (Ferma elaborazione "
                            "per interromperla)" % (n, "o" if n == 1 else "i"))
                    self.job.anteprime += 1

            non_elaborati: List[int] = []
            elab = self._elab = _ElaborazioneInFondo(
                elabora, int(self.cfg.memoria_elaborazione_mb) * 1024 * 1024,
                aiutanti=(int(self.cfg.elaboratori_massimo)
                          if self.cfg.elaboratori_rete else 0),
                aiuto=self._aiuto_rete)
            letti = {"n": 0}
            t_rullo = {"inizio": 0.0}
            # L'esposizione per canale si misura UNA volta per rullo, non a
            # ogni fotogramma: dipende dall'emulsione e dalla lampada, che nel
            # corso di un rullo non cambiano. Misurarla ogni volta farebbe
            # oscillare il colore da un fotogramma all'altro.
            coda = None      # fotogrammi ancora davanti alla finestra
            gains = self._esposizione_fissa(preset)
            # La misura si fa sul primo fotogramma vero, non qui: appena
            # caricata, in finestra c'e' quasi sempre la coda annerita, e la
            # misura fatta li' chiedeva x12 a tutti i canali (riserva, 27/10).
            da_misurare = (gains is None and preset.auto_exposure
                           and preset.channel_exposure is None)
            misure_opache = 0
            # la prima lettura unica dopo la misura si confronta con la misura
            verifica_unica = True
            self._livelli_misura = {}

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
            # un autofocus non riuscito da quando il carrello si e' mosso
            # l'ultima volta: dopo, il "fotogramma successivo" puo' essere
            # rifiutato (ERR_MOVE, riserva 7/11/2026), e si riprova
            fuoco_fallito = False
            # la prima posizione dopo l'inserimento di una striscia
            prima_posizione = True
            # nitidezza migliore vista nel rullo dopo un fuoco (_verifica_fuoco)
            rif_fuoco = [0.0]
            self._metodo_fuoco = None
            i = 0
            giri = 0
            while i < limite and giri < limite + 50:
                giri += 1
                conta = True
                fuoco_qui = False
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
                        if nuova_striscia(strisce):
                            # altra pellicola, altra esposizione: si rimisura
                            (gains, da_misurare, misure_opache,
                             verifica_unica) = self._riparti_esposizione(preset)
                        fuoco_da_fare = True
                        prima_posizione = True
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
                if not t_rullo["inizio"]:
                    t_rullo["inizio"] = time.time()
                tempi = {}
                base = i / float(max(limite, 1))
                # In automatico non si scrive "di 72": sarebbe un numero
                # inventato. Si scrive quello che si sa, cioe' a che fotogramma
                # siamo arrivati.
                self.progress(
                    ("Fotogramma %d" % (i + 1)) if automatico
                    else ("Fotogramma %d di %d" % (i + 1, rullo.attesi)),
                    min(base, 0.97))

                nome = "%s_%s_%03d" % (ordine.codice, rullo.etichetta.replace(" ", "-"), i + 1)
                # Esposizione per canale, sul primo fotogramma che porta luce.
                # Se la finestra e' su una zona opaca la misura non vale: il
                # fotogramma si legge a x1, senza autofocus (su una zona
                # senza immagine fallisce comunque). Se era la coda lo scarta
                # fotogramma_vuoto, se era un fotogramma denso resta salvato:
                # saltarlo senza leggerlo poteva perderne uno vero.
                opaca = False
                if da_misurare:
                    gains = self._misura_esposizione(
                        backend, preset, ultima=misure_opache >= 2)
                    if gains is None:
                        opaca = True
                        misure_opache += 1
                    else:
                        da_misurare = False

                # Autofocus a inizio striscia, col pressore giu' come per la
                # lettura: la pellicola deve stare dove sara' letta. Mai
                # sulla prima posizione: e' quasi sempre la coda, li' il
                # fuoco non si trova (ERR_FOCUS_FAILED), e dopo un fuoco
                # non riuscito il carrello rifiutava di avanzare: striscia
                # chiusa con 0 fotogrammi (riserva, 7/11/2026, 15:24 e
                # 16:47). Se la prima posizione e' gia' un fotogramma, il
                # fuoco si fa dopo averla letta, e la si rilegge.
                if (fuoco_da_fare and not opaca and not prima_posizione
                        and self.cfg.autofocus != "spento"):
                    with self._sotto_pressore(backend):
                        if self._autofocus(backend):
                            fuoco_qui = True
                        else:
                            fuoco_fallito = True

                # il numero stampato sul bordo, se la macchina lo sa dire:
                # mai bloccante, e se manca il rullo va avanti lo stesso
                try:
                    bordo = backend.numero_bordo() or ""
                except Emergenza:
                    raise
                except Exception:                       # noqa: BLE001
                    bordo = ""
                try:
                    t_lettura = time.time()
                    with self._sotto_pressore(backend):
                        raw = scan_frame(backend, self.cfg, preset, fmt,
                                         self.calib, frame_index=i,
                                         channel_exposure=gains if not opaca
                                         else dict((c, 1.0) for c in preset.channels),
                                         annulla=self.annullato)
                    tempi["lettura"] = time.time() - t_lettura
                    nota = getattr(backend, "log_disposizione", None)
                    if nota:
                        self.log(nota)
                        backend.log_disposizione = None
                    if (verifica_unica and not opaca and gains
                            and getattr(backend, "_lettura_unica", None) is True):
                        verifica_unica = False
                        if not self._lettura_unica_torna(raw):
                            backend._lettura_unica = False
                            with self._sotto_pressore(backend):
                                raw = scan_frame(backend, self.cfg, preset, fmt,
                                                 self.calib, frame_index=i,
                                                 channel_exposure=gains,
                                                 annulla=self.annullato)

                    vuoto, escursione = fotogramma_vuoto(
                        raw, self.cfg.soglia_fotogramma_vuoto_d)
                    era_la_prima = prima_posizione
                    prima_posizione = False
                    primo = inizio_striscia
                    inizio_striscia = False
                    if vuoto and (primo or opaca):
                        # appena caricata, la finestra sta spesso sulla coda
                        # della striscia: non e' un fotogramma (e il fuoco
                        # si rifa' sul prossimo). La coda annerita puo'
                        # occupare piu' di una posizione: finche' non si e'
                        # visto un fotogramma con luce (opaca), un vuoto e'
                        # ancora coda e non prende un numero.
                        inizio_striscia = True
                        raise _CodaIniziale()
                    if (era_la_prima and fuoco_da_fare and not opaca and not vuoto
                            and self.cfg.autofocus != "spento"):
                        # la striscia comincia gia' su un fotogramma: fuoco
                        # adesso, e se riesce lo si rilegge a fuoco
                        with self._sotto_pressore(backend):
                            if self._autofocus(backend):
                                fuoco_qui = True
                                raw = scan_frame(backend, self.cfg, preset, fmt,
                                                 self.calib, frame_index=i,
                                                 channel_exposure=gains,
                                                 annulla=self.annullato)
                            else:
                                fuoco_fallito = True
                    if fuoco_qui and not opaca and not vuoto:
                        def rileggi(i=i):
                            with self._sotto_pressore(backend):
                                return scan_frame(backend, self.cfg, preset, fmt,
                                                  self.calib, frame_index=i,
                                                  channel_exposure=gains,
                                                  annulla=self.annullato)
                        raw = self._verifica_fuoco(backend, raw, rif_fuoco, rileggi)
                    elif not vuoto and not opaca:
                        # la nitidezza di ogni fotogramma nel registro: e' da
                        # li' che si vede se il fuoco tiene lungo la striscia
                        try:
                            _registro.info("fotogramma %d: nitidezza %.1f", i + 1,
                                           self._nitidezza(raw))
                        except Exception:               # noqa: BLE001
                            pass
                    if not opaca:
                        fuoco_da_fare = False
                    if vuoto:
                        # Non esposto (o tutto opaco): niente export, ma il
                        # grezzo si tiene sempre, cosi' se la soglia ha
                        # sbagliato il fotogramma si rielabora senza
                        # rimettere la pellicola. Il numero resta quello
                        # della striscia, come lo conta il cliente.
                        elab.metti((raw, nome, i + 1, bordo,
                                    "non esposto: escursione %.2f D" % escursione,
                                    corrente["g"]),
                                   _peso(raw), lambda: None)
                        self.job.in_elaborazione += 1
                        vuoti += 1
                        self.log("Fotogramma %d vuoto (escursione %.2f D): "
                                 "saltato, grezzo conservato." % (i + 1, escursione))
                        raise _FotogrammaVuoto()

                    # Subito l'anteprima ridotta, e il fotogramma in striscia
                    # come "in elaborazione"; quella vera (e il salvataggio
                    # del grezzo) la fa il thread di elaborazione, mentre la
                    # macchina passa al successivo.
                    if not opaca:
                        # letto a x1 perche' opaco: scala diversa dagli altri,
                        # non entra nelle statistiche del rullo
                        corrente["g"]["stat"].aggiungi(i + 1, raw)
                    self._mostra_rapida(raw, self._opzioni_fotogramma(
                        opts, dict(corrente["g"]["stat"].correzione(i + 1),
                                   **corrente["g"]["extra"])), (rullo_id, i + 1))
                    self.archivio.fotogramma_registra(
                        rullo_id, i + 1, stato="elaborazione",
                        numero_bordo=bordo)
                    self.job.in_elaborazione += 1
                    letti["n"] += 1
                    numero = i + 1

                    def in_attesa(numero=numero):
                        self.job.message = ("Fotogramma %d letto: memoria "
                                            "piena, aspetto l'elaborazione "
                                            "dei precedenti" % numero)
                    def su_disco(compito, numero=numero):
                        r, n, num, b, v, gg = compito
                        percorso = r.save(self.cfg.path("raw"), n, compresso=False)
                        _registro.info("fotogramma %d: memoria piena, grezzo sul disco, "
                                       "la lettura continua", num)
                        return (percorso, n, num, b, v, gg)
                    elab.metti((raw, nome, numero, bordo, "", corrente["g"]), _peso(raw),
                               in_attesa, su_disco=su_disco)
                    del raw

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
                        t_trasporto = time.time()
                        try:
                            backend.prossimo_fotogramma(fmt)
                        except FinePellicola:
                            if not fuoco_fallito:
                                raise
                            # dopo un fuoco non riuscito il primo avanzamento
                            # puo' essere rifiutato: una seconda prova prima
                            # di dire che la striscia e' finita
                            fuoco_fallito = False
                            _registro.warning("avanzamento rifiutato dopo un fuoco non "
                                              "riuscito: riprovo")
                            self.log("Il carrello non e' avanzato dopo il fuoco non "
                                     "riuscito: riprovo.")
                            time.sleep(2.0)
                            backend.prossimo_fotogramma(fmt)
                        fuoco_fallito = False
                        tempi["trasporto"] = time.time() - t_trasporto
                        if "lettura" in tempi:
                            self.log("Fotogramma %d: lettura %.1f s, trasporto "
                                     "%.1f s." % (i + 1, tempi["lettura"],
                                                  tempi["trasporto"]))
                except FinePellicola:
                    self.log("Fine della striscia %d: il carrello non trova "
                             "altri fotogrammi dopo il %d."
                             % (strisce, i + (1 if conta else 0)))
                    continua, espulsa = self._prossima_striscia(
                        backend, rullo.formato)
                    if not continua:
                        break
                    strisce += 1
                    if nuova_striscia(strisce):
                        # altra pellicola, altra esposizione: si rimisura
                        (gains, da_misurare, misure_opache,
                         verifica_unica) = self._riparti_esposizione(preset)
                    inizio_striscia = True
                    prima_posizione = True
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
                        if nuova_striscia(strisce):
                            # altra pellicola, altra esposizione: si rimisura
                            (gains, da_misurare, misure_opache,
                             verifica_unica) = self._riparti_esposizione(preset)
                        inizio_striscia = True
                        fuoco_da_fare = True
                        prima_posizione = True
                if conta:
                    i += 1

            elab.lettura_finita.set()

            # Prima la macchina: ASF spento e pellicola fuori subito, senza
            # aspettare l'elaborazione, che non la riguarda.
            if ice_sdk:
                try:
                    backend.digital_ice(False)
                except Exception as exc:                # noqa: BLE001
                    self.log("Digital ICE (ASF) non spento: %s" % exc)
            if (self.cfg.auto_eject and letti["n"] > 0 and not espulsa
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
            if t_rullo["inizio"]:
                durata = time.time() - t_rullo["inizio"]
                self.log("Lettura del rullo: %d fotogrammi in %.0f s (%.1f s "
                         "l'uno)." % (letti["n"], durata,
                                      durata / max(letti["n"], 1)))

            # gli ultimi fotogrammi letti possono essere ancora in
            # elaborazione: il rullo si chiude (e si riordina) quando ci sono
            # tutti i file
            self._chiudi_elaborazione()
            fatti = elaborati["ok"]
            self._chiudi_statistiche(rullo_id, gruppi, memoria)
            if non_elaborati:
                avviso = ("%d fotogramm%s NON elaborat%s (%s): i grezzi sono salvati, "
                          "il tasto Completa (sopra la striscia) li elabora"
                          % (len(non_elaborati), "o" if len(non_elaborati) == 1 else "i",
                             "o" if len(non_elaborati) == 1 else "i",
                             ", ".join(str(n) for n in sorted(non_elaborati))))
                self.job.errors.append(avviso)
                self.job.non_elaborati = len(non_elaborati)
                self.log("Attenzione: " + avviso + ".")

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
            if not self._stop.is_set() and stato == "completato":
                self._fine_ordine(ordine.id)

        self._avvia("rullo", job,
                    total=rullo.attesi or FORMATS[rullo.formato].max_fotogrammi)

    # ------------------------------------------------------------------
    # avviso al cliente (WhatsApp)

    def prepara_whatsapp(self, ordine_id: int, cosa: str = "provino",
                         locale: bool = False) -> Dict[str, object]:
        """L'allegato dell'avviso: "provino" (il provino a contatto) o "foto"
        (quelle da stampare, se no tutte, in JPEG). Sul PC della macchina
        (locale) va negli appunti, e si incolla nella chat con Ctrl+V (o da
        solo, cfg.whatsapp_incolla); da un altro dispositivo lo si prende
        dal browser con Condividi."""
        from . import appunti, consegne
        o = self.archivio.ordine(ordine_id)
        if o is None:
            raise ValueError("ordine inesistente")
        cartella = self.archivio.cartella_ordine(self.cfg.out_dir, ordine_id)
        if cosa == "foto":
            file = consegne.copie_per_invio(self.archivio, ordine_id,
                                            os.path.join(cartella, "whatsapp"))
            if not file:
                raise ValueError("nessuna foto esportata per questo ordine")
        else:
            dest = os.path.join(cartella, "provino-%s.jpg" % o.codice)
            consegne.provino_contatto(self.archivio, ordine_id, dest)
            file = [dest]
        esito: Dict[str, object] = {"cosa": cosa, "file": len(file),
                                    "appunti": False, "messaggio": ""}
        if not (locale and self.cfg.whatsapp_appunti and appunti.WINDOWS):
            return esito
        ok = (appunti.copia_file(file) if cosa == "foto"
              else appunti.copia_immagine(file[0]))
        esito["appunti"] = bool(ok)
        quale = "Il provino e'" if cosa != "foto" else "%d foto sono" % len(file)
        if not ok:
            esito["messaggio"] = ("Appunti occupati da un altro programma: "
                                  "l'allegato e' nella cartella dell'ordine.")
        elif self.cfg.whatsapp_incolla:
            appunti.incolla_in_whatsapp(self.cfg.whatsapp_attesa_s, esito=self.log)
            esito["messaggio"] = ("%s negli appunti: si incolla da solo appena "
                                  "la chat e' aperta, poi Invio." % quale)
        else:
            esito["messaggio"] = ("%s negli appunti: nella chat Ctrl+V, poi "
                                  "Invio." % quale)
        return esito

    def _fine_ordine(self, ordine_id: int) -> None:
        """Rullo finito: se era l'ultimo dell'ordine, l'avviso al cliente.
        "proponi": un tasto nell'interfaccia (job.avviso_ordine); "apri": sul
        PC della macchina la chat si apre da sola con il provino negli
        appunti (os.startfile: nessun blocco dei popup)."""
        modo = getattr(self.cfg, "whatsapp_fine_ordine", "proponi")
        if modo not in ("proponi", "apri"):
            return
        try:
            rulli = self.archivio.rulli(ordine_id)
            if not rulli or any(r.stato in ("da_scansionare", "in_corso") for r in rulli):
                return
            self.job.avviso_ordine = ordine_id
            if modo != "apri":
                self.log("Ordine finito: si puo' avvisare il cliente.")
                return
            from . import appunti, consegne
            av = consegne.avviso(self.archivio, ordine_id, "",
                                 self.cfg.whatsapp_prefisso)
            esito = self.prepara_whatsapp(ordine_id, "provino", locale=True)
            link = av["link_app"] if self.cfg.whatsapp_modo == "app" else av["link_web"]
            if appunti.apri(link):
                self.log("Ordine finito: chat WhatsApp aperta%s. %s" % (
                    (" con " + str(av["numero"])) if av["ha_numero"] else "",
                    esito.get("messaggio", "")))
            else:
                self.log("Ordine finito: si puo' avvisare il cliente.")
        except Exception as exc:                        # noqa: BLE001
            self.log("Avviso di fine ordine non preparato: %s" % exc)

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
        opts = self.options
        if self._aperto:
            # fotogramma riaperto dall'archivio: con il profilo, la pellicola
            # e le correzioni del suo rullo, come in apri_fotogramma
            try:
                f, rullo, _ = self._fotogramma_e_rullo(self._aperto)
                opts = self._opzioni_fotogramma(
                    self._opzioni_rullo(rullo),
                    self._correzioni_salvate(rullo.id).get(str(f["numero"])))
                opts.negativo_lineare = False
                if self._geometria_aperta:
                    opts = dataclasses.replace(opts, geometria=dict(self._geometria_aperta))
            except (ValueError, ScannerError):
                opts = self.options
        res = self._processa(self.ultimo_raw, opts)
        self.preview = res.image
        self.preview_neg = res.negativo
        self.base_lineare = res.base
        self._canali_in_base = {}
        if self._aperto and res.geometria:
            self._geometria_aperta = dict(res.geometria)
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
