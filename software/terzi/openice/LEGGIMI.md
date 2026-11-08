# openICE (terze parti)

Il Digital ICE di Nikon Scan 4 ricostruito bit per bit da
[openICE](https://github.com/marcinz606/openICE), versione in C (`src_C/`),
commit indicato in `VERSIONE-ORIGINE.txt`. Licenza **GPL-3.0** (`LICENSE`):
i sorgenti sono qui accanto ai binari, come chiede la licenza.

Lo usa `filmscan/ice_openice.py` (motore "openice" del Digital ICE): gli
passa un TIFF a 16 bit con R, G, B e infrarosso e legge il DNG pulito.

| file | dove gira |
|---|---|
| `bin/openice-win32.exe` | la macchina (Windows 7 a 32 bit) e gli elaboratori Windows |
| `bin/openice-linux-x86_64` | gli elaboratori Linux (Unraid, Docker), statico |

Si ricompilano nel container con `sh compila.sh` (serve
`gcc-mingw-w64-i686`). `-m32 -mfpmath=387` sono quelli che openICE
richiede per il risultato bit per bit; il binario Linux a 64 bit usa SSE e
puo' differire di qualche pixel (lo dice il loro README).

Unica modifica ai sorgenti: nessuna. `-include unistd.h` in compilazione
serve solo a dichiarare `_exit`.
