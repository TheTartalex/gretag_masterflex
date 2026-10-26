@echo off
REM ===========================================================================
REM  INSTALLA.bat - installa o aggiorna filmscan sulla macchina. Uno solo.
REM
REM  Si lancia dalla cartella estratta dello zip. Guarda se il programma c'e'
REM  gia' (di solito Desktop\software):
REM
REM    - se NON c'e' (macchina pulita): copia il programma, installa Python
REM      e le librerie dalla cartella pacchetti dello zip, senza internet;
REM    - se c'e': aggiorna il codice, con il vecchio in backup\codice-<data>,
REM      e lascia ordini, scansioni, calibrazioni, impostazioni e il modo di
REM      lavoro dove sono.
REM
REM  In tutti e due i casi poi fa "Installa / ripara" (librerie, cartelle,
REM  firewall, controlli di driver, SDK e carrello) e chiede se attivare
REM  l'avvio automatico. Si chiede da solo i diritti di amministratore: una
REM  richiesta di Windows, all'inizio.
REM ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Installazione Masterflex D1008

if not exist "%~dp0software\MASTERFLEX.bat" (
    echo.
    echo  Questo file va lanciato dalla cartella estratta dello zip: accanto
    echo  deve esserci la cartella software. Tasto destro sullo zip, "Estrai
    echo  tutto", poi doppio clic su INSTALLA.bat nella cartella estratta.
    echo.
    pause
    exit /b 1
)

REM --- Diritti di amministratore -------------------------------------------
REM  Servono per il firewall, l'avvio automatico e la chiusura dei programmi
REM  originali. fltmc risponde solo a un amministratore.
fltmc >nul 2>&1
if errorlevel 1 goto :chiedi_diritti
goto :diritti_ok

:chiedi_diritti
if /i "%~1"=="elevato" (
    echo.
    echo  Diritti di amministratore non ottenuti. Tasto destro su INSTALLA.bat,
    echo  "Esegui come amministratore".
    echo.
    pause
    exit /b 1
)
echo.
echo  Servono i diritti di amministratore: confermare la richiesta di Windows.
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList 'elevato' -Verb RunAs" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  Non riesco a chiederli da qui. Tasto destro su INSTALLA.bat,
    echo  "Esegui come amministratore".
    echo.
    pause
    exit /b 1
)
REM  il lavoro continua nella finestra nuova, da amministratore
exit /b 0

:diritti_ok
echo.
echo  ==========================================================================
echo    Masterflex D1008 - installazione e aggiornamento
echo  ==========================================================================
echo.
type "%~dp0VERSIONE.txt" 2>nul
echo.

REM --- Dove ------------------------------------------------------------------
set "DEST=%USERPROFILE%\Desktop\software"
call :che_cosa
echo  Cartella del programma: !DEST!
if "!TIPO!"=="aggiornamento" (
    echo  Il programma c'e' gia': si AGGIORNA. Ordini, scansioni, calibrazioni,
    echo  impostazioni e modo di lavoro restano dove sono.
) else (
    echo  Il programma non c'e': INSTALLAZIONE NUOVA.
)
echo.
set "R="
set /p "R=  Invio per confermare, oppure scrivere un'altra cartella: "
if defined R (
    set "DEST=!R:"=!"
    call :che_cosa
    echo  Cartella scelta: !DEST! - !TIPO!
)
if "!DEST:~-1!"=="\" set "DEST=!DEST:~0,-1!"
if /i "!DEST!"=="%~dp0software" (
    echo  La cartella indicata e' quella dello zip: sceglierne un'altra.
    pause
    exit /b 1
)

echo.
echo  Prima di proseguire chiudere l'interfaccia web, la finestra "Agente
echo  scanner" e, se aperta, quella dell'osservatore.
pause

REM --- Copia -----------------------------------------------------------------
if "!TIPO!"=="aggiornamento" goto :aggiorna
echo.
echo  Copio il programma in !DEST!...
if not exist "!DEST!" mkdir "!DEST!"
xcopy "%~dp0software" "!DEST!" /E /I /Y /Q >nul
if errorlevel 1 (
    echo  [ERRORE] copia non riuscita in !DEST!.
    pause
    exit /b 1
)
goto :pacchetti

:aggiorna
call :trova_python
if not defined PY (
    echo  Python 3.8 a 32 bit non trovato: copio il codice senza backup.
    xcopy "%~dp0software" "!DEST!" /E /I /Y /Q >nul
    goto :pacchetti
)
REM  Il manutenzione.py nuovo fa il lavoro dalla cartella di destinazione:
REM  copia il codice (con backup), toglie i resti delle versioni vecchie e
REM  scrive MASTERFLEX.bat come .nuovo, che si sostituisce qui sotto.
copy /y "%~dp0software\manutenzione.py" "!DEST!\manutenzione.py" >nul
REM  "%~dp0." e non "%~dp0": la barra finale prima delle virgolette
REM  verrebbe letta come una virgoletta da Python.
"!PY!" "!DEST!\manutenzione.py" aggiorna "%~dp0."
if errorlevel 1 (
    echo.
    echo  [ERRORE] aggiornamento non riuscito, vedi sopra. Niente e' perso:
    echo  il codice di prima e' in "!DEST!\backup".
    pause
    exit /b 1
)
if exist "!DEST!\MASTERFLEX.bat.nuovo" move /y "!DEST!\MASTERFLEX.bat.nuovo" "!DEST!\MASTERFLEX.bat" >nul

:pacchetti
REM  Python e le librerie, per installare senza internet
if exist "%~dp0pacchetti\python-3.8.10.exe" (
    echo  Copio Python e le librerie ^(installazione senza internet^)...
    xcopy "%~dp0pacchetti" "!DEST!\pacchetti" /E /I /Y /Q >nul
)

REM --- Installa / ripara -----------------------------------------------------
echo.
echo  ==========================================================================
echo    Codice a posto. Adesso "Installa / ripara": Python se manca, librerie,
echo    cartelle, firewall, controlli di driver, SDK e firmware del carrello.
echo  ==========================================================================
cd /d "!DEST!"
call "!DEST!\MASTERFLEX.bat" installa
set "ESITO=!errorlevel!"
echo.
if not "!ESITO!"=="0" (
    echo  [ATTENZIONE] "Installa / ripara" non e' andato fino in fondo: vedi
    echo  sopra. Si puo' ripetere da "!DEST!\MASTERFLEX.bat", voce 7.
    pause
    exit /b 1
)
echo  ==========================================================================
echo    Fatto. Da qui in poi si usa "!DEST!\MASTERFLEX.bat".
echo  ==========================================================================
echo.
set "AV="
set /p "AV=  Aprire adesso il menu del programma? [s/N] "
if /i "!AV!"=="s" start "Masterflex D1008" /d "!DEST!" "!DEST!\MASTERFLEX.bat"
exit /b 0


REM ===========================================================================
:che_cosa
REM  Installazione nuova o aggiornamento, a seconda di cosa c'e' in DEST.
REM ===========================================================================
set "TIPO=nuova"
if exist "!DEST!\run.py" set "TIPO=aggiornamento"
exit /b 0


REM ===========================================================================
:trova_python
REM  Il Python della macchina, se c'e': serve per l'aggiornamento con backup.
REM ===========================================================================
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
exit /b 0
