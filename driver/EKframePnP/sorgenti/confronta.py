"""
Confronta un binario appena compilato con quello del pacchetto collaudato,
ignorando i due campi che cambiano a ogni compilazione: la data nell'header
COFF e il checksum PE. Se il resto coincide, il pacchetto e' davvero fatto da
questi sorgenti.

    python3 confronta.py EKframePnP.sys ../pacchetto/EKframePnP.sys
    python3 confronta.py ektest.exe     ../pacchetto/ektest.exe
"""
import sys
import pefile


def normalizzato(percorso):
    pe = pefile.PE(percorso)
    pe.FILE_HEADER.TimeDateStamp = 0
    pe.OPTIONAL_HEADER.CheckSum = 0
    return pe.write()


def main():
    if len(sys.argv) != 3:
        print(__doc__.strip())
        return 2
    a, b = (normalizzato(p) for p in sys.argv[1:])
    if a == b:
        print("UGUALI (a parte data e checksum)")
        return 0
    diff = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
    print("DIVERSI: %d byte" % diff)
    return 1


if __name__ == "__main__":
    sys.exit(main())
