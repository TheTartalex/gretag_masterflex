@echo off
REM ===========================================================================
REM  ELABORATORE.bat - questo PC elabora i fotogrammi della Masterflex.
REM
REM  Doppio clic e basta. La prima volta installa Python 3.8 (va anche su
REM  Windows 7) e le librerie, poi si collega alla macchina con indirizzo e
REM  chiave di elaboratore.json (nel pacchetto scaricato dalla macchina:
REM  pannello Macchina, "Elaboratori in rete"). Chiudendo la finestra si
REM  smette di elaborare: la macchina continua da sola.
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Elaboratore Masterflex

set "PY="
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python38\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python38-32\python.exe"
) do if not defined PY if exist "%%~P" set "PY=%%~P"
if not defined PY (
    for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%P"
)
if not defined PY (
    for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%P"
)
if defined PY goto :librerie

echo  Python non c'e': lo scarico e lo installo (solo per questo utente)...
set "ARCH=amd64"
if /i "%PROCESSOR_ARCHITECTURE%"=="x86" if not defined PROCESSOR_ARCHITEW6432 set "ARCH="
if defined ARCH (set "PYURL=https://www.python.org/ftp/python/3.8.10/python-3.8.10-amd64.exe") else (set "PYURL=https://www.python.org/ftp/python/3.8.10/python-3.8.10.exe")
set "PYEXE=%TEMP%\python-3.8.10-elaboratore.exe"
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=3072; (New-Object Net.WebClient).DownloadFile('%PYURL%','%PYEXE%')"
if not exist "%PYEXE%" (
    echo  Download non riuscito. Installare Python 3.8 o piu' recente da python.org
    echo  e rilanciare questo file.
    pause
    exit /b 1
)
"%PYEXE%" /quiet InstallAllUsers=0 PrependPath=0 Include_test=0 Include_launcher=0
if defined ARCH (set "PY=%LOCALAPPDATA%\Programs\Python\Python38\python.exe") else (set "PY=%LOCALAPPDATA%\Programs\Python\Python38-32\python.exe")
if not exist "%PY%" (
    echo  Installazione di Python non riuscita.
    pause
    exit /b 1
)

:librerie
"%PY%" -c "import numpy, scipy, PIL, tifffile" >nul 2>&1
if not errorlevel 1 goto :parti
echo  Installo le librerie dell'elaborazione (numpy, scipy, Pillow, tifffile)...
"%PY%" -m pip install --disable-pip-version-check --user numpy scipy pillow tifffile
"%PY%" -c "import numpy, scipy, PIL, tifffile" >nul 2>&1
if errorlevel 1 (
    echo  Librerie non installate: serve internet. Rilanciare questo file.
    pause
    exit /b 1
)

:parti
echo.
echo  Elaboratore avviato. Lasciare aperta questa finestra.
echo.
"%PY%" elaboratore.py %*
echo.
echo  Elaboratore fermo.
pause
