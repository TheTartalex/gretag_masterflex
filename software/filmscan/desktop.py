# -*- coding: utf-8 -*-
"""
Il desktop di Windows nascosto dal programma originale.

Alessandro, 7/11/2026: con il programma originale spariscono desktop, barra
delle applicazioni e cartelle, e non si sa quale processo lo faccia. I
programmi da banco come quello della Masterflex di solito fanno una di
queste cose (qui le si controlla tutte, senza cambiare niente):

1. sostituiscono la shell di Windows: Winlogon\\Shell non e' explorer.exe ma
   il loro programma. Allora all'accesso explorer non parte, e niente
   desktop ne' barra; chiudendo i programmi originali (come fa il nostro
   avvio) resta lo schermo vuoto;
2. mettono le policy di Explorer: NoDesktop (niente icone), NoTrayItemsDisplay,
   NoStartMenu..., NoViewOnDrive, DisableTaskMgr;
3. chiudono explorer.exe dopo l'avvio (allora explorer non gira, ma la shell
   e' quella giusta).

controlla() dice quale; mostra() fa ricomparire il desktop adesso senza
toccare la configurazione (vedi sotto); togli_blocchi() toglie le policy
dell'utente, con una copia per rimetterle (rimetti_blocchi).

Sulla riserva (7/11/2026) la causa e' la 2: NoDesktop=1 nell'utente, con la
shell giusta. ripristina_all_avvio() e' quello che fa il nostro avvio
automatico: se il programma all'accensione e' il nostro, il desktop deve
tornare da solo (Alessandro). Toglie i blocchi, avvia explorer se manca e
toglie dall'Esecuzione automatica il collegamento che lancia il programma
originale (Start MasterflexD.lnk), che li rimetterebbe. Tutto con una
copia in software/backup/windows: rimetti_blocchi rimette anche quello.

Solo libreria standard: lo usano anche manutenzione.py e la diagnosi.
"""

from __future__ import annotations

import datetime
import io
import json
import os
import shutil
import subprocess
import time
from typing import Dict, List, Optional

WINLOGON = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
POL_EXPLORER = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer"
POL_SISTEMA = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
AVVIO = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce")
# le policy che nascondono desktop, barra e cartelle (quelle note)
BLOCCHI = ("NoDesktop", "NoTrayItemsDisplay", "NoStartMenuMorePrograms", "NoRun",
           "NoViewOnDrive", "NoDrives", "NoFolderOptions", "NoControlPanel",
           "NoSetTaskbar", "NoTaskGrouping", "NoWinKeys", "NoClose", "NoFind",
           "NoSimpleStartMenu", "HideClock", "NoStartMenuPinnedList",
           "NoViewContextMenu", "NoTrayContextMenu", "DisableTaskMgr",
           "DisableRegistryTools", "NoDispCPL", "StartMenuLogOff", "NoLogoff")


# Le copie per tornare indietro: fuori da software/log, che si pulisce da
# solo dopo qualche settimana (registri.pulisci).
CARTELLA_COPIE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "backup", "windows")


def cartelle_esecuzione_automatica() -> List[str]:
    return [os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup"),
            os.path.join(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")]


def collegamenti_originale() -> List[str]:
    """I collegamenti dell'Esecuzione automatica che avviano il programma
    originale (sulla riserva: Start MasterflexD.lnk). Si riconoscono dal
    nome: i nostri si chiamano "Masterflex D1008", mai "MasterflexD"."""
    trovati = []
    for base in cartelle_esecuzione_automatica():
        try:
            nomi = os.listdir(base)
        except OSError:
            continue
        for n in nomi:
            if "masterflexd" in n.lower().replace(" ", "") and "d1008" not in n.lower():
                trovati.append(os.path.join(base, n))
    return trovati


def _avvia_explorer() -> None:
    """Explorer con i diritti normali dell'utente. Il nostro avvio
    automatico gira da amministratore: un explorer lanciato da li' sarebbe
    amministratore anche lui, e tutto cio' che si apre dal desktop lo
    sarebbe a sua volta.

    Fino al 14/11/2026 lo si abbassava con runas /trustlevel:0x20000. Ma
    quel livello ("utente di base" di SAFER) e' un gettone che il
    sandbox di Chrome non regge: la nostra pagina, aperta da un programma
    lanciato da quell'explorer, cadeva subito con STATUS_BREAKPOINT. Al
    primo avvio funzionava (lo apre l'avvio automatico), chiuso e
    riaperto dal desktop no. Ora lo si lancia con un'operazione pianificata
    al livello limitato, che da' lo stesso gettone di un accesso normale;
    se schtasks non c'e', lo si lancia diretto."""
    exe = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe")
    nome = "Masterflex explorer"
    zitto = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                 stdin=subprocess.DEVNULL, timeout=20)
    try:
        subprocess.call(["schtasks", "/Create", "/TN", nome, "/TR", exe, "/SC", "ONCE",
                         "/ST", "00:00", "/RL", "LIMITED", "/IT", "/F"], **zitto)
        subprocess.call(["schtasks", "/Run", "/TN", nome], **zitto)
        for _ in range(10):
            time.sleep(0.5)
            if "explorer.exe" in _processi():
                break
        subprocess.call(["schtasks", "/Delete", "/TN", nome, "/F"], **zitto)
        if "explorer.exe" in _processi():
            return
    except Exception:                                   # noqa: BLE001
        pass
    subprocess.Popen([exe])


def _reg():
    try:
        import winreg
        return winreg
    except ImportError:
        return None


def _valori(radice, chiave: str) -> Dict[str, object]:
    winreg = _reg()
    fuori: Dict[str, object] = {}
    try:
        k = winreg.OpenKey(radice, chiave)
    except OSError:
        return fuori
    try:
        i = 0
        while True:
            try:
                nome, valore, _ = winreg.EnumValue(k, i)
            except OSError:
                break
            fuori[nome] = valore
            i += 1
    finally:
        winreg.CloseKey(k)
    return fuori


def _processi() -> List[str]:
    try:
        uscita = subprocess.check_output(["tasklist", "/fo", "csv", "/nh"],
                                         stderr=subprocess.STDOUT, timeout=20)
        testo = uscita.decode("mbcs" if os.name == "nt" else "utf-8", "replace")
    except Exception:                                   # noqa: BLE001
        return []
    return [r.split('","')[0].strip('"').lower() for r in testo.splitlines() if r.strip()]


def controlla() -> Dict[str, object]:
    """Cosa nasconde il desktop. Non cambia niente."""
    winreg = _reg()
    if winreg is None:
        return {"windows": False, "problemi": [], "testo": "solo su Windows"}
    HKLM, HKCU = winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER
    wl_m, wl_u = _valori(HKLM, WINLOGON), _valori(HKCU, WINLOGON)
    shell_m = str(wl_m.get("Shell") or "")
    shell_u = str(wl_u.get("Shell") or "")
    shell = shell_u or shell_m or "explorer.exe"
    pol = {}
    for nome_radice, radice in (("HKCU", HKCU), ("HKLM", HKLM)):
        for chiave in (POL_EXPLORER, POL_SISTEMA):
            for k, v in _valori(radice, chiave).items():
                if k in BLOCCHI and v:
                    pol["%s %s" % (nome_radice, k)] = v
    avvio = {}
    for nome_radice, radice in (("HKCU", HKCU), ("HKLM", HKLM)):
        for chiave in AVVIO:
            for k, v in _valori(radice, chiave).items():
                avvio["%s %s\\%s" % (nome_radice, chiave.rsplit("\\", 1)[-1], k)] = v
    cartelle_avvio = []
    for base in cartelle_esecuzione_automatica():
        try:
            cartelle_avvio += [os.path.join(base, n) for n in os.listdir(base)]
        except OSError:
            pass
    processi = _processi()
    explorer = "explorer.exe" in processi
    problemi = []
    if os.path.basename(shell.split()[0].strip('"')).lower() != "explorer.exe":
        problemi.append("la shell di Windows e' sostituita: %s (%s). All'accesso explorer "
                        "non parte, quindi niente desktop ne' barra."
                        % (shell, "utente" if shell_u else "tutto il PC"))
    if pol:
        problemi.append("policy che nascondono parti di Windows: %s"
                        % ", ".join("%s=%s" % kv for kv in sorted(pol.items())))
    if not explorer:
        problemi.append("explorer.exe non e' in esecuzione: desktop e barra non ci sono.")
    originali = sorted(set(p for p in processi if p in (
        "gatecontrol.exe", "phxstatus.exe", "webfacade.exe", "logserver.exe",
        "statusappui.exe", "phxscanner.exe", "masterflex.exe")))
    d = {"windows": True, "shell_pc": shell_m, "shell_utente": shell_u,
         "userinit": wl_m.get("Userinit"), "accesso_automatico": wl_m.get("AutoAdminLogon"),
         "policy": pol, "avvio": avvio, "cartelle_avvio": cartelle_avvio,
         "explorer_attivo": explorer, "programmi_originali": originali,
         "problemi": problemi}
    d["testo"] = testo(d)
    return d


def testo(d: Dict[str, object]) -> str:
    righe = ["Desktop di Windows - controllo %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M")]
    if not d.get("windows"):
        return "solo su Windows"
    righe.append("Shell (tutto il PC): %s" % (d["shell_pc"] or "(non impostata = explorer.exe)"))
    righe.append("Shell (utente):      %s" % (d["shell_utente"] or "(non impostata)"))
    righe.append("Userinit: %s" % d.get("userinit"))
    righe.append("Accesso automatico: %s" % d.get("accesso_automatico"))
    righe.append("explorer.exe in esecuzione: %s" % ("si'" if d["explorer_attivo"] else "NO"))
    righe.append("Programmi originali in esecuzione: %s" % (", ".join(d["programmi_originali"]) or "nessuno"))
    righe.append("Policy che nascondono: %s" % (", ".join("%s=%s" % kv for kv in sorted(d["policy"].items())) or "nessuna"))
    righe.append("Avvio automatico (registro):")
    for k, v in sorted(d["avvio"].items()):
        righe.append("   %s = %s" % (k, v))
    righe.append("Cartelle Esecuzione automatica:")
    for p in d["cartelle_avvio"]:
        righe.append("   %s" % p)
    righe.append("")
    righe.append("Da guardare:" if d["problemi"] else "Niente che nasconda il desktop, adesso.")
    for p in d["problemi"]:
        righe.append(" - " + p)
    return "\n".join(righe)


def salva_rapporto(cartella: str) -> Optional[str]:
    d = controlla()
    if not d.get("windows"):
        return None
    nome = os.path.join(cartella, "desktop-%s.txt" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    with io.open(nome, "w", encoding="utf-8") as f:
        f.write(d["testo"] + "\n")
    return nome


def mostra() -> str:
    """Fa ricomparire desktop e barra ADESSO, senza cambiare la
    configurazione di Windows. Se la shell e' sostituita, explorer lanciato
    a mano aprirebbe solo una finestra di cartelle: per un attimo si mette
    explorer.exe come shell dell'utente (HKCU, che prevale su quella del
    PC), si lancia explorer, e si rimette com'era. Al prossimo accesso tutto
    torna come l'ha voluto il programma originale."""
    winreg = _reg()
    if winreg is None:
        raise RuntimeError("solo su Windows")
    if "explorer.exe" in _processi():
        d = controlla()
        if not [p for p in d["problemi"] if "sostituita" in p]:
            return "explorer e' gia' in esecuzione: se il desktop non si vede, sono le policy (Togli i blocchi)"
    HKCU = winreg.HKEY_CURRENT_USER
    prima = _valori(HKCU, WINLOGON).get("Shell")
    k = winreg.CreateKey(HKCU, WINLOGON)
    try:
        winreg.SetValueEx(k, "Shell", 0, winreg.REG_SZ, "explorer.exe")
        _avvia_explorer()
        time.sleep(8)               # explorer legge la shell all'avvio
    finally:
        if prima is None:
            try:
                winreg.DeleteValue(k, "Shell")
            except OSError:
                pass
        else:
            winreg.SetValueEx(k, "Shell", 0, winreg.REG_SZ, prima)
        winreg.CloseKey(k)
    return "explorer avviato: desktop e barra dovrebbero ricomparire"


def togli_blocchi(cartella: Optional[str] = None, riavvia: bool = True) -> str:
    """Toglie le policy dell'utente (HKCU) che nascondono desktop e barra,
    dopo averne fatto una copia (rimetti_blocchi). Quelle del PC (HKLM)
    chiedono l'amministratore: si segnalano soltanto. Vale dal prossimo
    avvio di explorer."""
    winreg = _reg()
    if winreg is None:
        raise RuntimeError("solo su Windows")
    HKCU = winreg.HKEY_CURRENT_USER
    copia = {}
    for chiave in (POL_EXPLORER, POL_SISTEMA):
        vals = dict((n, v) for n, v in _valori(HKCU, chiave).items() if n in BLOCCHI and v)
        if vals:
            copia[chiave] = vals
    if not copia:
        return "nessun blocco nell'utente da togliere"
    # prima la copia, poi si cancella: senza copia non si tocca niente
    os.makedirs(CARTELLA_COPIE, exist_ok=True)
    nome = os.path.join(CARTELLA_COPIE, "desktop-blocchi-%s.json" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    with io.open(nome, "w", encoding="utf-8") as f:
        json.dump(copia, f, indent=1)
    for chiave, vals in copia.items():
        k = winreg.OpenKey(HKCU, chiave, 0, winreg.KEY_SET_VALUE)
        try:
            for n in vals:
                winreg.DeleteValue(k, n)
        finally:
            winreg.CloseKey(k)
    riavviato = _riavvia_explorer() if riavvia else False
    return "tolti %d blocchi (copia in %s)%s" % (
        sum(len(v) for v in copia.values()), os.path.basename(nome),
        ": explorer riavviato, il desktop dovrebbe ricomparire" if riavviato
        else (": valgono dal prossimo avvio di explorer" if not riavvia
              else ": valgono dal prossimo accesso a Windows"))


def _riavvia_explorer() -> bool:
    """Le policy di Explorer (NoDesktop...) si leggono all'avvio di
    explorer: lo si chiude e lo si riapre, cosi' valgono subito."""
    try:
        subprocess.call(["taskkill", "/f", "/im", "explorer.exe"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        time.sleep(2)
        _avvia_explorer()
        return True
    except Exception:                                   # noqa: BLE001
        return False


def rimetti_blocchi(cartella: Optional[str] = None) -> str:
    """Rimette le policy dall'ultima copia fatta da togli_blocchi, e i
    collegamenti del programma originale nell'Esecuzione automatica.
    Le copie vecchie (prima del 7/11 sera) stavano in log/diagnosi: si
    guarda anche li'."""
    winreg = _reg()
    if winreg is None:
        raise RuntimeError("solo su Windows")
    collegamenti = rimetti_collegamenti()
    copie = []
    for c in [CARTELLA_COPIE] + ([cartella] if cartella else []):
        try:
            copie += [os.path.join(c, n) for n in os.listdir(c) if n.startswith("desktop-blocchi-")]
        except OSError:
            pass
    copie.sort(key=os.path.basename)
    if not copie:
        return "nessuna copia dei blocchi da rimettere" + (
            "; " + collegamenti if collegamenti else "")
    with io.open(copie[-1], encoding="utf-8") as f:
        copia = json.load(f)
    for chiave, vals in copia.items():
        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, chiave)
        try:
            for n, v in vals.items():
                winreg.SetValueEx(k, n, 0, winreg.REG_DWORD, int(v))
        finally:
            winreg.CloseKey(k)
    _riavvia_explorer()
    return "blocchi rimessi da %s%s" % (os.path.basename(copie[-1]),
                                        "; " + collegamenti if collegamenti else "")


def togli_collegamenti() -> List[str]:
    """Sposta in CARTELLA_COPIE i collegamenti dell'Esecuzione automatica
    che avviano il programma originale. Restituisce i nomi spostati."""
    spostati = []
    for p in collegamenti_originale():
        base = os.path.dirname(p)
        sotto = "comune" if base.lower().startswith(os.environ.get("PROGRAMDATA", "?").lower()) else "utente"
        dest_dir = os.path.join(CARTELLA_COPIE, "esecuzione-automatica-" + sotto)
        try:
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, os.path.basename(p))
            if os.path.exists(dest):
                os.remove(dest)
            shutil.move(p, dest)
            spostati.append(os.path.basename(p))
        except (IOError, OSError):
            pass                    # comune senza diritti: resta, si segnala
    return spostati


def rimetti_collegamenti() -> str:
    rimessi = []
    for sotto, base in zip(("utente", "comune"), cartelle_esecuzione_automatica()):
        d = os.path.join(CARTELLA_COPIE, "esecuzione-automatica-" + sotto)
        try:
            nomi = os.listdir(d)
        except OSError:
            continue
        for n in nomi:
            try:
                shutil.move(os.path.join(d, n), os.path.join(base, n))
                rimessi.append(n)
            except (IOError, OSError):
                pass
    return ("rimesso nell'Esecuzione automatica: %s" % ", ".join(rimessi)) if rimessi else ""


def ripristina_all_avvio() -> str:
    """Quello che fa il nostro avvio automatico, dopo aver chiuso i
    programmi originali: niente blocchi, collegamento dell'originale fuori
    dall'Esecuzione automatica, explorer acceso. Non solleva: all'avvio
    non c'e' nessuno a leggere un errore, lo si scrive e si va avanti."""
    winreg = _reg()
    if winreg is None:
        return "solo su Windows"
    fatto = []
    try:
        spostati = togli_collegamenti()
        if spostati:
            fatto.append("tolto dall'Esecuzione automatica: %s" % ", ".join(spostati))
        rimasti = collegamenti_originale()
        if rimasti:
            fatto.append("NON tolto (servono i diritti): %s" % ", ".join(rimasti))
    except Exception as exc:                            # noqa: BLE001
        fatto.append("collegamenti: %s" % exc)
    try:
        esito = togli_blocchi(riavvia=False)
        tolti = not esito.startswith("nessun")
        fatto.append(esito)
    except Exception as exc:                            # noqa: BLE001
        tolti = False
        fatto.append("blocchi: %s" % exc)
    try:
        if tolti and "explorer.exe" in _processi():
            # le policy le legge all'avvio: va riavviato
            _riavvia_explorer()
            fatto.append("explorer riavviato")
        elif "explorer.exe" not in _processi():
            _avvia_explorer()
            fatto.append("explorer avviato")
    except Exception as exc:                            # noqa: BLE001
        fatto.append("explorer: %s" % exc)
    return "; ".join(fatto) or "niente da fare"

