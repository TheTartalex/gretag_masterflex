#!/bin/sh
# Ricompila i binari di openICE (vedi LEGGIMI.md).
set -e
cd "$(dirname "$0")/sorgenti"
SRC="openice.c dng.c ice_setup.c ice_row.c ice_buffers.c ice_front.c ice_back.c ice_core.c ice_analyze.c ice_pump.c ice_streamcalib.c"
i686-w64-mingw32-gcc -m32 -O2 -msse2 -mfpmath=387 -static -include unistd.h -o ../bin/openice-win32.exe $SRC -lm
gcc -O2 -static -include unistd.h -o ../bin/openice-linux-x86_64 $SRC -lm
ls -la ../bin
