# -*- coding: utf-8 -*-
"""
Collegamento remoto: registri che partono da soli, aggiornamenti che
arrivano da soli. Niente piu' chiavetta fra la macchina e chi la segue.

Tutto passa da GitHub, con un token personale "fine-grained" che sta solo
sulla macchina (software/remoto.json, mai nel repository):

- **registri**: lo zip dei registri (registri.raccogli) va in un repository
  PRIVATO a parte, <macchina>/<AAAA-MM>/registri-<data>.zip. Prima di
  partire si tolgono nomi, telefoni ed email dei clienti (presi da
  ordini.db) e il PIN: dai registri si capisce la macchina, non chi sono i
  clienti. Nel repository del codice i registri non vanno mai.
- **aggiornamenti**: la macchina guarda l'ultimo commit di un ramo del
  repository del codice (di serie "main", dove si pubblica solo cio' che
  e' pronto per la macchina, come prima i pacchetti), lo scarica e lo installa
  con manutenzione.py aggiorna (backup del codice, dati intatti).
  All'avvio automatico si installa da solo se cosi' configurato; dall'
  interfaccia c'e' il tasto.

Solo libreria standard: deve girare anche quando le librerie mancano, ed e'
la strada per ripararle a distanza.

    python remoto.py configura        token, repository, nome della macchina
    python remoto.py stato            cosa c'e' di configurato, versione, ultimo invio
    python remoto.py invia [--giorni 2] [--motivo testo]
    python remoto.py controlla        c'e' un aggiornamento?
    python remoto.py aggiorna [--se-disponibile] [--all-avvio]
    python remoto.py menu             le voci qui sopra, per MASTERFLEX.bat
"""

from __future__ import print_function

import base64
import io
import json
import os
import re
import sqlite3
import sys
import time
import zipfile

QUI = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(QUI, "remoto.json")
VERSIONE = os.path.join(QUI, "versione.json")
API = "https://api.github.com"
DI_SERIE = {
    "token": "",
    "repo_codice": "TheTartalex/gretag_masterflex",
    "ramo": "main",
    "repo_registri": "TheTartalex/gretag_masterflex_registri",
    "macchina": "riserva",
    # ogni quante ore l'interfaccia manda i registri da sola (0 = mai)
    "invio_ore": 6,
    # all'avvio automatico l'aggiornamento si installa da solo
    "aggiorna_all_avvio": True,
}
LIMITE_MB = 40          # oltre, si rifa' lo zip senza le immagini


# --- configurazione -------------------------------------------------------

def carica():
    """La configurazione, o None se non c'e'. Il token serve per i registri
    (repository privato); per gli aggiornamenti dal repository del codice,
    che e' pubblico, si puo' anche farne a meno."""
    try:
        with io.open(CONFIG, encoding="utf-8") as f:
            return dict(DI_SERIE, **json.load(f))
    except (IOError, OSError, ValueError):
        return None


def salva(cfg):
    tmp = CONFIG + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(cfg, indent=1, ensure_ascii=False))
    os.replace(tmp, CONFIG)


def _annota(chiave, valore):
    cfg = carica()
    if cfg is None:
        return
    cfg.setdefault("stato", {})[chiave] = valore
    salva(cfg)


# --- HTTP -----------------------------------------------------------------

def _contesto():
    """TLS con i certificati di Windows PIU' quelli di Mozilla che viaggiano
    con il software (certificati/cacert.pem, dal pacchetto certifi): Windows 7
    non ha piu' Windows Update, e i suoi certificati radice invecchiano. Per
    rinnovarli basta un aggiornamento del software, che arriva da qui."""
    import ssl
    ctx = ssl.create_default_context()
    pem = os.path.join(QUI, "certificati", "cacert.pem")
    if os.path.exists(pem):
        try:
            ctx.load_verify_locations(cafile=pem)
        except (ssl.SSLError, OSError):
            pass
    return ctx


def _richiesta(cfg, metodo, url, dati=None, timeout=60, grezzo=False):
    from urllib.request import Request, urlopen
    corpo = None if dati is None else json.dumps(dati).encode("utf-8")
    r = Request(url if url.startswith("http") else API + url, data=corpo, method=metodo)
    if cfg.get("token"):
        r.add_header("Authorization", "Bearer %s" % cfg["token"])
    r.add_header("Accept", "application/vnd.github+json")
    r.add_header("X-GitHub-Api-Version", "2022-11-28")
    r.add_header("User-Agent", "masterflex-d1008")
    if corpo is not None:
        r.add_header("Content-Type", "application/json")
    risposta = urlopen(r, timeout=timeout, context=_contesto())
    contenuto = risposta.read()
    return contenuto if grezzo else json.loads(contenuto.decode("utf-8") or "null")


def spiega(exc):
    """Un errore di rete detto in modo che si capisca cosa fare."""
    testo = str(exc)
    codice = getattr(exc, "code", None)
    if codice == 401:
        return "token non valido o scaduto (401): rifare 'configura'"
    if codice == 403:
        return "token senza i permessi giusti (403): vedi la guida in 'configura'"
    if codice == 404:
        return ("repository o ramo non trovato (404): nome sbagliato, oppure il "
                "token non ha accesso a quel repository")
    if "CERTIFICATE" in testo.upper():
        return ("certificato di GitHub non riconosciuto, neanche con l'elenco di "
                "certificati del software (certificati/cacert.pem): controllare data "
                "e ora del PC, poi portare un software aggiornato (%s)" % testo)
    return testo


# --- versioni -------------------------------------------------------------

def versione_installata():
    try:
        with io.open(VERSIONE, encoding="utf-8") as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return {}


def ultima_versione(cfg):
    c = _richiesta(cfg, "GET", "/repos/%s/commits/%s" % (cfg["repo_codice"], cfg["ramo"]))
    return {"sha": c["sha"], "breve": c["sha"][:7],
            "messaggio": c["commit"]["message"].splitlines()[0],
            "data": c["commit"]["committer"]["date"]}


def controlla(cfg):
    """{"disponibile": bool, "remota": {...}, "installata": "sha"}"""
    remota = ultima_versione(cfg)
    installata = versione_installata().get("sha", "")
    esito = {"disponibile": remota["sha"] != installata, "remota": remota,
             "installata": installata[:7], "controllato": time.strftime("%Y-%m-%d %H:%M")}
    _annota("controllo", esito)
    return esito


def installa(cfg, sha=None, scrivi=print):
    """Scarica il codice di 'sha' (o l'ultimo del ramo) e lo installa con
    manutenzione.py aggiorna. MASTERFLEX.bat arriva come .nuovo: lo scambia
    chi ha lanciato (menu, avvio automatico)."""
    if sha is None:
        sha = ultima_versione(cfg)["sha"]
    lavoro = os.path.join(QUI, "backup", "_remoto")
    if not os.path.isdir(lavoro):
        os.makedirs(lavoro)
    zip_path = os.path.join(lavoro, "codice-%s.zip" % sha[:7])
    scrivi("Scarico %s@%s..." % (cfg["repo_codice"], sha[:7]))
    dati = _richiesta(cfg, "GET", "/repos/%s/zipball/%s" % (cfg["repo_codice"], sha),
                      timeout=300, grezzo=True)
    with open(zip_path, "wb") as f:
        f.write(dati)
    import manutenzione
    esito = manutenzione.cmd_aggiorna([zip_path])
    if esito:
        raise RuntimeError("manutenzione.py aggiorna non e' riuscito (uscita %s)" % esito)
    with io.open(VERSIONE, "w", encoding="utf-8") as f:
        f.write(json.dumps({"sha": sha, "installata": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "da": "%s@%s" % (cfg["repo_codice"], cfg["ramo"])}))
    _annota("installata", {"sha": sha[:7], "quando": time.strftime("%Y-%m-%d %H:%M")})
    try:
        os.remove(zip_path)
    except OSError:
        pass
    return sha


# --- registri -------------------------------------------------------------

def _sostituzioni(db):
    """(testo da togliere, segnaposto) per nomi, telefoni ed email dei clienti.
    I piu' lunghi prima, cosi' "Mario Rossi" non resta a meta'."""
    coppie = []
    if not db or not os.path.exists(db):
        return coppie
    try:
        conn = sqlite3.connect(db, timeout=5)
        righe = conn.execute("SELECT id, nome, telefono, email FROM clienti").fetchall()
        conn.close()
    except sqlite3.Error:
        return coppie
    for ident, nome, telefono, email in righe:
        segno = "cliente-%d" % ident
        for v in (nome, email):
            if v and len(v.strip()) >= 3:
                coppie.append((v.strip(), segno))
        if telefono:
            cifre = re.sub(r"\D", "", telefono)
            if len(cifre) >= 6:
                coppie.append((telefono.strip(), "tel-%d" % ident))
                coppie.append((cifre, "tel-%d" % ident))
                if not cifre.startswith("39"):
                    coppie.append(("39" + cifre, "tel-%d" % ident))
    coppie.sort(key=lambda c: -len(c[0]))
    return coppie


TESTO = (".log", ".txt", ".json", ".csv", ".ini")


def anonimizza(zip_in, zip_out, db, senza_immagini=False):
    coppie = _sostituzioni(db)
    tolti = 0
    with zipfile.ZipFile(zip_in) as zi, zipfile.ZipFile(zip_out, "w", zipfile.ZIP_DEFLATED) as zo:
        for voce in zi.infolist():
            nome = voce.filename
            if senza_immagini and nome.lower().endswith((".png", ".jpg", ".raw", ".npy")):
                continue
            dati = zi.read(nome)
            if nome.lower().endswith(TESTO) and coppie:
                t = dati.decode("utf-8", "replace")
                for vecchio, nuovo in coppie:
                    n = t.count(vecchio)
                    if n:
                        t = t.replace(vecchio, nuovo)
                        tolti += n
                dati = t.encode("utf-8")
            for vecchio, nuovo in coppie:          # anche nei nomi dei file
                nome = nome.replace(vecchio, nuovo)
            zo.writestr(nome, dati)
    return tolti


def invia(cfg, giorni=2, motivo="", db=None, scrivi=print):
    """Raccoglie, anonimizza e manda lo zip dei registri. Restituisce il
    percorso remoto."""
    from filmscan import registri
    if not cfg.get("token"):
        raise RuntimeError("per inviare i registri serve il token: 'configura'")
    db = db or os.path.join(QUI, "ordini.db")
    grezzo = registri.raccogli(giorni, r"C:\MasterflexD\ponte", r"C:\MasterflexD\Scanner",
                               os.path.join(QUI, "impostazioni.json"))
    pronto = grezzo[:-4] + "-anonimo.zip"
    tolti = anonimizza(grezzo, pronto, db)
    if os.path.getsize(pronto) > LIMITE_MB * 1024 * 1024:
        tolti = anonimizza(grezzo, pronto, db, senza_immagini=True)
    with open(pronto, "rb") as f:
        contenuto = base64.b64encode(f.read()).decode("ascii")
    nome = os.path.basename(grezzo)
    if motivo:
        nome = nome[:-4] + "-" + re.sub(r"[^a-z0-9]+", "-", motivo.lower()).strip("-")[:30] + ".zip"
    remoto = "%s/%s/%s" % (cfg["macchina"], time.strftime("%Y-%m"), nome)
    scrivi("Invio %s (%d KB, %d dati di clienti tolti)..."
           % (remoto, os.path.getsize(pronto) // 1024, tolti))
    _richiesta(cfg, "PUT", "/repos/%s/contents/%s" % (cfg["repo_registri"], remoto),
               {"message": "registri %s %s%s" % (cfg["macchina"], time.strftime("%Y-%m-%d %H:%M"),
                                                ": " + motivo if motivo else ""),
                "content": contenuto}, timeout=300)
    try:
        os.remove(pronto)
    except OSError:
        pass
    _annota("invio", {"file": remoto, "quando": time.strftime("%Y-%m-%d %H:%M")})
    return remoto


# --- riga di comando ------------------------------------------------------

def cmd_configura():
    cfg = dict(DI_SERIE, **(carica() or {}))
    print("")
    print("Aggiornamenti: dal repository del codice (%s), pubblico." % cfg["repo_codice"])
    print("Registri: serve un token di GitHub (fine-grained) con")
    print("  Contents: Read and write  sul repository dei registri, PRIVATO")
    print("  (%s); se il codice diventa privato, anche Contents: Read su quello." % cfg["repo_registri"])
    print("Si crea da github.com > Settings > Developer settings > Personal access")
    print("tokens > Fine-grained tokens. Invio lascia il valore fra parentesi.")
    print("")

    def chiedi(voce, testo, nascosto=False):
        attuale = cfg.get(voce, "")
        mostra = ("(gia' impostato)" if attuale else "") if nascosto else attuale
        v = input("  %s [%s]: " % (testo, mostra)).strip()
        if v:
            cfg[voce] = v
    chiedi("token", "Token", nascosto=True)
    chiedi("repo_codice", "Repository del codice")
    chiedi("ramo", "Ramo da cui aggiornarsi")
    chiedi("repo_registri", "Repository dei registri")
    chiedi("macchina", "Nome di questa macchina (riserva, produzione)")
    if not cfg.get("token"):
        print("  Senza token: aggiornamenti si', invio dei registri no.")
    salva(cfg)
    print("")
    print("Prova del collegamento...")
    try:
        v = ultima_versione(cfg)
        print("  codice: ok, ultima versione %s (%s)" % (v["breve"], v["messaggio"]))
    except Exception as exc:                            # noqa: BLE001
        print("  codice: NO - %s" % spiega(exc))
    if cfg.get("token"):
        try:
            _richiesta(cfg, "GET", "/repos/%s" % cfg["repo_registri"])
            print("  registri: ok")
        except Exception as exc:                        # noqa: BLE001
            print("  registri: NO - %s" % spiega(exc))
    print("Salvato in %s (resta su questa macchina)." % CONFIG)
    return 0


def cmd_stato():
    cfg = carica()
    if cfg is None:
        print("Collegamento remoto non configurato: 'python remoto.py configura'.")
        return 1
    v = versione_installata()
    print("Codice: %s, ramo %s; installata %s" % (cfg["repo_codice"], cfg["ramo"],
                                                   v.get("sha", "?")[:7]))
    print("Registri: %s, macchina '%s', invio ogni %s ore"
          % (cfg["repo_registri"], cfg["macchina"], cfg["invio_ore"]))
    for k, val in sorted((cfg.get("stato") or {}).items()):
        print("  ultimo %s: %s" % (k, json.dumps(val, ensure_ascii=False)))
    return 0


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Collegamento remoto: registri e aggiornamenti")
    ap.add_argument("azione", choices=("configura", "stato", "invia", "controlla",
                                       "aggiorna", "menu"))
    ap.add_argument("--giorni", type=int, default=2)
    ap.add_argument("--motivo", default="")
    ap.add_argument("--se-disponibile", action="store_true")
    ap.add_argument("--all-avvio", action="store_true",
                    help="solo se 'aggiorna_all_avvio' e' attivo; mai errori bloccanti")
    a = ap.parse_args(argv)

    if a.azione == "configura":
        return cmd_configura()
    if a.azione == "stato":
        return cmd_stato()
    if a.azione == "menu":
        while True:
            print("")
            print("  COLLEGAMENTO REMOTO")
            print("    1  Configura (token, repository, nome della macchina)")
            print("    2  Invia adesso i registri")
            print("    3  Controlla e installa l'aggiornamento")
            print("    4  Stato")
            print("    0  Indietro")
            s = input("  Scelta: ").strip()
            if s == "1":
                cmd_configura()
            elif s == "2":
                main(["invia", "--motivo", "dal menu"])
            elif s == "3":
                main(["aggiorna", "--se-disponibile"])
            elif s == "4":
                cmd_stato()
            else:
                return 0

    cfg = carica()
    if cfg is None:
        if not a.all_avvio:
            print("Collegamento remoto non configurato: 'python remoto.py configura'.")
        return 0 if a.all_avvio else 1
    try:
        if a.azione == "invia":
            print("Inviato: %s" % invia(cfg, a.giorni, a.motivo))
            return 0
        if a.azione == "controlla":
            c = controlla(cfg)
            print("Installata %s, sul ramo %s c'e' %s (%s): %s"
                  % (c["installata"] or "?", cfg["ramo"], c["remota"]["breve"],
                     c["remota"]["messaggio"],
                     "AGGIORNAMENTO DISPONIBILE" if c["disponibile"] else "gia' aggiornata"))
            return 0
        if a.azione == "aggiorna":
            if a.all_avvio and not cfg.get("aggiorna_all_avvio"):
                return 0
            c = controlla(cfg)
            if a.se_disponibile and not c["disponibile"]:
                print("Codice gia' aggiornato (%s)." % c["remota"]["breve"])
                return 0
            print("Aggiornamento: %s - %s" % (c["remota"]["breve"], c["remota"]["messaggio"]))
            installa(cfg, c["remota"]["sha"])
            print("Installato %s." % c["remota"]["breve"])
            return 0
    except Exception as exc:                            # noqa: BLE001
        print("Collegamento remoto: %s" % spiega(exc))
        # all'avvio automatico un errore di rete non deve fermare la macchina
        return 0 if a.all_avvio else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
