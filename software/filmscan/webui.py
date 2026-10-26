# -*- coding: utf-8 -*-
"""
Interfaccia web su IP.

Solo libreria standard: `http.server` e `json`. Nessun Flask, nessun pacchetto
da installare su Windows 7 a 32 bit, dove ogni dipendenza in piu' e' una wheel
che potrebbe non esistere.

Serve a comandare lo scanner da un portatile o da un telefono sulla stessa rete,
mentre si sta fisicamente davanti alla macchina a caricare le pellicole.

## Dove sta la logica

Qui dentro non c'e' nessuna logica di scansione: c'e' solo la traduzione fra
HTTP e `service.ScanService`, che e' lo stesso oggetto usato dall'interfaccia
locale. Due interfacce con due copie della stessa logica divergono sempre, e la
copia meno usata marcisce senza che nessuno se ne accorga.

Se serve una funzione nuova, va in `service.py`, non qui.

## Sicurezza

Di default ascolta su 127.0.0.1: **nessuno** dall'esterno puo' collegarsi.
Per usarlo in rete locale si passa `--host 0.0.0.0`, e in quel caso viene
richiesto un token, che va messo in ogni richiesta.

Questo non e' un server esposto su internet e non deve diventarlo: non c'e'
cifratura, non c'e' gestione utenti, e comanda un motore che tira pellicola.
Rete locale fidata e basta.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import traceback
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from . import compat
from . import consegne
from .config import AppConfig, FORMATS, PRESETS
from .invert import PROFILES
from .orders import Archivio
from .pipeline import ProcessOptions
from .service import ScanService


# --------------------------------------------------------------------------
# Servizio: stato condiviso fra le richieste HTTP
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Opzioni di elaborazione dalla richiesta
# --------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tavolozza e cambio tema
#
# Sta qui, in una stringa sola, invece che ripetuta nelle tre pagine: un colore
# che cambia in due posti su tre e' il modo classico di ritrovarsi
# un'interfaccia scucita dopo il secondo ritocco.
# ---------------------------------------------------------------------------

TEMA = r"""
/* --- scuro: il tema di casa, quello per lavorare al buio davanti alla
   macchina, dove uno schermo bianco in faccia stanca e falsa il giudizio --- */
:root{
 --bg:#14110f; --card:#1d1917; --ink:#efe9e2; --dim:#9a908a;
 --acc:#c8763c; --su-acc:#1a1310; --line:#332c28;
 --ok:#7fb069; --male:#e0655a; --avviso:#e0a355;
 --campo:#241f1c; --campo2:#2c2622; --griglia:#3a322d;
 /* Il fondo attorno all'immagine e' NEUTRO di proposito, non caldo come il
    resto: un contorno colorato falsa il giudizio sul colore, ed e' proprio
    per giudicare il colore che si guarda quell'immagine. */
 --neutro:#262626;
 --tela:#181513; --tela-riga:#332c28; --tela-diag:#4a4038;
 --ombra:rgba(0,0,0,.5);
}

/* --- chiaro: bianco, viola per i testi in evidenza, blu scuro tenue per
   il testo normale. Serve al banco e sul tablet in una stanza illuminata,
   dove il tema scuro si specchia e non si legge --- */
:root[data-tema="chiaro"]{
 --bg:#ffffff; --card:#f7f6fb; --ink:#22304d; --dim:#6b7794;
 --acc:#6d3fbf; --su-acc:#ffffff; --line:#e3e0ef;
 --ok:#2c7a4b; --male:#c0392b; --avviso:#a76a00;
 --campo:#ffffff; --campo2:#f0eef8; --griglia:#d8d4e8;
 /* Anche qui il contorno dell'immagine NON e' bianco: un bianco pieno
    attorno a una fotografia la fa sembrare piu' scura e piu' contrastata di
    quello che e'. Grigio neutro medio-chiaro, come su un tavolo luminoso. */
 --neutro:#cfcfd2;
 --tela:#faf9fd; --tela-riga:#e3e0ef; --tela-diag:#c9c3e0;
 --ombra:rgba(40,30,80,.18);
}

/* I tasti di allarme e le etichette di avviso nascono con colori da tema
   scuro (bordeaux cupo su nero). Sul bianco quel bordeaux diventa una
   macchia sporca invece che un allarme: qui tornano rossi veri. */
:root[data-tema="chiaro"] button.stop,
:root[data-tema="chiaro"] button.male{background:#c0392b;color:#fff;
 border-color:#a02c1e}
:root[data-tema="chiaro"] .tag.vecchio{border-color:#e6cba2}
:root[data-tema="chiaro"] .tag.senzafile{border-color:#efc4bf}
/* La maniglia del confronto e' bianca: sopra una fotografia chiara sparisce.
   Le si mette sotto un'ombra invece di un alone. */
:root[data-tema="chiaro"] #maniglia{background:rgba(60,50,90,.9);
 box-shadow:0 0 8px rgba(255,255,255,.7)}
"""

# Il tema si sceglie per dispositivo, non per utente: la macchina sta in
# penombra e vuole lo scuro, il tablet in mano all'operatore sotto le luci
# del banco vuole il chiaro. Percio' si ricorda nel browser che lo mostra.
SCRIPT_TEMA = r"""
(function(){
  var t = null;
  try{ t = localStorage.getItem('tema'); }catch(e){}
  if(t==='chiaro') document.documentElement.setAttribute('data-tema','chiaro');
})();
function cambiaTema(){
  var r = document.documentElement;
  var chiaro = r.getAttribute('data-tema') === 'chiaro';
  if(chiaro) r.removeAttribute('data-tema');
  else r.setAttribute('data-tema','chiaro');
  try{ localStorage.setItem('tema', chiaro ? 'scuro' : 'chiaro'); }catch(e){}
  if(window.dopoTema) window.dopoTema();
}
"""

# L'icona: sole quando si e' al buio (premi e fai giorno), luna quando si e'
# alla luce. Due tracciati, uno solo visibile, cosi' non serve ridisegnarla.
ICONA_TEMA = r"""<button class="tema" onclick="cambiaTema()" title="Cambia tema">
<svg viewBox="0 0 24 24" width="18" height="18" fill="none"
 stroke="currentColor" stroke-width="1.9" stroke-linecap="round">
<g class="sole"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.6v2M12 19.4v2
M2.6 12h2M19.4 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M18.7 5.3l-1.4 1.4
M6.7 17.3l-1.4 1.4"/></g>
<g class="luna"><path d="M20 14.2A8.2 8.2 0 0 1 9.8 4a8.2 8.2 0 1 0 10.2 10.2z"/></g>
</svg></button>"""

CSS_ICONA_TEMA = r"""
button.tema{width:auto;flex:0 0 auto;background:none;border:1px solid var(--line);
 color:var(--dim);border-radius:6px;padding:7px 9px;cursor:pointer;
 display:flex;align-items:center;min-height:0}
button.tema:hover{color:var(--acc);border-color:var(--acc)}
button.tema .luna{display:none}
:root[data-tema="chiaro"] button.tema .sole{display:none}
:root[data-tema="chiaro"] button.tema .luna{display:inline}
@media(pointer:coarse){button.tema{padding:12px 14px;min-height:48px}}
"""


def _butta_rullo(s, rullo_id: int) -> int:
    """Cancella i file di UN rullo (consegne.libera lavora sull'ordine intero).

    Torna quanti file sono spariti davvero. I percorsi si scordano dal
    database subito dopo: un percorso che punta al vuoto e' peggio di nessun
    percorso, perche' fa contare fotogrammi che non ci sono.
    """
    tolti = 0
    for f in s.archivio.fotogrammi(rullo_id):
        for campo in ("file_raw", "file_out", "file_maschera"):
            p = f.get(campo)
            if not p:
                continue
            try:
                if os.path.isfile(p):
                    os.remove(p)
                    tolti += 1
            except OSError:
                continue
            s.archivio.fotogramma_scorda_file(f["id"], [campo])
    return tolti


_MANCANTE: Dict[str, bytes] = {}


def _mancante(lato: int = 150) -> bytes:
    """Una piastrella grigia con una croce e la parola giusta.

    Si disegna al volo invece di tenere un file accanto al programma: un file
    in piu' e' un file in meno che qualcuno copia quando sposta la cartella.
    """
    if "jpg" in _MANCANTE:
        return _MANCANTE["jpg"]
    from io import BytesIO
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (lato, lato), (58, 54, 52))
    d = ImageDraw.Draw(im)
    d.rectangle([4, 4, lato - 5, lato - 5], outline=(96, 88, 84))
    d.line([18, 18, lato - 19, lato - 19], fill=(96, 88, 84), width=2)
    d.line([lato - 19, 18, 18, lato - 19], fill=(96, 88, 84), width=2)
    d.text((14, lato - 22), "file mancante", fill=(198, 188, 182))
    buf = BytesIO()
    im.save(buf, format="JPEG", quality=70)
    _MANCANTE["jpg"] = buf.getvalue()
    return _MANCANTE["jpg"]


def _opzioni(d: Dict) -> ProcessOptions:
    """Traduce il corpo JSON in ProcessOptions.

    La specchiatura NON si prende da qui: e' una proprieta' del rullo, salvata
    in archivio, e viene applicata da scansiona_rullo. Metterla fra le opzioni
    del singolo lavoro vorrebbe dire poterla cambiare a meta' rullo.
    """
    def f(k, default=0.0):
        try:
            return float(d.get(k, default))
        except (TypeError, ValueError):
            return float(default)

    return ProcessOptions(
        profile=PROFILES.get(d.get("profilo", "c41"), PROFILES["c41"]),
        auto_wb=f("bilanciamento", 1.0),
        auto_levels=bool(d.get("livelli", True)),
        ir_strength=f("ir", 1.0),
        auto_crop=bool(d.get("ritaglio", True)),
        auto_straighten=bool(d.get("raddrizza", True)),
        grain_reduction=f("grana_riduci"),
        grain_add=f("grana_aggiungi"),
        scratch_repair=f("graffi"),
        dust_repair=f("polvere"),
        sharpen=f("nitidezza"),
        auto_rotate=bool(d.get("auto_ruota", False)),

        # --- pannello colore ---
        esposizione=f("esposizione"),
        temperatura_k=f("temperatura_k"),
        tinta_vm=f("tinta_vm"),
        wb_guadagni=d.get("wb_guadagni") or None,
        barra_r=f("barra_r"), barra_g=f("barra_g"), barra_b=f("barra_b"),
        gamma_tono=f("gamma_tono", 1.0),
        contrasto=f("contrasto"),
        luminosita=f("luminosita"),
        chiarezza=f("chiarezza"),
        hue=f("hue"),
        saturation=f("saturazione", 1.0),
        curve=d.get("curva") or [],
        curva_r=d.get("curva_r") or [],
        curva_g=d.get("curva_g") or [],
        curva_b=d.get("curva_b") or [])


# --------------------------------------------------------------------------
# Pagina
# --------------------------------------------------------------------------

PAGINA_PIN = r"""<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Masterflex D1008</title>
<style>
/*TEMA*/
*{box-sizing:border-box}
body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
 background:var(--bg);color:var(--ink);
 font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
.scatola{background:var(--card);border:1px solid var(--line);border-radius:10px;
 padding:28px;width:330px;text-align:center}
h1{font-size:15px;margin:0 0 2px;font-weight:600}
p.sotto{font-size:12px;color:var(--dim);margin:0 0 22px}
input{width:100%;background:var(--campo);color:var(--ink);border:1px solid var(--line);
 border-radius:8px;padding:16px;font:600 30px/1 ui-monospace,Consolas,monospace;
 text-align:center;letter-spacing:9px;margin-bottom:14px}
input:focus{outline:none;border-color:var(--acc)}
input::placeholder{letter-spacing:6px;color:var(--griglia);font-size:24px}
button{width:100%;background:var(--acc);color:var(--su-acc);border:0;border-radius:8px;
 padding:15px;font:600 16px/1 inherit;cursor:pointer}
button:disabled{opacity:.4;cursor:not-allowed}
.errore{color:var(--male);font-size:13px;min-height:20px;margin-bottom:10px}
.nota{font-size:11px;color:var(--dim);margin-top:18px;line-height:1.6}
@media(pointer:coarse){input{padding:20px;font-size:34px}button{padding:19px}}
</style></head><body>
<div style="position:fixed;top:14px;right:14px">/*ICONA*/</div>
<form class="scatola" onsubmit="entra(event)">
 <h1>Masterflex D1008</h1>
 <p class="sotto">Interfaccia di scansione</p>
 <div class="errore" id="errore"></div>
 <input id="pin" inputmode="numeric" pattern="[0-9]*" maxlength="8"
        placeholder="&middot;&middot;&middot;&middot;&middot;&middot;&middot;&middot;" autocomplete="off" autofocus>
 <button id="tasto" type="submit">Entra</button>
 <div class="nota">Il PIN e' scritto nella finestra del server,
  sul monitor della macchina. Cambia a ogni accensione.</div>
</form>
<script>
/*STATO*/
var E=document.getElementById('errore'), P=document.getElementById('pin');
var T=document.getElementById('tasto');
if(BLOCCO>0){
  T.disabled=true; P.disabled=true;
  var resta=BLOCCO;
  var conto=setInterval(function(){
    resta--;
    E.textContent='Troppi tentativi. Riprovare fra '+resta+' second'+(resta===1?'o':'i')+'.';
    if(resta<=0){ clearInterval(conto); location.href='/'; }
  }, 1000);
  E.textContent='Troppi tentativi. Riprovare fra '+resta+' secondi.';
} else if(SBAGLIATO){ E.textContent='PIN errato.'; }
function entra(ev){ ev.preventDefault();
  var v=P.value.replace(/\D/g,'');
  if(!v){ P.focus(); return; }
  location.href='/?t='+encodeURIComponent(v); }
P.oninput=function(){ P.value=P.value.replace(/\D/g,''); };
</script></body></html>
"""


PAGINA_ARCHIVIO = r"""<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Archivio ordini</title>
<style>
/*TEMA*/
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);overflow:hidden;
 font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
header{display:flex;align-items:center;gap:12px;padding:8px 14px;
 border-bottom:1px solid var(--line)}
header .nome{font-weight:600;white-space:nowrap}
header .sp{flex:1}
a.link{color:var(--dim);text-decoration:none;font-size:13px;padding:8px 12px;
 border:1px solid var(--line);border-radius:6px}
a.link:hover{color:var(--ink);border-color:var(--acc)}
main{display:grid;grid-template-columns:minmax(0,380px) minmax(0,1fr);
 gap:10px;padding:10px;height:calc(100% - 49px);overflow:hidden}
aside,section.det{overflow-y:auto;display:flex;flex-direction:column;gap:10px}
.pannello{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}
.pannello h2{font-size:11px;text-transform:uppercase;letter-spacing:.9px;
 color:var(--acc);margin:0 0 10px;display:flex;justify-content:space-between}
.pannello h2 span{color:var(--dim);text-transform:none;letter-spacing:0}
label{display:block;font-size:12px;color:var(--dim);margin:9px 0 3px}
input,select,button,textarea{font:inherit;background:var(--campo);color:var(--ink);
 border:1px solid var(--line);border-radius:5px;padding:8px 9px;width:100%}
textarea{resize:vertical;min-height:66px;line-height:1.45}
button{background:var(--acc);border:0;color:var(--su-acc);font-weight:600;cursor:pointer}
button.sec{background:var(--campo2);color:var(--ink)}
button.ghost{background:none;border:1px solid var(--line);color:var(--dim)}
button.male{background:#6b2420;color:#ffd9d4;border:1px solid #8c3029}
button:disabled{opacity:.45;cursor:not-allowed}
.row{display:flex;gap:8px;margin-top:10px}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.g3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}

.elenco{max-height:none}
.ord{border:1px solid var(--line);border-radius:7px;padding:10px 11px;
 margin-bottom:7px;cursor:pointer;background:var(--campo)}
.ord:hover{border-color:var(--acc)}
.ord.sel{border-color:var(--acc);background:var(--campo2)}
.ord .riga1{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.ord .cod{font-weight:600;font-variant-numeric:tabular-nums}
.ord .cli{color:var(--ink)}
.ord .meta{font-size:11px;color:var(--dim);margin-top:3px;
 display:flex;gap:10px;flex-wrap:wrap}
.tag{font-size:10px;padding:1px 6px;border-radius:9px;border:1px solid var(--line);
 color:var(--dim)}
.tag.vecchio{color:var(--avviso);border-color:#5a4326}
.tag.senzafile{color:var(--male);border-color:#5a2b28}

.provini{display:grid;grid-template-columns:repeat(auto-fill,minmax(92px,1fr));
 gap:6px;margin-top:8px}
.prov{position:relative;border:1px solid var(--line);border-radius:4px;
 overflow:hidden;background:var(--neutro);aspect-ratio:3/2}
.prov img{width:100%;height:100%;object-fit:cover;display:block}
.prov .n{position:absolute;left:0;bottom:0;font-size:10px;padding:1px 4px;
 background:rgba(0,0,0,.7);border-radius:0 4px 0 0}
.vuoto{color:var(--dim);font-size:13px;padding:16px 0;text-align:center}
.nota{font-size:11px;color:var(--dim);opacity:.85;line-height:1.6;margin-top:8px}
.grande{font-size:19px;font-weight:600;font-variant-numeric:tabular-nums}
.cartelle{max-height:190px;overflow-y:auto;border:1px solid var(--line);
 border-radius:6px;margin-top:6px}
.cartelle div{padding:7px 10px;font-size:12px;cursor:pointer;
 border-bottom:1px solid var(--line);white-space:nowrap;overflow:hidden;
 text-overflow:ellipsis}
.cartelle div:hover{background:var(--campo2);color:var(--acc)}
table.sc{width:100%;border-collapse:collapse;font-size:12px;margin-top:6px}
table.sc td,table.sc th{padding:5px 4px;border-bottom:1px solid var(--line);text-align:left}
table.sc th{color:var(--dim);font-weight:500}
@media(pointer:coarse){
 body{font-size:16px}
 input,select,button,textarea{padding:13px 12px;font-size:16px;min-height:48px}
 button{min-height:52px;border-radius:8px}
 .ord{padding:14px}
}
@media(max-width:900px){
 body{overflow:auto}
 main{grid-template-columns:1fr;height:auto;overflow:visible}
 aside,section.det{overflow:visible}
}
</style></head><body>

<header>
 <div class="nome">Archivio ordini</div>
 <div id="conta" style="font-size:12px;color:var(--dim)"></div>
 <div class="sp"></div>
 <a class="link" id="vaiscansione" href="/">&larr; Scansione</a>
 /*ICONA*/
</header>

<main>
<aside>
 <section class="pannello">
  <h2>Ordini <span id="tot"></span></h2>
  <input id="cerca" placeholder="cerca un cliente...">
  <div class="elenco" id="elenco" style="margin-top:10px"></div>
 </section>

 <section class="pannello">
  <h2>Registro e archivio</h2>
  <label>Cartella dell'archivio</label>
  <input id="cartella" placeholder="C:\MasterflexD\Archivio">
  <div class="row">
   <button class="sec" onclick="sfoglia('')">Sfoglia</button>
   <button onclick="salvaImpostazioni()">Salva</button>
  </div>
  <div class="cartelle" id="cartelle" style="display:none"></div>
  <div class="g2" style="margin-top:12px">
   <div><label>Conservazione (giorni)</label>
    <input id="giorni" type="number" min="1" value="30"></div>
   <div><label>Prefisso telefono</label>
    <input id="prefisso" value="39"></div>
  </div>
  <label>Quando scadono</label>
  <select id="pulizia">
   <option value="segnala">Segnala soltanto - non cancella niente</option>
   <option value="grezzi">Cancella i grezzi, tieni le foto</option>
   <option value="tutto">Cancella grezzi e foto</option>
  </select>
  <div class="nota">I giorni si contano dall'ultima volta che si e' lavorato
   all'ordine, non da quando e' stato aperto. La riga dell'ordine non sparisce
   mai: resta in archivio con la sua storia.</div>
  <div class="row">
   <button class="sec" onclick="location.href=url('/api/archivio/registro')">
    Scarica il registro</button>
   <button class="sec" onclick="pulisciAdesso()">Applica adesso</button>
  </div>
  <div class="nota" id="notaregistro">
   Il registro e' una fotografia leggibile degli ordini, riscritta a ogni
   cambiamento. Il database vero resta un altro file: un foglio di calcolo
   aperto da qualcuno bloccherebbe le scritture.</div>
 </section>

 <section class="pannello">
  <h2>Spazio <span id="scadutitot"></span></h2>
  <div id="scaduti"></div>
 </section>
</aside>

<section class="det" id="dettaglio">
 <div class="pannello vuoto">Scegliere un ordine dall'elenco.</div>
</section>
</main>

<script>
var TOKEN = new URLSearchParams(location.search).get('t') || '';
function url(p){ return p + (TOKEN ? (p.indexOf('?')<0?'?':'&')+'t='+TOKEN : ''); }
function scaduto(r){ if(r && r.status===403){ location.href='/'; return true; } return false; }
function get(p){ return fetch(url(p)).then(function(r){
  if(scaduto(r)) return {}; return r.json(); }); }
function post(p,d){ return fetch(url(p),{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(d||{})})
  .then(function(r){ if(scaduto(r)) return {}; return r.json(); }); }
function el(id){ return document.getElementById(id); }
function mb(b){ return (b/1048576).toFixed(b>10485760?0:1)+' MB'; }
function data(t){ var d=new Date(t*1000);
  return d.toLocaleDateString('it-IT')+' '+d.toLocaleTimeString('it-IT',
    {hour:'2-digit',minute:'2-digit'}); }

el('vaiscansione').href = url('/');

var ORDINI=[], SEL=null;

function caricaOrdini(){
  get('/api/archivio/ordini?cerca='+encodeURIComponent(el('cerca').value))
   .then(function(r){
    ORDINI = r.ordini||[];
    el('tot').textContent = ORDINI.length ? (ORDINI.length+' in elenco') : '';
    el('conta').textContent = ORDINI.length
      ? (ORDINI.length+' ordini · '+ORDINI.reduce(function(a,o){return a+o.fotogrammi;},0)+' fotogrammi')
      : '';
    var e=el('elenco');
    if(!ORDINI.length){ e.innerHTML='<div class="vuoto">Nessun ordine.</div>'; return; }
    e.innerHTML='';
    ORDINI.forEach(function(o){
      var d=document.createElement('div');
      d.className='ord'+(SEL===o.id?' sel':'');
      var vecchio = o.giorni > (r.conservazione||30);
      /* Un ordine senza file puo' essere due cose molto diverse: mai
         scansionato, oppure scansionato e poi svuotato per fare spazio.
         Chiamarle tutte e due "file rimossi" farebbe cercare un guasto
         dove non c'e'. */
      var maifatto = !o.fotogrammi;
      var svuotato = o.fotogrammi && !o.n_esportati && !o.n_grezzi;
      d.innerHTML =
        '<div class="riga1"><span class="cod">'+o.codice+'</span>'+
        '<span class="cli">'+(o.cliente||'')+'</span></div>'+
        '<div class="meta"><span>'+data(o.creato)+'</span>'+
        '<span>'+o.rulli+(o.rulli===1?' rullo':' rulli')+' · '+
          o.fotogrammi+(o.fotogrammi===1?' fotogramma':' fotogrammi')+'</span>'+
        '<span>'+mb(o.peso_grezzi+o.peso_esportati)+'</span>'+
        (vecchio?'<span class="tag vecchio">'+Math.round(o.giorni)+' giorni</span>':'')+
        (maifatto?'<span class="tag">da scansionare</span>':'')+
        (svuotato?'<span class="tag senzafile">file rimossi</span>':'')+
        '</div>';
      d.onclick=function(){ apri(o.id); };
      e.appendChild(d);
    });
  });
}

/* Il riepilogo dell'ordine aperto: serve alle conferme di eliminazione, che
   devono poter dire QUANTI file stanno per sparire. Una conferma che non lo
   dice non e' una conferma, e' un ostacolo. */
var RIEP = {};
/* L'esito dell'ultima operazione sopravvive al ridisegno del pannello:
   altrimenti si cancellano dei file, la pagina si ricarica, e sullo schermo
   non resta niente che dica cos'e' successo. */
var ULTIMO_ESITO = '';

function apri(id){
  SEL=id; caricaOrdini();
  get('/api/archivio/ordine?id='+id).then(function(r){
    if(r.errore){ el('dettaglio').innerHTML='<div class="pannello vuoto">'+r.errore+'</div>'; return; }
    var o=r.ordine, c=r.cliente||{}, ri=r.riepilogo;
    RIEP = ri;
    var h='';

    h+='<section class="pannello"><h2>'+o.codice+
       ' <span>'+data(o.creato)+' · '+Math.round(ri.giorni)+' giorni fa</span></h2>'+
       '<div class="g2">'+
       '<div><label>Cliente</label><input id="d_cliente" value="'+(c.nome||'').replace(/"/g,"&quot;")+'"></div>'+
       '<div><label>Telefono</label><input id="d_tel" value="'+(c.telefono||'')+'"></div>'+
       '</div>'+
       '<label>Nota dell\'ordine</label><input id="d_note" value="'+(o.note||'').replace(/"/g,"&quot;")+'">'+
       '<div class="row"><button onclick="salvaNomi('+o.id+','+o.cliente_id+')">Salva le modifiche</button>'+
       '<button class="ghost" onclick="apri('+o.id+')">Annulla</button></div>'+
       '<div class="nota">Cambiando il nome qui si corregge la scheda del cliente, '+
       'quindi anche i suoi altri ordini. Per spostare questo ordine su un\'altra '+
       'persona, scrivere il nome nuovo e salvare: se non esiste viene creata.</div>'+
       '<div class="g2" style="margin-top:10px">'+
       '<button class="male" onclick="eliminaOrdine('+o.id+')">Elimina l\'ordine</button>'+
       '<button class="male" onclick="eliminaCliente('+o.cliente_id+')">Elimina il cliente</button>'+
       '</div>'+
       '<div class="nota" id="esitoelimina">Eliminare toglie le righe dall\'archivio. '+
       'I file sul disco si cancellano solo se lo si conferma a parte: sono due '+
       'decisioni diverse e si prendono due volte.</div>'+
       '</section>';

    /* Un pulsante che si puo' premere e poi risponde "questo ordine non ha
       file" e' un pulsante che ha fatto perdere tempo. Se sul disco non c'e'
       niente, il pulsante e' spento e sotto c'e' scritto perche'. */
    var cf = ri.n_esportati ? '' : ' disabled';
    var cg = ri.n_grezzi ? '' : ' disabled';
    h+='<section class="pannello"><h2>Consegna</h2><div class="g3">'+
       '<button class="sec"'+cf+' onclick="location.href=url(\'/api/archivio/zip?cosa=esportati&id='+o.id+'\')">Scarica le foto</button>'+
       '<button class="sec"'+cg+' onclick="location.href=url(\'/api/archivio/zip?cosa=grezzi&id='+o.id+'\')">Scarica i grezzi</button>'+
       '<button class="sec" onclick="riapri('+o.id+')">Riapri in scansione</button>'+
       '</div>'+
       '<div class="g2" style="margin-top:8px">'+
       '<button class="sec"'+cf+' onclick="window.open(url(\'/api/archivio/provino.jpg?id='+o.id+'\'),\'_blank\')">Vedi il provino</button>'+
       '<button class="sec"'+cf+' onclick="salvaProvino('+o.id+')">Salva il provino</button>'+
       '</div>'+
       '<div class="nota">'+ri.n_esportati+' fotogrammi esportati ('+mb(ri.peso_esportati)+') · '+
       ri.n_grezzi+' grezzi ('+mb(ri.peso_grezzi)+')'+
       (ri.mancanti ? ('<br><b>'+ri.mancanti+(ri.mancanti===1
          ? ' file risultava in archivio ma non e\' piu\''
          : ' file risultavano in archivio ma non sono piu\'')+
        ' sul disco.</b> Tolti dal conteggio; le righe dei fotogrammi restano.')
        : '')+
       (ri.n_esportati||ri.n_grezzi ? '' :
        '<br>Niente da scaricare: di questo ordine sul disco non e\' rimasto nessun file.')+
       '</div></section>';

    h+='<section class="pannello"><h2>Avvisa il cliente</h2>'+
       '<label>Messaggio</label><textarea id="d_msg">'+MODELLO+'</textarea>'+
       '<div class="nota">Si possono usare {cliente} {codice} {fotogrammi} {rulli} {data}.</div>'+
       '<div class="row"><button onclick="avvisa('+o.id+')">Apri WhatsApp</button>'+
       '<button class="ghost" onclick="salvaProvino('+o.id+')">Prepara il provino</button></div>'+
       '<div class="nota" id="esitoavviso"></div>'+
       '<div class="nota">WhatsApp non permette a un programma esterno di allegare '+
       'un file o di premere invio: si apre la conversazione con il messaggio gia\' '+
       'scritto, il provino resta nella cartella dell\'ordine a un trascinamento di '+
       'distanza, e l\'invio lo fa una persona.</div></section>';

    r.rulli.forEach(function(ru){
      h+='<section class="pannello"><h2>'+ru.etichetta+
         ' <span>'+ru.formato+' · '+ru.fotogrammi.length+' fotogrammi · '+ru.stato+'</span></h2>'+
         '<div class="row" style="margin-top:0"><button class="ghost" style="width:auto" '+
         'onclick="riapriRullo('+o.id+','+ru.id+')">Riapri questo rullo</button>'+
         '<button class="male" style="width:auto" '+
         'onclick="eliminaRullo('+o.id+','+ru.id+','+
           ru.fotogrammi.reduce(function(a,f){
             return a+(f.esiste?1:0)+(f.esiste_raw?1:0); },0)+
         ')">Elimina questo rullo</button></div>'+
         '<div class="provini">';
      ru.fotogrammi.forEach(function(f){
        h+='<div class="prov"><img loading="lazy" src="'+url('/api/provino.jpg?id='+f.id)+'"'+
           (f.esiste ? '' : ' title="Il file di questo fotogramma non e\' piu\' sul disco"')+
           '>'+'<div class="n">'+f.numero+'</div></div>';
      });
      h+='</div></section>';
    });

    h+='<section class="pannello"><h2>Spazio</h2>'+
       '<div class="g3">'+
       '<button class="ghost" onclick="libera('+o.id+',\'grezzi\')">Elimina i grezzi</button>'+
       '<button class="ghost" onclick="libera('+o.id+',\'esportati\')">Elimina le foto</button>'+
       '<button class="male" onclick="libera('+o.id+',\'tutto\')">Elimina tutto</button>'+
       '</div>'+
       '<div class="nota">I grezzi occupano quasi tutto lo spazio e sono l\'unica cosa '+
       'che non si rifa\': per riaverli bisogna rimettere la pellicola nella macchina. '+
       'La riga dell\'ordine resta comunque in archivio.</div></section>';

    el('dettaglio').innerHTML=h;
    if(ULTIMO_ESITO){ esitoElim(ULTIMO_ESITO); ULTIMO_ESITO=''; }
  });
}

var MODELLO = "Buongiorno {cliente}, le sue scansioni sono pronte: ordine {codice}, {fotogrammi} fotogrammi. Puo' passare a ritirarle quando vuole. Grazie!";

function salvaNomi(oid, cid){
  post('/api/archivio/rinomina', {cliente_id:cid, nome:el('d_cliente').value,
       telefono:el('d_tel').value, ordine_id:oid, note:el('d_note').value})
   .then(function(){ caricaOrdini(); apri(oid); });
}
function riapri(oid){ location.href = url('/?ordine='+oid); }
function riapriRullo(oid, rid){ location.href = url('/?ordine='+oid+'&rullo='+rid); }
function salvaProvino(oid){
  post('/api/archivio/provino',{ordine_id:oid}).then(function(r){
    var e=el('esitoavviso');
    if(e) e.textContent = r.errore ? r.errore : ('Provino salvato in: '+r.percorso);
    else alert(r.errore || ('Provino salvato in: '+r.percorso));
  });
}
function avvisa(oid){
  post('/api/archivio/avviso',{ordine_id:oid, modello:el('d_msg').value})
   .then(function(r){
    if(r.errore){ el('esitoavviso').textContent=r.errore; return; }
    el('esitoavviso').innerHTML = r.ha_numero
      ? ('Apro la conversazione con '+r.numero+'.')
      : ('Questo cliente non ha un numero in scheda: WhatsApp si apre con il '+
         'messaggio pronto e il destinatario lo scegli tu.');
    window.open(r.link, '_blank');
  });
}
/* --- eliminazioni ---
   Tutte e tre passano di qui perche' fanno la stessa cosa in scala diversa, e
   perche' la domanda difficile e' sempre la seconda: "anche i file?". Va posta
   a parte, con scritto quanti sono, e con "no" come risposta piu' facile. */
function chiediFile(quanti){
  /* quanti: un numero se lo si conosce, null se no. Scrivere "1 file" quando
     sono trenta e' peggio che non scrivere niente: la conferma serve a far
     capire la portata del gesto, e un numero sbagliato la fa sottovalutare. */
  if(quanti === 0) return false;
  var quali = (quanti === null || quanti === undefined)
    ? 'anche i file dal disco' : ('anche i '+quanti+' file dal disco');
  return confirm('Cancello '+quali+'?\n\n'+
    'OK = cancella i file (i grezzi non si rifanno: bisogna rimettere la '+
    'pellicola nella macchina).\n'+
    'Annulla = togli solo dall\'archivio, i file restano dove sono.');
}
function esitoElim(t){
  var e=el('esitoelimina'); if(e) e.textContent=t; else alert(t);
}
function eliminaRullo(oid, rid, quanti){
  if(!confirm('Elimino questo rullo dall\'archivio?')) return;
  post('/api/archivio/elimina',{cosa:'rullo', id:rid, file:chiediFile(quanti)})
   .then(function(r){
    if(r.errore){ esitoElim(r.errore); return; }
    caricaOrdini(); apri(oid); caricaScaduti();
  });
}
function eliminaOrdine(oid){
  var n = (RIEP.n_grezzi||0) + (RIEP.n_esportati||0);
  if(!confirm('Elimino l\'ordine '+(RIEP.codice||'')+' dall\'archivio?\n\n'+
              'Spariscono la riga dell\'ordine, i suoi rulli e i suoi fotogrammi.')) return;
  post('/api/archivio/elimina',{cosa:'ordine', id:oid, file:chiediFile(n)})
   .then(function(r){
    if(r.errore){ esitoElim(r.errore); return; }
    SEL=0; el('dettaglio').innerHTML='<div class="pannello vuoto">Ordine '+
      (r.codice||'')+' eliminato'+(r.file_tolti?(', '+r.file_tolti+' file cancellati'):'')+'.</div>';
    caricaOrdini(); caricaScaduti();
  });
}
function eliminaCliente(cid){
  var nome = el('d_cliente') ? el('d_cliente').value : '';
  if(!confirm('Elimino il cliente '+nome+' e TUTTI i suoi ordini?\n\n'+
              'Non e\' solo questo ordine: se ne va tutta la sua storia in archivio.')) return;
  if(!confirm('Confermi? Cliente: '+nome)) return;
  post('/api/archivio/elimina',{cosa:'cliente', id:cid, file:chiediFile(null)})
   .then(function(r){
    if(r.errore){ esitoElim(r.errore); return; }
    SEL=0; el('dettaglio').innerHTML='<div class="pannello vuoto">'+
      (r.nome||'Cliente')+' eliminato: '+r.ordini+
      (r.ordini===1?' ordine':' ordini')+
      (r.file_tolti?(', '+r.file_tolti+' file cancellati'):'')+'.</div>';
    caricaOrdini(); caricaScaduti();
  });
}

function libera(oid, cosa){
  var che = {grezzi:'i file grezzi', esportati:'le foto esportate', tutto:'TUTTI i file'}[cosa];
  if(!confirm('Elimino '+che+' di questo ordine?\n\nNon si torna indietro: i grezzi '+
              'si rifanno solo rimettendo la pellicola nella macchina.')) return;
  post('/api/archivio/libera',{ordine_id:oid, cosa:cosa}).then(function(r){
    if(r.errore){ esitoElim(r.errore); return; }
    var t = r.tolti+' file eliminati, '+mb(r.liberati)+' liberati.';
    if(r.gia_spariti) t += ' '+r.gia_spariti+' risultavano in archivio ma '+
      'non erano piu\' sul disco: tolti anche dal conteggio.';
    if(r.n_falliti) t += ' '+r.n_falliti+' non si sono lasciati cancellare '+
      '(di solito perche\' aperti da un altro programma): '+r.falliti.join(', ')+'.';
    ULTIMO_ESITO = t;
    caricaOrdini(); apri(oid); caricaScaduti();
  });
}

/* --- cartella d'archivio --- */
function sfoglia(p){
  get('/api/archivio/cartelle?percorso='+encodeURIComponent(p||'')).then(function(r){
    if(r.errore){ alert(r.errore); return; }
    var c=el('cartelle'); c.style.display='block'; c.innerHTML='';
    if(r.su!==undefined && r.percorso){
      var su=document.createElement('div'); su.textContent='.. (indietro)';
      su.onclick=function(){ sfoglia(r.su); }; c.appendChild(su);
    }
    if(r.percorso){
      var qui=document.createElement('div');
      qui.innerHTML='<b style="color:var(--acc)">Usa questa: '+r.percorso+'</b>';
      qui.onclick=function(){ el('cartella').value=r.percorso; c.style.display='none'; };
      c.appendChild(qui);
    }
    (r.cartelle||[]).forEach(function(d){
      var x=document.createElement('div'); x.textContent=d;
      x.onclick=function(){ sfoglia(d); }; c.appendChild(x);
    });
  });
}
function salvaImpostazioni(){
  post('/api/archivio/impostazioni',{archivio_dir:el('cartella').value,
       conservazione_giorni:parseInt(el('giorni').value)||30,
       whatsapp_prefisso:el('prefisso').value,
       pulizia:el('pulizia').value})
   .then(function(r){
    el('cartella').value=r.archivio_dir||'';
    if(r.pulizia) el('pulizia').value=r.pulizia;
    el('notaregistro').textContent = r.registro
      ? ('Registro scritto: '+r.registro)
      : 'Registro non scritto: controllare che la cartella esista.';
    caricaScaduti(); caricaOrdini();
  });
}
function pulisciAdesso(){
  var modo = el('pulizia').value;
  if(modo==='segnala'){
    el('scaduti').innerHTML='<div class="nota">La conservazione e\' su '+
      '"segnala soltanto": non c\'e\' niente da applicare. Per far cancellare '+
      'da sola, scegliere cosa cancellare e salvare.</div>';
    return;
  }
  if(!confirm('Applico adesso la conservazione a tutti gli ordini scaduti?\n\n'+
              'Verranno cancellati i file ('+modo+') degli ordini piu\' vecchi '+
              'di '+(el('giorni').value||30)+' giorni.')) return;
  post('/api/archivio/pulizia',{}).then(function(r){
    if(r.errore){ el('scaduti').innerHTML='<div class="nota">'+r.errore+'</div>'; return; }
    el('scaduti').innerHTML='<div class="nota">'+r.tolti+' file tolti da '+
      r.puliti+' ordini.</div>';
    setTimeout(function(){ caricaScaduti(); caricaOrdini(); }, 600);
  });
}
function caricaScaduti(){
  get('/api/archivio/scaduti?giorni='+(parseInt(el('giorni').value)||30))
   .then(function(r){
    var e=el('scaduti'), l=r.ordini||[];
    el('scadutitot').textContent = l.length ? (l.length+' oltre i '+r.giorni+' giorni') : '';
    if(!l.length){ e.innerHTML='<div class="nota">Nessun ordine con file fermi '+
      'da piu\' di '+r.giorni+' giorni. Niente da fare.</div>'; return; }
    var peso=l.reduce(function(a,o){return a+o.peso_grezzi+o.peso_esportati;},0);
    var modo = el('pulizia').value;
    var cosa = {segnala:'Non viene eliminato niente da solo: si apre l\'ordine e si decide.',
                grezzi:'Alla prossima pulizia i grezzi di questi ordini vengono cancellati; le foto restano.',
                tutto:'Alla prossima pulizia grezzi e foto di questi ordini vengono cancellati.'}[modo];
    var h='<div class="grande">'+mb(peso)+'</div>'+
      '<div class="nota">occupati da '+l.length+' ordini fermi da piu\' di '+r.giorni+
      ' giorni. '+cosa+'</div>'+
      '<table class="sc"><tr><th>Codice</th><th>Cliente</th><th>Giorni</th><th>Peso</th></tr>';
    l.slice(0,12).forEach(function(o){
      h+='<tr><td>'+o.codice+'</td><td>'+(o.cliente||'')+'</td><td>'+
         Math.round(o.giorni)+'</td><td>'+mb(o.peso_grezzi+o.peso_esportati)+'</td></tr>';
    });
    e.innerHTML=h+'</table>';
  });
}

el('cerca').oninput=function(){ clearTimeout(window._c);
  window._c=setTimeout(caricaOrdini,250); };
el('giorni').onchange=caricaScaduti;
el('pulizia').onchange=caricaScaduti;

get('/api/archivio/cartelle').then(function(r){ el('cartella').value=r.attuale||''; });
get('/api/archivio/ordini').then(function(r){
  el('giorni').value=r.conservazione||30;
  if(r.pulizia) el('pulizia').value=r.pulizia;
  caricaScaduti(); });
caricaOrdini();
</script></body></html>
"""


PAGINA = r"""<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Scansione pellicola</title>
<style>
/*TEMA*/
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);overflow:hidden;
 font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}

/* --- Impaginazione per il monitor della macchina: 4:3, al massimo
   1280x720, con il browser che si mangia una parte dell'altezza. Tutto sta
   in uno schermo senza scorrere la pagina: a sinistra la colonna delle
   schede (i sottomenu), poi il pannello della scheda aperta, al centro
   l'immagine con sotto la striscia del rullo e la barra dei comandi
   principali, che non sparisce mai. --- */
:root{--testa:46px;--binario:78px;--lato:300px;--striscia:72px}
body{display:flex;flex-direction:column}

header{display:flex;align-items:center;gap:12px;padding:0 12px;
 height:var(--testa);flex:0 0 auto;
 border-bottom:1px solid var(--line);background:var(--bg)}
header .nome{font-weight:600;letter-spacing:.2px;white-space:nowrap}
header .sp{flex:1}
#stato,#sensori{font-size:12px;color:var(--dim);white-space:nowrap;
 overflow:hidden;text-overflow:ellipsis;min-width:0}
.pallino{display:inline-block;width:8px;height:8px;border-radius:50%;
 background:var(--male);margin-right:5px;vertical-align:middle}
.pallino.on{background:var(--ok)}
button.stop{width:auto;flex:0 0 auto;background:#6b2420;color:#ffd9d4;
 border:1px solid #8c3029;font-weight:700;letter-spacing:.5px;
 padding:8px 22px;border-radius:6px;cursor:pointer}

main{flex:1;min-height:0;display:grid;
 grid-template-columns:var(--binario) var(--lato) minmax(0,1fr);
 gap:8px;padding:8px;overflow:hidden}

/* --- le schede: un pannello alla volta, invece di tre colonne da scorrere --- */
nav.binario{display:flex;flex-direction:column;gap:6px;min-height:0}
nav.binario button{background:var(--campo);color:var(--dim);
 border:1px solid var(--line);border-radius:8px;padding:10px 2px;
 font-size:12px;font-weight:600;line-height:1.15;
 display:flex;flex-direction:column;align-items:center;gap:5px}
nav.binario button svg{width:22px;height:22px;stroke:currentColor;fill:none;
 stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
nav.binario button.on{background:var(--acc);color:var(--su-acc);
 border-color:var(--acc)}
nav.binario .conta{font-size:10px;min-width:16px;padding:0 4px;
 border-radius:8px;background:var(--avviso);color:#1a1310;display:none}
nav.binario .conta.si{display:inline-block}
.schede{overflow-y:auto;min-height:0;min-width:0}
.scheda{display:none;flex-direction:column;gap:10px}
.scheda.on{display:flex}

aside{overflow-y:auto;display:flex;flex-direction:column;gap:10px}
.pannello{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}
.pannello h2{font-size:11px;text-transform:uppercase;letter-spacing:.9px;
 color:var(--acc);margin:0 0 10px;display:flex;justify-content:space-between}
.pannello h2 span{color:var(--dim);text-transform:none;letter-spacing:0}

label{display:block;font-size:12px;color:var(--dim);margin:9px 0 3px}
label b{color:var(--ink);float:right;font-weight:600;font-variant-numeric:tabular-nums}
input,select,button{font:inherit;background:var(--campo);color:var(--ink);
 border:1px solid var(--line);border-radius:5px;padding:7px 9px;width:100%}
/* I cursori: quelli di serie del browser sono azzurri, e in mezzo a una
   pagina calda dove si giudica il colore un azzurro acceso e' l'ultima cosa
   che serve. Il pollice e' grande abbastanza da prenderlo col dito. */
input[type=range]{-webkit-appearance:none;appearance:none;padding:0;
 background:none;border:0;height:22px;cursor:pointer}
input[type=range]::-webkit-slider-runnable-track{height:4px;border-radius:2px;
 background:var(--griglia)}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;
 width:18px;height:18px;border-radius:50%;background:var(--card);
 border:3px solid var(--acc);margin-top:-7px;
 box-shadow:0 1px 3px var(--ombra)}
input[type=range]::-moz-range-track{height:4px;border-radius:2px;background:var(--griglia)}
input[type=range]::-moz-range-thumb{width:16px;height:16px;border-radius:50%;
 background:var(--card);border:3px solid var(--acc);box-shadow:0 1px 3px var(--ombra)}
input[type=range]:focus{outline:none}
@media(pointer:coarse){
 input[type=range]{height:34px}
 input[type=range]::-webkit-slider-thumb{width:26px;height:26px;margin-top:-11px}
 input[type=range]::-moz-range-thumb{width:24px;height:24px}
}
button{background:var(--acc);border:0;color:var(--su-acc);font-weight:600;cursor:pointer}
button.sec{background:var(--campo2);color:var(--ink)}
button.ghost{background:none;border:1px solid var(--line);color:var(--dim)}
button:disabled{opacity:.45;cursor:not-allowed}
.row{display:flex;gap:8px;margin-top:10px}
.griglia2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.griglia3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}

/* --- centro: il visore --- */
.centro{display:flex;flex-direction:column;gap:8px;min-width:0;min-height:0}
.strumenti{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.gruppo{display:flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.gruppo button{background:var(--campo);color:var(--dim);border:0;border-radius:0;
 font-weight:500;padding:7px 13px;width:auto;white-space:nowrap}
.gruppo button.on{background:var(--acc);color:var(--su-acc);font-weight:600}
.strumenti .info{font-size:12px;color:var(--dim);margin-left:auto;
 font-variant-numeric:tabular-nums}

.visore{position:relative;flex:1;min-height:0;background:var(--neutro);
 border:1px solid var(--line);border-radius:8px;overflow:hidden;
 display:flex;align-items:center;justify-content:center}
.tela{position:relative;display:none;max-width:100%;max-height:100%;
 line-height:0;touch-action:none}
.tela img{display:block;max-width:100%;max-height:100%;
 -webkit-user-select:none;user-select:none;-webkit-user-drag:none}
#clip{position:absolute;inset:0;overflow:hidden;width:50%;display:none}
#clip img{max-width:none;max-height:none;height:100%}
#maniglia{position:absolute;top:0;bottom:0;left:50%;width:2px;
 background:rgba(255,255,255,.85);display:none;cursor:ew-resize;
 box-shadow:0 0 8px rgba(0,0,0,.6)}
#maniglia::after{content:'';position:absolute;top:50%;left:50%;width:34px;height:34px;
 margin:-17px 0 0 -17px;border-radius:50%;background:rgba(255,255,255,.85)}
.etichetta{position:absolute;top:8px;font-size:11px;letter-spacing:.5px;
 background:rgba(0,0,0,.55);color:#fff;padding:3px 8px;border-radius:4px;display:none}
#et_prima{left:8px} #et_dopo{right:8px}
#griglia{position:absolute;inset:0;display:none;pointer-events:none;
 background-image:linear-gradient(rgba(255,255,255,.28) 1px,transparent 1px),
                  linear-gradient(90deg,rgba(255,255,255,.28) 1px,transparent 1px);
 background-size:33.333% 33.333%}
.vuoto{color:var(--dim);font-size:13px;text-align:center;padding:24px}

.isto{background:var(--card);border:1px solid var(--line);border-radius:8px;
 padding:8px 10px}
.isto canvas{width:100%;height:96px;display:block}
.tagli{display:flex;gap:14px;font-size:11px;color:var(--dim);margin-top:6px;
 font-variant-numeric:tabular-nums;flex-wrap:wrap}
.tagli b{font-weight:600}
.tagli .male{color:var(--male)} .tagli .avviso{color:var(--avviso)}

/* --- sotto l'immagine: la striscia del rullo, come una pellicola --- */
.filmina{flex:0 0 var(--striscia);display:flex;gap:8px;min-height:0;
 background:var(--card);border:1px solid var(--line);border-radius:8px;
 padding:6px}
.filmina .testa{flex:0 0 92px;display:flex;flex-direction:column;
 justify-content:center;font-size:11px;color:var(--dim);line-height:1.3;
 overflow:hidden}
.filmina .testa b{color:var(--acc);text-transform:uppercase;letter-spacing:.8px}
.striscia{flex:1;min-width:0;display:flex;gap:6px;overflow-x:auto;
 overflow-y:hidden;align-items:stretch}
.prov{position:relative;flex:0 0 auto;height:100%;aspect-ratio:3/2;
 border:2px solid transparent;border-radius:4px;
 overflow:hidden;background:var(--neutro);cursor:pointer}
.prov.sel{border-color:var(--acc)}
.prov img{width:100%;height:100%;object-fit:cover;display:block}
.prov .n{position:absolute;left:0;bottom:0;font-size:10px;padding:1px 4px;
 background:rgba(0,0,0,.7);color:#fff;border-radius:0 4px 0 0}
.prov .s{position:absolute;right:3px;top:3px;width:7px;height:7px;border-radius:50%}
.prov .s.ok{background:var(--ok)} .prov .s.avviso{background:var(--avviso)}
.prov .s.errore{background:var(--male)}

/* --- la barra dei comandi principali: sempre visibile, tasti grandi --- */
.comandi{flex:0 0 auto;display:flex;gap:8px;align-items:center;
 background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px}
.comandi button{width:auto;flex:0 0 auto;padding:11px 16px;white-space:nowrap}
.comandi button.principale{padding:11px 26px;font-size:15px}
.comandi .avanz{flex:1;min-width:0}
.comandi #msg,.comandi .avvisi{white-space:nowrap;overflow:hidden;
 text-overflow:ellipsis}
.comandi .avvisi{margin-top:2px;cursor:pointer}
.vuota{color:var(--dim);font-size:12px;padding:6px 0}

.bar{height:6px;background:var(--campo2);border-radius:3px;overflow:hidden;margin:4px 0 6px}
.bar>div{height:100%;background:var(--acc);width:0;transition:width .3s}
#msg{font-size:12px;color:var(--dim)}
#log{font:11px/1.55 ui-monospace,Consolas,monospace;color:var(--dim);
 max-height:140px;overflow:auto;margin-top:8px;white-space:pre-wrap}
.avvisi{color:var(--avviso);font-size:12px;margin-top:6px}
table{width:100%;border-collapse:collapse;font-size:12px;margin-top:6px}
td,th{padding:5px 4px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--dim);font-weight:500}

/* --- pannello colore, a sezioni richiudibili --- */
details.sez{border-top:1px solid var(--line)}
details.sez:first-of-type{border-top:0}
details.sez>summary{list-style:none;cursor:pointer;padding:9px 0;
 font-size:11px;text-transform:uppercase;letter-spacing:.9px;color:var(--acc);
 display:flex;justify-content:space-between;align-items:center;user-select:none}
details.sez>summary::-webkit-details-marker{display:none}
details.sez>summary::after{content:'+';color:var(--dim);font-size:14px}
details.sez[open]>summary::after{content:'\2212'}
details.sez>div{padding-bottom:10px}
.cur{display:flex;align-items:baseline;justify-content:space-between;
 font-size:12px;color:var(--dim);margin:8px 0 2px}
.cur b{color:var(--ink);font-variant-numeric:tabular-nums}
.cur i{font-style:normal;color:var(--dim);cursor:pointer;padding:0 2px 0 9px;
 font-size:13px}
.pipetta{background:var(--campo2);color:var(--ink)}
.pipetta.on{background:var(--acc);color:var(--su-acc)}
#tela.campiona{cursor:crosshair}
#curva{width:100%;height:150px;display:block;background:var(--tela);
 border:1px solid var(--line);border-radius:6px;touch-action:none}
.canali{display:flex;gap:6px;margin-top:8px}
.canali button{padding:6px 0;font-size:12px;font-weight:500;background:var(--campo);
 color:var(--dim);border:1px solid var(--line)}
.canali button.on{background:var(--acc);color:var(--su-acc);border-color:var(--acc)}

/* --- Il dito non e' il mouse. Sotto i 44 px si sbaglia bersaglio, e qui
       sbagliare bersaglio vuol dire comandare un motore che tira pellicola. --- */
@media(pointer:coarse){
 body{font-size:16px}
 input,select,button{padding:13px 12px;font-size:16px;min-height:48px}
 button{min-height:52px;border-radius:8px}
 .gruppo button{min-height:48px;padding:12px 16px}
 label{font-size:14px}
 .prov{aspect-ratio:3/2}
}
/* 4:3 a 1024 o 960 di larghezza: pannello piu' stretto, niente nome. */
@media(max-width:1100px){
 :root{--lato:264px;--binario:70px}
 header .nome{display:none}
 .comandi .facoltativo{display:none}
 /* l'avanzamento sopra i tasti: accanto, a 960 px, restavano tre parole */
 .comandi{flex-wrap:wrap;row-gap:6px}
 .comandi .avanz{order:-1;flex-basis:100%}
 .comandi button{padding:10px 12px;flex:1 1 auto}
 .comandi button.principale{padding:10px 18px}
}
/* Poca altezza (1280x720 con le barre del browser): si stringe tutto in
   verticale, non si toglie niente. */
@media(max-height:700px){
 :root{--testa:40px;--striscia:58px}
 main{padding:6px;gap:6px}
 .pannello{padding:9px 10px}
 label{margin:6px 0 2px}
 nav.binario button{padding:7px 2px;gap:3px}
 .strumenti .gruppo button{padding:5px 11px}
 .comandi{padding:6px}
 .comandi button{padding:8px 12px}
 .comandi button.principale{padding:8px 18px}
 .isto canvas{height:72px}
}
/* Tablet in verticale e telefoni: una colonna, le schede diventano una fila
   in alto e la pagina scorre. */
@media(max-width:860px){
 body{overflow:auto}
 main{display:flex;flex-direction:column;height:auto;overflow:visible}
 nav.binario{flex-direction:row;order:-2}
 nav.binario button{flex:1}
 .centro{order:-1}
 .visore{height:52vh;flex:0 0 auto}
 .schede{overflow:visible}
 .comandi{flex-wrap:wrap}
 .comandi .avanz{flex-basis:100%}
}
</style></head><body>

<header>
 <div class="nome">Masterflex D1008</div>
 <div id="stato"><span class="pallino"></span>non connesso</div>
 <div id="sensori"></div>
 <div class="sp"></div>
 <a id="vaiarchivio" href="/archivio" style="color:var(--dim);text-decoration:none;
  font-size:13px;padding:7px 12px;border:1px solid var(--line);border-radius:6px;
  white-space:nowrap">Archivio &rarr;</a>
 /*ICONA*/
 <button class="stop" onclick="fermaTutto()">STOP</button>
</header>

<main>
<!-- ============ SCHEDE: i sottomenu, uno alla volta ============ -->
<nav class="binario" id="binario">
 <button data-s="lavoro" onclick="apriScheda('lavoro')"><svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 5v14M17 5v14M3 9h4M3 15h4M17 9h4M17 15h4"/></svg>Lavoro</button>
 <button data-s="colore" onclick="apriScheda('colore')"><svg viewBox="0 0 24 24"><circle cx="9" cy="10" r="5"/><circle cx="15" cy="10" r="5"/><circle cx="12" cy="15" r="5"/></svg>Colore</button>
 <button data-s="correzioni" onclick="apriScheda('correzioni')"><svg viewBox="0 0 24 24"><path d="M4 20l9-9M14 4l1.2 2.8L18 8l-2.8 1.2L14 12l-1.2-2.8L10 8l2.8-1.2z"/></svg>Ritocco</button>
 <button data-s="macchina" onclick="apriScheda('macchina')"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/></svg>Macchina</button>
 <button data-s="diario" onclick="apriScheda('diario')"><svg viewBox="0 0 24 24"><path d="M6 3h9l3 3v15H6z"/><path d="M9 10h6M9 14h6M9 18h4"/></svg>Diario<span class="conta" id="conta_avvisi"></span></button>
</nav>

<div class="schede">
 <div class="scheda" id="s_lavoro">
 <section class="pannello">
  <h2>Ordine</h2>
  <div class="griglia2">
   <div><label>Cliente</label><input id="cliente" placeholder="Nome"></div>
   <div><label>Note</label><input id="note"></div>
  </div>
  <div class="row"><button onclick="creaOrdine()">Crea ordine</button>
   <button class="sec" onclick="caricaOrdini()">Aggiorna</button></div>
  <label style="margin-top:12px">Ordine aperto</label>
  <select id="ordine_id"></select>
  <table id="ordini"></table>
 </section>
 <section class="pannello">
  <h2>Rullo</h2>
  <div class="griglia2">
   <div><label>Etichetta</label><input id="etichetta" placeholder="rullo 1"></div>
   <div><label>Fotogrammi</label>
    <input id="attesi" type="number" placeholder="automatico" min="0"></div>
   <div><label>Formato</label><select id="formato"></select></div>
   <div><label>Preset</label><select id="preset"></select></div>
   <div><label>Profilo</label><select id="profilo"></select></div>
   <div><label>Specchiato</label>
    <select id="specchiato"><option value="">no</option><option value="1">si</option></select></div>
  </div>
  <div style="font-size:11px;color:var(--dim);margin-top:6px">
   Lasciare vuoto: i fotogrammi si contano leggendo la striscia e ci si ferma
   quando la pellicola finisce. Il tetto del formato (72 per il mezzo formato,
   40 per il 135) serve solo da rete di sicurezza.</div>
  <div class="row"><button onclick="creaRullo()">Aggiungi rullo</button></div>
  <label style="margin-top:12px">Rullo in lavorazione</label>
  <select id="rullo_id"></select>
 </section>
 </div>

 <div class="scheda" id="s_colore">
  <div class="isto">
   <canvas id="isto" width="512" height="96"></canvas>
   <div class="tagli" id="tagli"></div>
  </div>
 <section class="pannello" id="colore">
  <details class="sez" open><summary>Bilanciamento</summary><div>
   <div class="griglia3">
    <button class="pipetta" id="b_pip" onclick="pipetta()">Pipetta</button>
    <button class="sec" onclick="wbAuto()">Auto</button>
    <button class="ghost" onclick="wbAzzera()">Azzera</button>
   </div>
   <div id="pip_nota" style="font-size:11px;color:var(--dim);margin-top:6px"></div>
   <div class="cur"><span>Temperatura</span><span><b id="v_temperatura_k">5500</b> K
    <i onclick="reset('temperatura_k')">&#8635;</i></span></div>
   <input type="range" id="temperatura_k" min="2000" max="10000" step="50" value="5500">
   <div class="cur"><span>Tinta <span style="color:#6f8f5a">verde</span> &rarr;
    <span style="color:#a86b9a">magenta</span></span>
    <span><b id="v_tinta_vm">0</b><i onclick="reset('tinta_vm')">&#8635;</i></span></div>
   <input type="range" id="tinta_vm" min="-100" max="100" step="1" value="0">
  </div></details>

  <details class="sez" open><summary>Tono</summary><div>
   <div class="cur"><span>Esposizione</span><span><b id="v_esposizione">0,00</b> stop
    <i onclick="reset('esposizione')">&#8635;</i></span></div>
   <input type="range" id="esposizione" min="-3" max="3" step="0.05" value="0">
   <div class="cur"><span>Contrasto</span><span><b id="v_contrasto">0</b>
    <i onclick="reset('contrasto')">&#8635;</i></span></div>
   <input type="range" id="contrasto" min="-100" max="100" step="1" value="0">
   <div class="cur"><span>Luminosita</span><span><b id="v_luminosita">0</b>
    <i onclick="reset('luminosita')">&#8635;</i></span></div>
   <input type="range" id="luminosita" min="-100" max="100" step="1" value="0">
   <div class="cur"><span>Gamma</span><span><b id="v_gamma_tono">1,00</b>
    <i onclick="reset('gamma_tono')">&#8635;</i></span></div>
   <input type="range" id="gamma_tono" min="0.5" max="2" step="0.01" value="1">
   <div class="cur"><span>Chiarezza</span><span><b id="v_chiarezza">0</b>
    <i onclick="reset('chiarezza')">&#8635;</i></span></div>
   <input type="range" id="chiarezza" min="-100" max="100" step="1" value="0">
  </div></details>

  <details class="sez"><summary>Colore</summary><div>
   <div class="cur"><span>Saturazione</span><span><b id="v_saturazione">1,00</b>
    <i onclick="reset('saturazione')">&#8635;</i></span></div>
   <input type="range" id="saturazione" min="0" max="2" step="0.01" value="1">
   <div class="cur"><span>Tinta generale</span><span><b id="v_hue">0</b>&deg;
    <i onclick="reset('hue')">&#8635;</i></span></div>
   <input type="range" id="hue" min="-30" max="30" step="0.5" value="0">
   <div class="cur" style="margin-top:14px"><span>Tricromia</span>
    <span style="font-size:11px">scatti di densita</span></div>
   <div class="cur"><span style="color:#e0655a">Rosso</span><span><b id="v_barra_r">0</b>
    <i onclick="reset('barra_r')">&#8635;</i></span></div>
   <input type="range" id="barra_r" min="-10" max="10" step="0.5" value="0">
   <div class="cur"><span style="color:#7fb069">Verde</span><span><b id="v_barra_g">0</b>
    <i onclick="reset('barra_g')">&#8635;</i></span></div>
   <input type="range" id="barra_g" min="-10" max="10" step="0.5" value="0">
   <div class="cur"><span style="color:#5a8ce0">Blu</span><span><b id="v_barra_b">0</b>
    <i onclick="reset('barra_b')">&#8635;</i></span></div>
   <input type="range" id="barra_b" min="-10" max="10" step="0.5" value="0">
  </div></details>

  <details class="sez"><summary>Curve</summary><div>
   <canvas id="curva" width="300" height="300"></canvas>
   <div class="canali" id="canali">
    <button data-c="m" class="on">RGB</button>
    <button data-c="r">R</button><button data-c="g">G</button><button data-c="b">B</button>
    <button class="ghost" onclick="curvaAzzera()">&#8635;</button>
   </div>
   <div style="font-size:11px;color:var(--dim);margin-top:6px">
    Tocca per aggiungere un punto, trascina per spostarlo,
    tocca due volte per toglierlo.</div>
  </div></details>

  <div class="row"><button class="ghost" onclick="azzeraTutto()">Azzera tutto</button>
   <button onclick="applica(true)">Applica</button></div>
 </section>
 </div>

 <div class="scheda" id="s_correzioni">
 <section class="pannello">
  <h2>Correzioni <span id="tocco"></span></h2>
  <label>Pulizia infrarosso <b id="v_ir">1,0</b></label>
  <input type="range" id="ir" min="0" max="1" step="0.1" value="1">
  <label>Riduzione grana <b id="v_grana_riduci">0,0</b></label>
  <input type="range" id="grana_riduci" min="0" max="1" step="0.1" value="0">
  <label>Nitidezza <b id="v_nitidezza">0,0</b></label>
  <input type="range" id="nitidezza" min="0" max="1" step="0.1" value="0">
  <label>Graffi senza IR <b id="v_graffi">0,0</b></label>
  <input type="range" id="graffi" min="0" max="1" step="0.1" value="0">
  <label>Polvere <b id="v_polvere">0,0</b></label>
  <input type="range" id="polvere" min="0" max="1" step="0.1" value="0">
  <label>Grana sintetica <b id="v_grana_aggiungi">0,00</b></label>
  <input type="range" id="grana_aggiungi" min="0" max="0.1" step="0.01" value="0">
  <div style="margin-top:12px">
   <label><input type="checkbox" id="ritaglio" checked style="width:auto"> Ritaglio automatico</label>
   <label><input type="checkbox" id="auto_ruota" style="width:auto"> Orientamento automatico</label>
  </div>
  <div class="row"><button class="ghost" onclick="azzeraCorrezioni()">Azzera</button></div>
 </section>
 </div>

 <div class="scheda" id="s_macchina">
  <section class="pannello">
   <h2>Collegamento</h2>
   <label>Scanner</label>
   <select id="driver">
    <option value="sdk">scanner &mdash; macchina vera</option>
    <option value="originale">programma originale &mdash; osservatore</option>
    <option value="mock">simulato &mdash; nessuna macchina</option>
   </select>
   <div style="font-size:11px;color:var(--dim);margin-top:6px">
    <b>Programma originale</b>: si scansiona da PhxScanner, con
    MASTERFLEX.bat voce 14 (osservatore) aperta; "Scansiona rullo" qui
    raccoglie le immagini nel rullo scelto fino a STOP.</div>
   <div class="row"><button onclick="connetti()">Connetti</button></div>
   <div id="amb" style="font-size:11px;color:var(--dim);margin-top:10px"></div>
  </section>
  <section class="pannello">
   <h2>Movimenti a mano</h2>
   <div class="griglia3">
    <button class="sec" onclick="muovi(-1)">&larr; 1 mm</button>
    <button class="sec" onclick="muovi(-0.1)">&larr; 0,1</button>
    <button class="sec" onclick="muovi(1)">1 mm &rarr;</button>
   </div>
   <div class="griglia2" style="margin-top:8px">
    <button class="sec" onclick="fotogramma(-1)">&lsaquo; Fotogramma</button>
    <button class="sec" onclick="fotogramma(1)">Fotogramma &rsaquo;</button>
   </div>
   <div class="griglia2" style="margin-top:8px">
    <button class="sec" onclick="post('/api/fuoco',{mm:0}).then(esito)">Fuoco</button>
    <span></span>
   </div>
   <div class="griglia2" style="margin-top:8px">
    <button class="sec" onclick="espelli('davanti')" title="Esce da dove e' entrata">Espelli davanti</button>
    <button class="sec" onclick="espelli('retro')" title="Prosegue nel verso del trasporto fino a uscire dal retro">Espelli retro</button>
   </div>
   <div style="font-size:11px;color:var(--dim);margin-top:8px">
    La regolazione fine funziona solo con la pellicola caricata: a percorso
    vuoto l'SDK la rifiuta.</div>
  </section>
 </div>

 <div class="scheda" id="s_diario">
  <section class="pannello">
   <h2>Avvisi del lavoro</h2>
   <div id="avvisi_tutti" class="avvisi" style="margin-top:0">nessuno</div>
  </section>
  <section class="pannello">
   <h2>Diario</h2>
   <div id="log" style="max-height:none"></div>
  </section>
 </div>
</div>

<!-- ============ CENTRO: immagine, rullo, comandi ============ -->
<section class="centro">
 <div class="strumenti">
  <div class="gruppo" id="g_vista">
   <button data-v="prima">Negativo</button>
   <button data-v="dopo" class="on">Positivo</button>
   <button data-v="confronto">Confronto</button>
  </div>
  <div class="gruppo" id="g_zoom">
   <button data-z="0" class="on">Adatta</button>
   <button data-z="1">1:1</button>
  </div>
  <button class="ghost" id="b_griglia" style="width:auto" onclick="togliGriglia()">Griglia</button>
  <span class="info" id="zoominfo"></span>
 </div>

 <div class="visore" id="visore">
  <div class="tela" id="tela">
   <img id="im_dopo" alt="">
   <div id="clip"><img id="im_prima" alt=""></div>
   <div id="maniglia"></div>
   <div id="griglia"></div>
   <div class="etichetta" id="et_prima">NEGATIVO</div>
   <div class="etichetta" id="et_dopo">POSITIVO</div>
  </div>
  <div class="vuoto" id="vuoto">Nessuna anteprima.<br>Premere <b>Anteprima</b> qui sotto.</div>
 </div>

 <div class="filmina">
  <div class="testa"><b>Rullo</b><span id="rullo_info"></span>
   <span id="prov_info"></span></div>
  <div class="striscia" id="striscia"></div>
 </div>

 <div class="comandi">
  <button class="sec" onclick="connetti()">Connetti</button>
  <button class="sec" onclick="calibra()">Calibra</button>
  <button class="sec" onclick="carica()" title="Guide al formato del rullo e carrello pronto a ricevere la pellicola">Carica</button>
  <button class="sec" onclick="fotogramma(1)" title="Il carrello porta in finestra il fotogramma successivo">Fotogramma &rsaquo;</button>
  <button class="sec" onclick="anteprima()">Anteprima</button>
  <button class="principale" onclick="scansiona()">Scansiona rullo</button>
  <div class="avanz">
   <div class="bar" style="margin:0 0 3px"><div id="barra"></div></div>
   <div id="msg">pronto</div>
   <div id="avvisi" class="avvisi" onclick="apriScheda('diario')"></div>
  </div>
  <button class="sec" onclick="espelli('davanti')" title="Fa uscire la pellicola da dove e' entrata. Il retro sta nella scheda Macchina">Espelli</button>
 </div>
</section>
</main>

<script>
/* Le schede a sinistra. Quella aperta si ricorda nel browser: e' una
   comodita' di chi sta a quello schermo, non un dato della macchina. */
function apriScheda(n){
  var tutte=document.querySelectorAll('.scheda');
  for(var i=0;i<tutte.length;i++) tutte[i].classList.toggle('on', tutte[i].id==='s_'+n);
  var t=document.querySelectorAll('#binario button');
  for(var j=0;j<t.length;j++) t[j].classList.toggle('on', t[j].getAttribute('data-s')===n);
  try{ localStorage.setItem('scheda', n); }catch(e){}
  if(n==='colore' && window.disegnaCurva){ try{ disegnaCurva(); disegnaIstogramma(); }catch(e){} }
}
(function(){
  var n='lavoro';
  try{ n=localStorage.getItem('scheda')||'lavoro'; }catch(e){}
  if(!document.getElementById('s_'+n)) n='lavoro';
  apriScheda(n);
  /* gli avvisi della barra finiscono anche nel Diario, interi, con un
     numerino sulla scheda: nella barra c'e' posto solo per una riga */
  var a=document.getElementById('avvisi');
  new MutationObserver(function(){
    var testo=a.textContent.trim(), tutti=document.getElementById('avvisi_tutti');
    var c=document.getElementById('conta_avvisi');
    tutti.textContent = testo ? testo.split(' \u00b7 ').join('\n') : 'nessuno';
    tutti.style.whiteSpace='pre-wrap';
    var n=testo ? testo.split(' \u00b7 ').length : 0;
    c.textContent=n; c.classList.toggle('si', n>0);
    a.title=testo;
  }).observe(a,{childList:true,characterData:true,subtree:true});
})();
</script>

<script>
var TOKEN = new URLSearchParams(location.search).get('t') || '';
function url(p){ return p + (TOKEN ? (p.indexOf('?')<0?'?':'&')+'t='+TOKEN : ''); }
/* Se il server non riconosce piu' il PIN - tipicamente perche' e' stato
   riavviato e questa scheda e' rimasta aperta da prima - non ha senso
   continuare a bussare: si torna alla richiesta del PIN. Prima la pagina
   restava aperta a fallire una volta al secondo. */
function scaduto(r){
  if(r && r.status===403){ location.href='/'; return true; }
  return false;
}
function get(p){ return fetch(url(p)).then(function(r){
  if(scaduto(r)) return {}; return r.json(); }); }
function post(p,d){ return fetch(url(p),{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(d||{})})
  .then(function(r){ if(scaduto(r)) return {}; return r.json(); }); }
function val(id){ var e=document.getElementById(id);
  return e.type==='checkbox'?e.checked:e.value; }
function el(id){ return document.getElementById(id); }
function esito(r){
  if(!r || !r.errore) return;
  /* Un alert blocca la pagina e va chiuso a mano: se la macchina non risponde
     capita spesso, e l'operatore si ritrova a chiudere finestrelle. Meglio
     scriverlo dove sta gia' guardando. */
  var e=el('avvisi');
  if(e){ e.textContent=r.errore; setTimeout(function(){
    if(e.textContent===r.errore) e.textContent=''; }, 12000); }
  else alert(r.errore);
}

/* ---------- stato del visore ---------- */
var vista='dopo', uno=false, cx=0.5, cy=0.5, hoAnteprima=false;

function impostaVista(v){
  vista=v;
  Array.prototype.forEach.call(el('g_vista').children,function(b){
    b.classList.toggle('on', b.dataset.v===v); });
  var confronto = (v==='confronto');
  el('clip').style.display = confronto?'block':'none';
  el('maniglia').style.display = confronto?'block':'none';
  el('et_prima').style.display = confronto?'block':'none';
  el('et_dopo').style.display = confronto?'block':'none';
  aggiornaImmagini(true); disegnaIstogramma();
}
function impostaZoom(z){
  uno=z;
  Array.prototype.forEach.call(el('g_zoom').children,function(b){
    b.classList.toggle('on', (b.dataset.z==='1')===z); });
  aggiornaImmagini(true);
}
function togliGriglia(){
  var g=el('griglia'), on=g.style.display!=='block';
  g.style.display=on?'block':'none';
  el('b_griglia').classList.toggle('on',on);
}

function sorgente(quale){
  var v = (vista==='confronto') ? quale : vista;
  var p='/api/anteprima.jpg?vista='+v+(uno?('&zoom=1&cx='+cx.toFixed(3)+'&cy='+cy.toFixed(3)):'');
  return url(p)+'&v='+Date.now();
}
function aggiornaImmagini(forza){
  if(!hoAnteprima) return;
  var dopo = (vista==='prima') ? 'prima' : 'dopo';
  el('im_dopo').src = sorgente(dopo);
  if(vista==='confronto') el('im_prima').src = sorgente('prima');
  el('zoominfo').textContent = uno
    ? ('1:1 · trascina per spostarti · ' + Math.round(cx*100) + '/' + Math.round(cy*100))
    : 'adattata alla finestra';
}

/* Confronto: la maniglia si trascina, col dito o col mouse. */
(function(){
  var tela=el('tela'), man=el('maniglia'), clip=el('clip'), trascino=false;
  function poni(x){
    var r=tela.getBoundingClientRect();
    var f=Math.max(0,Math.min(1,(x-r.left)/r.width));
    clip.style.width=(f*100)+'%'; man.style.left=(f*100)+'%';
  }
  man.addEventListener('pointerdown',function(e){trascino=true;man.setPointerCapture(e.pointerId);e.preventDefault();});
  man.addEventListener('pointermove',function(e){ if(trascino) poni(e.clientX); });
  man.addEventListener('pointerup',function(){trascino=false;});

  /* A 1:1 si trascina l'immagine per spostarsi: e' il gesto che tutti si
     aspettano, e su un tablet e' l'unico che ha senso. */
  var px=0,py=0,pan=false;
  tela.addEventListener('pointerdown',function(e){
    if(!uno || e.target===man) return;
    pan=true; px=e.clientX; py=e.clientY; tela.setPointerCapture(e.pointerId);
  });
  tela.addEventListener('pointermove',function(e){
    if(!pan) return;
    var r=tela.getBoundingClientRect();
    cx=Math.max(0,Math.min(1,cx-(e.clientX-px)/r.width));
    cy=Math.max(0,Math.min(1,cy-(e.clientY-py)/r.height));
    px=e.clientX; py=e.clientY;
  });
  tela.addEventListener('pointerup',function(){ if(pan){pan=false; aggiornaImmagini(true);} });
})();

/* ---------- istogramma ---------- */
function disegnaIstogramma(){
  var v = (vista==='prima') ? 'prima' : 'dopo';
  get('/api/istogramma?vista='+v).then(function(h){
    var c=el('isto'), g=c.getContext('2d');
    g.clearRect(0,0,c.width,c.height);
    if(!h.disponibile){ el('tagli').textContent=''; return; }
    var w=c.width, ht=c.height, n=h.r.length, mx=Math.max(1,h.massimo);
    /* somma additiva, come in ogni programma di scansione: dove i tre canali
       si sovrappongono viene bianco, e dove uno manca si vede quale. Disegnati
       uno sopra l'altro in opacita' normale, l'ultimo coprirebbe gli altri due
       e l'istogramma direbbe meno di quello che sa. */
    var chiaro = document.documentElement.getAttribute('data-tema')==='chiaro';
    g.globalCompositeOperation = chiaro ? 'multiply' : 'lighter';
    var colori = chiaro
      ? {r:'rgba(214,86,74,.72)',g:'rgba(64,150,84,.72)',b:'rgba(72,116,214,.72)'}
      : {r:'rgba(200,60,50,.9)',g:'rgba(70,170,80,.9)',b:'rgba(60,110,220,.9)'};
    ['r','g','b'].forEach(function(k){
      g.beginPath(); g.moveTo(0,ht);
      for(var i=0;i<n;i++){
        /* radice quadrata: senza, un picco solo schiaccia tutto il resto
           a una riga piatta e l'istogramma non dice piu' niente */
        var y = ht - Math.sqrt(h[k][i]/mx)*(ht-2);
        g.lineTo(i*w/(n-1), y);
      }
      g.lineTo(w,ht); g.closePath(); g.fillStyle=colori[k]; g.fill();
    });
    g.globalCompositeOperation='source-over';
    var t=[], nomi={r:'R',g:'G',b:'B'};
    ['r','g','b'].forEach(function(k){
      var lo=h[k+'_basso'], hi=h[k+'_alto'];
      var cl=function(x){ return x>2?'male':(x>0.5?'avviso':''); };
      t.push('<span>'+nomi[k]+' ombre <b class="'+cl(lo)+'">'+lo.toFixed(2)+'%</b>'+
             ' · luci <b class="'+cl(hi)+'">'+hi.toFixed(2)+'%</b></span>');
    });
    el('tagli').innerHTML = t.join('') +
      '<span style="color:var(--dim)">oltre il 2% \u00e8 informazione persa</span>';
  }).catch(function(){});
}

/* ---------- striscia del rullo ---------- */
var provSel=null;
function caricaStriscia(){
  var r=val('rullo_id'); var s=el('striscia');
  if(!r){ s.innerHTML='<div class="vuota">Nessun rullo selezionato.</div>';
          el('rullo_info').textContent=''; return; }
  get('/api/fotogrammi?rullo_id='+r).then(function(d){
    var f=d.fotogrammi||[];
    el('rullo_info').textContent = f.length ? (f.length+' fotogrammi') : '';
    if(!f.length){ s.innerHTML='<div class="vuota">Ancora niente. I fotogrammi compaiono qui mentre la scansione procede.</div>'; return; }
    s.innerHTML='';
    f.forEach(function(x){
      var d1=document.createElement('div'); d1.className='prov'+(provSel===x.id?' sel':'');
      var im=document.createElement('img'); im.loading='lazy';
      im.src=url('/api/provino.jpg?id='+x.id); im.alt='';
      var n=document.createElement('div'); n.className='n'; n.textContent=x.numero;
      var st=document.createElement('div');
      st.className='s '+(x.stato==='ok'?(x.avvisi?'avviso':'ok'):'errore');
      d1.appendChild(im); d1.appendChild(n); d1.appendChild(st);
      d1.onclick=function(){
        provSel=x.id; caricaStriscia();
        el('prov_info').innerHTML = 'Fotogramma <b>'+x.numero+'</b> · '+x.stato+
          (x.rotazione?(' · ruotato '+x.rotazione+'°'):'') +
          (x.avvisi?('<br><span style="color:var(--avviso)">'+x.avvisi+'</span>'):'') +
          (x.file_out?('<br>'+x.file_out):'');
      };
      s.appendChild(d1);
    });
  }).catch(function(){});
}

/* ---------- comandi ---------- */
var wbGuadagni=null;          /* guadagni dal campionamento neutro */
var curve={m:[],r:[],g:[],b:[]};
var canaleCurva='m';

function num(id){ return parseFloat(val(id)); }
function opzioni(){ return {
  profilo:val('profilo'),
  ir:num('ir'), grana_riduci:num('grana_riduci'),
  graffi:num('graffi'), polvere:num('polvere'),
  nitidezza:num('nitidezza'), grana_aggiungi:num('grana_aggiungi'),
  auto_ruota:val('auto_ruota'), ritaglio:val('ritaglio'),
  /* pannello colore */
  esposizione:num('esposizione'),
  /* i guadagni campionati hanno la precedenza sui kelvin: sono una misura,
     non una stima, e sommarli sarebbe correggere due volte */
  temperatura_k: wbGuadagni ? 0 : num('temperatura_k'),
  tinta_vm: num('tinta_vm')/100,
  wb_guadagni: wbGuadagni,
  barra_r:num('barra_r'), barra_g:num('barra_g'), barra_b:num('barra_b'),
  gamma_tono:num('gamma_tono'),
  contrasto:num('contrasto')/100,
  luminosita:num('luminosita')/100,
  chiarezza:num('chiarezza')/100,
  hue:num('hue'), saturazione:num('saturazione'),
  curva:curve.m, curva_r:curve.r, curva_g:curve.g, curva_b:curve.b }; }

/* ---------- rielaborazione, senza rimettere la pellicola nella macchina ---------- */
var attesa=null, inCorso=false;
function applica(subito){
  if(attesa) clearTimeout(attesa);
  attesa=setTimeout(function(){
    if(inCorso) return;
    inCorso=true;
    post('/api/rielabora', opzioni()).then(function(r){
      inCorso=false;
      if(r && r.errore){ el('pip_nota').textContent=r.errore; return; }
      aggiornaImmagini(true); disegnaIstogramma();
    }).catch(function(){ inCorso=false; });
  }, subito?0:350);   /* il cursore si trascina: si aspetta che si fermi */
}

/* ---------- pipetta del neutro ---------- */
var pipettaAttiva=false;
function pipetta(){
  if(uno){ el('pip_nota').textContent='La pipetta si usa a immagine intera: tornare ad "Adatta".'; return; }
  pipettaAttiva=!pipettaAttiva;
  el('b_pip').classList.toggle('on',pipettaAttiva);
  el('tela').classList.toggle('campiona',pipettaAttiva);
  el('pip_nota').textContent = pipettaAttiva
    ? 'Tocca qualcosa che nella realta era grigio, bianco o nero neutro.' : '';
}
function campiona(ev){
  var im=el('im_dopo'), r=im.getBoundingClientRect();
  var x=(ev.clientX-r.left)/r.width, y=(ev.clientY-r.top)/r.height;
  if(x<0||x>1||y<0||y>1) return;
  var d=opzioni(); d.x=x; d.y=y; d.wb_guadagni=null;
  post('/api/campiona',d).then(function(res){
    if(res && res.errore){ el('pip_nota').textContent=res.errore; return; }
    wbGuadagni=res.guadagni;
    el('pip_nota').innerHTML='Bilanciato sul punto: R <b>'+res.guadagni[0].toFixed(2)+
      '</b> G <b>'+res.guadagni[1].toFixed(2)+'</b> B <b>'+res.guadagni[2].toFixed(2)+'</b>';
    aggiornaImmagini(true); disegnaIstogramma();
  });
  pipetta();
}
function wbAuto(){ wbGuadagni=null; el('temperatura_k').value=5500;
  el('tinta_vm').value=0; mostraValore('temperatura_k'); mostraValore('tinta_vm');
  el('pip_nota').textContent='Bilanciamento automatico della pipeline.'; applica(true); }
function wbAzzera(){ wbGuadagni=null; el('temperatura_k').value=5500;
  el('tinta_vm').value=0; mostraValore('temperatura_k'); mostraValore('tinta_vm');
  el('pip_nota').textContent=''; applica(true); }

/* ---------- curve ---------- */
function curvaAzzera(){ curve[canaleCurva]=[]; disegnaCurva(); applica(true); }
function azzeraTutto(){
  var d={esposizione:0,temperatura_k:5500,tinta_vm:0,contrasto:0,luminosita:0,
         gamma_tono:1,chiarezza:0,saturazione:1,hue:0,barra_r:0,barra_g:0,barra_b:0};
  Object.keys(d).forEach(function(k){ el(k).value=d[k]; mostraValore(k); });
  curve={m:[],r:[],g:[],b:[]}; wbGuadagni=null; el('pip_nota').textContent='';
  disegnaCurva(); azzeraCorrezioni(); applica(true);
}
/* Le tele si disegnano a mano: non ereditano i colori dal foglio di
   stile, se li devono andare a prendere, e ripescarli a ogni cambio tema. */
function tinta(n){
  return getComputedStyle(document.documentElement).getPropertyValue(n).trim();
}
window.dopoTema = function(){ disegnaCurva(); disegnaIstogramma(); };
function disegnaCurva(){
  var c=el('curva'), g=c.getContext('2d'), n=c.width;
  g.clearRect(0,0,n,n);
  g.strokeStyle=tinta('--tela-riga'); g.lineWidth=1;
  for(var i=1;i<4;i++){ var v=i*n/4;
    g.beginPath(); g.moveTo(v,0); g.lineTo(v,n); g.moveTo(0,v); g.lineTo(n,v); g.stroke(); }
  g.strokeStyle=tinta('--tela-diag'); g.beginPath(); g.moveTo(0,n); g.lineTo(n,0); g.stroke();

  var p=curve[canaleCurva].slice().sort(function(a,b){return a[0]-b[0];});
  var pts=[[0,0]].concat(p).concat([[1,1]]);
  var col={m:tinta('--ink'),r:'#e0655a',g:'#7fb069',b:'#5a8ce0'}[canaleCurva];
  g.strokeStyle=col; g.lineWidth=2; g.beginPath();
  pts.forEach(function(q,i){ var x=q[0]*n, y=n-q[1]*n;
    if(i===0) g.moveTo(x,y); else g.lineTo(x,y); });
  g.stroke();
  g.fillStyle=col;
  p.forEach(function(q){ g.beginPath();
    g.arc(q[0]*n, n-q[1]*n, 6, 0, 6.29); g.fill(); });
}
(function(){
  var c=el('curva'), trascino=-1;
  function pos(e){ var r=c.getBoundingClientRect();
    return [Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),
            Math.max(0,Math.min(1,1-(e.clientY-r.top)/r.height))]; }
  function vicino(q){
    var p=curve[canaleCurva];
    for(var i=0;i<p.length;i++)
      if(Math.abs(p[i][0]-q[0])<0.06 && Math.abs(p[i][1]-q[1])<0.06) return i;
    return -1; }
  c.addEventListener('pointerdown',function(e){
    var q=pos(e), i=vicino(q);
    if(i<0){ curve[canaleCurva].push(q); i=curve[canaleCurva].length-1; }
    trascino=i; c.setPointerCapture(e.pointerId); disegnaCurva(); });
  c.addEventListener('pointermove',function(e){
    if(trascino<0) return;
    curve[canaleCurva][trascino]=pos(e); disegnaCurva(); });
  c.addEventListener('pointerup',function(){
    if(trascino>=0){ trascino=-1; applica(); } });
  c.addEventListener('dblclick',function(e){
    var i=vicino(pos(e));
    if(i>=0){ curve[canaleCurva].splice(i,1); disegnaCurva(); applica(true); } });
})();

function connetti(){ post('/api/connetti',{driver:val('driver')}).then(mostraStato); }
function fotogramma(v){ post('/api/fotogramma',{verso:v,formato:val('formato')}).then(mostraStato); }
function espelli(v){ post('/api/espelli',{verso:v}).then(esito); }
function carica(){ post('/api/carica',{formato:val('formato')}).then(esito); }
function calibra(){ if(confirm('Togliere la pellicola dal percorso ottico. Procedere?'))
  post('/api/calibra',{}).then(esito); }
function muovi(mm){ post('/api/muovi',{mm:mm}).then(mostraStato); }
/* STOP non e' istantaneo, e fingere che lo sia e' peggio che dirlo: la
   macchina si ferma fra un passaggio e l'altro, non a meta'. Quindi il tasto
   dice subito che ha ricevuto, e resta in quello stato finche' il lavoro non
   e' davvero finito. Prima non diceva niente e sembrava rotto. */
var fermando=false;
function fermaTutto(){
  var b=document.querySelector('button.stop');
  post('/api/ferma',{}).then(function(r){
    if(r && r.errore){ esito(r); return; }
    if(r && r.messaggio){
      var a=el('avvisi');
      if(a){ a.textContent=r.messaggio;
             setTimeout(function(){ if(a.textContent===r.messaggio) a.textContent=''; }, 12000); }
    }
    if(r && r.fermato){
      fermando=true;
      if(b){ b.textContent='ARRESTO...'; b.disabled=true; }
    }
  });
}
function anteprima(){ post('/api/anteprima',
  Object.assign({formato:val('formato')},opzioni())).then(esito); }
function scansiona(){
  var r=val('rullo_id');
  if(!r){alert('Selezionare un rullo: serve prima un ordine e un rullo.');return;}
  post('/api/scansiona',Object.assign({rullo_id:parseInt(r)},opzioni())).then(esito); }

function azzeraCorrezioni(){
  var d={ir:1,grana_riduci:0,nitidezza:0,graffi:0,polvere:0,grana_aggiungi:0};
  Object.keys(d).forEach(function(k){ el(k).value=d[k]; mostraValore(k); });
}
var DECIMALI={grana_aggiungi:2,esposizione:2,gamma_tono:2,saturazione:2,
              temperatura_k:0,tinta_vm:0,contrasto:0,luminosita:0,chiarezza:0,
              hue:1,barra_r:1,barra_g:1,barra_b:1};
function mostraValore(id){
  var e=el(id), b=el('v_'+id);
  if(!b) return;
  var d = (id in DECIMALI) ? DECIMALI[id] : 1;
  b.textContent = parseFloat(e.value).toFixed(d).replace('.',',');
}
function reset(id){
  var d={temperatura_k:5500,gamma_tono:1,saturazione:1};
  el(id).value = (id in d) ? d[id] : 0;
  if(id==='temperatura_k'||id==='tinta_vm') wbGuadagni=null;
  mostraValore(id); applica(true);
}

function creaOrdine(){
  var n=val('cliente'); if(!n){alert('Inserire il nome del cliente');return;}
  post('/api/ordini',{cliente:n,note:val('note')}).then(caricaOrdini); }
function creaRullo(){
  var o=val('ordine_id'); if(!o){alert('Scegliere prima un ordine');return;}
  post('/api/rulli',{ordine_id:parseInt(o),etichetta:val('etichetta'),
    formato:val('formato'),preset:val('preset'),profilo:val('profilo'),
    attesi:parseInt(val('attesi'))||0,specchiato:val('specchiato')})
    .then(function(){caricaRulli();caricaOrdini();}); }

function caricaOrdini(){ get('/api/ordini').then(function(r){
  var t=el('ordini');
  t.innerHTML='<tr><th>Codice</th><th>Cliente</th><th>Stato</th><th>Foto</th></tr>';
  var sel=el('ordine_id'), prec=sel.value;
  sel.innerHTML='<option value="">scegli...</option>';
  r.ordini.forEach(function(o){
    var tr=t.insertRow(); tr.insertCell().textContent=o.codice;
    tr.insertCell().textContent=o.cliente; tr.insertCell().textContent=o.stato;
    tr.insertCell().textContent=o.fotogrammi;
    var op=document.createElement('option'); op.value=o.id;
    op.textContent=o.codice+' — '+o.cliente; sel.appendChild(op); });
  if(ORDINE_CHIESTO){ sel.value=ORDINE_CHIESTO; ORDINE_CHIESTO=null; caricaRulli(); }
  else if(prec) sel.value=prec;
  /* Un laboratorio con un ordine solo aperto e' il caso normale: farglielo
     scegliere a mano ogni volta e' una seccatura, non una precauzione. */
  else if(r.ordini.length===1){ sel.value=r.ordini[0].id; caricaRulli(); }
  }); }

function caricaRulli(){
  var o=val('ordine_id'); var sel=el('rullo_id');
  if(!o){ sel.innerHTML='<option value="">nessuno</option>'; caricaStriscia(); return; }
  get('/api/rulli?ordine_id='+o).then(function(r){
    sel.innerHTML='<option value="">nessuno</option>';
    r.rulli.forEach(function(x){
      var op=document.createElement('option'); op.value=x.id;
      op.textContent=x.etichetta+' · '+x.formato+' · '+
        (x.attesi ? (x.scansionati+'/'+x.attesi)
                  : (x.scansionati+' fotogrammi'))+' · '+x.stato+
        (x.specchiato?' · specchiato':'');
      sel.appendChild(op); });
    if(RULLO_CHIESTO){
      for(var i=0;i<sel.options.length;i++)
        if(sel.options[i].value===String(RULLO_CHIESTO)){ sel.value=RULLO_CHIESTO; break; }
    }
    if(!sel.value && r.rulli.length===1) sel.value=r.rulli[0].id;
    caricaStriscia(); }); }

function mostraStato(s){
  var m=s.macchina, on=!!(m && m.connesso);
  var finto=false;
  try{ finto = on && JSON.parse(m.messaggio).simulato===true; }catch(e){}
  el('stato').innerHTML='<span class="pallino'+(on?' on':'')+'"></span>'+
    (on ? ('connesso · '+s.driver+' · '+m.posizione_mm.toFixed(2)+' mm')
        : 'non connesso') +
    /* Se i fotogrammi sono finti bisogna che si veda sempre, non solo la
       prima volta: e' l'unica differenza che conta fra una prova e un rullo
       del cliente. */
    (finto ? ' <b style="color:var(--avviso)">· SIMULAZIONE</b>' : '');
  /* Il menu del collegamento deve dire come stanno le cose, non ripartire
     ogni volta dalla prima voce: e' il modo migliore per far scegliere a
     qualcuno un backend diverso da quello con cui il server e' partito. */
  var dr=el('driver');
  if(dr && !dr.dataset.tocco && s.driver){
    for(var i=0;i<dr.options.length;i++)
      if(dr.options[i].value===s.driver) dr.selectedIndex=i;
  }
  ['formato','preset','profilo'].forEach(function(k){
    var e=el(k), key={formato:'formati',preset:'preset',profilo:'profili'}[k];
    if(e && e.options.length===0 && s[key]) s[key].forEach(function(v){
      var o=document.createElement('option'); o.textContent=v; e.appendChild(o); }); });
  var a=s.ambiente;
  if(a) el('amb').textContent = a.warnings.length ? a.warnings.join(' ')
    : (a.system+' · '+a.bits+' bit · max '+a.max_safe_dpi+' dpi');
  var j=s.job;
  if(j){
    el('barra').style.width=(j.progress*100)+'%';
    /* il messaggio del servizio spesso contiene gia' "Fotogramma 5 di 6":
       ripeterlo sotto sarebbe rumore */
    /* Il contatore ha senso solo mentre si scansiona: a lavoro finito
       "fotogramma 2 di 40" accanto a "fermato dopo 2 fotogrammi" fa sembrare
       che ne manchino 38 da fare, che e' il contrario di quel che e'
       successo. */
    var conta = (j.running && j.total &&
                 j.message.toLowerCase().indexOf('fotogramm')<0)
      ? ' · fotogramma '+j.frame+' di '+j.total : '';
    el('msg').textContent = j.message + conta;
    if(j.errors.length) el('avvisi').textContent = j.errors.join(' · ');
    el('log').textContent = j.log.join('\n');
    /* Il tasto torna disponibile quando il lavoro e' finito davvero, non
       quando la richiesta di arresto e' partita: e' l'unico modo che ha
       l'operatore di sapere che la macchina si e' fermata. */
    if(fermando && !j.running){
      fermando=false;
      var b=document.querySelector('button.stop');
      if(b){ b.textContent='STOP'; b.disabled=false; }
    }
  }
}

function mostraSensori(){ get('/api/sensori').then(function(x){
  var e=el('sensori');
  if(!x.disponibili){
    /* carrello Gretag: la pellicola la vede il carrello e l'SDK lo dice con
       un evento, dopo Carica. Gli ingressi singoli non servono. */
    if(x.pellicola_sdk===true){ e.innerHTML='pellicola <b style="color:var(--ok)">inserita</b>'; return; }
    if(x.pellicola_sdk===false){ e.innerHTML='pellicola <b>non inserita</b> \u00b7 Carica, poi inserirla'; return; }
    e.textContent='pellicola: premere Carica, poi inserirla'; return; }
  e.innerHTML='ingresso <b style="color:'+(x.ingresso?'var(--ok)':'var(--dim)')+'">'+
    (x.ingresso?'pellicola':'libero')+'</b> · uscita <b style="color:'+
    (x.uscita?'var(--ok)':'var(--dim)')+'">'+(x.uscita?'pellicola':'libero')+'</b>'; }); }

/* ---------- ciclo ---------- */
var ultimo=0, eraInCorso=false;
function ciclo(){
  mostraSensori();
  get('/api/stato').then(function(s){
    mostraStato(s);
    var j=s.job, ora=Date.now();
    if(j && !j.running){
      /* appena finisce un lavoro si aggiorna subito; altrimenti ogni 3 s */
      if(eraInCorso || ora-ultimo>3000){
        ultimo=ora; eraInCorso=false;
        hoAnteprima=true;
        el('tela').style.display='block'; el('vuoto').style.display='none';
        aggiornaImmagini(true); disegnaIstogramma(); caricaStriscia();
      }
    } else if(j && j.running){ eraInCorso=true; }
  }).catch(function(){});
}

Array.prototype.forEach.call(el('g_vista').children,function(b){
  b.onclick=function(){ impostaVista(b.dataset.v); }; });
Array.prototype.forEach.call(el('g_zoom').children,function(b){
  b.onclick=function(){ impostaZoom(b.dataset.z==='1'); }; });
['ir','grana_riduci','nitidezza','graffi','polvere','grana_aggiungi'].forEach(function(id){
  el(id).oninput=function(){ mostraValore(id); }; mostraValore(id); });

/* i cursori del colore rielaborano da soli: si sposta e si guarda, senza
   dover premere niente. La pellicola non si muove: si rifa' solo il calcolo. */
['esposizione','temperatura_k','tinta_vm','contrasto','luminosita','gamma_tono',
 'chiarezza','saturazione','hue','barra_r','barra_g','barra_b'].forEach(function(id){
  el(id).oninput=function(){
    mostraValore(id);
    if(id==='temperatura_k'||id==='tinta_vm') wbGuadagni=null;
    applica();
  };
  mostraValore(id); });

Array.prototype.forEach.call(el('canali').children,function(b){
  if(!b.dataset.c) return;
  b.onclick=function(){
    canaleCurva=b.dataset.c;
    Array.prototype.forEach.call(el('canali').children,function(x){
      if(x.dataset.c) x.classList.toggle('on', x.dataset.c===canaleCurva); });
    disegnaCurva(); }; });
el('tela').addEventListener('click',function(e){
  if(pipettaAttiva) campiona(e); });
disegnaCurva();
el('driver').onchange=function(){ el('driver').dataset.tocco='1'; };
el('ordine_id').onchange=function(){ caricaRulli(); };
el('rullo_id').onchange=function(){ provSel=null; caricaStriscia(); };

/* Arrivando dall'archivio con ?rullo=N si apre subito quel rullo: e' il
   gesto "riaprilo in scansione", e deve funzionare al primo colpo. */
el('vaiarchivio').href = url('/archivio');
var RULLO_CHIESTO = new URLSearchParams(location.search).get('rullo');
var ORDINE_CHIESTO = new URLSearchParams(location.search).get('ordine');
get('/api/stato').then(mostraStato); caricaOrdini(); caricaStriscia();
setInterval(ciclo,1000);
</script></body></html>

"""


# --------------------------------------------------------------------------
# Montaggio del tema nelle tre pagine
#
# Le pagine restano scritte con i segnaposto /*TEMA*/ e /*ICONA*/: cosi' la
# tavolozza e' scritta una volta sola e non ci si ritrova, fra sei mesi, il
# viola giusto in due pagine su tre.
#
# Lo script del tema va nel <head>, PRIMA del corpo: se lo si mette in fondo,
# la pagina compare per un istante scura e poi sbianca. Quel lampo, su un
# tablet al banco, e' l'unica cosa che l'occhio nota.
# --------------------------------------------------------------------------

def _vesti(pagina: str, icona: bool = True) -> str:
    """Sostituisce i segnaposto con tavolozza, script e iconcina."""
    assert "/*TEMA*/" in pagina and "/*ICONA*/" in pagina
    pagina = pagina.replace("/*TEMA*/", TEMA + CSS_ICONA_TEMA, 1)
    pagina = pagina.replace("/*ICONA*/", ICONA_TEMA if icona else "", 1)
    return pagina.replace("</style></head>",
                          "</style>\n<script>" + SCRIPT_TEMA + "</script></head>", 1)


PAGINA = _vesti(PAGINA)
PAGINA_ARCHIVIO = _vesti(PAGINA_ARCHIVIO)
PAGINA_PIN = _vesti(PAGINA_PIN)


# --------------------------------------------------------------------------
# Handler
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    servizio: ScanService = None            # type: ignore
    token: str = ""
    impostazioni: Optional[str] = None      # file in cui salvare le scelte
    server_version = "filmscan"

    def handle_error(self, request, client_address):
        """Un errore per riga, non due pagine di traceback.

        La console del server la guarda l'operatore, non un programmatore:
        se a ogni ricaricamento di pagina esce un muro di righe rosse, quando
        arriva l'errore vero nessuno lo vede.
        """
        import sys as _sys
        exc = _sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, OSError)) and not isinstance(exc, ValueError):
            return                       # il client se n'e' andato: normale
        print("  errore servendo %s: %s: %s"
              % (client_address[0], type(exc).__name__, exc))

    def log_message(self, fmt, *args):      # silenzia il log su stdout
        pass

    # ---- utilita' ----

    # PIN sbagliati gia' visti, per indirizzo: {ip: [insieme, bloccato_fino_a]}
    #
    # Si contano i valori DIVERSI, non le richieste. La differenza e' tutto:
    # una scheda del browser rimasta aperta da un avvio precedente ripropone
    # lo stesso PIN vecchio una volta al secondo, e a contare le richieste
    # bloccherebbe la macchina intera - compresa la pagina giusta, che parla
    # dallo stesso indirizzo. A contare i valori diversi, quella scheda vale
    # un tentativo solo, mentre chi prova a indovinare deve per forza
    # cambiare numero e viene fermato lo stesso.
    _tentativi: Dict[str, list] = {}
    MAX_PIN_DIVERSI = 5
    BLOCCO_SECONDI = 30

    def _bloccato_per(self) -> float:
        """Secondi di attesa ancora da scontare per chi sta chiamando."""
        v = self._tentativi.get(self.client_address[0])
        return max(0.0, v[1] - time.time()) if v else 0.0

    def _segna_pin(self, valore: str, riuscito: bool) -> None:
        ip = self.client_address[0]
        if riuscito:
            self._tentativi.pop(ip, None)
            return
        v = self._tentativi.setdefault(ip, [set(), 0.0])
        v[0].add(valore[:16])
        if len(v[0]) >= self.MAX_PIN_DIVERSI:
            # Cinque PIN diversi poi mezzo minuto fermi: per chi sbaglia a
            # digitare e' un fastidio da nulla, per chi prova a indovinare
            # cento milioni di numeri diventa un lavoro di secoli.
            v[0] = set()
            v[1] = time.time() + self.BLOCCO_SECONDI

    def _autorizzato(self, query: Dict) -> bool:
        if not self.token:
            return True
        if self._bloccato_per() > 0:
            return False
        # Qui si verifica soltanto. Il conteggio dei tentativi avviene in un
        # posto solo - la pagina del PIN - perche' l'interfaccia interroga il
        # server ogni secondo: contare anche quelle chiamate significherebbe
        # che una scheda vecchia rimasta aperta blocca tutti quanti.
        return query.get("t", [""])[0] == self.token

    def _json(self, data, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, OSError):
            # Il browser ha chiuso prima della risposta: succede ogni volta che
            # si ricarica la pagina o si esce da un comando lungo. Non e' un
            # guasto, e riempire la console di traceback per questo vuol dire
            # nascondere gli errori veri sotto quelli finti.
            pass

    def _corpo(self) -> Dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return {}

    # ---- GET ----

    def do_GET(self) -> None:
        u = urlparse(self.path)
        q = parse_qs(u.query)
        s = self.servizio

        if u.path == "/":
            # Senza PIN valido non si serve l'interfaccia ma la richiesta del
            # PIN: prima la pagina si apriva lo stesso e poi moriva a colpi di
            # 403 sulle chiamate, che e' il modo peggiore di dire "non entri".
            if self.token and not self._autorizzato(q):
                dato = q.get("t", [""])[0]
                if dato:
                    self._segna_pin(dato, False)
                attesa = int(round(self._bloccato_per()))
                sbagliato = "true" if dato else "false"
                body = PAGINA_PIN.replace(
                    "/*STATO*/",
                    "var BLOCCO=%d; var SBAGLIATO=%s;" % (attesa, sbagliato)
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.token:
                self._segna_pin(q.get("t", [""])[0], True)
            body = PAGINA.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if u.path == "/archivio":
            # Stesso cancello della pagina di scansione: senza PIN valido non
            # si vede nemmeno il nome di un cliente.
            if self.token and not self._autorizzato(q):
                self.send_response(303)
                self.send_header("Location", "/")
                self.end_headers()
                return
            body = PAGINA_ARCHIVIO.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if not self._autorizzato(q):
            return self._json({"errore": "token mancante o errato"}, 403)

        if u.path == "/api/stato":
            st = s.stato()
            st["formati"] = list(FORMATS.keys())
            st["preset"] = list(PRESETS.keys())
            st["profili"] = list(PROFILES.keys())
            return self._json(st)

        if u.path == "/api/ordini":
            ordini = [{"id": o.id, "codice": o.codice, "cliente": o.cliente_nome,
                       "stato": o.stato, "rulli": o.rulli,
                       "fotogrammi": o.fotogrammi}
                      for o in s.archivio.ordini()]
            return self._json({"ordini": ordini,
                               "riepilogo": s.archivio.riepilogo()})

        if u.path == "/api/sensori":
            sn = s.sensori()
            leggi = getattr(s.backend, "pellicola_sdk", None)
            return self._json({"ingresso": sn.ingresso, "uscita": sn.uscita,
                               "disponibili": sn.disponibili,
                               "pellicola_sdk": leggi() if leggi else None})

        if u.path == "/api/rulli":
            oid = q.get("ordine_id", [""])[0]
            if not oid:
                return self._json({"rulli": []})
            rulli = [{"id": r.id, "etichetta": r.etichetta, "formato": r.formato,
                      "preset": r.preset, "stato": r.stato, "attesi": r.attesi,
                      "scansionati": r.scansionati, "specchiato": r.specchiato}
                     for r in s.archivio.rulli(int(oid))]
            return self._json({"rulli": rulli})

        if u.path == "/api/anteprima.jpg":
            def num(chiave, default):
                try:
                    return float(q.get(chiave, [default])[0])
                except (TypeError, ValueError):
                    return default
            try:
                data = s.anteprima_jpeg(
                    vista=q.get("vista", ["dopo"])[0],
                    zoom=q.get("zoom", ["0"])[0] == "1",
                    cx=num("cx", 0.5), cy=num("cy", 0.5))
            except ValueError as exc:
                return self._json({"errore": str(exc)}, 404)
            return self._immagine(data)

        if u.path == "/api/istogramma":
            return self._json(s.istogramma(vista=q.get("vista", ["dopo"])[0]))

        if u.path == "/api/fotogrammi":
            try:
                rid = int(q.get("rullo_id", ["0"])[0])
            except (TypeError, ValueError):
                rid = 0
            if not rid:
                return self._json({"fotogrammi": []})
            return self._json({"fotogrammi": s.archivio.fotogrammi(rid)})

        if u.path == "/api/provino.jpg":
            try:
                fid = int(q.get("id", ["0"])[0])
            except (TypeError, ValueError):
                fid = 0
            percorso = ""
            for f in s.archivio.fotogrammi_per_id(fid):
                percorso = f.get("file_out") or ""
            try:
                return self._immagine(s.provino_jpeg(percorso))
            except (ValueError, OSError):
                # Un 404 qui diventa l'icona di immagine rotta del browser,
                # uguale per "file cancellato", "disco staccato" e "errore di
                # rete". Una piastrella che lo dice a parole e' l'unica che
                # l'operatore puo' leggere da un tablet a mezzo metro.
                return self._immagine(_mancante())

        # ---------------- archivio degli ordini fatti ----------------

        if u.path == "/api/archivio/ordini":
            cerca = q.get("cerca", [""])[0]
            fuori = []
            for o in s.archivio.ordini(cliente=cerca, limite=300):
                r = consegne.riepilogo_ordine(s.archivio, o.id)
                cli = s.archivio.cliente(o.cliente_id)
                r.update({"cliente": o.cliente_nome, "cliente_id": o.cliente_id,
                          "telefono": cli.telefono if cli else "",
                          "rulli": o.rulli, "fotogrammi": o.fotogrammi})
                fuori.append(r)
            return self._json({"ordini": fuori,
                               "conservazione": s.cfg.conservazione_giorni,
                               "pulizia": s.cfg.pulizia})

        if u.path == "/api/archivio/ordine":
            oid = int(q.get("id", ["0"])[0] or 0)
            o = s.archivio.ordine(oid)
            if o is None:
                return self._json({"errore": "ordine inesistente"}, 404)
            cli = s.archivio.cliente(o.cliente_id)
            rulli = []
            for r in s.archivio.rulli(oid):
                fg = s.archivio.fotogrammi(r.id)
                for f in fg:
                    # Se il file c'e' lo sa solo il server: il browser non
                    # puo' guardare nel disco, e senza questo dato l'unica
                    # cosa che vede e' una miniatura che non arriva.
                    p_out = f.get("file_out") or ""
                    p_raw = f.get("file_raw") or ""
                    f["esiste"] = bool(p_out and os.path.isfile(p_out))
                    f["esiste_raw"] = bool(p_raw and os.path.isfile(p_raw))
                rulli.append({"id": r.id, "etichetta": r.etichetta,
                              "formato": r.formato, "stato": r.stato,
                              "fotogrammi": fg})
            return self._json({
                "ordine": {"id": o.id, "codice": o.codice, "stato": o.stato,
                           "note": o.note, "creato": o.creato,
                           "cliente": o.cliente_nome, "cliente_id": o.cliente_id},
                "cliente": {"nome": cli.nome, "telefono": cli.telefono,
                            "email": cli.email} if cli else {},
                "rulli": rulli,
                "riepilogo": consegne.riepilogo_ordine(s.archivio, oid)})

        if u.path == "/api/archivio/scaduti":
            giorni = int(q.get("giorni", [str(s.cfg.conservazione_giorni)])[0])
            return self._json({"giorni": giorni,
                               "ordini": consegne.scaduti(s.archivio, giorni)})

        if u.path == "/api/archivio/cartelle":
            try:
                d = consegne.sfoglia(q.get("percorso", [""])[0])
            except ValueError as exc:
                return self._json({"errore": str(exc)}, 400)
            d["libero"] = consegne.spazio_libero(d["percorso"] or ".")
            d["attuale"] = s.cfg.path("archivio")
            return self._json(d)

        if u.path == "/api/archivio/registro":
            percorso = os.path.join(s.cfg.path("archivio"), s.cfg.registro_nome)
            try:
                percorso = consegne.scrivi_registro(s.archivio, percorso)
            except Exception as exc:                     # noqa: BLE001
                return self._json({"errore": str(exc)}, 500)
            return self._scarica(percorso, tipo="application/vnd.ms-excel")

        if u.path == "/api/archivio/zip":
            oid = int(q.get("id", ["0"])[0] or 0)
            cosa = q.get("cosa", ["esportati"])[0]
            o = s.archivio.ordine(oid)
            if o is None:
                return self._json({"errore": "ordine inesistente"}, 404)
            fuori = os.path.join(s.cfg.path("archivio"), "_consegne")
            if not os.path.isdir(fuori):
                os.makedirs(fuori)
            dest = os.path.join(fuori, "%s-%s.zip" % (o.codice, cosa))
            try:
                consegne.zip_ordine(s.archivio, oid, dest, cosa)
            except ValueError as exc:
                return self._json({"errore": str(exc)}, 400)
            return self._scarica(dest, tipo="application/zip")

        if u.path == "/api/archivio/provino.jpg":
            oid = int(q.get("id", ["0"])[0] or 0)
            o = s.archivio.ordine(oid)
            if o is None:
                return self._json({"errore": "ordine inesistente"}, 404)
            dest = os.path.join(s.archivio.cartella_ordine(s.cfg.out_dir, oid),
                                "provino-%s.jpg" % o.codice)
            try:
                consegne.provino_contatto(s.archivio, oid, dest)
            except ValueError as exc:
                return self._json({"errore": str(exc)}, 400)
            with open(dest, "rb") as f:
                return self._immagine(f.read())

        self._json({"errore": "percorso sconosciuto"}, 404)

    def _scarica(self, percorso: str, nome: str = "",
                 tipo: str = "application/octet-stream") -> None:
        """Manda un file al browser, a pezzi.

        A pezzi e non tutto in memoria: lo zip di un ordine puo' pesare
        mezzo giga, e su un processo a 32 bit caricarlo in RAM per spedirlo
        sarebbe il modo piu' rapido di far morire il server proprio mentre
        l'operatore sta consegnando.
        """
        try:
            peso = os.path.getsize(percorso)
        except OSError:
            return self._json({"errore": "file non trovato"}, 404)
        try:
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(peso))
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"'
                             % (nome or os.path.basename(percorso)))
            self.end_headers()
            with open(percorso, "rb") as f:
                while True:
                    pezzo = f.read(262144)
                    if not pezzo:
                        break
                    self.wfile.write(pezzo)
        except (ConnectionError, OSError):
            pass          # il browser ha annullato lo scaricamento: capita

    def _immagine(self, data: bytes) -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionError, OSError):
            pass

    # ---- POST ----

    def do_POST(self) -> None:
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._autorizzato(q):
            return self._json({"errore": "token mancante o errato"}, 403)

        s = self.servizio
        d = self._corpo()

        try:
            if u.path == "/api/connetti":
                return self._json(s.connetti(d.get("driver")))

            if u.path == "/api/calibra":
                s.calibra()
                return self._json({"avviato": True})

            if u.path == "/api/muovi":
                return self._json(s.muovi(float(d.get("mm", 0))))

            if u.path == "/api/fotogramma":
                return self._json(s.fotogramma(int(d.get("verso", 1)) >= 0,
                                               str(d.get("formato", "135-full"))))

            if u.path == "/api/fuoco":
                return self._json(s.fuoco(float(d.get("mm", 0))))

            if u.path == "/api/carica":
                s.carica(str(d.get("formato", "135-full")))
                return self._json({"avviato": True})

            if u.path == "/api/espelli":
                s.espelli(str(d.get("verso", "davanti")))
                return self._json({"avviato": True})

            if u.path == "/api/ferma":
                return self._json(s.ferma())

            if u.path == "/api/rielabora":
                s.options = _opzioni(d)
                return self._json(s.rielabora())

            if u.path == "/api/campiona":
                s.options = _opzioni(d)
                return self._json(s.campiona_neutro(
                    float(d.get("x", 0.5)), float(d.get("y", 0.5))))

            if u.path == "/api/archivio/rinomina":
                if d.get("cliente_id"):
                    s.archivio.cliente_rinomina(
                        int(d["cliente_id"]), d.get("nome", ""),
                        d.get("telefono"), d.get("email"))
                if d.get("ordine_id"):
                    s.archivio.ordine_rinomina(
                        int(d["ordine_id"]), d.get("sposta_su", ""),
                        d.get("note"))
                s.aggiorna_registro()
                return self._json({"ok": True})

            if u.path == "/api/archivio/impostazioni":
                if "archivio_dir" in d:
                    s.cfg.archivio_dir = str(d["archivio_dir"] or "")
                if "conservazione_giorni" in d:
                    s.cfg.conservazione_giorni = max(1, int(d["conservazione_giorni"]))
                if "whatsapp_prefisso" in d:
                    s.cfg.whatsapp_prefisso = str(d["whatsapp_prefisso"] or "39")
                if "pulizia" in d:
                    modo = str(d["pulizia"] or "segnala")
                    if modo not in consegne.PULIZIE:
                        return self._json({"errore": "pulizia sconosciuta"}, 400)
                    s.cfg.pulizia = modo
                if self.impostazioni:
                    try:
                        s.cfg.save(self.impostazioni)
                    except (IOError, OSError) as exc:
                        s.log("Impostazioni non salvate: %s" % exc)
                percorso = s.aggiorna_registro()
                return self._json({"archivio_dir": s.cfg.path("archivio"),
                                   "conservazione_giorni": s.cfg.conservazione_giorni,
                                   "whatsapp_prefisso": s.cfg.whatsapp_prefisso,
                                   "pulizia": s.cfg.pulizia,
                                   "registro": percorso or ""})

            if u.path == "/api/archivio/libera":
                cosa = d.get("cosa", "grezzi")
                if cosa not in ("grezzi", "esportati", "tutto"):
                    return self._json({"errore": "cosa liberare?"}, 400)
                try:
                    r = consegne.libera(s.archivio,
                                        int(d.get("ordine_id", 0)), cosa)
                except ValueError as exc:
                    return self._json({"errore": str(exc)}, 404)
                s.aggiorna_registro()
                return self._json(r)

            if u.path == "/api/archivio/elimina":
                # Tre livelli, un solo endpoint: le righe del database si
                # tolgono con la stessa cautela, cambia solo quante ne vanno
                # via insieme. I file si cancellano solo se richiesto a parte,
                # perche' "togli dall'elenco" e "butta le fotografie" sono due
                # decisioni diverse e vanno prese due volte.
                cosa = d.get("cosa", "")
                anche_file = bool(d.get("file"))
                try:
                    ident = int(d.get("id", 0))
                except (TypeError, ValueError):
                    ident = 0
                if not ident:
                    return self._json({"errore": "id mancante"}, 400)

                if cosa == "rullo":
                    ru = s.archivio.rullo(ident)
                    if ru is None:
                        return self._json({"errore": "rullo inesistente"}, 404)
                    tolti = 0
                    if anche_file:
                        tolti = _butta_rullo(s, ident)
                    s.archivio.rullo_elimina(ident)
                    s.aggiorna_registro()
                    return self._json({"ok": True, "cosa": "rullo",
                                       "file_tolti": tolti})

                if cosa == "ordine":
                    o = s.archivio.ordine(ident)
                    if o is None:
                        return self._json({"errore": "ordine inesistente"}, 404)
                    tolti = 0
                    if anche_file:
                        tolti = consegne.libera(s.archivio, ident,
                                                "tutto")["tolti"]
                    s.archivio.ordine_elimina(ident)
                    s.aggiorna_registro()
                    return self._json({"ok": True, "cosa": "ordine",
                                       "codice": o.codice, "file_tolti": tolti})

                if cosa == "cliente":
                    cli = s.archivio.cliente(ident)
                    if cli is None:
                        return self._json({"errore": "cliente inesistente"}, 404)
                    tolti = 0
                    ordini = [o.id for o in s.archivio.ordini(limite=2000)
                              if o.cliente_id == ident]
                    if anche_file:
                        for oid in ordini:
                            tolti += consegne.libera(s.archivio, oid,
                                                     "tutto")["tolti"]
                    n = s.archivio.cliente_elimina(ident)
                    s.aggiorna_registro()
                    return self._json({"ok": True, "cosa": "cliente",
                                       "nome": cli.nome, "ordini": n,
                                       "file_tolti": tolti})

                return self._json({"errore": "cosa eliminare?"}, 400)

            if u.path == "/api/archivio/pulizia":
                return self._json(s.pulizia_scaduti())

            if u.path == "/api/archivio/avviso":
                return self._json(consegne.avviso(
                    s.archivio, int(d.get("ordine_id", 0)),
                    d.get("modello", ""), s.cfg.whatsapp_prefisso))

            if u.path == "/api/archivio/provino":
                oid = int(d.get("ordine_id", 0))
                o = s.archivio.ordine(oid)
                if o is None:
                    return self._json({"errore": "ordine inesistente"}, 404)
                dest = os.path.join(
                    s.archivio.cartella_ordine(s.cfg.out_dir, oid),
                    "provino-%s.jpg" % o.codice)
                try:
                    consegne.provino_contatto(s.archivio, oid, dest)
                except (ValueError, OSError) as exc:
                    return self._json({"errore": str(exc)}, 400)
                return self._json({"percorso": dest})

            if u.path == "/api/opzioni":
                s.options = _opzioni(d)
                return self._json({"ok": True})

            if u.path == "/api/ordini":
                o = s.archivio.ordine_crea(d.get("cliente", ""), d.get("note", ""))
                return self._json({"id": o.id, "codice": o.codice})

            if u.path == "/api/rulli":
                rid = s.archivio.rullo_crea(
                    int(d["ordine_id"]), d.get("etichetta", "rullo"),
                    d.get("formato", "135-full"), d.get("preset", "standard"),
                    d.get("profilo", "c41"), int(d.get("attesi", 36)))
                if d.get("specchiato"):
                    s.archivio.rullo_specchiatura(rid, True, confermata=True)
                return self._json({"id": rid})

            if u.path == "/api/anteprima":
                s.options = _opzioni(d)
                s.anteprima(d.get("formato", "135-full"))
                return self._json({"avviato": True})

            if u.path == "/api/scansiona":
                s.options = _opzioni(d)
                s.scansiona_rullo(int(d["rullo_id"]))
                return self._json({"avviato": True})

        except (KeyError, ValueError) as exc:
            return self._json({"errore": "richiesta non valida: %s" % exc}, 400)
        except Exception as exc:                        # noqa: BLE001
            from .hal import ScannerError
            if isinstance(exc, ScannerError):
                # non e' un difetto del programma: e' la macchina che non
                # risponde, e va detto all'operatore, non stampato in console
                return self._json({"errore": str(exc)}, 503)
            traceback.print_exc()
            return self._json({"errore": str(exc)}, 500)

        self._json({"errore": "percorso sconosciuto"}, 404)


# --------------------------------------------------------------------------

def indirizzi_locali() -> List[str]:
    """Gli indirizzi IPv4 di questa macchina sulla rete locale.

    Si prova prima con una socket UDP verso un indirizzo esterno: non manda
    niente, ma costringe il sistema a dire da quale interfaccia uscirebbe, che
    e' quella giusta nel 99 per cento dei casi. Il giro per hostname resta come
    ripiego, perche' su alcune macchine risponde 127.0.0.1 e basta.
    """
    trovati = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("192.0.2.1", 9))       # rete di documentazione: non esiste
        trovati.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in trovati and not ip.startswith("127."):
                trovati.append(ip)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in trovati and not ip.startswith("127."):
                trovati.append(ip)
    except OSError:
        pass
    return trovati


# Browser che su Windows 7 sanno ancora mostrare la nostra interfaccia.
# L'ordine e' quello di preferenza; il numero fra parentesi e' l'ultima
# versione che gira sul 7, per chi dovesse installarli.
BROWSER_BUONI = [
    (r"Mozilla Firefox\firefox.exe", "Firefox"),          # ESR 115
    (r"Google\Chrome\Application\chrome.exe", "Chrome"),  # 109
]


def trova_browser():
    """Cerca un browser capace, prima di ripiegare su quello predefinito.

    Su una macchina del 2009 appena installata il browser predefinito e'
    Internet Explorer, e la nostra interfaccia li' non si vede: usa griglie
    CSS, canvas ed eventi che quel motore non conosce. Meglio cercarne uno
    vero prima di aprire una finestra che mostrerebbe una pagina rotta.
    """
    if os.name != "nt":
        return None, None
    radici = [os.environ.get("ProgramFiles", r"C:\Program Files"),
              os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    for rel, nome in BROWSER_BUONI:
        for radice in radici:
            if not radice:
                continue
            percorso = os.path.join(radice, rel)
            if os.path.exists(percorso):
                return percorso, nome
    return None, None


def scrivi_scorciatoia_desktop(url: str) -> Optional[str]:
    """Mette (o aggiorna) una scorciatoia sul desktop con l'indirizzo del momento.

    Serve per il caso banale e frequente: l'operatore chiude la finestra del
    browser. Senza scorciatoia dovrebbe riavviare tutto per riavere un
    indirizzo valido, perche' il token cambia a ogni accensione. Il file si
    riscrive a ogni avvio, quindi non invecchia mai.
    """
    if os.name != "nt":
        return None
    desktop = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
    if not os.path.isdir(desktop):
        return None
    percorso = os.path.join(desktop, "Interfaccia scansione.url")
    try:
        with open(percorso, "w") as f:
            f.write("[InternetShortcut]\nURL=%s\n" % url)
        return percorso
    except OSError:
        return None


def apri_nel_browser(url: str, ritardo: float = 1.2) -> None:
    """Apre l'interfaccia sul monitor della macchina, appena il server e' su.

    Parte in un filo separato con un attimo di ritardo: il server sta gia'
    ascoltando, ma cosi' la prima richiesta arriva a giochi fatti e non si
    rischia una pagina bianca da ricaricare a mano - che all'accensione, con
    l'operatore che guarda, fa una pessima figura.
    """
    def lavoro():
        time.sleep(ritardo)
        percorso, nome = trova_browser()
        try:
            if percorso:
                import subprocess
                # Chrome in modalita' app: niente schede ne' barra degli
                # indirizzi. Sul monitor 4:3 della macchina (al massimo
                # 1280x720) sono un'ottantina di pixel di immagine in piu'.
                if nome == "Chrome":
                    subprocess.Popen([percorso, "--app=" + url, "--start-maximized"])
                else:
                    subprocess.Popen([percorso, url])
                print("  Interfaccia aperta in %s su questa macchina." % nome)
            else:
                import webbrowser
                # webbrowser.open dice se ce l'ha fatta: fidarsi e stampare
                # "aperta" comunque vorrebbe dire mentire a chi guarda uno
                # schermo dove non si e' aperto niente
                if webbrowser.open(url):
                    print("  Interfaccia aperta nel browser predefinito.")
                    if os.name == "nt":
                        print("  Se la pagina appare rotta e' Internet Explorer: su Windows 7")
                        print("  servono Firefox ESR 115 o Chrome 109, gli ultimi che ci girano.")
                else:
                    print("  Nessun browser trovato su questa macchina.")
                    print("  L'indirizzo resta valido: %s" % url)
        except Exception as exc:                           # noqa: BLE001
            print("  (non sono riuscito ad aprire il browser: %s)" % exc)
            print("   L'indirizzo resta valido: %s" % url)

    threading.Thread(target=lavoro, daemon=True).start()


def avvia(host: str = "127.0.0.1", porta: int = 8080,
          cfg: Optional[AppConfig] = None, db: str = "./ordini.db",
          token: Optional[str] = None, apri: bool = False,
          impostazioni: Optional[str] = None) -> None:
    cfg = cfg or AppConfig()
    Handler.impostazioni = impostazioni

    if host not in ("127.0.0.1", "localhost") and token is None:
        # Otto cifre, non una sequenza di caratteri strani: si detta al
        # telefono, si scrive su un post-it, si digita su un tablet con le
        # mani sporche di pellicola. Restano casuali sul serio (secrets, non
        # random), ma cento milioni di combinazioni si proverebbero tutte in
        # poche ore: per questo il server conta i tentativi sbagliati e si
        # chiude - vedi Handler._autorizzato.
        token = "%08d" % secrets.randbelow(100000000)

    Handler.servizio = ScanService(cfg, Archivio(db))
    Handler.token = token or ""

    # La conservazione si applica all'accensione, prima di aprire la porta:
    # e' il momento in cui nessuno sta aspettando una risposta. Di serie non
    # cancella niente e si limita a scrivere quanti ordini sono fuori tempo.
    try:
        esito = Handler.servizio.pulizia_scaduti()
        if esito.get("tolti"):
            print("  Conservazione: %d file tolti, %.0f MB liberati."
                  % (esito["tolti"], esito["liberati"] / 1048576.0))
        elif esito.get("ordini"):
            print("  Conservazione: %d ordini fermi da oltre %d giorni "
                  "(nessuna cancellazione automatica)."
                  % (esito["ordini"], esito["giorni"]))
    except Exception as exc:                               # noqa: BLE001
        print("  (conservazione non applicata: %s)" % exc)

    srv = ThreadingHTTPServer((host, porta), Handler)

    if host in ("127.0.0.1", "localhost"):
        print("Interfaccia web attiva su http://127.0.0.1:%d/" % porta)
        print("In ascolto solo su questa macchina. Per la rete locale: --host 0.0.0.0")
    else:
        # "http://0.0.0.0:8080" non si puo' digitare su un tablet. Serve
        # l'indirizzo vero di questa macchina sulla rete, ed e' la prima cosa
        # che chi accende la macchina deve poter leggere.
        coda = "?t=%s" % token if token else ""
        indirizzi = indirizzi_locali()
        print("")
        print("  Interfaccia web attiva. Da tablet o telefono, sulla stessa rete:")
        print("")
        for ip in indirizzi:
            print("      http://%s:%d/%s" % (ip, porta, coda))
        if not indirizzi:
            print("      nessun indirizzo di rete trovato.")
            print("      Il cavo e' collegato? In alternativa: apri un prompt e")
            print("      scrivi  ipconfig  - l'indirizzo IPv4 e' quello da usare,")
            print("      con  :%d/%s  dopo." % (porta, coda))
        print("")
        # La pagina di collegamento dentro il programma originale: si riscrive
        # a ogni avvio perche' il token cambia ogni volta, e un collegamento
        # con il token di ieri e' peggio di nessun collegamento.
        try:
            from .originale import scrivi_pagina, CARTELLA_AIUTO
            import os as _os
            if _os.path.isdir(CARTELLA_AIUTO):
                # senza indirizzo di rete la pagina serve lo stesso: dal monitor
                # della macchina si apre comunque, e almeno c'e' il token giusto
                dove = indirizzi[0] if indirizzi else "127.0.0.1"
                if True:
                    p = scrivi_pagina("http://%s:%d/%s" % (dove, porta, coda))
                    print("  Collegamento aggiornato nell'aiuto del programma originale.")
        except Exception as exc:                           # noqa: BLE001
            print("  (collegamento nel programma originale non aggiornato: %s)" % exc)

        if token:
            print("  PIN di accesso:  %s" % token)
            print("  Chi apre l'indirizzo senza PIN se lo vede chiedere.")
            print("  Cinque tentativi sbagliati e il server si chiude per mezzo minuto.")
        print("  Rete locale fidata soltanto. Mai su internet: non c'e' cifratura,")
        print("  e di la' c'e' un motore che tira pellicola.")
        print("")

    amb = compat.check_environment()
    for w in amb["warnings"]:
        print("Attenzione: %s" % w)

    if apri:
        # Sul monitor della macchina si apre sempre l'indirizzo locale: funziona
        # anche prima che la rete sia pronta, e all'accensione la rete spesso
        # non lo e' ancora.
        coda_locale = "?t=%s" % token if token else ""
        locale = "http://127.0.0.1:%d/%s" % (porta, coda_locale)
        if scrivi_scorciatoia_desktop(locale):
            print("  Scorciatoia aggiornata sul desktop.")
        apri_nel_browser(locale)

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nArresto.")
        srv.shutdown()


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Interfaccia web dello scanner")
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 per esporlo sulla rete locale")
    ap.add_argument("--porta", type=int, default=8080)
    ap.add_argument("--db", default="./ordini.db")
    ap.add_argument("--token", default=None)
    ap.add_argument("--apri", action="store_true",
                    help="apre l'interfaccia nel browser di questa macchina")
    ap.add_argument("--driver", default="mock")
    a = ap.parse_args()

    cfg = AppConfig()
    cfg.driver = a.driver
    avvia(a.host, a.porta, cfg, a.db, a.token, a.apri)


if __name__ == "__main__":
    main()
