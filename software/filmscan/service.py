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

import os
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np

from .acquire import (Annullato, Calibration, film_present,
                      measure_channel_exposure, run_calibration, scan_frame)
from .compat import check_environment
from .config import AppConfig, FORMATS, PRESETS
from .hal import FilmSensors, ScannerBackend, ScannerError, make_backend
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

    def as_dict(self) -> Dict[str, object]:
        return {"running": self.running, "kind": self.kind,
                "message": self.message, "progress": self.progress,
                "frame": self.frame, "total": self.total,
                "errors": self.errors[-10:], "log": self.log[-40:]}


class ScanService:

    def __init__(self, cfg: AppConfig, archivio: Optional[Archivio] = None):
        self.cfg = cfg
        self.archivio = archivio or Archivio(
            os.path.join(os.path.dirname(os.path.abspath(cfg.out_dir)) or ".",
                         "ordini.db"))
        self.backend: Optional[ScannerBackend] = None
        self.calib: Optional[Calibration] = None
        self.job = JobState()
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

    # ------------------------------------------------------------------

    def log(self, msg: str) -> None:
        self.job.log.append("%s  %s" % (time.strftime("%H:%M:%S"), msg))

    def progress(self, msg: str, frac: float) -> None:
        self.job.message = msg
        self.job.progress = float(max(0.0, min(1.0, frac)))

    def stato(self) -> Dict[str, object]:
        st = None
        if self.backend is not None:
            try:
                s = self.backend.status()
                st = {"connesso": s.connected, "posizione_mm": round(s.position_mm, 3),
                      "fuoco_mm": round(s.focus_mm, 4), "messaggio": s.message}
            except Exception as exc:                    # noqa: BLE001
                st = {"connesso": False, "messaggio": str(exc)}

        calib = None
        if self.calib is not None:
            calib = {"eta_giorni": round(self.calib.age_days(), 1)}

        return {"macchina": st, "calibrazione": calib,
                "driver": self.cfg.driver, "job": self.job.as_dict(),
                "ambiente": check_environment(dpi=self.cfg.optical_dpi)}

    # ------------------------------------------------------------------

    def connetti(self, driver: Optional[str] = None) -> Dict[str, object]:
        if driver:
            self.cfg.driver = driver
        self.backend = make_backend(self.cfg)
        self.backend.connect()
        self.log("Backend '%s' connesso." % self.cfg.driver)
        existing = Calibration.latest(self.cfg.path("calib"))
        if existing is not None:
            self.calib = existing
            self.log("Calibrazione esistente, %.0f giorni." % existing.age_days())
        return self.stato()

    def _require(self) -> ScannerBackend:
        if self.backend is None:
            raise ScannerError("macchina non connessa")
        return self.backend

    def muovi(self, mm: float) -> Dict[str, object]:
        self._require().advance(float(mm))
        return self.stato()

    def fuoco(self, mm: float) -> Dict[str, object]:
        self._require().set_focus(float(mm))
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

    def espelli(self) -> None:
        """Espelle la pellicola dal retro.

        Due strade, in ordine di preferenza:

        1. comando di espulsione della macchina, se esiste;
        2. avanzamento a passi finche' il sensore di USCITA non si libera.

        Non si usa mai un avanzamento fisso: con strisce di lunghezza variabile
        non si sa quanta pellicola resti nel percorso, e sbagliare per eccesso
        vuol dire tirare a vuoto un motore del 1995. C'e' comunque un limite di
        corsa: se dopo quello il sensore risulta ancora coperto, la pellicola e'
        inceppata e continuare peggiorerebbe le cose.
        """
        def job():
            backend = self._require()

            if getattr(backend, "supports_eject", False):
                try:
                    self.progress("Espulsione", 0.5)
                    backend.eject()
                    self.log("Pellicola espulsa.")
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
            passo = 10.0
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

    def calibra(self) -> None:
        def job():
            self.progress("Calibrazione in corso", 0.0)
            calib = run_calibration(self._require(), progress=self.progress,
                                    annulla=self.annullato)
            path = calib.save(self.cfg.path("calib"))
            self.calib = calib
            self.log("Calibrazione salvata: %s" % os.path.basename(path))
        self._avvia("calibrazione", job)

    def anteprima(self, formato: str) -> None:
        def job():
            fmt = FORMATS[formato]
            raw = scan_frame(self._require(), self.cfg, PRESETS["provino"], fmt,
                             self.calib, progress=self.progress,
                             annulla=self.annullato)
            res = process(raw, self.options, progress=self.progress)
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
        dest = self.archivio.cartella_ordine(self.cfg.out_dir, ordine.id)
        mirror = RollMirror(flip_h=rullo.specchiato, source="operatore",
                            confirmed=rullo.conf_specchio)

        opts = ProcessOptions(**{k: v for k, v in vars(self.options).items()})
        opts.profile = PROFILES.get(rullo.profilo, PROFILES["c41"])
        opts.roll_mirror = mirror

        def job():
            backend = self._require()

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
            fatti = 0
            # L'esposizione per canale si misura UNA volta per rullo, non a
            # ogni fotogramma: dipende dall'emulsione e dalla lampada, che nel
            # corso di un rullo non cambiano. Misurarla ogni volta farebbe
            # oscillare il colore da un fotogramma all'altro.
            coda = None      # fotogrammi ancora davanti alla finestra
            gains = None
            if preset.auto_exposure and preset.channel_exposure is None:
                self.progress("Misura esposizione per canale", 0.0)
                gains = measure_channel_exposure(backend, preset.channels,
                                                 self.calib,
                                                 annulla=self.annullato)
                self.log("Esposizioni: " + ", ".join(
                    "%s x%.2f" % (k, v) for k, v in gains.items()))

            # Quanti fotogrammi ha la striscia non lo sa nessuno prima di
            # leggerla, e chiederlo all'operatore vuol dire farglielo indovinare.
            # Si conta scansionando e ci si ferma quando la pellicola finisce.
            # Il tetto del formato serve solo a non tirare all'infinito se il
            # rilevamento della coda sbaglia: e' una rete, non un obiettivo.
            automatico = not rullo.attesi
            limite = rullo.attesi or fmt.max_fotogrammi

            for i in range(limite):
                if self._stop.is_set():
                    self.log("Interrotto dall'operatore al fotogramma %d." % (i + 1))
                    break

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
                                    "annotarla in NOTE-MACCHINA.md.")
                                self.log(self.job.errors[-1])
                                coda = 0
                            else:
                                coda = max(0, int(resto / fmt.pitch))
                                self.log(
                                    "Coda della striscia al sensore d'ingresso: "
                                    "restano %d fotogrammi davanti alla finestra."
                                    % coda)
                    elif not film_present(backend, self.calib):
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
                try:
                    raw = scan_frame(backend, self.cfg, preset, fmt, self.calib,
                                     frame_index=i, channel_exposure=gains,
                                     annulla=self.annullato)
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
                        stato="ok", avvisi=" | ".join(res.warnings))
                    self.ultimo_raw = raw
                    self.preview = res.image
                    self.base_lineare = res.base
                    self.preview_ridotta = False
                    self.preview_neg = res.negativo
                    fatti += 1
                    for w in res.warnings:
                        self.log("Fotogramma %d: %s" % (i + 1, w))

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

                if self._stop.is_set():
                    self.log("Interrotto dall'operatore dopo il fotogramma %d."
                             % (i + 1))
                    break
                try:
                    backend.advance(fmt.pitch)
                except Exception as exc:                # noqa: BLE001
                    self.log("Avanzamento fallito: %s. Interrompo." % exc)
                    break

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
            if self.cfg.auto_eject and fatti > 0 and not self._stop.is_set():
                try:
                    if getattr(backend, "supports_eject", False):
                        backend.eject()
                        self.log("Pellicola espulsa dal retro.")
                    else:
                        sens = backend.read_sensors()
                        percorso = 0.0
                        while sens.disponibili and sens.uscita and \
                                percorso < self.cfg.eject_max_mm:
                            backend.advance(10.0)
                            percorso += 10.0
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
                self.log("Rullo terminato: %d fotogrammi." % fatti)
                if fatti >= limite:
                    self.log(
                        "Raggiunto il tetto di %d fotogrammi per il formato %s: "
                        "se la striscia continua, scansionare il resto come "
                        "rullo nuovo." % (limite, rullo.formato))
            else:
                self.log("Rullo terminato: %d fotogrammi su %d." % (fatti, rullo.attesi))

        self._avvia("rullo", job,
                    total=rullo.attesi or FORMATS[rullo.formato].max_fotogrammi)

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
