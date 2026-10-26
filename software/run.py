#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Avvio.

    python run.py                     interfaccia locale (finestra)
    python run.py --web               interfaccia web, solo questa macchina
    python run.py --web --rete        interfaccia web sulla rete locale
    python run.py --controllo         diagnosi dell'ambiente (memoria, versioni)

Sulla macchina vera si usa questo, ed e' quello che fa AVVIA.bat:

    python run.py --web --rete --driver sdk --apri

che prende i fotogrammi dallo scanner attraverso l'agente (ponte/agente.py) e
pubblica l'interfaccia sulla rete locale, per comandarla dal tablet.
"""
import argparse
import sys


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--web", action="store_true", help="interfaccia web")
    ap.add_argument("--rete", action="store_true",
                    help="con --web: esponi sulla rete locale (genera un token)")
    ap.add_argument("--porta", type=int, default=8080)
    ap.add_argument("--db", default="./ordini.db")
    ap.add_argument("--driver", default="mock",
                    choices=["mock", "bridge", "sdk", "native"],
                    help="sdk = la macchina vera, tramite l'agente sul ponte")
    ap.add_argument("--ponte", default=r"C:\MasterflexD\ponte",
                    help="cartella di scambio con l'agente (driver sdk o bridge)")
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

    if a.web:
        from filmscan.config import AppConfig
        from filmscan.webui import avvia
        cfg = AppConfig()
        cfg.driver = a.driver
        if a.driver in ("sdk", "bridge"):
            cfg.bridge_path = a.ponte
        avvia("0.0.0.0" if a.rete else "127.0.0.1", a.porta, cfg, a.db,
              apri=a.apri)
        return 0

    from filmscan.ui import main as ui_main
    ui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
