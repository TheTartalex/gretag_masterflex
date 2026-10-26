@echo off
setlocal EnableDelayedExpansion
title Installazione driver Kodak IMAQ (EKframePnP) - Windows 7 x86

REM ===========================================================================
REM  INSTALLA.bat
REM
REM  1. installa il driver Plug and Play EKframePnP
REM  2. disabilita la sospensione S3 e l'ibernazione
REM  3. attiva la combinazione "Prestazioni elevate"
REM  4. impedisce a Windows di spegnere la scheda per risparmiare energia
REM
REM  Motivo del punto 4: questo driver inoltra soltanto gli IRP di power e non
REM  riprogramma i registri del bridge PLX 9080 al risveglio. Se Windows
REM  mettesse la scheda in D3 o il PC in S3, al ritorno la scheda resterebbe
REM  muta finche' non viene disabilitata e riabilitata a mano.
REM
REM  Le modifiche di alimentazione sono di sistema: si annullano con
REM  RIPRISTINA_Alimentazione.bat, che sta nella stessa cartella.
REM ===========================================================================

set CLASSGUID={533245A7-2867-11d5-BE3E-00902731AC5D}
set HWID=VEN_11B2^&DEV_0008
set SCHEME_HIGHPERF=8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c

REM --- GUID delle impostazioni di alimentazione (piu' affidabili degli alias)
set SUB_SLEEP=238c9fa8-0aad-41ed-83f4-97be242c8f20
set SET_STANDBYIDLE=29f6c1db-86da-48c5-9fdb-f2b67b1f44da
set SET_HIBERNATEIDLE=9d7815a6-7ee4-497e-8888-515a05f02364
set SET_HYBRIDSLEEP=94ac6d29-73ce-41a6-809f-6363ba21b47e
set SET_ALLOWSTANDBY=abfc2519-3608-4c2a-94ea-171b0ed546ab
set SET_WAKETIMERS=bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d
set SUB_DISK=0012ee47-9041-4b5d-9b77-535fba8b1442
set SET_DISKIDLE=6738e2c4-e8a5-4a42-b16a-e040e769756e
set SUB_PCIEXPRESS=501a4d13-42af-4429-9fd1-a8218c268e20
set SET_ASPM=ee12f906-d277-404b-b6da-e5fa1a576df5
set SUB_PROCESSOR=54533251-82be-4824-96c1-47b60b740d00
set SET_PROCMIN=893dee8e-2bef-41e0-89c6-b55d0929964c
set SET_PROCMAX=bc5038f7-23e0-4960-96da-33abaf5935ec

echo.
echo ===========================================================================
echo   Installazione driver Kodak IMAQ - EKframePnP
echo   Windows 7 x86
echo ===========================================================================
echo.

REM --------------------------------------------------------------------------
REM  Controllo privilegi di amministratore
REM --------------------------------------------------------------------------
net session >nul 2>&1
if errorlevel 1 (
    echo  ERRORE: servono i privilegi di amministratore.
    echo.
    echo  Chiudi questa finestra, fai clic destro su INSTALLA.bat e scegli
    echo  "Esegui come amministratore".
    echo.
    pause
    exit /b 1
)

REM --------------------------------------------------------------------------
REM  Controllo che sia un sistema a 32 bit
REM --------------------------------------------------------------------------
if /i "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
    echo  ATTENZIONE: questo sistema e' a 64 bit.
    echo.
    echo  Il driver e' compilato per x86 e su Windows a 64 bit non puo'
    echo  caricare, perche' li' la firma del kernel e' obbligatoria.
    echo.
    set /p CONTINUA="  Continuare comunque solo con le impostazioni di alimentazione? (S/N) "
    if /i not "!CONTINUA!"=="S" exit /b 1
    set SOLO_ALIMENTAZIONE=1
)

REM ==========================================================================
REM  FASE 1 - Installazione del driver
REM ==========================================================================
if defined SOLO_ALIMENTAZIONE goto :fase2

echo.
echo --- FASE 1 di 4 : installazione del driver ---------------------------------
echo.

if not exist "%~dp0EKframePnP.inf" (
    echo  ERRORE: EKframePnP.inf non trovato in questa cartella.
    echo  Metti INSTALLA.bat, EKframePnP.inf e EKframePnP.sys nella stessa cartella.
    pause
    exit /b 1
)
if not exist "%~dp0EKframePnP.sys" (
    echo  ERRORE: EKframePnP.sys non trovato in questa cartella.
    echo  Il file e' incluso nel pacchetto: estrai di nuovo lo zip completo.
    pause
    exit /b 1
)

REM  Il vecchio ekframe.sys, se carica all'avvio, lascia un dispositivo
REM  "zombie" che impedisce al driver nuovo di partire (Codice 31).
reg query "HKLM\SYSTEM\CurrentControlSet\Services" /s /f "ekframe.sys" /d 2>nul | findstr /b /i "HKEY_" >nul
if not errorlevel 1 goto :vecchio
if exist "%windir%\system32\drivers\ekframe.sys" goto :vecchio
goto :installa

:vecchio
echo.
echo  ATTENZIONE: e' presente il vecchio driver ekframe.sys.
echo  Va rimosso prima, poi serve un riavvio.
echo.
call "%~dp0rimuovi-driver-vecchio.bat"
exit /b 1

:installa
echo  Aggiungo il driver all'archivio driver di Windows...
echo  ^(comparira' l'avviso "Impossibile verificare l'autore": scegli
echo   "Installa il software del driver comunque" - su Win7 x86 e' normale,
echo   il kernel a 32 bit non richiede driver firmati^)
echo.
pnputil -i -a "%~dp0EKframePnP.inf"
if errorlevel 1 (
    echo.
    echo  AVVISO: pnputil ha segnalato un problema.
    echo  Puoi installare a mano da Gestione dispositivi: clic destro sulla
    echo  scheda, "Aggiorna driver", "Cerca il software nel computer",
    echo  e indica questa cartella.
    echo.
)

:fase2
REM ==========================================================================
REM  FASE 2 - Combinazione risparmio energia "Prestazioni elevate"
REM ==========================================================================
echo.
echo --- FASE 2 di 4 : combinazione "Prestazioni elevate" -----------------------
echo.

powercfg -setactive %SCHEME_HIGHPERF% >nul 2>&1
if errorlevel 1 (
    echo  La combinazione "Prestazioni elevate" non risulta presente: la ricreo.
    powercfg -duplicatescheme %SCHEME_HIGHPERF% >nul 2>&1
    powercfg -setactive %SCHEME_HIGHPERF% >nul 2>&1
)
echo  Combinazione attiva impostata su "Prestazioni elevate".

REM  Processore sempre al massimo
powercfg -setacvalueindex SCHEME_CURRENT %SUB_PROCESSOR% %SET_PROCMIN% 100 >nul 2>&1
powercfg -setdcvalueindex SCHEME_CURRENT %SUB_PROCESSOR% %SET_PROCMIN% 100 >nul 2>&1
powercfg -setacvalueindex SCHEME_CURRENT %SUB_PROCESSOR% %SET_PROCMAX% 100 >nul 2>&1
powercfg -setdcvalueindex SCHEME_CURRENT %SUB_PROCESSOR% %SET_PROCMAX% 100 >nul 2>&1
echo  Stato minimo e massimo del processore impostati al 100%%.

REM  Risparmio energia del bus PCI Express disattivato: il PLX 9080 e' su PCI,
REM  ma se la scheda e' dietro a un ponte PCIe l'ASPM puo' interferire.
powercfg -setacvalueindex SCHEME_CURRENT %SUB_PCIEXPRESS% %SET_ASPM% 0 >nul 2>&1
powercfg -setdcvalueindex SCHEME_CURRENT %SUB_PCIEXPRESS% %SET_ASPM% 0 >nul 2>&1
echo  Risparmio energia dei collegamenti PCI Express disattivato.

REM ==========================================================================
REM  FASE 3 - Sospensione S3 e ibernazione
REM ==========================================================================
echo.
echo --- FASE 3 di 4 : sospensione e ibernazione --------------------------------
echo.

REM  Timeout a 0 = mai
powercfg -change -standby-timeout-ac 0 >nul 2>&1
powercfg -change -standby-timeout-dc 0 >nul 2>&1
powercfg -change -hibernate-timeout-ac 0 >nul 2>&1
powercfg -change -hibernate-timeout-dc 0 >nul 2>&1
powercfg -change -disk-timeout-ac 0 >nul 2>&1
powercfg -change -disk-timeout-dc 0 >nul 2>&1
echo  Sospensione automatica, ibernazione e spegnimento dischi: mai.

powercfg -setacvalueindex SCHEME_CURRENT %SUB_SLEEP% %SET_STANDBYIDLE% 0 >nul 2>&1
powercfg -setdcvalueindex SCHEME_CURRENT %SUB_SLEEP% %SET_STANDBYIDLE% 0 >nul 2>&1
powercfg -setacvalueindex SCHEME_CURRENT %SUB_SLEEP% %SET_HIBERNATEIDLE% 0 >nul 2>&1
powercfg -setdcvalueindex SCHEME_CURRENT %SUB_SLEEP% %SET_HIBERNATEIDLE% 0 >nul 2>&1
powercfg -setacvalueindex SCHEME_CURRENT %SUB_SLEEP% %SET_HYBRIDSLEEP% 0 >nul 2>&1
powercfg -setdcvalueindex SCHEME_CURRENT %SUB_SLEEP% %SET_HYBRIDSLEEP% 0 >nul 2>&1
powercfg -setacvalueindex SCHEME_CURRENT %SUB_SLEEP% %SET_WAKETIMERS% 0 >nul 2>&1
powercfg -setdcvalueindex SCHEME_CURRENT %SUB_SLEEP% %SET_WAKETIMERS% 0 >nul 2>&1
powercfg -setacvalueindex SCHEME_CURRENT %SUB_DISK% %SET_DISKIDLE% 0 >nul 2>&1
powercfg -setdcvalueindex SCHEME_CURRENT %SUB_DISK% %SET_DISKIDLE% 0 >nul 2>&1

REM  "Consenti stati di standby (S1-S3)" = disattivato:
REM  e' questo che toglie davvero la S3 dalle scelte del sistema.
powercfg -setacvalueindex SCHEME_CURRENT %SUB_SLEEP% %SET_ALLOWSTANDBY% 0 >nul 2>&1
powercfg -setdcvalueindex SCHEME_CURRENT %SUB_SLEEP% %SET_ALLOWSTANDBY% 0 >nul 2>&1
echo  Stati di standby S1-S3 disattivati.

REM  Ibernazione disattivata del tutto (libera anche hiberfil.sys)
powercfg -h off >nul 2>&1
echo  Ibernazione disattivata a livello di sistema.

REM  Applica le modifiche alla combinazione attiva
powercfg -setactive SCHEME_CURRENT >nul 2>&1

REM  Il pulsante di sospensione della tastiera non deve fare nulla (0).
REM  Il pulsante di accensione lo lascio com'e': cambiarlo non serve allo scopo
REM  e sarebbe una modifica invasiva non richiesta.
powercfg -setacvalueindex SCHEME_CURRENT 4f971e89-eebd-4455-a8de-9e59040e7347 96996bc0-ad50-47ec-923b-6f41874dd9eb 0 >nul 2>&1
powercfg -setdcvalueindex SCHEME_CURRENT 4f971e89-eebd-4455-a8de-9e59040e7347 96996bc0-ad50-47ec-923b-6f41874dd9eb 0 >nul 2>&1
powercfg -setactive SCHEME_CURRENT >nul 2>&1
echo  Pulsante di sospensione disattivato.

REM ==========================================================================
REM  FASE 4 - Impedire lo spegnimento della scheda
REM ==========================================================================
echo.
echo --- FASE 4 di 4 : blocco del risparmio energia sulla scheda ----------------
echo.

REM  PnPCapabilities = 24 (0x18) nella chiave di classe del driver: toglie la
REM  casella "Consenti al computer di spegnere il dispositivo per risparmiare
REM  energia" e impedisce a Windows di mandare la scheda in D3 per inattivita'.
set TROVATA=0
for /f "tokens=*" %%K in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Class\%CLASSGUID%" 2^>nul ^| findstr /r "\\[0-9][0-9][0-9][0-9]$"') do (
    reg query "%%K" /v MatchingDeviceId 2>nul | findstr /i "ven_11b2" >nul
    if not errorlevel 1 (
        reg add "%%K" /v PnPCapabilities /t REG_DWORD /d 24 /f >nul 2>&1
        echo  Impostato PnPCapabilities=24 su:
        echo     %%K
        set TROVATA=1
    )
)

if "!TROVATA!"=="0" (
    echo  Nessuna chiave di classe trovata per la scheda.
    echo  E' normale se il driver non e' ancora stato installato oppure se la
    echo  scheda non e' presente: l'INF imposta comunque PnPCapabilities da solo
    echo  al momento dell'installazione.
)

REM  Stessa cosa sul nodo di enumerazione, per i casi in cui Windows legge di li'
for /f "tokens=*" %%E in ('reg query "HKLM\SYSTEM\CurrentControlSet\Enum\PCI" /f "VEN_11B2&DEV_0008" /k 2^>nul ^| findstr /i "VEN_11B2"') do (
    for /f "tokens=*" %%I in ('reg query "%%E" 2^>nul ^| findstr /v "VEN_11B2&DEV_0008$"') do (
        reg add "%%I\Device Parameters" /v EnableIdlePowerManagement /t REG_DWORD /d 0 /f >nul 2>&1
    )
)

echo.
echo ===========================================================================
echo   RIEPILOGO
echo ===========================================================================
echo.
echo  Combinazione attiva:
powercfg -getactivescheme
echo.
echo  Stati di sospensione ancora disponibili sul sistema:
powercfg -a
echo.
echo ===========================================================================
echo   FATTO
echo ===========================================================================
echo.
echo  Verifiche consigliate adesso:
echo.
echo   0. Lancia  ektest  da questa cartella: controlla driver, risorse e registri.
echo.
echo   1. Gestione dispositivi: la scheda deve risultare "funziona
echo      correttamente". Un codice 10 significa che START_DEVICE e' fallito.
echo.
echo   2. Proprieta' della scheda: la scheda "Risparmio energia" NON deve
echo      piu' comparire. Se compare ancora, riavvia e ricontrolla.
echo.
echo   3. Proprieta' della scheda, scheda "Risorse": annota quanti
echo      INTERVALLI DI MEMORIA ci sono e di che dimensione, piu' l'IRQ.
echo      Servono per completare il driver.
echo.
echo  Per annullare le modifiche di alimentazione: RIPRISTINA_Alimentazione.bat
echo.
pause
endlocal
