# -*- coding: utf-8 -*-
"""
Gli ordini gia' fatti: registro, consegna, avvisi, conservazione.

Questa e' la parte che si usa al banco, non davanti alla macchina: ritrovare
un ordine di tre settimane fa, prepararne una copia per il cliente, avvisarlo
che e' pronto, e ogni tanto fare spazio.

Tre scelte che vale la pena conoscere prima di leggere il codice.

**Il registro Excel non e' il database.** Il database resta SQLite, perche' un
foglio di calcolo aperto da qualcuno blocca il file e perde le scritture. Il
registro e' una fotografia leggibile che si riscrive a ogni cambiamento: si
apre, si stampa, si manda al commercialista. Se si rompe, si rifa' da capo con
un clic; se si rompesse il database sarebbe un'altra storia.

**La conservazione la decide chi lavora, e di serie non cancella niente.** La
scadenza a 30 giorni segnala cosa e' vecchio e quanto spazio libererebbe; se e
cosa cancellare da soli e' una scelta esplicita fra tre (`segnala`, `grezzi`,
`tutto`), e resta su `segnala` finche' qualcuno non la cambia. I grezzi in
particolare sono irripetibili: per rifarli bisogna rimettere la pellicola nella
macchina, ammesso che il cliente ce l'abbia ancora. Nessun conto alla rovescia
vale quanto le fotografie di qualcuno.

**L'eta' di un ordine si conta dall'ultima volta che ci si e' lavorato**, non
da quando e' stato aperto. Un ordine aperto a gennaio e scansionato ieri ha un
giorno, non nove mesi: contare dall'apertura vorrebbe dire proporre di
cancellare il lavoro appena finito.

**WhatsApp: il messaggio si prepara, non si manda.** Non esiste un modo onesto
di allegare un file o premere invio da fuori: l'unica strada e' l'API a
pagamento per aziende, oppure pilotare il browser di nascosto, che e' contro
le condizioni d'uso e si rompe al primo aggiornamento. Quello che si puo' fare
- e che facciamo - e' aprire WhatsApp con il messaggio gia' scritto e il
provino a contatto gia' pronto nella cartella dell'ordine, a un trascinamento
di distanza. L'invio resta un gesto di chi lavora, ed e' giusto cosi'.
"""

from __future__ import annotations

import io as _io
import os
import shutil
import time
import zipfile
from typing import Dict, List, Optional
from urllib.parse import quote

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment
    HA_EXCEL = True
except ImportError:                                     # pragma: no cover
    HA_EXCEL = False


GIORNO = 86400.0


# ---------------------------------------------------------------------------
# misure e scadenze
# ---------------------------------------------------------------------------

def _dimensione(percorsi) -> int:
    tot = 0
    for p in percorsi:
        try:
            if p and os.path.isfile(p):
                tot += os.path.getsize(p)
        except OSError:
            pass
    return tot


def voci_ordine(arch, ordine_id: int) -> List[dict]:
    """Ogni file di un ordine con l'id del fotogramma da cui viene.

    Serve a chi cancella: dopo aver tolto il file dal disco bisogna togliere il
    percorso dal database, altrimenti l'archivio continua a contare fotogrammi
    che non esistono piu'.
    """
    voci = []
    for rullo in arch.rulli(ordine_id):
        for f in arch.fotogrammi(rullo.id):
            for campo, gruppo in (("file_raw", "grezzi"),
                                  ("file_out", "esportati"),
                                  ("file_maschera", "maschere"),
                                  ("file_negativo", "negativi")):
                p = f.get(campo)
                if p:
                    voci.append({"id": f["id"], "campo": campo,
                                 "gruppo": gruppo, "percorso": p,
                                 "creato": f.get("creato") or 0.0})
    return voci


def ultimo_tocco(arch, ordine_id: int, creato: float = 0.0) -> float:
    """Quando si e' lavorato a questo ordine l'ultima volta.

    Il piu' recente fra: apertura dell'ordine, ultimo fotogramma registrato,
    ultima modifica di un file ancora sul disco. E' questa la data da cui
    contare i giorni di conservazione.
    """
    ultimo = float(creato or 0.0)
    for v in voci_ordine(arch, ordine_id):
        ultimo = max(ultimo, float(v["creato"] or 0.0))
        try:
            if os.path.isfile(v["percorso"]):
                ultimo = max(ultimo, os.path.getmtime(v["percorso"]))
        except OSError:
            pass
    return ultimo


def file_ordine(arch, ordine_id: int) -> Dict[str, List[str]]:
    """Tutti i file di un ordine, divisi per tipo.

    I grezzi stanno a parte perche' hanno un peso diverso in ogni senso:
    occupano quasi tutto lo spazio, e sono l'unica cosa che non si rifa'.
    """
    grezzi, esportati, maschere, negativi, selezionati = [], [], [], [], []
    for rullo in arch.rulli(ordine_id):
        for f in arch.fotogrammi(rullo.id):
            if f.get("file_raw"):
                grezzi.append(f["file_raw"])
            if f.get("file_out"):
                esportati.append(f["file_out"])
                if (f.get("voto") or 0) > 0:
                    selezionati.append(f["file_out"])
            if f.get("file_maschera"):
                maschere.append(f["file_maschera"])
            if f.get("file_negativo"):
                negativi.append(f["file_negativo"])
    return {"grezzi": grezzi, "esportati": esportati, "maschere": maschere,
            "negativi": negativi, "selezionati": selezionati}


def riepilogo_ordine(arch, ordine_id: int) -> Dict[str, object]:
    """Quanto pesa e quanti file ha un ordine.

    I conteggi sono di file CHE ESISTONO SUL DISCO, non di righe del database.
    E' una differenza che si nota il giorno in cui si cancella qualcosa: se si
    contassero le righe, dopo aver liberato mezzo giga il pannello direbbe
    ancora "36 grezzi" e sembrerebbe che non sia successo niente.
    """
    o = arch.ordine(ordine_id)
    if o is None:
        raise ValueError("ordine inesistente")
    file = file_ordine(arch, ordine_id)
    ci_sono = lambda gruppo: [p for p in file[gruppo]
                              if p and os.path.isfile(p)]
    grezzi, esportati = ci_sono("grezzi"), ci_sono("esportati")
    negativi, selezionati = ci_sono("negativi"), ci_sono("selezionati")
    mancanti = ((len(file["grezzi"]) - len(grezzi)) +
                (len(file["esportati"]) - len(esportati)))
    tocco = ultimo_tocco(arch, ordine_id, o.creato or 0.0) or (o.creato or time.time())
    eta = (time.time() - tocco) / GIORNO
    return {
        "id": o.id, "codice": o.codice, "stato": o.stato, "note": o.note,
        "creato": o.creato, "ultimo": tocco, "giorni": round(max(eta, 0.0), 1),
        "n_grezzi": len(grezzi), "n_esportati": len(esportati),
        "n_negativi": len(negativi), "n_selezionati": len(selezionati),
        "mancanti": mancanti,
        "peso_grezzi": _dimensione(grezzi),
        "peso_esportati": _dimensione(esportati),
    }


def scaduti(arch, giorni: int = 30, limite: int = 500) -> List[Dict[str, object]]:
    """Ordini piu' vecchi del periodo di conservazione.

    Ritorna un elenco, non una condanna: chi guarda decide.
    """
    fuori = []
    for o in arch.ordini(limite=limite):
        r = riepilogo_ordine(arch, o.id)
        if r["giorni"] < giorni:
            continue
        # Un ordine gia' svuotato non e' "da fare spazio": non ha piu' niente
        # da liberare, e tenerlo nell'elenco vorrebbe dire proporre ogni
        # giorno una cancellazione gia' fatta.
        if not r["peso_grezzi"] and not r["peso_esportati"]:
            continue
        r["cliente"] = o.cliente_nome
        fuori.append(r)
    fuori.sort(key=lambda x: x["ultimo"])
    return fuori


def libera(arch, ordine_id: int, cosa: str = "grezzi") -> Dict[str, object]:
    """Cancella i file di un ordine. Solo se qualcuno lo chiede, mai da soli.

    cosa: "grezzi" (il caso normale: liberano quasi tutto lo spazio e i file
    consegnati restano), "esportati", oppure "tutto".

    Il database non si tocca: l'ordine resta in elenco con la sua storia, e si
    vede che i file non ci sono piu'. Cancellare anche la riga vorrebbe dire
    non sapere nemmeno che quell'ordine e' esistito.
    """
    if arch.ordine(ordine_id) is None:
        raise ValueError("ordine inesistente")
    gruppi = {"grezzi": ["grezzi"],
              "esportati": ["esportati", "maschere", "negativi"],
              "tutto": ["grezzi", "esportati", "maschere", "negativi"]}[cosa]

    tolti, liberati, gia_spariti, falliti = 0, 0, 0, []
    scordare = {}
    for v in voci_ordine(arch, ordine_id):
        if v["gruppo"] not in gruppi:
            continue
        p = v["percorso"]
        try:
            if os.path.isfile(p):
                peso = os.path.getsize(p)
                os.remove(p)
                liberati += peso
                tolti += 1
                # Il grezzo e' due file: il .npz con i dati e il .json con i
                # dati di scansione accanto. Togliere solo il primo lascia in
                # giro un descrittore di qualcosa che non c'e' piu'.
                if p.lower().endswith(".npz"):
                    gemello = p[:-4] + ".json"
                    try:
                        if os.path.isfile(gemello):
                            liberati += os.path.getsize(gemello)
                            os.remove(gemello)
                    except OSError:
                        pass
            else:
                gia_spariti += 1
        except OSError as exc:
            # Su Windows questo vuol dire quasi sempre che il file e' aperto
            # da qualcuno - un'anteprima di Esplora risorse basta. Il percorso
            # NON si scorda: il file c'e' ancora.
            falliti.append("%s (%s)" % (os.path.basename(p), exc))
            continue
        scordare.setdefault(v["id"], []).append(v["campo"])

    # Tolto il file, si toglie anche il percorso. Senza questo passaggio il
    # database continua a indicare file che non esistono: l'archivio conta
    # fotogrammi che non ci sono, le miniature si rompono una a una, e chi
    # guarda conclude - a ragione - che il pulsante non ha fatto niente.
    for fid, campi in scordare.items():
        arch.fotogramma_scorda_file(fid, campi)

    return {"tolti": tolti, "liberati": liberati,
            "gia_spariti": gia_spariti, "falliti": falliti[:5],
            "n_falliti": len(falliti)}


PULIZIE = ("segnala", "grezzi", "tutto")


def pulizia_automatica(arch, giorni: int = 30, modo: str = "segnala",
                       limite: int = 500) -> Dict[str, object]:
    """Applica la politica di conservazione agli ordini scaduti.

    `modo`:
      - "segnala" (di serie): non tocca niente, si limita a dire cosa e' fuori
        tempo. E' il comportamento giusto finche' qualcuno non decide altro.
      - "grezzi": cancella i .npz grezzi degli ordini scaduti e lascia le foto
        esportate. Libera quasi tutto lo spazio e lascia consegnabile l'ordine.
      - "tutto": cancella grezzi, foto esportate e maschere.

    In nessun caso sparisce la riga dell'ordine: chi ha portato cosa e quando
    resta in archivio per sempre. Cancellare anche quella vorrebbe dire non
    sapere piu' nemmeno che quel lavoro e' esistito.
    """
    if modo not in PULIZIE:
        raise ValueError("modo di pulizia sconosciuto: %s" % modo)
    fuori = scaduti(arch, giorni, limite)
    esito = {"modo": modo, "giorni": giorni, "ordini": len(fuori),
             "puliti": 0, "tolti": 0, "liberati": 0, "falliti": 0}
    if modo == "segnala":
        return esito
    for o in fuori:
        r = libera(arch, int(o["id"]), modo)
        if r["tolti"]:
            esito["puliti"] += 1
        esito["tolti"] += r["tolti"]
        esito["liberati"] += r["liberati"]
        esito["falliti"] += r.get("n_falliti", 0)
    return esito


# ---------------------------------------------------------------------------
# registro
# ---------------------------------------------------------------------------

COLONNE = ["Codice", "Cliente", "Telefono", "Data", "Stato", "Rulli",
           "Fotogrammi", "Giorni", "Grezzi (MB)", "Esportati (MB)", "Note"]


def _righe(arch, limite: int = 2000) -> List[list]:
    righe = []
    for o in arch.ordini(limite=limite):
        r = riepilogo_ordine(arch, o.id)
        cli = arch.cliente(o.cliente_id)
        righe.append([
            o.codice, o.cliente_nome, (cli.telefono if cli else ""),
            time.strftime("%d/%m/%Y %H:%M", time.localtime(o.creato or 0)),
            o.stato, o.rulli, o.fotogrammi, r["giorni"],
            round(r["peso_grezzi"] / 1048576.0, 1),
            round(r["peso_esportati"] / 1048576.0, 1),
            o.note or "",
        ])
    return righe


def scrivi_registro(arch, percorso: str, limite: int = 2000) -> str:
    """Riscrive il registro degli ordini. Excel se si puo', CSV altrimenti.

    Il CSV non e' un ripiego vergognoso: Excel lo apre lo stesso, e su una
    macchina dove openpyxl non si e' potuto installare e' meglio di niente.
    Il separatore e' il punto e virgola, che e' quello che l'Excel italiano si
    aspetta senza bisogno di procedure guidate.
    """
    cartella = os.path.dirname(os.path.abspath(percorso))
    if cartella and not os.path.isdir(cartella):
        os.makedirs(cartella)
    righe = _righe(arch, limite)

    if not HA_EXCEL or not percorso.lower().endswith((".xlsx", ".xlsm")):
        percorso = os.path.splitext(percorso)[0] + ".csv"
        with _io.open(percorso, "w", encoding="utf-8-sig", newline="") as f:
            import csv
            w = csv.writer(f, delimiter=";")
            w.writerow(COLONNE)
            w.writerows(righe)
        return percorso

    wb = Workbook()
    ws = wb.active
    ws.title = "Ordini"
    ws.append(COLONNE)
    for cella in ws[1]:
        cella.font = Font(bold=True)
        cella.alignment = Alignment(horizontal="center")
    for r in righe:
        ws.append(r)
    ws.freeze_panes = "A2"
    for i, larghezza in enumerate([12, 26, 15, 17, 15, 7, 12, 8, 12, 14, 40], 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = larghezza
    wb.save(percorso)
    return percorso


# ---------------------------------------------------------------------------
# consegna
# ---------------------------------------------------------------------------

def zip_ordine(arch, ordine_id: int, destinazione: str,
               cosa: str = "esportati") -> str:
    """Mette i file di un ordine in un unico archivio zip.

    Senza compressione, di proposito: i TIFF a 16 bit di un rullo pesano
    centinaia di megabyte e comprimerli su un PC del 2009 significa lasciare
    l'operatore ad aspettare per guadagnare poco. Lo zip qui serve a fare UN
    file da copiare, non a risparmiare spazio.
    """
    o = arch.ordine(ordine_id)
    if o is None:
        raise ValueError("ordine inesistente")
    file = file_ordine(arch, ordine_id)
    # "selezionati": solo i fotogrammi segnati da stampare; "negativi": i
    # negativi lineari (TIFF o DNG) per chi converte con altri programmi
    if cosa in ("esportati", "grezzi", "negativi", "selezionati"):
        dentro = file[cosa]
    else:
        dentro = (file["esportati"] + file["grezzi"] + file["maschere"]
                  + file["negativi"])
    dentro = [p for p in dentro if p and os.path.isfile(p)]
    if not dentro:
        raise ValueError("questo ordine non ha file di tipo '%s' sul disco" % cosa)

    with zipfile.ZipFile(destinazione, "w", zipfile.ZIP_STORED,
                         allowZip64=True) as z:
        for p in dentro:
            z.write(p, os.path.join(o.codice, os.path.basename(p)))
    return destinazione


def provino_contatto(arch, ordine_id: int, destinazione: str,
                     colonne: int = 5, lato: int = 420,
                     margine: int = 14) -> str:
    """Il provino a contatto dell'ordine: tutti i fotogrammi in una pagina.

    E' la cosa che il cliente guarda per dire "questa si', questa no", ed e'
    anche l'allegato naturale del messaggio di avviso. Si genera dai file gia'
    esportati, quindi e' veloce e non tocca la macchina.
    """
    from PIL import Image, ImageDraw

    file = file_ordine(arch, ordine_id)
    esistenti = [p for p in file["esportati"] if p and os.path.isfile(p)]
    if not esistenti:
        raise ValueError("nessun fotogramma esportato per questo ordine")

    o = arch.ordine(ordine_id)
    miniature = []
    for p in esistenti:
        try:
            im = Image.open(p)
            im.thumbnail((lato, lato))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            miniature.append((os.path.basename(p), im))
        except Exception:                               # noqa: BLE001
            continue
    if not miniature:
        raise ValueError("nessun fotogramma leggibile")

    celle_w = max(im.width for _, im in miniature)
    celle_h = max(im.height for _, im in miniature)
    righe = (len(miniature) + colonne - 1) // colonne
    intestazione = 46
    largh = colonne * (celle_w + margine) + margine
    alt = intestazione + righe * (celle_h + margine + 16) + margine

    foglio = Image.new("RGB", (largh, alt), (250, 249, 247))
    d = ImageDraw.Draw(foglio)
    d.text((margine, 14), "%s  -  %s  -  %d fotogrammi"
           % (o.codice, o.cliente_nome, len(miniature)), fill=(30, 25, 22))

    for i, (nome, im) in enumerate(miniature):
        r, c = divmod(i, colonne)
        x = margine + c * (celle_w + margine)
        y = intestazione + r * (celle_h + margine + 16)
        foglio.paste(im, (x + (celle_w - im.width) // 2, y))
        d.text((x + 2, y + celle_h + 3), nome[:34], fill=(110, 100, 92))

    cartella = os.path.dirname(os.path.abspath(destinazione))
    if cartella and not os.path.isdir(cartella):
        os.makedirs(cartella)
    foglio.save(destinazione, format="JPEG", quality=86)
    return destinazione


# ---------------------------------------------------------------------------
# avviso al cliente
# ---------------------------------------------------------------------------

MESSAGGIO_PREDEFINITO = (
    "Buongiorno {cliente}, le sue scansioni sono pronte: "
    "ordine {codice}, {fotogrammi} fotogrammi. "
    "Puo' passare a ritirarle quando vuole. Grazie!"
)


def numero_whatsapp(grezzo: str, prefisso: str = "39") -> str:
    """Da un numero scritto come capita a uno che WhatsApp accetta.

    Toglie spazi, punti, trattini e parentesi; trasforma il + iniziale e gli
    zeri internazionali; aggiunge il prefisso del paese se manca. Un numero
    italiano si scrive in almeno sei modi diversi e l'operatore non deve
    saperlo.
    """
    n = "".join(ch for ch in (grezzo or "") if ch.isdigit() or ch == "+")
    if not n:
        return ""
    if n.startswith("+"):
        n = n[1:]
    elif n.startswith("00"):
        n = n[2:]
    elif n.startswith("0"):                 # 0 di teleselezione italiano
        n = prefisso + n[1:]
    if not n.startswith(prefisso) and len(n) <= 10:
        n = prefisso + n
    return n


def avviso(arch, ordine_id: int, modello: str = "",
           prefisso: str = "39") -> Dict[str, object]:
    """Prepara l'avviso: numero, testo e collegamento a WhatsApp.

    Non manda niente. Ritorna il collegamento da aprire, che porta WhatsApp
    (l'applicazione o web.whatsapp.com) con la conversazione giusta e il
    messaggio gia' scritto: resta da rileggerlo e premere invio.
    """
    o = arch.ordine(ordine_id)
    if o is None:
        raise ValueError("ordine inesistente")
    cli = arch.cliente(o.cliente_id)
    numero = numero_whatsapp(cli.telefono if cli else "", prefisso)

    testo = (modello or MESSAGGIO_PREDEFINITO).format(
        cliente=o.cliente_nome or "", codice=o.codice,
        fotogrammi=o.fotogrammi, rulli=o.rulli,
        data=time.strftime("%d/%m/%Y", time.localtime(o.creato or 0)))

    if numero:
        link = "https://wa.me/%s?text=%s" % (numero, quote(testo))
    else:
        # senza numero si apre comunque WhatsApp con il testo pronto:
        # il destinatario lo sceglie l'operatore dalla rubrica
        link = "https://wa.me/?text=%s" % quote(testo)

    return {"numero": numero, "testo": testo, "link": link,
            "ha_numero": bool(numero)}


# ---------------------------------------------------------------------------
# cartelle
# ---------------------------------------------------------------------------

def sfoglia(percorso: str = "") -> Dict[str, object]:
    """Elenca le sottocartelle di un percorso, per scegliere dove archiviare.

    Il browser non puo' aprire il dialogo di Windows per una cartella del
    server - e in rete il "server" e' un'altra macchina, quindi non avrebbe
    nemmeno senso. Si sfoglia da qui.
    """
    if not percorso:
        radici = []
        if os.name == "nt":
            for lettera in "CDEFGHIJKLMNOPQRSTUVWXYZ":
                d = "%s:\\" % lettera
                if os.path.isdir(d):
                    radici.append(d)
        else:
            radici = ["/"]
        return {"percorso": "", "su": "", "cartelle": radici}

    percorso = os.path.abspath(percorso)
    if not os.path.isdir(percorso):
        raise ValueError("cartella inesistente: %s" % percorso)
    dentro = []
    try:
        for nome in sorted(os.listdir(percorso)):
            p = os.path.join(percorso, nome)
            if os.path.isdir(p) and not nome.startswith("."):
                dentro.append(p)
    except OSError as exc:
        raise ValueError("non riesco a leggere %s (%s)" % (percorso, exc))
    su = os.path.dirname(percorso)
    return {"percorso": percorso, "su": su if su != percorso else "",
            "cartelle": dentro[:400]}


def spazio_libero(percorso: str) -> int:
    try:
        return shutil.disk_usage(percorso).free
    except OSError:
        return 0
