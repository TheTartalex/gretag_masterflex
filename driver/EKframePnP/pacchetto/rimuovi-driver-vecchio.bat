@echo off
rem ===================================================================
rem  rimuovi-driver-vecchio.bat
rem
rem  Toglie di mezzo il vecchio ekframe.sys (Eastman Kodak, 2000).
rem
rem  Su Windows 7 il vecchio driver parte all'accensione, crea il
rem  dispositivo \\.\EKFrameDev, poi fallisce in HalAssignSlotResources
rem  e se ne va SENZA cancellarlo: resta un dispositivo "zombie" senza
rem  gestori. Effetti: ScanDiag "NO scanner detected", l'SDK non apre lo
rem  scanner (errore 1), e il driver nuovo non puo' partire perche' il
rem  nome e' occupato (Gestione dispositivi, Codice 31).
rem
rem  Lo script: trova ogni servizio che carica ekframe.sys, lo disattiva e
rem  lo cancella, rinomina il file in ekframe.sys.vecchio. Non tocca
rem  EKframePnP.sys. Dopo serve un RIAVVIO.
rem ===================================================================
setlocal EnableDelayedExpansion

net session >nul 2>&1
if errorlevel 1 (
    echo  Servono i privilegi di amministratore:
    echo  clic destro, "Esegui come amministratore".
    pause
    exit /b 1
)

echo.
echo  Cerco servizi che caricano il vecchio ekframe.sys...
set TROVATI=0
for /f "delims=" %%L in ('reg query "HKLM\SYSTEM\CurrentControlSet\Services" /s /f "ekframe.sys" /d 2^>nul ^| findstr /b /i "HKEY_"') do (
    set "KEY=%%L"
    for %%N in ("%%L") do set "SVC=%%~nxN"
    set "IMG="
    for /f "tokens=2,*" %%A in ('reg query "%%L" /v ImagePath 2^>nul ^| findstr /i "ImagePath"') do set "IMG=%%B"
    echo.
    echo   servizio !SVC!  file: !IMG!
    sc stop "!SVC!" >nul 2>&1
    sc config "!SVC!" start= disabled >nul 2>&1
    sc delete "!SVC!" >nul 2>&1
    if errorlevel 1 (echo     cancellazione: segnata per il riavvio) else (echo     servizio cancellato)
    set /a TROVATI+=1
)
if "!TROVATI!"=="0" echo   nessun servizio carica ekframe.sys

echo.
echo  Rinomino il file del vecchio driver...
set FILE=%windir%\system32\drivers\ekframe.sys
if exist "%FILE%" (
    move /y "%FILE%" "%FILE%.vecchio" >nul 2>&1
    if exist "%FILE%" (
        echo   NON riuscito: il file e' in uso. Riavvia e rilancia questo script.
    ) else (
        echo   %FILE%  -^>  ekframe.sys.vecchio
    )
) else (
    echo   %FILE% non presente
)

echo.
echo  Controllo che il driver nuovo sia ancora configurato:
sc qc EKFrame 2>nul | findstr /i "BINARY_PATH_NAME START_TYPE"
if errorlevel 1 echo   servizio EKFrame assente: dopo il riavvio rilancia INSTALLA.bat

echo.
echo  ==============================================================
echo   ORA RIAVVIA LA MACCHINA.
echo   Al riavvio Gestione dispositivi dovrebbe mostrare la scheda
echo   senza triangolo giallo. Se c'e' ancora, rilancia INSTALLA.bat.
echo   Poi doppio clic su ektest.
echo  ==============================================================
echo.
pause
