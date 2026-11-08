# -*- coding: utf-8 -*-
"""
L'elaboratore: un PC o un server della rete che elabora i fotogrammi al posto
del PC della macchina.

    python elaboratore.py                       indirizzo e chiave da elaboratore.json
    python elaboratore.py --macchina http://192.168.1.50:8080 --chiave abcd...
    python elaboratore.py --processi 3          tre fotogrammi alla volta

Le stesse cose si possono dare con le variabili d'ambiente (comodo in un
container Docker, per esempio su Unraid): MASTERFLEX_MACCHINA,
MASTERFLEX_CHIAVE, MASTERFLEX_NOME, MASTERFLEX_PROCESSI. Senza --processi
i processi si contano da soli: meta' dei processori che il PC (o il
container) puo' usare, 2 GB di memoria l'uno, al massimo 16.

Acceso giorno e notte va bene: con la macchina spenta aspetta e la cerca
ogni 30 s (solo il primo processo cerca nella rete, gli altri usano
l'indirizzo che ha trovato lui), e quando la macchina si riaccende
riprende da solo.

Su Windows lo si avvia con ELABORATORE.bat (installa Python e le librerie la
prima volta). Il pacchetto con questo file, il .bat e elaboratore.json
(indirizzo e chiave) si scarica dalla macchina: pannello Macchina,
"Elaborati in rete", "Scarica il pacchetto".

Come funziona (la meta' della macchina e' filmscan/remoti.py):

1. trova la macchina: l'indirizzo di elaboratore.json, o se non risponde la
   cerca nella rete locale (porta 8080). La macchina dimostra di conoscere la
   chiave (remoti.prova_chiave) PRIMA che le si chieda il codice: senza
   quella prova non si scarica ne' si esegue niente;
2. scarica dalla macchina il codice dell'elaborazione (filmscan): cosi'
   elabora sempre con la stessa versione della macchina, e un aggiornamento
   della macchina vale subito anche qui (il processo riparte da solo);
3. chiede un lavoro (la richiesta resta aperta fino a 20 s), lo elabora,
   rimanda i file finiti. Mentre lavora dice ogni 5 s che e' vivo: se
   sparisce, la macchina rifa' il fotogramma da sola.

Quello che rimanda e' solo JSON, immagini e array numpy senza oggetti: la
macchina non esegue niente che venga da qui.

Solo libreria standard fino al punto 2; per elaborare servono numpy, scipy,
Pillow e tifffile (le versioni recenti vanno bene: qui non c'e' il vincolo
della CPU senza AVX della macchina).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import zipfile

QUI = os.path.dirname(os.path.abspath(__file__))
FILE_CONF = os.path.join(QUI, "elaboratore.json")
PORTA = 8080
ESCI_AGGIORNA = 3               # il figlio esce cosi': codice nuovo, ripartire


def scrivi(msg: str) -> None:
    print("%s  %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def carica_conf() -> dict:
    try:
        with io.open(FILE_CONF, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (IOError, OSError, ValueError):
        return {}


def salva_conf(d: dict) -> None:
    try:
        with io.open(FILE_CONF + ".tmp", "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        os.replace(FILE_CONF + ".tmp", FILE_CONF)
    except (IOError, OSError):
        pass


# --- la macchina -----------------------------------------------------------

def _get(url: str, intestazioni=None, timeout: float = 10.0):
    req = urllib.request.Request(url, headers=intestazioni or {})
    return urllib.request.urlopen(req, timeout=timeout)


def prova(url: str, chiave: str, timeout: float = 2.0) -> bool:
    """La macchina all'indirizzo url conosce la chiave?"""
    sfida = secrets.token_hex(8)
    try:
        with _get(url.rstrip("/") + "/api/elaboratore/ciao?sfida=" + sfida,
                  timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception:                                   # noqa: BLE001
        return False
    atteso = hmac.new(chiave.encode(), sfida.encode(), hashlib.sha256).hexdigest()
    return (d.get("servizio") == "filmscan-elaborazione" and bool(d.get("attivo"))
            and hmac.compare_digest(str(d.get("prova") or ""), atteso))


def indirizzi_locali():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))        # nessun pacchetto: solo l'instradamento
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            ips.add(ip)
    except OSError:
        pass
    return [ip for ip in ips if not ip.startswith("127.")]


def cerca(chiave: str, porta: int = PORTA):
    """La macchina nella rete locale: le /24 dei propri indirizzi, porta
    8080, chi risponde a "ciao" con la prova giusta. Niente UDP: passa da
    qualsiasi firewall che lascia aprire l'interfaccia dal browser."""
    trovata = []
    candidati = []
    for ip in indirizzi_locali():
        base = ip.rsplit(".", 1)[0]
        candidati += ["http://%s.%d:%d" % (base, k, porta) for k in range(1, 255)]
    if not candidati:
        return None
    scrivi("cerco la macchina nella rete (%d indirizzi)..." % len(candidati))
    lock = threading.Lock()

    def lavora(lista):
        for u in lista:
            if trovata:
                return
            host = u.split("//")[1].split(":")[0]
            try:
                with socket.create_connection((host, porta), timeout=0.3):
                    pass
            except OSError:
                continue
            if prova(u, chiave):
                with lock:
                    trovata.append(u)
                return

    fili = [threading.Thread(target=lavora, args=(candidati[k::32],), daemon=True)
            for k in range(32)]
    for t in fili:
        t.start()
    for t in fili:
        t.join()
    return trovata[0] if trovata else None


def trova_macchina(conf: dict, chiave: str, porta: int):
    url = (conf.get("macchina") or "").rstrip("/")
    if url and prova(url, chiave):
        return url
    if url:
        scrivi("la macchina non risponde a %s" % url)
    url = cerca(chiave, porta)
    if url:
        conf["macchina"] = url
        salva_conf(conf)
    return url


# --- il codice dell'elaborazione -------------------------------------------

def scarica_codice(url: str, chiave: str, cartella: str) -> str:
    with _get(url + "/api/elaboratore/codice.zip", {"X-Chiave": chiave}, timeout=60) as r:
        versione = r.headers.get("X-Versione") or "senza-versione"
        dati = r.read()
    dest = os.path.join(cartella, "codice-" + "".join(c for c in versione if c.isalnum()))
    if not os.path.isdir(os.path.join(dest, "filmscan")):
        tmp = dest + ".tmp%d" % os.getpid()
        with zipfile.ZipFile(io.BytesIO(dati)) as z:
            for n in z.namelist():
                # solo filmscan/*.py ed elaboratore.py, nessun percorso strano
                if n.startswith("/") or ".." in n.split("/"):
                    continue
                z.extract(n, tmp)
        try:
            os.replace(tmp, dest)
        except OSError:
            pass                    # l'ha gia' fatto un altro processo
    return versione, dest


# --- il lavoro ---------------------------------------------------------------

def elabora_pacco(pacco: bytes, nome: str) -> bytes:
    import pickle
    import numpy as np
    from filmscan.acquire import RawScan
    from filmscan.pipeline import export, export_mask, export_negativo, process
    t0 = time.time()
    z = zipfile.ZipFile(io.BytesIO(pacco))
    lavoro = json.loads(z.read("lavoro.json").decode("utf-8"))
    with tempfile.TemporaryDirectory(prefix="elaboratore-") as tmp:
        with open(os.path.join(tmp, "grezzo.npz"), "wb") as f:
            f.write(z.read("grezzo.npz"))
        with open(os.path.join(tmp, "grezzo.json"), "wb") as f:
            f.write(z.read("grezzo.json"))
        raw = RawScan.load(os.path.join(tmp, "grezzo.npz"))
        # le opzioni vengono dalla macchina, che ha dimostrato la chiave
        opts = pickle.loads(z.read("opzioni.pkl"))
        res = process(raw, opts)
        esito = {"elaboratore": nome, "avvisi": list(res.warnings),
                 "rotazione": int(res.orientation.rotation) if res.orientation else 0,
                 "geometria": res.geometria, "tempi": res.tempi,
                 "maschera_pct": float(res.mask_coverage)}
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED, allowZip64=True) as r:
            if lavoro.get("tipo") == "processa":
                buf = io.BytesIO()
                np.save(buf, np.asarray(res.base, dtype=np.float16), allow_pickle=False)
                r.writestr("base.npy", buf.getvalue())
                if res.negativo is not None:
                    buf = io.BytesIO()
                    np.save(buf, np.asarray(res.negativo), allow_pickle=False)
                    r.writestr("negativo.npy", buf.getvalue())
            else:
                fmt = lavoro.get("formato") or "tiff16"
                ext = {"tiff16": ".tif", "tiff8": ".tif", "png": ".png",
                       "jpeg": ".jpg"}.get(fmt, ".tif")
                meta = lavoro.get("meta") or {}
                p = export(res.image, os.path.join(tmp, "finale" + ext), fmt,
                           int(lavoro.get("qualita") or 95), meta=meta)
                r.write(p, "file/finale")
                if lavoro.get("maschera") and res.mask is not None:
                    r.write(export_mask(res.mask, os.path.join(tmp, "maschera.png")),
                            "file/maschera")
                if lavoro.get("positivo") and res.base is not None:
                    # il negativo invertito per l'archivio di rete della macchina
                    r.write(export_negativo(np.clip(np.asarray(res.base, dtype=np.float32), 0.0, 1.0),
                                            os.path.join(tmp, "positivo.dng"), "dng", meta=meta),
                            "file/positivo")
                neg = lavoro.get("negativo") or ""
                if neg in ("tiff", "dng") and res.lineare is not None:
                    r.write(export_negativo(res.lineare, os.path.join(
                        tmp, "negativo." + ("dng" if neg == "dng" else "tif")), neg, meta=meta),
                        "file/negativo")
                from PIL import Image
                im = Image.fromarray((np.clip(res.image, 0, 1) * 255).astype(np.uint8))
                im.thumbnail((1600, 1600))
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=88)
                r.writestr("anteprima.jpg", buf.getvalue())
            esito["secondi"] = round(time.time() - t0, 2)
            r.writestr("esito.json", json.dumps(esito, default=float))
    return out.getvalue()


def _processori() -> int:
    """I processori usabili davvero: quelli assegnati al processo e, in un
    container con --cpus, la quota del cgroup."""
    try:
        n = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        n = os.cpu_count() or 2
    try:
        with open("/sys/fs/cgroup/cpu.max") as f:            # cgroup v2
            quota, periodo = f.read().split()[:2]
        if quota != "max":
            n = min(n, max(1, int(int(quota) / int(periodo))))
    except (IOError, OSError, ValueError):
        pass
    return max(1, n)


def _memoria_gb() -> float:
    """La memoria del PC (o il limite del container), in GB."""
    gb = 0.0
    try:
        gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    except (AttributeError, ValueError, OSError):
        pass
    if not gb and os.name == "nt":
        try:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                gb = m.ullTotalPhys / 1e9
        except Exception:                               # noqa: BLE001
            pass
    try:
        with open("/sys/fs/cgroup/memory.max") as f:          # container con -m
            v = f.read().strip()
        if v != "max":
            gb = min(gb or 1e9, int(v) / 1e9)
    except (IOError, OSError, ValueError):
        pass
    return gb or 4.0


def processi_di_serie() -> int:
    """Meta' dei processori, 2 GB di memoria a processo, al massimo 16. Un
    Threadripper con 64 GB: 16; un PC da ufficio con 4 core e 8 GB: 2."""
    return max(1, min(16, _processori() // 2, int(_memoria_gb() // 2)))


def figlio(args) -> int:
    conf = carica_conf()
    chiave = args.chiave or conf.get("chiave") or ""
    if not chiave:
        scrivi("manca la chiave: va in elaboratore.json (pacchetto scaricato dalla "
               "macchina) o in --chiave")
        return 2
    if args.macchina:
        conf["macchina"] = args.macchina
    nome = args.nome or ("%s-%d" % (socket.gethostname(), args.indice))
    nome = "".join(c for c in nome if c.isalnum() or c in "-_.")[:40]
    url = None
    detto = False
    while not url:
        if args.indice == 1:
            url = trova_macchina(conf, chiave, args.porta)
        else:
            # gli altri processi non scandiscono la rete: aspettano
            # l'indirizzo che trova il primo (elaboratore.json)
            conf = dict(carica_conf(), **({"macchina": args.macchina} if args.macchina else {}))
            noto = (conf.get("macchina") or "").rstrip("/")
            url = noto if noto and prova(noto, chiave) else None
        if not url:
            if not detto or args.indice == 1:
                scrivi("macchina non trovata: riprovo ogni 30 s (spenta? interfaccia "
                       "accesa? elaboratori accesi nel pannello Macchina?)")
                detto = True
            time.sleep(30)
    cartella = args.cartella or os.path.join(QUI, "cache")
    os.makedirs(cartella, exist_ok=True)
    versione, dest = scarica_codice(url, chiave, cartella)
    sys.path.insert(0, dest)
    import numpy                                        # noqa: F401 (per un errore chiaro)
    import filmscan.pipeline                            # noqa: F401
    scrivi("%s collegato a %s, codice %s" % (nome, url, versione))
    base = {"X-Chiave": chiave, "X-Nome": nome, "X-Versione": versione}
    errori = 0
    while True:
        try:
            r = _get(url + "/api/elaboratore/prossimo", base, timeout=45)
            stato = r.status
            ident = r.headers.get("X-Lavoro") or ""
            pacco = r.read() if stato == 200 else b""
            r.close()
            errori = 0
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                scrivi("la macchina ha un codice nuovo: riparto")
                return ESCI_AGGIORNA
            if exc.code in (403, 503):
                scrivi("la macchina rifiuta (%d): elaboratori spenti o chiave cambiata. "
                       "Riprovo fra 30 s." % exc.code)
                time.sleep(30)
                if not prova(url, chiave):
                    return 1        # il padre riparte e la cerca di nuovo
                continue
            errori += 1
            time.sleep(min(30, 2 * errori))
            continue
        except Exception as exc:                        # noqa: BLE001
            errori += 1
            scrivi("macchina non raggiungibile (%s)" % exc)
            if errori >= 5:
                return 1
            time.sleep(min(30, 2 * errori))
            continue
        if stato != 200 or not ident:
            continue
        scrivi("lavoro %s (%.1f MB)" % (ident, len(pacco) / 1048576.0))
        fine = threading.Event()

        def battito():
            while not fine.wait(5.0):
                try:
                    req = urllib.request.Request(url + "/api/elaboratore/vivo", data=b"",
                                                 headers=base, method="POST")
                    urllib.request.urlopen(req, timeout=5).close()
                except Exception:                       # noqa: BLE001
                    pass
        t = threading.Thread(target=battito, daemon=True)
        t.start()
        t0 = time.time()
        intest = dict(base)
        try:
            corpo = elabora_pacco(pacco, nome)
        except Exception as exc:                        # noqa: BLE001
            traceback.print_exc()
            corpo = b""
            intest["X-Errore"] = ("%s: %s" % (type(exc).__name__, exc))[:250] \
                .encode("ascii", "replace").decode("ascii")
        finally:
            fine.set()
            del pacco
        try:
            req = urllib.request.Request(
                url + "/api/elaboratore/risultato?id=" + ident, data=corpo,
                headers=dict(intest, **{"Content-Type": "application/octet-stream"}),
                method="POST")
            urllib.request.urlopen(req, timeout=120).close()
            scrivi("lavoro %s %s in %.1f s" % (ident, "FALLITO" if "X-Errore" in intest
                                                else "fatto", time.time() - t0))
        except Exception as exc:                        # noqa: BLE001
            scrivi("risultato non consegnato (%s): la macchina lo rifara'" % exc)


def padre(args) -> int:
    """Tiene in vita N processi figli: uno per fotogramma in parallelo.
    Un figlio che esce con 3 (codice nuovo sulla macchina) riparte subito;
    uno che cade riparte dopo 5 s."""
    n = max(1, int(args.processi or 0) or processi_di_serie())
    scrivi("elaboratore Masterflex: %d processi (%d processori, %.0f GB) - Ctrl+C per smettere"
           % (n, _processori(), _memoria_gb()))
    base = [sys.executable, os.path.abspath(__file__), "--figlio"]
    for k in ("macchina", "chiave", "nome", "cartella"):
        v = getattr(args, k)
        if v:
            base += ["--" + k, str(v)]
    base += ["--porta", str(args.porta)]
    figli = {}
    try:
        while True:
            for i in range(1, n + 1):
                p = figli.get(i)
                if p is None or p.poll() is not None:
                    if p is not None and p.returncode == 2:
                        return 2            # configurazione sbagliata: inutile insistere
                    if p is not None and p.returncode != ESCI_AGGIORNA:
                        time.sleep(5)
                    figli[i] = subprocess.Popen(base + ["--indice", str(i)])
                    if i == 1 and p is None:
                        time.sleep(3)       # il primo trova la macchina e scarica
            time.sleep(1)
    except KeyboardInterrupt:
        scrivi("fermo gli elaboratori")
        for p in figli.values():
            try:
                p.terminate()
            except OSError:
                pass
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Elaboratore della rete per la Masterflex D1008")
    amb = os.environ.get
    ap.add_argument("--macchina", default=amb("MASTERFLEX_MACCHINA") or None,
                    help="indirizzo dell'interfaccia, es. http://192.168.1.50:8080")
    ap.add_argument("--chiave", default=amb("MASTERFLEX_CHIAVE") or None,
                    help="la chiave degli elaboratori (pannello Macchina)")
    ap.add_argument("--nome", default=amb("MASTERFLEX_NOME") or None,
                    help="come si chiama questo elaboratore nella macchina")
    ap.add_argument("--processi", type=int, default=int(amb("MASTERFLEX_PROCESSI") or 0),
                    help="fotogrammi alla volta (di serie: meta' dei processori, "
                         "2 GB di memoria l'uno, al massimo 16)")
    ap.add_argument("--porta", type=int, default=PORTA)
    ap.add_argument("--cartella", help="dove tenere il codice scaricato")
    ap.add_argument("--figlio", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--indice", type=int, default=1, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.figlio:
        if args.nome and args.indice > 1:
            args.nome = "%s-%d" % (args.nome, args.indice)
        try:
            return figlio(args)
        except KeyboardInterrupt:
            return 0
    return padre(args)


if __name__ == "__main__":
    sys.exit(main())
