@echo off
setlocal
title Ripristino impostazioni di alimentazione - Windows 7 x86

REM ===========================================================================
REM  RIPRISTINA_Alimentazione.bat
REM
REM  Annulla le modifiche di sistema fatte da INSTALLA.bat: rimette la
REM  combinazione "Bilanciato", riabilita sospensione e ibernazione e
REM  restituisce alla scheda la gestione del risparmio energia.
REM
REM  NON disinstalla il driver: per quello usa Gestione dispositivi.
REM
REM  Attenzione: se riabiliti la sospensione, dopo un risveglio da S3 la
REM  scheda Kodak con ogni probabilita' non rispondera' piu' finche' non la
REM  disabiliti e riabiliti da Gestione dispositivi. E' il motivo per cui
REM  INSTALLA.bat la disattiva.
REM ===========================================================================

set CLASSGUID={533245A7-2867-11d5-BE3E-00902731AC5D}
set SCHEME_BALANCED=381b4222-f694-41f0-9685-ff5bb260df2e
set SUB_SLEEP=238c9fa8-0aad-41ed-83f4-97be242c8f20
set SET_ALLOWSTANDBY=abfc2519-3608-4c2a-94ea-171b0ed546ab

net session >nul 2>&1
if errorlevel 1 (
    echo  ERRORE: servono i privilegi di amministratore.
    echo  Clic destro sul file, "Esegui come amministratore".
    pause
    exit /b 1
)

echo.
echo  Ripristino della combinazione "Bilanciato"...
powercfg -setactive %SCHEME_BALANCED% >nul 2>&1

echo  Riabilitazione degli stati di standby S1-S3...
powercfg -setacvalueindex SCHEME_CURRENT %SUB_SLEEP% %SET_ALLOWSTANDBY% 1 >nul 2>&1
powercfg -setdcvalueindex SCHEME_CURRENT %SUB_SLEEP% %SET_ALLOWSTANDBY% 1 >nul 2>&1

echo  Ripristino dei timeout predefiniti...
powercfg -change -standby-timeout-ac 30 >nul 2>&1
powercfg -change -standby-timeout-dc 15 >nul 2>&1
powercfg -change -disk-timeout-ac 20 >nul 2>&1
powercfg -change -disk-timeout-dc 10 >nul 2>&1

echo  Riabilitazione dell'ibernazione...
powercfg -h on >nul 2>&1

powercfg -setactive SCHEME_CURRENT >nul 2>&1

echo  Rimozione di PnPCapabilities dalla scheda...
for /f "tokens=*" %%K in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Class\%CLASSGUID%" 2^>nul ^| findstr /r "\\[0-9][0-9][0-9][0-9]$"') do (
    reg query "%%K" /v MatchingDeviceId 2>nul | findstr /i "ven_11b2" >nul
    if not errorlevel 1 (
        reg delete "%%K" /v PnPCapabilities /f >nul 2>&1
        echo     ripristinata: %%K
    )
)

echo.
echo  Combinazione attiva:
powercfg -getactivescheme
echo.
echo  Stati di sospensione disponibili:
powercfg -a
echo.
echo  Fatto. Riavvia per applicare tutto.
echo.
pause
endlocal
