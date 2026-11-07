# -*- coding: utf-8 -*-
"""
La copia degli ordini in un archivio di rete (NAS, cartella condivisa).

Richiesta di Alessandro (7/11/2026): come l'archivio locale della macchina,
ma in una cartella di rete, per vedere subito le scansioni dalle altre
postazioni e tenerne una copia di sicurezza. Per ogni rullo:

    <archivio>\\<cliente>\\<rullo>\\
        JPEG alta risoluzione\\     la foto finale in JPEG, piena risoluzione
        TIFF 16 bit\\               la foto finale a 16 bit
        Negativi invertiti DNG\\    il positivo lineare (Lightroom, Camera Raw)
        Grezzi\\                    .npz e .json, per rielaborare
        <rullo> provino.jpg        il provino a contatto del rullo

Il nome del rullo e' l'etichetta data all'ordine; se lo stesso cliente ha
gia' una cartella con quel nome di un altro ordine, si aggiunge il codice
dell'ordine (succede con "rullo 1" in ordini diversi).

Si copia quando la macchina non lavora: la rete non deve rallentare ne' la
lettura ne' l'elaborazione. I rulli da copiare stanno in una coda su disco
(sopravvive ai riavvii); se l'archivio non risponde, si riprova piu' tardi.
Si copia solo cio' che e' cambiato (dimensione e data), con file temporaneo
e rinomina, cosi' dalle altre postazioni non si vede mai un file a meta'.
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Dict, List, Optional

_registro = logging.getLogger("filmscan.archivio_rete")

CARTELLE = {"jpeg": "JPEG alta risoluzione", "tiff": "TIFF 16 bit",
            "positivi": "Negativi invertiti DNG", "grezzi": "Grezzi"}
VIETATI = '<>:"/\\|?*'
RIPROVA_S = 120.0


def nome_cartella(testo: str, di_riserva: str) -> str:
    nome = "".join("_" if ch in VIETATI else ch for ch in str(testo or "")).strip()
    return nome.rstrip(". ") or di_riserva


class ArchivioRete(object):
    def __init__(self, servizio):
        self.s = servizio
        self._lock = threading.Lock()
        self._coda: List[int] = self._leggi_coda()
        self._evento = threading.Event()
        self.ultimo: Dict[str, object] = {}
        self._connesso = False
        threading.Thread(target=self._giro, name="archivio-rete", daemon=True).start()

    # --- configurazione ---------------------------------------------------
    @property
    def cfg(self):
        return self.s.cfg

    def attivo(self) -> bool:
        return bool(self.cfg.rete_attivo and self.cfg.rete_percorso)

    def _file_coda(self) -> str:
        return os.path.join(self.cfg.path("raw"), "archivio_rete_coda.json")

    def _leggi_coda(self) -> List[int]:
        try:
            with io.open(self._file_coda(), encoding="utf-8") as f:
                return [int(x) for x in json.load(f)]
        except (IOError, OSError, ValueError, TypeError):
            return []

    def _scrivi_coda(self) -> None:
        try:
            with io.open(self._file_coda() + ".tmp", "w", encoding="utf-8") as f:
                json.dump(self._coda, f)
            os.replace(self._file_coda() + ".tmp", self._file_coda())
        except (IOError, OSError):
            pass

    def accoda(self, rullo_id: int) -> None:
        if not self.attivo():
            return
        with self._lock:
            if int(rullo_id) not in self._coda:
                self._coda.append(int(rullo_id))
                self._scrivi_coda()
        self._evento.set()

    def accoda_ordine(self, ordine_id: int) -> int:
        rulli = self.s.archivio.rulli(int(ordine_id))
        for r in rulli:
            self.accoda(r.id)
        return len(rulli)

    def accoda_tutto(self) -> int:
        n = 0
        for o in self.s.archivio.ordini(limite=1000000):
            n += self.accoda_ordine(o.id)
        return n

    def stato(self) -> Dict[str, object]:
        with self._lock:
            coda = len(self._coda)
        return {"attivo": self.attivo(), "percorso": self.cfg.rete_percorso,
                "utente": self.cfg.rete_utente, "password": bool(self.cfg.rete_password),
                "in_coda": coda, "ultimo": dict(self.ultimo)}

    # --- collegamento -------------------------------------------------------
    def _collega(self) -> None:
        """Con utente e password, un "net use" sulla condivisione (\\\\server\\
        cartella) una volta: poi i percorsi UNC si aprono come cartelle."""
        if self._connesso or os.name != "nt" or not self.cfg.rete_utente:
            return
        p = self.cfg.rete_percorso
        if not p.startswith("\\\\"):
            return
        parti = p.lstrip("\\").split("\\")
        if len(parti) < 2:
            return
        radice = "\\\\" + parti[0] + "\\" + parti[1]
        cmd = ["net", "use", radice]
        if self.cfg.rete_password:
            cmd.append(self.cfg.rete_password)
        cmd.append("/user:" + self.cfg.rete_utente)
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as exc:                        # noqa: BLE001
            _registro.warning("net use %s non riuscito: %s", radice, exc)
        self._connesso = True

    def prova(self) -> str:
        """Scrive e cancella un file nell'archivio: dice se si puo' usare."""
        if not self.cfg.rete_percorso:
            raise ValueError("manca il percorso dell'archivio di rete")
        self._connesso = False
        self._collega()
        p = self.cfg.rete_percorso
        if not os.path.isdir(p):
            raise ValueError("la cartella %s non si raggiunge (rete, nome, permessi?)" % p)
        prova = os.path.join(p, ".prova-filmscan-%d.tmp" % int(time.time()))
        with open(prova, "wb") as f:
            f.write(b"prova")
        os.remove(prova)
        return "archivio di rete raggiungibile e scrivibile: %s" % p

    # --- copia ----------------------------------------------------------------
    def _giro(self) -> None:
        while True:
            self._evento.wait(30.0)
            self._evento.clear()
            while self.attivo():
                # mai mentre la macchina lavora: la rete aspetta
                if self.s.job.running:
                    time.sleep(5.0)
                    continue
                with self._lock:
                    if not self._coda:
                        break
                    rid = self._coda[0]
                try:
                    self._collega()
                    if not os.path.isdir(self.cfg.rete_percorso):
                        raise IOError("archivio %s non raggiungibile" % self.cfg.rete_percorso)
                    n = self.copia_rullo(rid)
                    with self._lock:
                        if rid in self._coda:
                            self._coda.remove(rid)
                        self._scrivi_coda()
                    self.ultimo = {"quando": time.strftime("%d/%m %H:%M"), "rullo": rid,
                                   "file": n, "errore": ""}
                except Exception as exc:                # noqa: BLE001
                    _registro.warning("copia del rullo %s nell'archivio di rete: %s", rid, exc)
                    self.ultimo = {"quando": time.strftime("%d/%m %H:%M"), "rullo": rid,
                                   "file": 0, "errore": str(exc)}
                    self._connesso = False
                    self._evento.wait(RIPROVA_S)
                    self._evento.clear()

    def _cartella_rullo(self, ordine, rullo) -> str:
        cliente = nome_cartella(ordine.cliente_nome, "cliente")
        nome = nome_cartella(rullo.etichetta, "rullo %d" % rullo.id)
        base = os.path.join(self.cfg.rete_percorso, cliente)
        for candidato in (nome, "%s (%s)" % (nome, ordine.codice)):
            p = os.path.join(base, candidato)
            segno = os.path.join(p, ".filmscan.json")
            if not os.path.exists(p):
                break
            try:
                with io.open(segno, encoding="utf-8") as f:
                    if int(json.load(f).get("rullo_id", -1)) == rullo.id:
                        break
            except (IOError, OSError, ValueError):
                pass        # cartella fatta a mano o di un altro ordine
        os.makedirs(p, exist_ok=True)
        with io.open(os.path.join(p, ".filmscan.json"), "w", encoding="utf-8") as f:
            json.dump({"rullo_id": rullo.id, "ordine": ordine.codice,
                       "cliente": ordine.cliente_nome, "rullo": rullo.etichetta}, f)
        return p

    @staticmethod
    def _uguale(sorgente: str, dest: str) -> bool:
        try:
            a, b = os.stat(sorgente), os.stat(dest)
        except OSError:
            return False
        return a.st_size == b.st_size and abs(a.st_mtime - b.st_mtime) < 2.0

    def _copia(self, sorgente: str, dest: str) -> bool:
        if not sorgente or not os.path.isfile(sorgente) or self._uguale(sorgente, dest):
            return False
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(sorgente, dest + ".tmp")
        os.replace(dest + ".tmp", dest)
        return True

    def copia_rullo(self, rullo_id: int) -> int:
        """Copia (o aggiorna) un rullo nell'archivio di rete. Ritorna quanti
        file ha scritto."""
        from .consegne import apri_immagine
        arch = self.s.archivio
        rullo = arch.rullo(int(rullo_id))
        if rullo is None:
            return 0
        ordine = arch.ordine(rullo.ordine_id)
        if ordine is None:
            return 0
        cartella = self._cartella_rullo(ordine, rullo)
        scritti = 0
        finiti = []
        for f in arch.fotogrammi(rullo.id):
            raw = f.get("file_raw") or ""
            if raw and os.path.isfile(raw):
                scritti += self._copia(raw, os.path.join(cartella, CARTELLE["grezzi"],
                                                         os.path.basename(raw)))
                js = raw[:-4] + ".json"
                scritti += self._copia(js, os.path.join(cartella, CARTELLE["grezzi"],
                                                        os.path.basename(js)))
            out = f.get("file_out") or ""
            if f.get("stato") != "ok" or not out or not os.path.isfile(out):
                continue
            finiti.append(out)
            base = os.path.splitext(os.path.basename(out))[0]
            if out.lower().endswith((".tif", ".tiff")):
                scritti += self._copia(out, os.path.join(cartella, CARTELLE["tiff"],
                                                         os.path.basename(out)))
                jpg = os.path.join(cartella, CARTELLE["jpeg"], base + ".jpg")
                if not os.path.exists(jpg) or os.path.getmtime(jpg) < os.path.getmtime(out):
                    scritti += self._jpeg(out, jpg)
            elif out.lower().endswith((".jpg", ".jpeg")):
                scritti += self._copia(out, os.path.join(cartella, CARTELLE["jpeg"],
                                                         os.path.basename(out)))
            pos = os.path.join(os.path.dirname(out), base + "_positivo.dng")
            scritti += self._copia(pos, os.path.join(cartella, CARTELLE["positivi"],
                                                     os.path.basename(pos)))
        if finiti:
            scritti += self._provino(finiti, os.path.join(
                cartella, "%s provino.jpg" % nome_cartella(rullo.etichetta, "rullo")),
                "%s - %s - %s" % (ordine.cliente_nome, ordine.codice, rullo.etichetta))
        _registro.info("rullo %d copiato in %s: %d file scritti", rullo.id, cartella, scritti)
        return scritti

    @staticmethod
    def _jpeg(sorgente: str, dest: str) -> int:
        """La foto finale (TIFF 16 bit) in JPEG a piena risoluzione, qualita' 95."""
        from .consegne import _leggi_per_convertire
        from . import pipeline
        img, descrizione = _leggi_per_convertire(sorgente)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".tmp.jpg"
        pipeline.export(img, tmp, "jpeg", jpeg_quality=95, meta={"descrizione": descrizione})
        os.replace(tmp, dest)
        try:
            t = os.path.getmtime(sorgente)
            os.utime(dest, (t, t))
        except OSError:
            pass
        return 1

    @staticmethod
    def _provino(file: List[str], dest: str, titolo: str, colonne: int = 6,
                 lato: int = 360, margine: int = 12) -> int:
        from PIL import Image, ImageDraw
        from .consegne import apri_immagine
        mini = []
        for p in file:
            try:
                im = apri_immagine(p)
                im.thumbnail((lato, lato))
                mini.append(im.convert("RGB"))
            except Exception:                           # noqa: BLE001
                continue
        if not mini:
            return 0
        righe = (len(mini) + colonne - 1) // colonne
        larg = colonne * (lato + margine) + margine
        alt = righe * (lato + margine + 18) + margine + 30
        foglio = Image.new("RGB", (larg, alt), (255, 255, 255))
        d = ImageDraw.Draw(foglio)
        d.text((margine, 8), titolo, fill=(0, 0, 0))
        for k, im in enumerate(mini):
            x = margine + (k % colonne) * (lato + margine)
            y = 30 + margine + (k // colonne) * (lato + margine + 18)
            foglio.paste(im, (x + (lato - im.size[0]) // 2, y + (lato - im.size[1]) // 2))
            d.text((x, y + lato + 2), str(k + 1), fill=(0, 0, 0))
        tmp = dest + ".tmp.jpg"
        foglio.save(tmp, format="JPEG", quality=88)
        os.replace(tmp, dest)
        return 1
