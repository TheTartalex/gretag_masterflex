#!/bin/sh
# Prepara lo zip di aggiornamento per la macchina, dal commit corrente.
#
#   sh strumenti/aggiornamento/prepara.sh [cartella-di-uscita] [--offline]
#
# Produce masterflex-aggiornamento-<data>-<commit>.zip con AGGIORNA.bat,
# LEGGIMI.txt, VERSIONE.txt e software/ come sono nel commit (git archive:
# niente dati locali, e i .bat escono con CRLF come vuole cmd.exe).
# Con --offline anche ...-pacchetti.zip: installer di Python 3.8.10 e wheel
# win32, con la stessa cartella principale, da estrarre accanto al primo.
set -e
cd "$(git rev-parse --show-toplevel)"
OUT="dist"
OFFLINE=""
for a in "$@"; do
    case "$a" in
        --offline) OFFLINE=1 ;;
        *) OUT="$a" ;;
    esac
done
if [ -n "$(git status --porcelain -- software strumenti/aggiornamento)" ]; then
    echo "Ci sono modifiche non committate in software/ o strumenti/: prima il commit." >&2
    exit 1
fi
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
DATA=$(date +%Y-%m-%d)
REV=$(git rev-parse --short HEAD)
NOME="masterflex-aggiornamento-$DATA-$REV"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

git archive --format=tar --prefix="$NOME/" HEAD software strumenti/aggiornamento \
    | tar -x -C "$TMP"
mv "$TMP/$NOME/strumenti/aggiornamento/AGGIORNA.bat" "$TMP/$NOME/"
mv "$TMP/$NOME/strumenti/aggiornamento/LEGGIMI.txt" "$TMP/$NOME/"
rm -rf "$TMP/$NOME/strumenti"
{
    printf 'Masterflex D1008 - pacchetto di aggiornamento\r\n'
    printf 'Data:    %s\r\n' "$DATA"
    printf 'Commit:  %s (%s)\r\n' "$REV" "$(git log -1 --format=%cs)"
    printf 'Ramo:    %s\r\n' "$(git rev-parse --abbrev-ref HEAD)"
    printf '\r\nUltime modifiche:\r\n'
    git log -8 --format='  %cs  %s' -- software strumenti/aggiornamento | sed 's/$/\r/'
} > "$TMP/$NOME/VERSIONE.txt"

(cd "$TMP" && zip -q -r -X "$OUT/$NOME.zip" "$NOME")
echo "$OUT/$NOME.zip"

if [ -n "$OFFLINE" ]; then
    mkdir -p "$TMP/off/$NOME"
    cp -r software "$TMP/off/sw"
    rm -rf "$TMP/off/sw/pacchetti"
    python3 -I "$TMP/off/sw/manutenzione.py" offline >/dev/null
    mv "$TMP/off/sw/pacchetti" "$TMP/off/$NOME/pacchetti"
    (cd "$TMP/off" && zip -q -r -X "$OUT/$NOME-pacchetti.zip" "$NOME")
    echo "$OUT/$NOME-pacchetti.zip"
fi
