@echo off
REM ===========================================================================
REM  AGGIORNA.bat - aggiorna filmscan sulla macchina con questo pacchetto.
REM
REM  Da lanciare dalla cartella estratta dello zip di aggiornamento. Sostituisce
REM  il codice nella cartella del programma gia' installata (di solito
REM  Desktop\software) e lascia dove sono ordini, scansioni, calibrazioni e
REM  impostazioni. Il codice vecchio va in backup\codice-<data>.
REM
REM  Funziona anche sopra la versione di settembre (quella con windows\*.bat):
REM  i vecchi .bat e il vecchio avvio automatico vengono tolti.
REM ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Aggiornamento Masterflex D1008

if not exist "%~dp0software\MASTERFLEX.bat" (
    echo  Questo file va lanciato dalla cartella estratta dello zip:
    echo  accanto deve esserci la cartella software.
    pause
    exit /b 1
)

echo.
echo  ==========================================================================
echo    Aggiornamento Masterflex D1008
echo  ==========================================================================
echo.
type "%~dp0VERSIONE.txt" 2>nul
echo.
echo  Chiudere prima l'interfaccia web e la finestra "Agente scanner".
echo.

set "DEST=%USERPROFILE%\Desktop\software"
if not exist "%DEST%\run.py" set "DEST="
if defined DEST (
    echo  Cartella del programma trovata: !DEST!
    set "R="
    set /p "R=  Invio per aggiornare questa, oppure scrivere un altro percorso: "
    if defined R set "DEST=!R!"
) else (
    set /p "DEST=  Percorso della cartella del programma (quella con run.py): "
)
if not defined DEST (
    echo  Nessuna cartella indicata.
    pause
    exit /b 1
)
set "DEST=%DEST:"=%"
if not exist "%DEST%\run.py" (
    echo.
    echo  In "%DEST%" non c'e' run.py.
    echo  Per una prima installazione: copiare la cartella software dove si
    echo  vuole, poi doppio clic su software\MASTERFLEX.bat, voce 7.
    pause
    exit /b 1
)
if /i "%DEST%"=="%~dp0software" (
    echo  La cartella indicata e' quella dello zip: niente da aggiornare.
    pause
    exit /b 1
)

REM --- Python della macchina: serve per il backup e la copia ---------------
set "PY="
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python38-32\python.exe"
    "%ProgramFiles%\Python38-32\python.exe"
    "C:\Python38-32\python.exe"
) do (
    if not defined PY if exist "%%~P" set "PY=%%~P"
)
if not defined PY (
    for /f "delims=" %%P in ('py -3.8-32 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%P"
)

if not defined PY (
    echo.
    echo  Python 3.8 a 32 bit non trovato: copio i file senza backup.
    xcopy "%~dp0software" "%DEST%" /E /I /Y /Q >nul
    goto :dopo
)

REM  Il manutenzione.py nuovo fa il lavoro dalla cartella di destinazione:
REM  copia il codice (con backup), toglie i resti della versione vecchia e
REM  scrive MASTERFLEX.bat come .nuovo, che si sostituisce qui sotto.
copy /y "%~dp0software\manutenzione.py" "%DEST%\manutenzione.py" >nul
REM  "%~dp0." e non "%~dp0": la barra finale prima delle virgolette
REM  verrebbe letta come una virgoletta da Python.
"%PY%" "%DEST%\manutenzione.py" aggiorna "%~dp0."
if errorlevel 1 (
    echo.
    echo  [ERRORE] aggiornamento non riuscito, vedi sopra. Niente e' stato perso:
    echo  il codice di prima e' in "%DEST%\backup".
    pause
    exit /b 1
)
if exist "%DEST%\MASTERFLEX.bat.nuovo" move /y "%DEST%\MASTERFLEX.bat.nuovo" "%DEST%\MASTERFLEX.bat" >nul

:dopo
REM  Wheel e installer di Python, se lo zip offline e' stato estratto qui accanto
if exist "%~dp0pacchetti\*.whl" (
    echo  Copio i pacchetti per l'installazione senza internet...
    xcopy "%~dp0pacchetti" "%DEST%\pacchetti" /E /I /Y /Q >nul
)

echo.
echo  ==========================================================================
echo    Codice aggiornato. Adesso parte "Installa / ripara" (voce 7):
echo    librerie alle versioni giuste, cartelle, firewall e controlli di
echo    driver, SDK e firmware del carrello.
echo    Per il firewall serve "Esegui come amministratore".
echo  ==========================================================================
echo.
pause
cd /d "%DEST%"
call "%DEST%\MASTERFLEX.bat" installa
echo.
echo  Fatto. Da qui in poi si usa "%DEST%\MASTERFLEX.bat".
pause
exit /b 0
