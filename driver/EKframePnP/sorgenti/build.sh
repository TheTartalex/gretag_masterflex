#!/bin/sh
# Compilazione con MinGW-w64 (i686) - produce EKframePnP.sys ed ektest.exe
# per Windows 7 x86. Requisiti: apt-get install gcc-mingw-w64-i686; pip install pefile
set -e
CC=i686-w64-mingw32-gcc
DDK=/usr/i686-w64-mingw32/include/ddk
CFLAGS="-O2 -Wall -Wextra -Wno-unused-parameter -Wno-missing-field-initializers \
 -std=gnu99 -march=i586 -mno-sse -mno-sse2 -mno-mmx -mno-3dnow \
 -fno-builtin -fno-stack-protector -fno-asynchronous-unwind-tables -fno-exceptions \
 -fno-strict-aliasing -fno-omit-frame-pointer \
 -D_X86_=1 -Di386=1 -DSTD_CALL -D_WIN32_WINNT=0x0601 -DNTDDI_VERSION=0x06010000 \
 -DWINVER=0x0601 -I$DDK"
for f in driver pnp ioctl hw arcnet dma; do
  $CC $CFLAGS -c $f.c -o obj_$f.o
done
$CC -shared -nostdlib -nostartfiles \
  -Wl,--subsystem,native -Wl,--image-base,0x10000 \
  -Wl,--entry,_DriverEntry@8 -Wl,--file-alignment,0x200 -Wl,--section-alignment,0x1000 \
  -Wl,--stack,0x40000 -Wl,--dynamicbase -Wl,--nxcompat \
  -Wl,--enable-stdcall-fixup -Wl,--exclude-all-symbols -Wl,--no-insert-timestamp \
  -o EKframePnP.sys obj_driver.o obj_pnp.o obj_ioctl.o obj_hw.o obj_arcnet.o obj_dma.o \
  -lntoskrnl -lhal -lgcc
# Flag WDM e checksum PE corretto
python3 -I - <<'PY'
import pefile
pe = pefile.PE("EKframePnP.sys")
pe.OPTIONAL_HEADER.DllCharacteristics |= 0x2000      # IMAGE_DLLCHARACTERISTICS_WDM_DRIVER
pe.OPTIONAL_HEADER.CheckSum = 0
pe.OPTIONAL_HEADER.CheckSum = pe.generate_checksum()
pe.write("EKframePnP.sys")
print("checksum %08X" % pe.OPTIONAL_HEADER.CheckSum)
PY
# Strumento di collaudo
$CC -O2 -march=i586 -mno-sse -static-libgcc -Wl,--no-insert-timestamp -o ektest.exe ektest.c
