# -*- coding: utf-8 -*-
"""
Collaudi dello scanner vero, attraverso l'agente (ponte/agente.py).

Servono a rispondere con dei numeri alle domande aperte sulla riserva: le
immagini arrivano giuste? i canali sono quelli che crediamo? i motori vanno
nel verso e della quantita' che crediamo? Ogni collaudo scrive un rapporto e
le immagini in collaudi/<tipo>-<data>/, da allegare alla segnalazione.

    python collaudo_scanner.py colori   [--ponte C:\\MasterflexD\\ponte]
        R, G e B (e IR con --ir) a piu' esposizioni. Misura livelli,
        saturazione, rumore, righe e colonne anomale, bordi neri, linearita'
        con l'esposizione, e se i canali sono davvero diversi fra loro.
        Salva un'immagine per canale e il composito a colori.
        Non muove motori.

    python collaudo_scanner.py sensori  [--durata 120]
        Trascinamento A MANO: si fa scorrere una striscia con le mani nel
        percorso, e intanto si leggono tutti gli ingressi digitali. Dice quale
        ingresso cambia e quando: e' cosi' che si mappano i sensori
        (piano dei lavori 3.12). Non muove motori.

    python collaudo_scanner.py passi    [--passi 20]
        Trascinamento COMANDATO: piccoli movimenti della regolazione fine,
        con lo spostamento misurato sull'immagine per correlazione di fase.
        Dice verso, scala (mm per passo) e gioco al ritorno (piano 3.13).
        Muove il trasporto: serve una striscia di prova nella finestra.

L'agente deve essere in esecuzione (MASTERFLEX.bat, voce 1, 2 o 3).

Dal 31/10/2026 il menu ha solo i collaudi automatici (collaudo_auto.py),
che usano da qui Rapporto, misura e salva_png. Queste prove manuali restano
lanciabili da riga di comando, per i casi in cui serve una persona davanti.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
import time
import traceback

import numpy as np

from filmscan.hal import PonteSdkBackend, ScannerError

QUI = os.path.dirname(os.path.abspath(__file__))
PONTE = r"C:\MasterflexD\ponte"

# Dimensioni attese delle letture grezze (scan_raw) per ImageResolution 0..4:
# il fotogramma di 2100x1400 ridotto di 2^r per lato (ponte/sdk.py,
# DIMENSIONI_GREZZE). Non sono quelle di capture(): 1052x700, 524x350...
DIMENSIONI = {0: (2100, 1400), 1: (1050, 700), 2: (525, 350),
              3: (262, 175), 4: (131, 87)}
# Pixel per mm sul 135 a piena risoluzione: 2100 pixel per 36 mm,
# cioe' i 1569 dpi dell'SDK (vedi config.py).
PX_PER_MM_PIENA = 1569 / 25.4


class Rapporto(object):
    """Scrive a schermo e in rapporto.txt; tiene i numeri per rapporto.json."""

    # L'ultimo rapporto aperto: main() ci scrive l'errore che ferma il
    # collaudo. Prima l'errore finiva solo a schermo e il rapporto restava
    # con la sola intestazione, inutile da allegare.
    aperto = None

    def __init__(self, tipo):
        data = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        # nella cartella dei registri, con il resto (filmscan/registri.py)
        from filmscan import registri
        self.cartella = os.path.join(registri.sottocartella("collaudi"),
                                     "%s-%s" % (tipo, data))
        os.makedirs(self.cartella)
        self._f = open(os.path.join(self.cartella, "rapporto.txt"), "w",
                       encoding="utf-8")
        self.dati = {"tipo": tipo, "data": data}
        self.avvisi = []
        Rapporto.aperto = self

    def __call__(self, testo=""):
        print(testo)
        self._f.write(testo + "\n")
        self._f.flush()

    def avviso(self, testo):
        self.avvisi.append(testo)
        self("  ATTENZIONE: " + testo)

    def chiudi(self):
        self("")
        if self.avvisi:
            self("RIEPILOGO: %d punti da guardare" % len(self.avvisi))
            for a in self.avvisi:
                self("  - " + a)
        else:
            self("RIEPILOGO: niente di anomalo nei controlli fatti.")
        self.dati["avvisi"] = self.avvisi
        with open(os.path.join(self.cartella, "rapporto.json"), "w",
                  encoding="utf-8") as f:
            json.dump(self.dati, f, indent=2, ensure_ascii=False)
        self._f.close()
        Rapporto.aperto = None
        print("")
        print("Rapporto e immagini in:")
        print("  %s" % self.cartella)


def collega(ponte, risoluzione=0, ir=False, r=print):
    b = PonteSdkBackend(ponte, risoluzione=risoluzione, ir=ir)
    print("Collegamento all'agente. Se lo scanner si e' appena aperto, l'SDK")
    print("impiega circa 40 secondi a essere pronto: si aspetta qui.")
    b.connect()
    info = b._info
    r("Agente: scanner %s, tipo %s, motori %s%s"
      % (info.get("id", "?"), info.get("tipo_scanner", "?"),
         "attivi" if info.get("motori", True) else "BLOCCATI",
         ", SIMULAZIONE" if info.get("simulato") else ""))
    if info.get("simulato"):
        r("  (agente simulato: i numeri servono solo a provare il collaudo)")
    for voce, errore in sorted((info.get("errori") or {}).items()):
        r("  ATTENZIONE, stato '%s' non letto: %s" % (voce, errore))
    return b


def chiedi(domanda, scelte=None):
    while True:
        risposta = input(domanda).strip()
        if scelte is None or risposta.lower() in scelte:
            return risposta.lower()


def salva_png(arr, percorso):
    """PNG a meta' lato e con compressione minima: sono immagini da guardare,
    i numeri veri stanno nei .raw dell'agente e in rapporto.json. A piena
    risoluzione e compressione di serie, sulla CPU della macchina, il
    salvataggio durava tanto da sembrare bloccato (26/10/2026)."""
    from PIL import Image
    im = Image.fromarray(arr)
    im = im.resize((max(1, im.width // 2), max(1, im.height // 2)))
    im.save(percorso, compress_level=1)
    return percorso


def a8bit(x, alto):
    return (np.clip(x / max(float(alto), 1.0), 0, 1) * 255).astype(np.uint8)


# --------------------------------------------------------------------------
# Colori e intensita'
# --------------------------------------------------------------------------

def misura(img):
    """I numeri di un fotogramma grezzo a 16 bit."""
    x = img.astype(np.float64)
    col = x.mean(axis=0)
    rig = x.mean(axis=1)
    med_col = float(np.median(col)) or 1.0
    # Rumore: dalla differenza fra righe vicine, che toglie l'immagine e
    # lascia il rumore (diviso radice di 2). Mediana degli scarti assoluti,
    # cosi' i bordi dell'immagine non contano.
    d = np.diff(x, axis=0)
    rumore = float(np.median(np.abs(d - np.median(d))) * 1.4826 / np.sqrt(2))
    # Bordi neri: colonne all'inizio e alla fine molto piu' scure del resto.
    scure = col < 0.05 * med_col
    sx = int(np.argmax(~scure)) if not scure.all() else len(col)
    dx = int(np.argmax(~scure[::-1])) if not scure.all() else len(col)
    fondo = 4095.0 if float(x.max()) <= 4095 else 65535.0
    return {
        "larghezza": int(x.shape[1]), "altezza": int(x.shape[0]),
        "minimo": float(x.min()), "massimo": float(x.max()),
        "media": float(x.mean()),
        "p1": float(np.percentile(x, 1)), "p50": float(np.percentile(x, 50)),
        "p99": float(np.percentile(x, 99)),
        # L'SDK restituisce 12 bit (0..4095), non 16: la soglia dei 65000
        # dava 0% di saturati anche con mezzo fotogramma a 4095 (riserva,
        # 31/10/2026). Il fondo scala si deduce dai dati.
        "fondo_scala": fondo,
        "saturati_pct": float((x >= fondo).mean() * 100),
        "neri_pct": float((x <= 300).mean() * 100),
        "rumore": rumore,
        # Righe verticali: variazione fra colonne; orizzontali: fra righe.
        "colonne_var_pct": float(np.std(col) / med_col * 100),
        "righe_var_pct": float(np.std(rig) / (float(np.median(rig)) or 1.0) * 100),
        "colonne_morte": int((col < 0.10 * med_col).sum() - sx - dx
                             if not scure.all() else 0),
        "colonne_calde": int((col > 3.0 * med_col).sum()),
        "bordo_nero_sx": sx, "bordo_nero_dx": dx,
    }


def cmd_colori(a):
    r = Rapporto("colori")
    r("COLLAUDO COLORI E INTENSITA' - %s" % datetime.datetime.now())
    b = collega(a.ponte, a.risoluzione, a.ir, r)
    canali = [c for c in a.canali.split(",") if c]
    esposizioni = [float(e) for e in a.esposizioni.split(",")]
    r("Canali %s, esposizioni %s, risoluzione SDK %d %s"
      % (",".join(canali), ", ".join("%.2gx" % e for e in esposizioni),
         a.risoluzione, DIMENSIONI.get(a.risoluzione)))
    print("")
    print("Cosa c'e' nella finestra di scansione?")
    print("  1  niente: luce piena (consigliato per il primo collaudo)")
    print("  2  un negativo o una striscia di prova")
    print("  3  una diapositiva o un target a colori")
    contenuto = {"1": "vuota", "2": "negativo", "3": "positivo"}[
        chiedi("Scelta [1/2/3]: ", ("1", "2", "3"))]
    r("Finestra: %s" % contenuto)
    r.dati.update(canali=canali, esposizioni=esposizioni, finestra=contenuto,
                  risoluzione=a.risoluzione, misure={})

    immagini = {}
    for ch in canali:
        for e in esposizioni:
            t0 = time.time()
            try:
                img = b.capture_line_block(ch, 1 << 20, exposure=e)
            except ScannerError as exc:
                r.avviso("%s %.2gx: acquisizione fallita: %s" % (ch, e, exc))
                continue
            m = misura(img)
            m["secondi"] = round(time.time() - t0, 1)
            r.dati["misure"]["%s@%g" % (ch, e)] = m
            immagini[(ch, e)] = img
            r("%-2s %5.2gx  %4dx%-4d  media %7.0f  p1 %6.0f  p99 %6.0f  "
              "sat %5.2f%%  neri %5.2f%%  rumore %6.1f  col %5.2f%%  righe %5.2f%%  (%.1fs)"
              % (ch, e, m["larghezza"], m["altezza"], m["media"], m["p1"], m["p99"],
                 m["saturati_pct"], m["neri_pct"], m["rumore"],
                 m["colonne_var_pct"], m["righe_var_pct"], m["secondi"]))

    if not immagini:
        r.avviso("nessuna acquisizione riuscita: controllare l'agente e il "
                 "suo registro (cartella log del ponte)")
        r.chiudi()
        return 1

    # --- controlli -------------------------------------------------------
    r("")
    r("Controlli")
    attese = DIMENSIONI.get(a.risoluzione)
    for (ch, e), img in sorted(immagini.items()):
        m = r.dati["misure"]["%s@%g" % (ch, e)]
        tag = "%s %.2gx" % (ch, e)
        if attese and (m["larghezza"], m["altezza"]) != attese:
            r.avviso("%s: dimensioni %dx%d invece di %dx%d (immagine tagliata "
                     "o risoluzione sbagliata)" % (tag, m["larghezza"],
                                                   m["altezza"], *attese))
        if m["massimo"] - m["minimo"] < 50:
            r.avviso("%s: immagine piatta (min %.0f, max %.0f): nessun segnale. "
                     "Lampada, DMA o ISR del driver" % (tag, m["minimo"], m["massimo"]))
        elif m["neri_pct"] > 90:
            r.avviso("%s: quasi tutta nera (%.0f%%)" % (tag, m["neri_pct"]))
        if m["bordo_nero_sx"] + m["bordo_nero_dx"] > 0.05 * m["larghezza"]:
            r.avviso("%s: bordi neri di %d e %d colonne: immagine spostata o "
                     "fotogramma fuori posizione" % (tag, m["bordo_nero_sx"],
                                                     m["bordo_nero_dx"]))
        if contenuto == "vuota":
            if m["colonne_var_pct"] > 15:
                r.avviso("%s: le colonne variano del %.0f%% a luce piena: righe "
                         "verticali (CCD o calibrazione)" % (tag, m["colonne_var_pct"]))
            if m["righe_var_pct"] > 5:
                r.avviso("%s: le righe variano del %.0f%% a luce piena: righe "
                         "orizzontali (lampada che sfarfalla o trasferimento "
                         "DMA a pezzi)" % (tag, m["righe_var_pct"]))
        if m["colonne_morte"] > 0 or m["colonne_calde"] > 0:
            r.avviso("%s: %d colonne morte e %d calde" % (tag, m["colonne_morte"],
                                                          m["colonne_calde"]))

    # linearita' con l'esposizione
    r("")
    r("Linearita' con l'esposizione (rapporto delle medie, atteso = rapporto dei tempi)")
    for ch in canali:
        punti = [(e, r.dati["misure"]["%s@%g" % (ch, e)]["media"])
                 for e in esposizioni if (ch, e) in immagini
                 and r.dati["misure"]["%s@%g" % (ch, e)]["saturati_pct"] < 1]
        if len(punti) < 2:
            continue
        e0, m0 = punti[0]
        rapporti = ["%.2gx -> %.2f (atteso %.2f)" % (e, m / m0 if m0 else 0, e / e0)
                    for e, m in punti[1:]]
        r("  %s: %s" % (ch, "; ".join(rapporti)))
        ultimo_e, ultimo_m = punti[-1]
        if m0 and abs((ultimo_m / m0) / (ultimo_e / e0) - 1) > 0.35:
            r.avviso("%s: il segnale non segue l'esposizione (%.2f invece di %.2f): "
                     "il moltiplicatore di ExposureTime non ha l'effetto atteso"
                     % (ch, ultimo_m / m0, ultimo_e / e0))

    # i canali sono davvero diversi?
    rif = esposizioni[min(range(len(esposizioni)),
                          key=lambda i: abs(esposizioni[i] - 1.0))]
    presenti = [c for c in canali if (c, rif) in immagini]
    for i, c1 in enumerate(presenti):
        for c2 in presenti[i + 1:]:
            x, y = immagini[(c1, rif)], immagini[(c2, rif)]
            if x.shape == y.shape and np.array_equal(x, y):
                r.avviso("%s e %s sono identici byte per byte: il numero di "
                         "canale passato all'SDK viene ignorato" % (c1, c2))
    if contenuto == "vuota" and all(c in presenti for c in "RGB"):
        medie = dict((c, r.dati["misure"]["%s@%g" % (c, rif)]["media"]) for c in "RGB")
        r("")
        r("Bilanciamento a luce piena (%.2gx): R %.0f  G %.0f  B %.0f"
          % (rif, medie["R"], medie["G"], medie["B"]))
        r("  Sono le intensita' della lampada nei tre filtri: vanno annotate.")

    # --- immagini --------------------------------------------------------
    for (ch, e), img in immagini.items():
        salva_png(a8bit(img.astype(np.float64), np.percentile(img, 99.5)),
                  os.path.join(r.cartella, "%s_%gx.png" % (ch, e)))
    if all((c, rif) in immagini for c in "RGB"):
        piani = [immagini[(c, rif)].astype(np.float64) for c in "RGB"]
        if len(set(p.shape for p in piani)) == 1:
            # intensita' vere: stessa scala per i tre canali
            alto = max(np.percentile(p, 99.5) for p in piani)
            salva_png(np.dstack([a8bit(p, alto) for p in piani]),
                      os.path.join(r.cartella, "composito_intensita_vere.png"))
            # colori bilanciati: ogni canale sulla sua scala
            salva_png(np.dstack([a8bit(p, np.percentile(p, 99.5)) for p in piani]),
                      os.path.join(r.cartella, "composito_bilanciato.png"))
            r("")
            r("Composito a colori salvato (intensita' vere e bilanciato).")
            if contenuto == "negativo":
                r("  Un negativo appare arancione e invertito: e' normale.")
    r.chiudi()
    return 0


# --------------------------------------------------------------------------
# Trascinamento a mano: lettura dei sensori
# --------------------------------------------------------------------------

def cmd_sensori(a):
    r = Rapporto("sensori")
    r("TRASCINAMENTO A MANO - LETTURA DEI SENSORI - %s" % datetime.datetime.now())
    b = collega(a.ponte, r=r)
    print("")
    print("Durante la prova nessun motore viene comandato.")
    print("Far scorrere a mano una striscia di prova nel percorso, lentamente,")
    print("dall'ingresso verso l'uscita e poi indietro. Ogni ingresso che cambia")
    print("viene scritto con l'ora. Ctrl+C per finire prima.")
    chiedi("Invio per iniziare...")
    prima = None
    cambi = {}
    fine = time.time() + a.durata
    try:
        while time.time() < fine:
            try:
                stato = json.loads(b._send("sensori"))
            except (ScannerError, ValueError) as exc:
                r.avviso("lettura dei sensori fallita: %s" % exc)
                break
            if prima is None:
                r("Stato iniziale: " + ", ".join(
                    "%s=%s" % (k, {True: "1", False: "0", None: "?"}[v])
                    for k, v in sorted(stato.items(), key=lambda kv: int(kv[0]))))
            else:
                for k, v in stato.items():
                    if prima.get(k) != v:
                        cambi[k] = cambi.get(k, 0) + 1
                        r("%s  ingresso %2s: %s -> %s"
                          % (time.strftime("%H:%M:%S"), k, prima.get(k), v))
            prima = stato
            time.sleep(a.intervallo)
    except KeyboardInterrupt:
        r("(interrotto)")
    r("")
    if cambi:
        r("Ingressi che sono cambiati (numero di cambi):")
        for k in sorted(cambi, key=int):
            r("  ingresso %s: %d" % (k, cambi[k]))
        r("Quelli che cambiano quando la striscia passa davanti al sensore")
        r("d'ingresso e a quello d'uscita vanno scritti in impostazioni.json:")
        r('  "sdk_ingressi": {"ingresso": N, "uscita": M}')
    else:
        r.avviso("nessun ingresso e' cambiato: o la striscia non e' passata "
                 "davanti ai sensori, o i sensori non sono fra gli ingressi "
                 "0-15 di diag_input")
    r.dati["cambi"] = cambi
    r.chiudi()
    return 0


# --------------------------------------------------------------------------
# Trascinamento comandato: verso e scala della regolazione fine
# --------------------------------------------------------------------------

def cmd_passi(a):
    from filmscan.registration import phase_shift, _prep
    r = Rapporto("passi")
    r("TRASCINAMENTO COMANDATO - VERSO E SCALA - %s" % datetime.datetime.now())
    b = collega(a.ponte, a.risoluzione, r=r)
    if b._info.get("motori") is False:
        r.avviso("l'agente ha i motori bloccati: avviarlo con la voce 1 del menu")
        r.chiudi()
        return 1
    passi = max(1, min(a.passi, 100))
    piano = [passi, -passi, 2 * passi, -2 * passi] * a.ripetizioni
    print("")
    print("Serve una striscia di prova (non il rullo di un cliente) nella finestra,")
    print("con un'immagine qualunque. Saranno fatti %d movimenti di %d-%d passi"
          % (len(piano), passi, 2 * passi))
    print("(%.2f-%.2f mm), tornando ogni volta al punto di partenza." % (
        passi / 100.0, passi / 50.0))
    print("Mano sull'interruttore: se si sente spingere, spegnere.")
    if chiedi("Procedo? [s/n] ", ("s", "n")) != "s":
        r("Annullato.")
        r.chiudi()
        return 1

    px_mm = PX_PER_MM_PIENA * DIMENSIONI[a.risoluzione][0] / 2100.0
    r("Scala dell'immagine: %.1f pixel per mm" % px_mm)

    def foto():
        return _prep(b.capture_line_block("G", 1 << 20).astype(np.float32))

    rif = foto()
    posizione = 0
    misure = []
    for n in piano:
        try:
            b.advance(n * 0.01)
        except ScannerError as exc:
            r.avviso("movimento di %d passi rifiutato o fallito: %s" % (n, exc))
            break
        posizione += n
        dy, dx, fiducia = phase_shift(rif, foto(), return_confidence=True)
        lungo = dx if abs(dx) >= abs(dy) else dy
        r("dopo %+4d passi (posizione %+4d): spostamento %+7.2f px in x, %+7.2f px "
          "in y (affidabilita' %.2f)" % (n, posizione, dx, dy, fiducia))
        misure.append({"passi": n, "posizione": posizione, "dx": dx, "dy": dy,
                       "fiducia": fiducia})
    r.dati["misure"] = misure

    andate = [m for m in misure if m["posizione"] != 0]
    ritorni = [m for m in misure if m["posizione"] == 0]
    if andate:
        asse = "dx" if np.mean([abs(m["dx"]) for m in andate]) >= \
            np.mean([abs(m["dy"]) for m in andate]) else "dy"
        scale = [m[asse] / m["posizione"] / px_mm for m in andate]
        mm_passo = float(np.median(scale))
        r("")
        r("Asse del trasporto nell'immagine: %s" % ("orizzontale" if asse == "dx" else "verticale"))
        r("Spostamento per passo: %.4f mm (nominale 0,0100)" % abs(mm_passo))
        r("Verso: passi positivi spostano l'immagine verso %s"
          % (("destra" if mm_passo > 0 else "sinistra") if asse == "dx"
             else ("il basso" if mm_passo > 0 else "l'alto")))
        r.dati.update(asse=asse, mm_per_passo=mm_passo)
        if abs(mm_passo) < 0.002:
            r.avviso("l'immagine non si e' mossa: il comando non muove il "
                     "trasporto, oppure la finestra e' vuota")
        elif abs(abs(mm_passo) - 0.01) > 0.003:
            r.avviso("scala diversa dal nominale: %.4f mm per passo invece di "
                     "0,0100" % abs(mm_passo))
    if ritorni:
        gioco = max(abs(m["dx"]) + abs(m["dy"]) for m in ritorni) / px_mm
        r("Errore massimo al ritorno al punto di partenza: %.3f mm (gioco)" % gioco)
        r.dati["gioco_mm"] = gioco
        if gioco > 0.05:
            r.avviso("gioco al ritorno di %.2f mm: oltre 5 centesimi" % gioco)
    if any(m["fiducia"] < 0.3 for m in misure):
        r.avviso("correlazione poco affidabile in alcune misure: serve "
                 "un'immagine con dettagli nella finestra")
    r.chiudi()
    return 0


# --------------------------------------------------------------------------

def cmd_pressore(a):
    """Il pressore della pellicola (gi_clamp, richieste Gretag 19 e 20), da
    solo e attorno a una lettura. Sulla riserva, 31/10/2026, col pressore
    automatico scendeva e poi il "su" dava errore: qui si vede quale, e se
    dopo una lettura il pressore si rialza da solo (nel qual caso il nostro
    "su" chiederebbe di alzare un pressore gia' alzato).

    Non muove la pellicola. L'operatore guarda il pressore e risponde."""
    r = Rapporto("pressore")
    r("PROVA DEL PRESSORE - %s" % datetime.datetime.now())
    b = collega(a.ponte, a.risoluzione, False, r)
    r.dati["passi"] = []
    print("")
    print("Serve una striscia nel carrello (Carica, poi inserirla), cosi' il")
    print("pressore lavora come in una scansione. Tenere d'occhio il pressore:")
    print("a ogni passo si chiede cosa ha fatto.")
    chiedi("Invio quando e' pronta: ")

    def comando(nome, fn):
        t0 = time.time()
        try:
            fn()
            esito = "ok"
        except ScannerError as exc:
            esito = "ERRORE: %s" % exc
        r("  %-38s %s (%.1f s)" % (nome, esito, time.time() - t0))
        r.dati["passi"].append({"passo": nome, "esito": esito})
        if esito != "ok":
            r.avvisi.append("%s: %s" % (nome, esito))
        return esito == "ok"

    def guarda(domanda):
        risposta = chiedi("  %s [s/n] " % domanda, ("s", "n"))
        r("  %-38s %s" % (domanda, "si'" if risposta == "s" else "no"))
        r.dati["passi"].append({"domanda": domanda, "risposta": risposta})
        return risposta == "s"

    r("")
    r("1. Partenza")
    guarda("Il pressore e' su adesso?")
    comando("su, con il pressore gia' su", lambda: b.pressore(False))
    r("")
    r("2. Giu' e su, senza leggere")
    comando("giu'", lambda: b.pressore(True))
    guarda("E' sceso?")
    comando("su", lambda: b.pressore(False))
    guarda("E' salito?")
    r("")
    r("3. Giu', una lettura, su")
    comando("giu'", lambda: b.pressore(True))
    comando("lettura del verde (grab)",
            lambda: b.capture_line_block("G", 1 << 20, exposure=1.0))
    da_solo = guarda("Dopo la lettura e' gia' salito da solo?")
    comando("su, dopo la lettura", lambda: b.pressore(False))
    guarda("Adesso e' su?")
    r("")
    if da_solo:
        r("CONCLUSIONE: la lettura rialza il pressore da sola. Il nostro 'su'")
        r("dopo la lettura va tolto o reso tollerante: mandare la cartella.")
    else:
        r("CONCLUSIONE: dopo la lettura il pressore resta giu' e lo alza il")
        r("nostro 'su'. Se il 'su' ha dato errore, il codice e' qui sopra.")
    print("")
    print("Se il pressore e' rimasto giu': interfaccia, Macchina > Alza pressore,")
    print("prima di muovere la pellicola.")
    r.chiudi()
    return 0


def cmd_sdk(a):
    """Le sonde dell'SDK, una volta, con una striscia di prova (meglio un
    negativo con un po' di polvere): codice DX, percorso "capture" del
    programma originale con e senza Digital ICE, Digital ICE chiamato
    direttamente. Muovono la pellicola come una scansione dell'originale:
    agente a motori attivi.

    Servono a chiudere i formati che dal disassemblato non si leggono con
    certezza (FrameInfo, buffer ASF/ICE, posizione del DX): i file finiscono
    nel rapporto, da mandare."""
    r = Rapporto("sdk")
    r("SONDE DELL'SDK - %s" % datetime.datetime.now())
    b = collega(a.ponte, a.risoluzione, True, r)
    print("")
    print("Nel carrello serve una striscia di negativo (meglio con un po' di")
    print("polvere), caricata con Carica come per una scansione.")
    chiedi("Invio quando e' pronta: ")
    # Senza pellicola il DX risponde 12 (ERR_NO_FILM_LOADED) e il capture
    # aspetta una striscia che non arriva: sulla riserva, 31/10/2026, il
    # rapporto si e' fermato li'. Se l'SDK non la vede, si arma il carrello
    # (come Carica) e si aspetta che la prenda.
    if not b.pellicola_sdk():
        try:
            b.prepara_carico("135-full")
            print("Carrello pronto: inserire la striscia finche' il carrello la prende.")
        except ScannerError as exc:
            r.avviso("carrello non armato: %s" % exc)
        fine = time.time() + 180.0
        while time.time() < fine and not b.pellicola_sdk():
            time.sleep(0.5)
    if not b.pellicola_sdk():
        r.avviso("nessuna pellicola nel carrello secondo l'SDK dopo 3 minuti: "
                 "le sonde servono con una striscia caricata, non le faccio")
        r.chiudi()
        return 1
    r("Pellicola nel carrello: si parte.")
    prove = [("codice DX", "dx", 15.0),
             ("capture senza ICE", "sonda_capture_%d_0" % a.risoluzione, 120.0),
             ("capture con ICE (ASF)", "sonda_capture_%d_1" % a.risoluzione, 120.0),
             ("Digital ICE diretto", "sonda_ice_%d" % a.risoluzione, 180.0)]
    r.dati["sonde"] = {}
    for nome, comando, attesa in prove:
        r("")
        r("== %s (%s)" % (nome, comando))
        t0 = time.time()
        try:
            risposta = b._send(comando, attesa=attesa)
            try:
                dati = json.loads(risposta)
            except ValueError:
                dati = risposta
            r.dati["sonde"][comando] = dati
            r("  fatto in %.1f s" % (time.time() - t0))
            r("  " + json.dumps(dati)[:1500])
        except ScannerError as exc:
            r.avviso("%s non riuscita: %s" % (nome, exc))
    # i file delle sonde, dalla cartella del ponte al rapporto
    sorgente = os.path.join(a.ponte, "raw")
    copiati = 0
    for nome in sorted(os.listdir(sorgente)) if os.path.isdir(sorgente) else []:
        if nome.startswith("sonda_"):
            try:
                shutil.copy2(os.path.join(sorgente, nome), os.path.join(r.cartella, nome))
                copiati += 1
            except (IOError, OSError) as exc:
                r.avviso("%s non copiato: %s" % (nome, exc))
    r("")
    r("%d file delle sonde nel rapporto. Mandare tutta la cartella." % copiati)
    r.chiudi()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Collaudi dello scanner vero")
    ap.add_argument("prova", choices=("colori", "sensori", "passi", "sdk", "pressore"))
    ap.add_argument("--ponte", default=PONTE)
    ap.add_argument("--risoluzione", type=int, default=None, choices=range(5),
                    help="ImageResolution dell'SDK (0 = 2100x1400)")
    ap.add_argument("--canali", default="R,G,B")
    ap.add_argument("--ir", action="store_true", help="prova anche il canale IR (3)")
    # a finestra vuota l'SDK satura gia' a 0,5x (riserva, 31/10/2026): le
    # esposizioni basse servono a vedere il profilo vero della luce
    ap.add_argument("--esposizioni", default="0.125,0.25,0.5,1,2")
    ap.add_argument("--durata", type=float, default=120.0)
    ap.add_argument("--intervallo", type=float, default=0.3)
    ap.add_argument("--passi", type=int, default=20)
    ap.add_argument("--ripetizioni", type=int, default=2)
    a = ap.parse_args(argv)
    if a.ir and "IR" not in a.canali:
        a.canali += ",IR"
    if a.risoluzione is None:
        a.risoluzione = 0 if a.prova in ("colori", "sdk") else 1
    try:
        return {"colori": cmd_colori, "sensori": cmd_sensori,
                "passi": cmd_passi, "sdk": cmd_sdk,
                "pressore": cmd_pressore}[a.prova](a)
    except Exception as exc:
        if isinstance(exc, ScannerError):
            testo = "ERRORE: %s" % exc
        else:
            testo = "ERRORE INATTESO:\n" + traceback.format_exc()
        r = Rapporto.aperto
        if r is not None:
            r("")
            r(testo)
            r.avviso("collaudo interrotto")
            r.dati["errore"] = str(exc)
            r.chiudi()
        else:
            print("")
            print(testo)
        return 1


if __name__ == "__main__":
    sys.exit(main())
