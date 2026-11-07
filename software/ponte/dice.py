# -*- coding: utf-8 -*-
"""
Il Digital ICE della Kodak (DMLDICELib.dll dell'SDK) su un fotogramma letto
da noi: R, G, B e infrarosso in, R G B puliti fuori.

    python dice.py <cartella con DMLDICELib.dll> <ingresso.raw> <uscita.raw> <larghezza> <altezza>

ingresso: uint16 little endian, pixel intercalati R G B IR, valori a 14 bit
(0..16383). uscita: lo stesso formato, come l'ha lasciato la libreria.
Stampa una riga JSON: {"process": .., "pixel": .., "difettosi": .., "secondi": ..}.

Gira in un processo a parte (32 bit, sulla macchina: la DLL e' dell'SDK e
non esce mai da li'), cosi' se la libreria cade non porta giu' l'interfaccia.
Lo chiama filmscan/ice_kodak.py.

Strutture (sonda del 7/11/2026 sulla riserva, sdk.sonda_ice): DMLDICEBegin(101,
statica 32, 2, 1, 14, 2, 0, 0, 0) a 0x1803B6D0 dell'SDK; DMLDICEProcess(handle,
dinamica 48, buffer, larghezza, altezza, 0, 1, 0, 0, 0, larghezza, altezza, 0)
a 0x1803E3A5. Con i piani separati la libreria non trova niente; con i pixel
intercalati RGBI ha trovato 17679 pixel difettosi su 2.940.000: e' questa la
disposizione. DMLDICEDefectCount restituisce (pixel totali, difettosi).
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time


def main(argv) -> int:
    cartella, ingresso, uscita, w, h = argv[0], argv[1], argv[2], int(argv[3]), int(argv[4])
    # le DLL da cui dipende stanno nella stessa cartella dell'SDK
    os.environ["PATH"] = cartella + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(cartella)
    os.chdir(cartella)
    lib = ctypes.CDLL(os.path.join(cartella, "DMLDICELib.dll"))

    class Statica(ctypes.Structure):
        _fields_ = [("v", ctypes.c_uint32 * 8)]

    class Dinamica(ctypes.Structure):
        _fields_ = [("v", ctypes.c_uint32 * 12)]

    begin = lib["?DMLDICEBegin@@YAPAXKUDICEInfoStaticTag@@@Z"]
    begin.restype, begin.argtypes = ctypes.c_void_p, [ctypes.c_uint32, Statica]
    process = lib["?DMLDICEProcess@@YAJPAXUDICEInfoDynamicTag@@@Z"]
    process.restype, process.argtypes = ctypes.c_long, [ctypes.c_void_p, Dinamica]
    conta = lib["?DMLDICEDefectCount@@YAJPAXUDICEInfoDynamicTag@@PAK2@Z"]
    conta.restype = ctypes.c_long
    conta.argtypes = [ctypes.c_void_p, Dinamica, ctypes.POINTER(ctypes.c_uint32),
                      ctypes.POINTER(ctypes.c_uint32)]
    fine = lib["?DMLDICEEnd@@YAJPAX@Z"]
    fine.restype, fine.argtypes = ctypes.c_long, [ctypes.c_void_p]

    with open(ingresso, "rb") as f:
        dati = f.read()
    n = w * h * 4
    if len(dati) != n * 2:
        raise SystemExit("ingresso di %d byte, attesi %d" % (len(dati), n * 2))
    buf = (ctypes.c_uint16 * (n * 2))()          # margine doppio, come nella sonda
    ctypes.memmove(buf, dati, len(dati))
    st = Statica()
    for k, v in enumerate((32, 2, 1, 14, 2, 0, 0, 0)):
        st.v[k] = v
    din = Dinamica()
    for k, v in enumerate((48, ctypes.addressof(buf), w, h, 0, 1, 0, 0, 0, w, h, 0)):
        din.v[k] = v
    t0 = time.time()
    handle = begin(101, st)
    if not handle:
        raise SystemExit("DMLDICEBegin non riuscito")
    esito = {"process": int(process(handle, din))}
    tot, dif = ctypes.c_uint32(0), ctypes.c_uint32(0)
    conta(handle, din, ctypes.byref(tot), ctypes.byref(dif))
    esito["pixel"], esito["difettosi"] = int(tot.value), int(dif.value)
    esito["end"] = int(fine(handle))
    esito["secondi"] = round(time.time() - t0, 2)
    with open(uscita, "wb") as f:
        f.write(ctypes.string_at(ctypes.addressof(buf), n * 2))
    print(json.dumps(esito))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
