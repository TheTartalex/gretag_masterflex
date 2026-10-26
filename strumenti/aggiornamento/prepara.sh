#!/bin/sh
# Prepara l'installer per la macchina, dal commit corrente.
#
#   sh strumenti/aggiornamento/prepara.sh [cartella-di-uscita] [--leggero]
#
# Produce masterflex-installer-<data>-<commit>.zip con INSTALLA.bat,
# LEGGIMI.txt, VERSIONE.txt, software/ come sono nel commit (git archive:
# niente dati locali, e i .bat escono con CRLF come vuole cmd.exe) e
# pacchetti/: installer di Python 3.8.10 e wheel win32, cosi' lo stesso zip
# installa su una macchina pulita senza internet e aggiorna quella che c'e'.
# I pacchetti si tengono in <uscita>/cache-pacchetti per non riscaricarli.
# Con --leggero niente pacchetti (solo per aggiornare una macchina che ha
# gia' Python e le librerie).
set -e
cd "$(git rev-parse --show-toplevel)"
OUT="dist"
LEGGERO=""
for a in "$@"; do
    case "$a" in
        --leggero) LEGGERO=1 ;;
        --offline) ;;   # vecchia opzione: ora i pacchetti ci sono sempre
        *) OUT="$a" ;;
    esac
done
if [ -n "$(git status --porcelain -- software strumenti/aggiornamento)" ]; then
    echo "Ci sono modifiche non committate in software/ o strumenti/: prima il commit." >&2
    exit 1
fi
# Ogni file di software/ deve essere nella lista CODICE di manutenzione.py,
# altrimenti l'aggiornamento lo salta in silenzio (era successo con
# collaudo_scanner.py).
MANCANTI=$(git ls-tree --name-only HEAD software/ | sed 's|^software/||' | python3 -c '
import sys, re
t = open("software/manutenzione.py", encoding="utf-8").read()
codice = eval(re.search(r"^CODICE = (\[.*?\])", t, re.S | re.M).group(1))
print(" ".join(n.strip() for n in sys.stdin if n.strip() not in codice))')
if [ -n "$MANCANTI" ]; then
    echo "Non sono in CODICE (manutenzione.py) e l'aggiornamento li salterebbe: $MANCANTI" >&2
    exit 1
fi
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
DATA=$(date +%Y-%m-%d)
REV=$(git rev-parse --short HEAD)
NOME="masterflex-installer-$DATA-$REV"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

git archive --format=tar --prefix="$NOME/" HEAD software strumenti/aggiornamento \
    | tar -x -C "$TMP"
# ektest per la diagnosi (voce 6): lo strumento del driver collaudato
git show HEAD:driver/EKframePnP/pacchetto/ektest.exe > "$TMP/$NOME/software/ektest.exe"
mv "$TMP/$NOME/strumenti/aggiornamento/INSTALLA.bat" "$TMP/$NOME/"
mv "$TMP/$NOME/strumenti/aggiornamento/LEGGIMI.txt" "$TMP/$NOME/"
rm -rf "$TMP/$NOME/strumenti"
{
    printf 'Masterflex D1008 - installazione e aggiornamento\r\n'
    printf 'Data:    %s\r\n' "$DATA"
    printf 'Commit:  %s (%s)\r\n' "$REV" "$(git log -1 --format=%cs)"
    printf 'Ramo:    %s\r\n' "$(git rev-parse --abbrev-ref HEAD)"
    printf '\r\nUltime modifiche:\r\n'
    git log -8 --format='  %cs  %s' -- software strumenti/aggiornamento | sed 's/$/\r/'
} > "$TMP/$NOME/VERSIONE.txt"

if [ -z "$LEGGERO" ]; then
    # manutenzione.py offline riempie pacchetti/ (Python con l'impronta, le
    # wheel con pip download per win32): partendo dalla cache scarica solo
    # quello che manca
    CACHE="$OUT/cache-pacchetti"
    mkdir -p "$CACHE" "$TMP/off"
    cp -r "$TMP/$NOME/software" "$TMP/off/sw"
    rm -rf "$TMP/off/sw/pacchetti"
    cp -r "$CACHE" "$TMP/off/sw/pacchetti"
    if ! python3 -I "$TMP/off/sw/manutenzione.py" offline >"$TMP/off/log" 2>&1; then
        cat "$TMP/off/log" >&2
        echo "Pacchetti non preparati: senza, l'installer non va su una macchina pulita senza internet." >&2
        exit 1
    fi
    cp "$TMP/off/sw/pacchetti/"* "$CACHE/"
    mkdir -p "$TMP/$NOME/pacchetti"
    cp "$TMP/off/sw/pacchetti/"* "$TMP/$NOME/pacchetti/"
fi

(cd "$TMP" && zip -q -r -X "$OUT/$NOME.zip" "$NOME")
echo "$OUT/$NOME.zip"
