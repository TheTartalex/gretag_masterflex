# -*- coding: utf-8 -*-
"""
Alleggerisce il software originale e gli mette dentro il nostro collegamento.

COSA FA, E PERCHE' COSI'
------------------------
Il programma originale e' un'applicazione MFC del 2001: non ha estensioni, non
ha plugin, e i suoi pulsanti stanno dentro le risorse di un eseguibile da 8 MB.
Modificare quel binario si puo', ma sarebbe fragile e irreversibile.

Non serve. Le due cose che ci interessano hanno un interruttore nei file di
configurazione, e sono percio' **reversibili**:

  stampa        Products.ini elenca 24 prodotti, tutti con Target=Paper: sono
                tutti lavori di stampa. Ognuno ha Defined=1. Messo a 0, il
                prodotto sparisce dall'elenco che l'operatore puo' ordinare.

  densitometro  Daily_Factory.ini ha ColorCalibrationEnabled: e' la
                calibrazione colore giornaliera, cioe' la routine
                densitometrica. Messa a false, non parte piu'; e allungando
                CalDueCheckInterval il programma smette anche di chiederla.

Volutamente NON si toccano le voci di SWComponent_Factory.ini: PhxStartup
cerca i componenti per ProgId e ha messaggi d'errore pronti del tipo "Cannot
get ProgID of PrintServer". Togliere un componente vuol dire rischiare che il
programma non parta piu' - e a quel punto non ci sarebbe piu' niente da
alleggerire.

  collegamento  Il visualizzatore di aiuto (PhxHelp.exe) mostra pagine HTML
                prese da una cartella. E' l'unico punto del programma
                originale che apre un contenuto che possiamo scrivere noi.
                Ci mettiamo una pagina con l'indirizzo della nostra
                interfaccia, che l'operatore raggiunge dal pulsante Aiuto.

DA SAPERE
---------
I due programmi non vanno usati insieme. Il driver non lo impedisce - la
scheda e' aperta con Exclusive=FALSE, l'ho verificato dentro EKframe.sys - ma
sotto c'e' un solo motore DMA e una sola coda di comandi: due padroni che
mandano comandi alla stessa scheda si pestano i piedi a meta' scansione.
Prima si chiude uno, poi si apre l'altro.

USO
    python originale.py --stato
    python originale.py --alleggerisci
    python originale.py --ripristina
    python originale.py --collegamento http://192.168.1.42:8080/?t=xxxx
"""

from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import sys
import time

CARTELLA_INI = r"C:\MasterflexD\IniFiles"
CARTELLA_AIUTO = os.environ.get("MASTERFLEX_AIUTO",
                               r"C:\MasterflexD\PhxHelp\Data")
NOME_PAGINA = "Interfaccia scansione.html"


# ---------------------------------------------------------------------------
# utilita'
# ---------------------------------------------------------------------------

def _leggi(percorso: str) -> str:
    with io.open(percorso, encoding="latin-1") as f:
        return f.read()


def _scrivi(percorso: str, testo: str) -> None:
    with io.open(percorso, "w", encoding="latin-1") as f:
        f.write(testo)


def _salvataggio(percorso: str) -> str:
    """Copia di sicurezza con la data, una volta sola per giornata di lavoro.

    Il nome porta la data e l'ora: se qualcosa va storto si sa sempre da dove
    ripartire, e non si sovrascrive il salvataggio buono con uno gia' guasto.
    """
    base = percorso + ".prima-di-noi"
    if not os.path.exists(base):
        shutil.copy2(percorso, base)
        return base
    datato = "%s.%s.bak" % (percorso, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(percorso, datato)
    return datato


# ---------------------------------------------------------------------------
# stampa
# ---------------------------------------------------------------------------

def prodotti(testo: str):
    """Elenco dei prodotti: (sezione, nome, destinazione, attivo)."""
    fuori = []
    for blocco in re.split(r"\n(?=\[)", testo):
        sez = re.search(r"^\[(\w+)\]", blocco)
        if not sez:
            continue
        nome = re.search(r"^Name=(.*)$", blocco, re.M)
        dest = re.search(r"^Target=(.*)$", blocco, re.M)
        attivo = re.search(r"^Defined=\s*(\d+)", blocco, re.M)
        fuori.append((sez.group(1),
                      nome.group(1).strip() if nome else "",
                      dest.group(1).strip() if dest else "",
                      attivo.group(1) == "1" if attivo else False))
    return fuori


def togli_stampa(cartella: str, riattiva: bool = False) -> int:
    percorso = os.path.join(cartella, "Products.ini")
    if not os.path.exists(percorso):
        print("  Products.ini non trovato in %s" % cartella)
        return 0
    testo = _leggi(percorso)
    _salvataggio(percorso)

    valore = "1" if riattiva else "0"
    cambiati = [0]

    def sostituisci(m):
        cambiati[0] += 1
        return "Defined=" + valore

    nuovo = re.sub(r"Defined=\s*[01]", sostituisci, testo)
    _scrivi(percorso, nuovo)
    return cambiati[0]


# ---------------------------------------------------------------------------
# densitometro (calibrazione colore giornaliera)
# ---------------------------------------------------------------------------

def togli_densitometro(cartella: str, riattiva: bool = False) -> bool:
    percorso = os.path.join(cartella, "Daily_Factory.ini")
    if not os.path.exists(percorso):
        print("  Daily_Factory.ini non trovato in %s" % cartella)
        return False
    testo = _leggi(percorso)
    _salvataggio(percorso)

    valore = "true" if riattiva else "false"
    nuovo, n = re.subn(r"(ColorCalibrationEnabled\s*=\s*)\w+",
                       r"\g<1>" + valore, testo)
    if not n:
        nuovo = nuovo.rstrip() + "\n\n[ColorCalibration]\nColorCalibrationEnabled=%s\n" % valore

    # e smettila anche di chiedere quando scade la calibrazione:
    # 1200000 ms sono venti minuti, 86400000 sono ventiquattr'ore
    atteso = "1200000" if riattiva else "86400000"
    nuovo = re.sub(r"(CalDueCheckInterval\s*=\s*)\d+", r"\g<1>" + atteso, nuovo)
    _scrivi(percorso, nuovo)
    return True


# ---------------------------------------------------------------------------
# il collegamento dentro il programma originale
# ---------------------------------------------------------------------------

PAGINA = u"""<html><head>
<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">
<title>Interfaccia di scansione</title>
<style type="text/css">
body{font-family:Tahoma,Arial,sans-serif;background:#e8e8e8;color:#202020;
 margin:0;padding:24px}
.riquadro{background:#ffffff;border:1px solid #a0a0a0;padding:22px;width:640px}
h1{font-size:20px;margin:0 0 4px}
p.sotto{color:#606060;margin:0 0 18px;font-size:12px}
a.tasto{display:block;background:#c8763c;color:#ffffff;text-decoration:none;
 font-size:17px;font-weight:bold;text-align:center;padding:16px;
 border:1px solid #9a5628;margin-bottom:16px}
.indirizzo{font-family:Consolas,'Courier New',monospace;font-size:19px;
 background:#f4f0ec;border:1px solid #d0c8c0;padding:12px;text-align:center;
 word-wrap:break-word}
table.note{font-size:12px;color:#404040;margin-top:16px;border-collapse:collapse}
table.note td{padding:3px 8px 3px 0;vertical-align:top}
.data{color:#909090;font-size:11px;margin-top:18px}
</style></head><body>
<div class="riquadro">
<h1>Interfaccia di scansione</h1>
<p class="sotto">Masterflex D1008 &#8212; acquisizione a piena risoluzione</p>

<a class="tasto" href="%(url)s" target="_blank">Apri l'interfaccia</a>

<p style="font-size:12px;color:#606060;margin:0 0 6px">
Dal tablet o da un altro computer sulla stessa rete, aprire questo indirizzo.
Il numero dopo <b>?t=</b> &egrave; il PIN: aprendo l'indirizzo senza, la pagina
lo chiede.</p>
<div class="indirizzo">%(url)s</div>

<table class="note">
<tr><td><b>Se il tasto non apre nulla</b></td>
    <td>copiare l'indirizzo qui sopra nel browser. Il visualizzatore di aiuto
        del programma originale &egrave; vecchio e non sempre riesce ad aprire
        una finestra nuova.</td></tr>
<tr><td><b>Se il tablet non si collega</b></td>
    <td>nove volte su dieci &egrave; il firewall di Windows sulla porta 8080,
        oppure il tablet &egrave; su una rete diversa.</td></tr>
<tr><td><b>Attenzione</b></td>
    <td>il programma originale e l'interfaccia di scansione non vanno usati
        nello stesso momento: comandano la stessa scheda.</td></tr>
</table>

<p class="data">Indirizzo aggiornato il %(data)s. Questa pagina viene riscritta
a ogni avvio: se l'indirizzo non funziona, riavviare l'interfaccia.</p>
</div></body></html>
"""


def scrivi_pagina(url: str, cartella: str = CARTELLA_AIUTO) -> str:
    """Scrive la pagina di collegamento dove il programma originale la trova.

    HTML volutamente antico - niente CSS moderno, niente JavaScript - perche'
    a mostrarla e' il controllo Internet Explorer incorporato in PhxHelp.exe,
    che su queste macchine si comporta come un IE di venticinque anni fa.
    """
    if not os.path.isdir(cartella):
        os.makedirs(cartella)
    percorso = os.path.join(cartella, NOME_PAGINA)
    testo = PAGINA % {"url": url, "data": time.strftime("%d/%m/%Y alle %H:%M")}
    with io.open(percorso, "w", encoding="latin-1", errors="replace") as f:
        f.write(testo)
    return percorso


def aggiungi_voce_aiuto(cartella_ini: str) -> bool:
    """Aggiunge la pagina all'elenco dei documenti di aiuto.

    Le voci sono numerate per lingua: 10-13 inglese, 50-53 italiano. Si
    aggiunge la 54 al gruppo italiano. Se il programma non la mostrasse -
    e' possibile, non abbiamo il suo codice - la pagina resta comunque
    raggiungibile aprendola dalla cartella, e il collegamento sul desktop
    funziona sempre.
    """
    percorso = os.path.join(cartella_ini, "HelpFile_Factory.ini")
    if not os.path.exists(percorso):
        print("  HelpFile_Factory.ini non trovato")
        return False
    testo = _leggi(percorso)
    if NOME_PAGINA in testo:
        return True
    _salvataggio(percorso)
    voce = "HelpFile54 = %s\n" % NOME_PAGINA
    testo = re.sub(r"(HelpFile53\s*=.*\n)", r"\g<1>" + voce, testo)
    _scrivi(percorso, testo)
    return True


# ---------------------------------------------------------------------------

def stato(cartella: str, cartella_aiuto: str = CARTELLA_AIUTO) -> None:
    print("Configurazione del programma originale in %s\n" % cartella)

    p = os.path.join(cartella, "Products.ini")
    if os.path.exists(p):
        elenco = prodotti(_leggi(p))
        attivi = [x for x in elenco if x[3]]
        print("  Prodotti (stampa): %d in elenco, %d attivi"
              % (len(elenco), len(attivi)))
        for s, n, d, a in elenco[:4]:
            print("      %-8s %-30s %-6s %s" % (s, n[:30], d, "attivo" if a else "spento"))
        if len(elenco) > 4:
            print("      ... e altri %d" % (len(elenco) - 4))
    else:
        print("  Products.ini non trovato")

    d = os.path.join(cartella, "Daily_Factory.ini")
    if os.path.exists(d):
        t = _leggi(d)
        m = re.search(r"ColorCalibrationEnabled\s*=\s*(\w+)", t)
        i = re.search(r"CalDueCheckInterval\s*=\s*(\d+)", t)
        print("\n  Calibrazione colore (densitometro): %s"
              % (m.group(1) if m else "non impostata"))
        if i:
            print("  Controllo scadenza calibrazione ogni %.0f minuti"
                  % (int(i.group(1)) / 60000.0))
    else:
        print("\n  Daily_Factory.ini non trovato")

    pagina = os.path.join(cartella_aiuto, NOME_PAGINA)
    print("\n  Collegamento nell'aiuto: %s"
          % ("presente" if os.path.exists(pagina) else "assente"))


def principale(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Alleggerisce il programma originale e ci mette il nostro collegamento")
    ap.add_argument("--ini", default=CARTELLA_INI, help="cartella IniFiles del programma originale")
    ap.add_argument("--aiuto-dir", default=CARTELLA_AIUTO, help="cartella dei documenti di aiuto")
    ap.add_argument("--stato", action="store_true", help="mostra com'e' messo adesso")
    ap.add_argument("--alleggerisci", action="store_true", help="spegne stampa e densitometro")
    ap.add_argument("--ripristina", action="store_true", help="riaccende stampa e densitometro")
    ap.add_argument("--collegamento", metavar="URL",
                    help="scrive la pagina di collegamento con questo indirizzo")
    a = ap.parse_args(argv)

    if not (a.stato or a.alleggerisci or a.ripristina or a.collegamento):
        ap.print_help()
        return 1

    if a.stato:
        stato(a.ini, a.aiuto_dir)
        return 0

    if a.alleggerisci or a.ripristina:
        riattiva = bool(a.ripristina)
        verbo = "Riattivo" if riattiva else "Spengo"
        print("%s stampa e densitometro nel programma originale." % verbo)
        print("  (una copia di sicurezza di ogni file resta accanto all'originale)\n")
        n = togli_stampa(a.ini, riattiva)
        print("  prodotti di stampa toccati: %d" % n)
        ok = togli_densitometro(a.ini, riattiva)
        print("  calibrazione colore: %s" % ("riattivata" if riattiva else "spenta")
              if ok else "  calibrazione colore: non trovata")
        print("\nFatto. Il programma originale parte ancora: non gli e' stato")
        print("tolto nessun componente, solo spente delle funzioni.")

    if a.collegamento:
        p = scrivi_pagina(a.collegamento, a.aiuto_dir)
        print("\n  pagina scritta: %s" % p)
        if aggiungi_voce_aiuto(a.ini):
            print("  voce aggiunta all'elenco dei documenti di aiuto")
    return 0


if __name__ == "__main__":
    sys.exit(principale())
