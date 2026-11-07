# -*- coding: utf-8 -*-
"""
Elaborazione su altri computer della rete (gli "elaboratori").

Il PC della macchina (IBM del 2007) impiega ~23 s a fotogramma per
l'elaborazione completa (riserva, 7/11/2026). Un PC recente o un server della
stessa rete ne impiega una frazione. Qui c'e' la meta' della macchina:

- una coda di lavori: un lavoro e' un grezzo con le sue opzioni, gia'
  impacchettato da chi lo manda (service._lavoro_remoto);
- gli elaboratori (software/elaboratore.py, uno o piu', su PC Windows o
  server) chiedono un lavoro con una richiesta che resta aperta fino a 20 s
  ("long poll"), lo elaborano e rimandano i file finiti;
- chi ha messo il lavoro aspetta l'esito; se l'elaboratore sparisce (niente
  segni di vita per 30 s) o non risponde in tempo, il lavoro torna indietro
  e lo fa la macchina. Nessun fotogramma si perde perche' un PC si spegne.

Tutto passa dal server web dell'interfaccia (stessa porta, gia' aperta nel
firewall), con una chiave condivisa al posto del PIN: un elaboratore non e'
una persona davanti a un tablet. Senza la chiave giusta non si prende niente.
"""

from __future__ import annotations

import collections
import hashlib
import hmac
import io
import os
import secrets
import threading
import time
import zipfile
from typing import Dict, List, Optional, Tuple

PRESENZA_S = 20.0        # visto entro questo tempo = collegato
SPARITO_S = 30.0         # con un lavoro in mano, muto per tanto = sparito
ATTESA_CODA_S = 120.0    # in coda senza che nessuno lo prenda: lo fa la macchina

LEGGIMI_KIT = """Elaboratore per la Masterflex D1008
===================================

Questo PC (o server) elabora i fotogrammi al posto del PC della macchina.

WINDOWS (7 o piu' recente, meglio a 64 bit)
  Doppio clic su ELABORATORE.bat. La prima volta installa Python e le
  librerie (serve internet, qualche minuto), poi si collega da solo alla
  macchina. Lasciare la finestra aperta finche' serve: chiudendola si
  smette di elaborare, e la macchina fa da sola.

SERVER / NAS (Linux, Docker, ...)
  Serve Python 3.8 o piu' recente con numpy, scipy, Pillow e tifffile:
      pip install numpy scipy pillow tifffile
      python3 elaboratore.py
  Nella stessa cartella di elaboratore.json.

COME TROVA LA MACCHINA
  elaboratore.json contiene l'indirizzo della macchina e la chiave. Se
  l'indirizzo cambia, l'elaboratore la cerca da solo nella rete locale
  (porta 8080). Si puo' anche dare a mano:
      python3 elaboratore.py --macchina http://192.168.1.50:8080

Il codice dell'elaborazione lo scarica ogni volta dalla macchina: non c'e'
niente da aggiornare qui. Nella macchina: pannello Macchina, "Elaboratori in
rete", si vede chi e' collegato e quanti fotogrammi ha fatto.
"""


def nuova_chiave() -> str:
    return secrets.token_hex(8)


def prova_chiave(chiave: str, sfida: str) -> str:
    """La risposta della macchina a una sfida: dimostra di conoscere la
    chiave senza mandarla. L'elaboratore la controlla prima di fidarsi
    (scarica ed esegue codice da qui)."""
    return hmac.new(chiave.encode(), sfida.encode(), hashlib.sha256).hexdigest()


class Elaboratori(object):
    def __init__(self, chiave: str):
        self.chiave = chiave
        self._cond = threading.Condition()
        self._attesa: "collections.deque[str]" = collections.deque()
        self._lavori: Dict[str, dict] = {}
        self._visti: Dict[str, dict] = {}
        self._in_ascolto = 0
        self._contatore = 0

    # --- chi c'e' -----------------------------------------------------------
    def chiave_ok(self, chiave) -> bool:
        return bool(self.chiave) and hmac.compare_digest(str(chiave or ""), self.chiave)

    def _visto(self, nome: str, ip: str = "") -> dict:
        v = self._visti.setdefault(nome, {"nome": nome, "fatti": 0, "errori": 0,
                                          "ip": ip, "ultimo": 0.0, "secondi": 0.0})
        v["ultimo"] = time.time()
        if ip:
            v["ip"] = ip
        return v

    def vivo(self, nome: str, ip: str = "") -> None:
        with self._cond:
            self._visto(nome, ip)

    def presenti(self) -> List[str]:
        adesso = time.time()
        with self._cond:
            return [n for n, v in self._visti.items() if adesso - v["ultimo"] < PRESENZA_S]

    def liberi(self) -> int:
        """Elaboratori in ascolto adesso, meno i lavori gia' in coda per loro."""
        with self._cond:
            return max(0, self._in_ascolto - len(self._attesa))

    def stato(self) -> Dict[str, object]:
        adesso = time.time()
        with self._cond:
            return {"elaboratori": [
                        {"nome": v["nome"], "ip": v["ip"], "fatti": v["fatti"],
                         "errori": v["errori"],
                         "media_s": round(v["secondi"] / v["fatti"], 1) if v["fatti"] else None,
                         "collegato": adesso - v["ultimo"] < PRESENZA_S}
                        for v in self._visti.values()],
                    "in_coda": len(self._attesa),
                    "in_lavorazione": sum(1 for l in self._lavori.values() if l.get("chi"))}

    # --- lato macchina ------------------------------------------------------
    def elabora(self, pacco: bytes, timeout: float = 600.0,
                ferma=None) -> bytes:
        """Mette un lavoro in coda e aspetta l'esito (lo zip dei file finiti).
        RuntimeError se nessuno lo prende, se chi l'ha preso sparisce o se
        l'elaboratore risponde con un errore: chi chiama lo fa in casa."""
        with self._cond:
            self._contatore += 1
            ident = "%d-%d" % (int(time.time()), self._contatore)
            lavoro = {"pacco": pacco, "evento": threading.Event(), "esito": None,
                      "errore": None, "chi": None, "dato_alle": 0.0, "messo": time.time()}
            self._lavori[ident] = lavoro
            self._attesa.append(ident)
            self._cond.notify_all()
        try:
            fine = time.time() + timeout
            while not lavoro["evento"].wait(1.0):
                if ferma is not None and ferma():
                    raise RuntimeError("elaborazione fermata")
                adesso = time.time()
                with self._cond:
                    chi = lavoro["chi"]
                    if chi is None:
                        vivi = any(adesso - v["ultimo"] < PRESENZA_S for v in self._visti.values())
                        attesa = adesso - lavoro["messo"]
                        # elaboratori occupati con altri fotogrammi: si
                        # aspetta, ma non all'infinito; spariti tutti: subito
                        if (attesa > 15.0 and not vivi and not self._in_ascolto) \
                                or attesa > ATTESA_CODA_S:
                            raise RuntimeError("nessun elaboratore ha preso il lavoro")
                    if chi is not None and adesso - self._visti[chi]["ultimo"] > SPARITO_S:
                        self._visti[chi]["errori"] += 1
                        raise RuntimeError("l'elaboratore %s non da' piu' segni di vita" % chi)
                if adesso > fine:
                    raise RuntimeError("l'elaboratore non ha finito in %.0f s" % timeout)
            if lavoro["errore"]:
                raise RuntimeError("l'elaboratore %s: %s" % (lavoro["chi"], lavoro["errore"]))
            return lavoro["esito"]
        finally:
            with self._cond:
                self._lavori.pop(ident, None)
                try:
                    self._attesa.remove(ident)
                except ValueError:
                    pass

    # --- lato elaboratore (richieste HTTP) ----------------------------------
    def prendi(self, nome: str, ip: str = "", attesa: float = 20.0) -> Optional[Tuple[str, bytes]]:
        fine = time.time() + attesa
        with self._cond:
            self._visto(nome, ip)
            self._in_ascolto += 1
            try:
                while not self._attesa:
                    resto = fine - time.time()
                    if resto <= 0:
                        return None
                    self._cond.wait(min(resto, 2.0))
                    self._visto(nome, ip)
                ident = self._attesa.popleft()
                lavoro = self._lavori[ident]
                lavoro["chi"] = nome
                lavoro["dato_alle"] = time.time()
                return ident, lavoro["pacco"]
            finally:
                self._in_ascolto -= 1

    def consegna(self, ident: str, nome: str, esito: Optional[bytes] = None,
                 errore: str = "") -> bool:
        with self._cond:
            lavoro = self._lavori.get(ident)
            v = self._visto(nome)
            if lavoro is None:
                return False            # gia' rifatto in casa: niente da fare
            lavoro["esito"], lavoro["errore"] = esito, errore
            if errore:
                v["errori"] += 1
            else:
                v["fatti"] += 1
                v["secondi"] += time.time() - lavoro["dato_alle"]
            lavoro["evento"].set()
            return True


# --- il codice per gli elaboratori ---------------------------------------

_codice: Dict[str, object] = {}


def codice_zip(software: str) -> Tuple[bytes, str]:
    """filmscan ed elaboratore.py in uno zip, con la sua impronta: gli
    elaboratori lo scaricano dalla macchina, cosi' elaborano sempre con lo
    stesso codice della macchina, senza installarlo a parte."""
    file = []
    for base, cartelle, nomi in os.walk(os.path.join(software, "filmscan")):
        cartelle[:] = [c for c in cartelle if c != "__pycache__"]
        for n in sorted(nomi):
            if n.endswith(".py"):
                file.append(os.path.join(base, n))
    file.append(os.path.join(software, "elaboratore.py"))
    impronta = hashlib.sha256()
    for p in file:
        impronta.update(os.path.relpath(p, software).encode())
        impronta.update(str(os.path.getmtime(p)).encode())
    versione = impronta.hexdigest()[:12]
    if _codice.get("versione") != versione:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in file:
                z.write(p, os.path.relpath(p, software).replace(os.sep, "/"))
        _codice.update(versione=versione, dati=buf.getvalue())
    return _codice["dati"], versione
