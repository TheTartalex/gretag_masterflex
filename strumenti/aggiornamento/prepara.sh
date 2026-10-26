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
# Ogni file di software/ deve essere nella lista CODICE di manutenzione.py,
# altrimenti AGGIORNA.bat lo salta in silenzio (era successo con
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
NOME="masterflex-aggiornamento-$DATA-$REV"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

git archive --format=tar --prefix="$NOME/" HEAD software strumenti/aggiornamento \
    | tar -x -C "$TMP"
# ektest per la diagnosi (voce 6): lo strumento del driver collaudato
git show HEAD:driver/EKframePnP/pacchetto/ektest.exe > "$TMP/$NOME/software/ektest.exe"
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
