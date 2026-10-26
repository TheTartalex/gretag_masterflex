#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Avvio dell'interfaccia web.

    python run.py                     solo questa macchina, scanner simulato
    python run.py --rete              sulla rete locale (genera il PIN)
    python run.py --controllo         diagnosi dell'ambiente (memoria, versioni)

Sulla macchina vera si usa MASTERFLEX.bat, che fa partire l'agente e poi:

    python run.py --rete --driver sdk --apri

L'interfaccia a finestra (ui.py, tkinter) e' stata tolta a ottobre 2026: aveva
una sua copia della logica, non sapeva comandare la macchina vera e muoveva i
motori senza passare dal lock dei lavori. L'interfaccia web si apre comunque
anche sul monitor della macchina (--apri).
"""
import argparse
import sys


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--web", action="store_true",
                    help="ignorato: l'interfaccia e' sempre quella web")
    ap.add_argument("--rete", action="store_true",
                    help="esponi sulla rete locale (genera un PIN)")
    ap.add_argument("--porta", type=int, default=8080)
    ap.add_argument("--db", default="./ordini.db")
    ap.add_argument("--impostazioni", default="./impostazioni.json",
                    help="dove si ricordano le scelte fatte dall'interfaccia")
    ap.add_argument("--driver", default="mock", choices=["mock", "sdk", "originale"],
                    help="sdk = la macchina vera, tramite l'agente sul ponte; "
                         "originale = immagini dal programma originale "
                         "(ponte/osservatore.py)")
    ap.add_argument("--ponte", default=r"C:\MasterflexD\ponte",
                    help="cartella di scambio con l'agente (driver sdk)")
    ap.add_argument("--apri", action="store_true",
                    help="apre l'interfaccia nel browser di questa macchina")
    ap.add_argument("--controllo", action="store_true",
                    help="verifica ambiente e limiti di memoria")
    a = ap.parse_args()

    if a.controllo:
        from filmscan.compat import check_environment
        r = check_environment()
        print("Sistema:      %s" % r["system"])
        print("Python:       %s (%d bit)" % (r["python"], r["bits"]))
        mb = r["available_mb"]
        print("Memoria:      %s" % ("%.0f MB disponibili" % mb if mb else "n/d"))
        print("Max sicuro:   %d dpi" % r["max_safe_dpi"])
        for w in r["warnings"]:
            print("Attenzione:   %s" % w)
        return 0

    from filmscan.config import AppConfig
    from filmscan.webui import avvia
    # Prima le scelte fatte dall'interfaccia (cartella d'archivio,
    # conservazione...) si perdevano a ogni riavvio.
    cfg = AppConfig.load(a.impostazioni)
    cfg.driver = a.driver
    # la cartella del ponte serve sia all'agente sia all'osservatore, e
    # dall'interfaccia si puo' passare da un collegamento all'altro
    cfg.bridge_path = a.ponte
    avvia("0.0.0.0" if a.rete else "127.0.0.1", a.porta, cfg, a.db,
          apri=a.apri, impostazioni=a.impostazioni)
    return 0


if __name__ == "__main__":
    sys.exit(main())
