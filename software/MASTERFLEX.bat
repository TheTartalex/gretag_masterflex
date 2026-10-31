@echo off
REM ===========================================================================
REM  MASTERFLEX.bat - l'unico file da lanciare.
REM
REM  Doppio clic: menu con tutte le funzioni (installare, aggiornare, avviare,
REM  avvio automatico, collaudi, diagnosi...).
REM  Da riga di comando o dall'avvio automatico, senza menu:
REM
REM     MASTERFLEX.bat avvia [si]           completo, macchina vera ("si" salta la conferma)
REM     MASTERFLEX.bat avvia-senza-motori   completo, motori bloccati
REM     MASTERFLEX.bat simulazione          nessuna macchina
REM     MASTERFLEX.bat osservatore          man in the middle (programma originale)
REM     MASTERFLEX.bat automatico           l'ultimo modo usato, senza domande
REM                                         (lo lancia l'Utilita' di pianificazione)
REM     MASTERFLEX.bat installa | aggiorna | collaudo | diagnosi
REM
REM  Sostituisce i dodici .bat della cartella windows\ della versione di
REM  settembre. Il lavoro vero sta in manutenzione.py (solo libreria standard),
REM  qui resta solo quello che serve prima che Python esista.
REM ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Masterflex D1008

REM --- impostazioni: si possono cambiare qui -------------------------------
if not defined PONTE   set "PONTE=C:\MasterflexD\ponte"
if not defined SCANNER set "SCANNER=C:\MasterflexD\Scanner"
if not defined PORTA   set "PORTA=8080"
REM  completa = come il programma originale; leggera = l'altra combinazione di
REM  flag dell'SDK, da provare per capire se e' l'apertura a muovere le guide.
if not defined APERTURA set "APERTURA=completa"

set "PY_URL=https://www.python.org/ftp/python/3.8.10/python-3.8.10.exe"
set "PY_SHA256=ad07633a1f0cd795f3bf9da33729f662281df196b4567fa795829f3bb38a30ac"

call :trova_python

REM  L'ultimo modo di lavoro usato: Invio nel menu riparte con quello, ed e'
REM  quello dell'avvio automatico.
set "MODO_ULTIMO=avvia-senza-motori"
if defined PY for /f "usebackq delims=" %%m in (`call "%PY%" manutenzione.py modo`) do set "MODO_ULTIMO=%%m"

if not "%~1"=="" (
    call :azione %*
    exit /b !errorlevel!
)

:menu
cls
echo.
echo  ==========================================================================
echo    MASTERFLEX D1008 - scanner pellicola
echo  ==========================================================================
if defined PY (echo    Python: !PY!) else (echo    Python 3.8 a 32 bit: NON TROVATO - scegliere 7)
echo.
echo    AVVIO   ^(Invio = l'ultimo usato: !MODO_ULTIMO!^)
echo     1  Completo - macchina vera ^(chiude il programma originale^)
echo     2  Completo - motori BLOCCATI, solo acquisizione ^(idem^)
echo     3  Simulazione ^(nessuna macchina^)
echo    14  Man in the middle - programma originale + osservatore:
echo        non chiude niente e non avvia l'agente
echo.
echo    COLLAUDI
echo     4  Collaudo dello scanner: colori, intensita', trascinamento
echo     5  Collaudo del programma (senza macchina)
echo     6  Diagnosi e registri: uno zip da inviare ^(cartella log^)
echo    13  Firmware del carrello: controllo versione ^(richiesta 1.15^)
echo.
echo    MANUTENZIONE
echo     7  Installa / ripara (Python, librerie, cartelle, firewall)
echo     8  Aggiorna il programma
echo     9  Avvio automatico all'accensione
echo    10  Programma originale: stampa e densitometro
echo    11  Prepara il pacchetto per installare senza internet
echo    12  Disinstalla (avvio automatico, firewall, scorciatoie)
echo.
echo     0  Esci
echo.
set "SCELTA="
set /p "SCELTA=  Scelta: "
if not defined SCELTA (
    call :azione !MODO_ULTIMO!
    echo.
    pause
    goto :menu
)
if "%SCELTA%"=="0" exit /b 0
if "%SCELTA%"=="1"  call :azione avvia
if "%SCELTA%"=="2"  call :azione avvia-senza-motori
if "%SCELTA%"=="3"  call :azione simulazione
if "%SCELTA%"=="4"  call :azione collaudo-scanner
if "%SCELTA%"=="5"  call :azione collaudo
if "%SCELTA%"=="6"  call :azione diagnosi
if "%SCELTA%"=="7"  call :azione installa
if "%SCELTA%"=="8"  call :azione aggiorna
if "%SCELTA%"=="9"  call :azione avvio-automatico
if "%SCELTA%"=="10" call :azione originale
if "%SCELTA%"=="11" call :azione offline
if "%SCELTA%"=="12" call :azione disinstalla
if "%SCELTA%"=="13" call :azione firmware
if "%SCELTA%"=="14" call :azione osservatore
echo.
pause
goto :menu


REM ===========================================================================
:azione
REM ===========================================================================
if /i "%~1"=="installa"          goto :installa
if /i "%~1"=="offline"           goto :offline
if not defined PY (
    echo.
    echo  Python 3.8 a 32 bit non c'e'. Scegliere prima "Installa / ripara".
    exit /b 1
)
if /i "%~1"=="avvia"             goto :avvia
if /i "%~1"=="avvia-senza-motori" goto :avvia
if /i "%~1"=="simulazione"       goto :avvia
if /i "%~1"=="collaudo-scanner"  goto :collaudo_scanner
if /i "%~1"=="collaudo"          goto :collaudo
if /i "%~1"=="diagnosi"          goto :diagnosi
if /i "%~1"=="aggiorna"          goto :aggiorna
if /i "%~1"=="avvio-automatico"  goto :avvio_automatico
if /i "%~1"=="originale"         goto :originale
if /i "%~1"=="disinstalla"       goto :disinstalla
if /i "%~1"=="firmware"          goto :firmware
if /i "%~1"=="osservatore"       goto :osservatore
if /i "%~1"=="automatico"        goto :automatico
echo  Azione sconosciuta: %~1
exit /b 1


REM ===========================================================================
:avvia
REM  Agente nella sua finestra (i suoi messaggi si devono vedere), interfaccia
REM  web in questa. Due processi di proposito: se l'SDK del 2001 cade,
REM  l'interfaccia resta viva e lo dice.
REM ===========================================================================
set "MODO=%~1"
set "OPZ="
set "DRIVER=sdk"
set "CARTELLA=%PONTE%"
if /i "%MODO%"=="avvia-senza-motori" set "OPZ=--senza-motori"
if /i "%MODO%"=="simulazione" (
    set "OPZ=--simula"
    set "CARTELLA=C:\MasterflexD\ponte-prova"
)
if /i "%MODO%"=="avvia" (
    echo.
    echo  ATTENZIONE - problema aperto al 26/10/2026: all'avvio i motori delle
    echo  guide sono andati a fine corsa continuando a spingere. Prima della
    echo  prova con i motori attivi: percorso pellicola VUOTO e mano
    echo  sull'interruttore. Vedi documentazione\stato.md.
    echo.
    if not "%~2"=="si" (
        set "OK="
        set /p "OK=  Scrivere SI per avviare con i motori attivi: "
        if /i not "!OK!"=="SI" exit /b 1
    )
)
if not exist "%CARTELLA%" mkdir "%CARTELLA%" 2>nul
"%PY%" manutenzione.py modo %MODO% >nul 2>&1

"%PY%" -c "import manutenzione as m,sys;sys.exit(0 if m.agente_attivo(r'%CARTELLA%') else 1)" >nul 2>&1
if not errorlevel 1 goto :avvia_agente_presente
if /i "%MODO%"=="simulazione" goto :avvia_agente
REM  Il programma originale si apre da solo e tiene scheda e COM5: si chiude
REM  tutto quello che parte da C:\MasterflexD, PRIMA dell'agente. Dopo no:
REM  l'SDK dell'agente usa a sua volta il GateControl di quella cartella.
echo  Chiusura dei programmi del software originale ^(C:\MasterflexD^)...
set "ATTESA_CHIUSURA=--quiete 3 --massimo 30"
if defined AUTO set "ATTESA_CHIUSURA=--quiete 15 --massimo 120"
"%PY%" manutenzione.py termina !ATTESA_CHIUSURA!
:avvia_agente
echo  Avvio dell'agente scanner ^(%MODO%^)...
start "Agente scanner" cmd /k call "%PY%" ponte\agente.py --condivisa "%CARTELLA%" --scanner "%SCANNER%" --apertura %APERTURA% %OPZ%
goto :avvia_web
:avvia_agente_presente
echo  L'agente e' gia' in esecuzione: uso quello.
:avvia_web
call :firewall silenzioso
echo  Avvio dell'interfaccia web: si connette da sola appena lo scanner e' pronto...
"%PY%" run.py --rete --porta %PORTA% --driver %DRIVER% --ponte "%CARTELLA%" --apri --connetti
echo.
echo  Interfaccia chiusa. L'agente resta aperto nella sua finestra:
echo  chiuderla per fermare anche quello.
exit /b 0


REM ===========================================================================
:collaudo_scanner
REM ===========================================================================
echo.
echo  COLLAUDO DELLO SCANNER - serve l'agente in esecuzione
echo.
echo    1  Colori e intensita': R, G, B a esposizioni diverse (nessun motore)
echo    2  Trascinamento A MANO: si sposta la pellicola con le mani e si
echo       guarda quali sensori cambiano (nessun motore)
echo    3  Trascinamento COMANDATO a piccoli passi, con lo spostamento misurato
echo       sull'immagine (muove il trasporto)
echo    4  Prova guidata dei motori, con l'agente in questa finestra
echo    5  Strumenti originali del carrello ^(COM5^): selftest di servizio,
echo       SensorTest, Compad ^(guide, flag, versione^)
echo    6  Sonde dell'SDK: codice DX, acquisizione "capture" con e senza
echo       Digital ICE, Digital ICE diretto ^(muove la pellicola^)
echo    0  Indietro
echo.
set "C="
set /p "C=  Scelta: "
if "%C%"=="0" exit /b 0
if "%C%"=="4" (
    "%PY%" -c "import manutenzione as m,sys;sys.exit(0 if m.agente_attivo(r'%PONTE%') else 1)" >nul 2>&1
    if not errorlevel 1 (
        echo  Chiudere prima la finestra dell'agente: la scheda puo' averla un solo programma.
        exit /b 1
    )
    "%PY%" ponte\agente.py --condivisa "%PONTE%" --scanner "%SCANNER%" --apertura %APERTURA% --prova-motori
    exit /b !errorlevel!
)
if "%C%"=="5" goto :strumenti_carrello
set "CS="
if "%C%"=="1" set "CS=colori"
if "%C%"=="2" set "CS=sensori"
if "%C%"=="3" set "CS=passi"
if "%C%"=="6" set "CS=sdk"
if not defined CS exit /b 0
set "DOVE=%PONTE%"
set "AG="
set /p "AG=  Agente sulla macchina vera (V) o in simulazione (S)? [V] "
if /i "%AG%"=="S" set "DOVE=C:\MasterflexD\ponte-prova"
"%PY%" collaudo_scanner.py %CS% --ponte "%DOVE%"
exit /b !errorlevel!


REM ===========================================================================
:strumenti_carrello
REM  I programmi Gretag del pacchetto CapriFirmware. Parlano al carrello sulla
REM  COM5, che puo' avere un solo programma: agente e programma originale
REM  vanno chiusi prima.
REM ===========================================================================
set "CAPRI="
for /f "usebackq delims=" %%c in (`call "%PY%" -c "import manutenzione as m;print(m.trova_capri() or '')"`) do set "CAPRI=%%c"
if not defined CAPRI (
    echo  Cartella CapriFirmware non trovata: copiarla in C:\MasterflexD\CapriFirmware
    exit /b 1
)
"%PY%" -c "import manutenzione as m,sys;sys.exit(0 if m.agente_attivo(r'%PONTE%') else 1)" >nul 2>&1
if not errorlevel 1 (
    echo  Chiudere prima la finestra dell'agente: la COM5 puo' averla un solo programma.
    exit /b 1
)
REM  Nel pacchetto SelfTestInterface e' impostato sulla COM1: lo porta sulla COM5.
"%PY%" manutenzione.py porta-carrello
echo.
echo  Chiudere anche il programma originale. Percorso pellicola VUOTO.
echo.
echo    1  SelfTestInterface: tasto SelfTest, poi Yes = conferma, No = voce
echo       successiva. "Automatic test (Service)" prova HOME e PRECISION di
echo       guide, CCD, lente e tamburo, e i sensori. Poi voce 6 "Report".
echo    2  SensorTest: le fotocellule del carrello ^(rosso = coperta^)
echo    3  Compad: comandi singoli. CP_Calibrate_Size.ini = guide sinistra e
echo       destra ^(posizione, 5 o 50 passi^); CP_Change_Flags2.ini = Get Flags;
echo       CP_FirmwareDownload.ini = Get Version
echo    0  Indietro
set "T="
set /p "T=  Scelta: "
if "%T%"=="1" start "" /d "%CAPRI%\SelfTestInterface" "%CAPRI%\SelfTestInterface\SelfTestInterface.exe"
if "%T%"=="2" start "" /d "%CAPRI%\SensorTest" "%CAPRI%\SensorTest\SensorTest_COM5.exe"
if "%T%"=="3" start "" /d "%CAPRI%\Compad2_5" "%CAPRI%\Compad2_5\Compad2_5.exe"
exit /b 0


REM ===========================================================================
:osservatore
REM  Scansiona il programma originale; ponte\osservatore.py copia le immagini
REM  grezze che riceve, e l'interfaccia (collegamento "programma originale")
REM  le elabora. L'agente NON deve girare: la scheda la usa PhxScanner.
REM  Qui non si chiude niente: i programmi originali servono tutti.
REM ===========================================================================
"%PY%" -c "import manutenzione as m,sys;sys.exit(0 if m.agente_attivo(r'%PONTE%') else 1)" >nul 2>&1
if not errorlevel 1 (
    echo  Chiudere prima la finestra dell'agente: con il programma originale
    echo  la scheda e lo scanner li usa lui.
    exit /b 1
)
"%PY%" manutenzione.py modo osservatore >nul 2>&1
set "ATTESE=0"
:osservatore_attesa
tasklist /FI "IMAGENAME eq PhxScanner.exe" /NH 2>nul | find /i "PhxScanner.exe" >nul
REM  All'accensione il programma originale si apre da solo: lo si aspetta,
REM  senza domande, fino a 10 minuti.
if errorlevel 1 if defined AUTO (
    set /a ATTESE+=1
    if !ATTESE! GTR 120 (
        echo  PhxScanner.exe non si e' aperto in 10 minuti: avviare il
        echo  programma originale e poi MASTERFLEX.bat, voce 14.
        exit /b 1
    )
    if !ATTESE!==1 echo  Attendo che il programma originale apra PhxScanner.exe...
    timeout /t 5 /nobreak >nul
    goto :osservatore_attesa
)
if errorlevel 1 (
    echo  PhxScanner.exe non e' in esecuzione: avviare il programma originale
    echo  ^(voce 10^) e aspettare che sia pronto, SENZA aprire ancora un ordine.
    set "OK="
    set /p "OK=  Invio quando e' aperto, 0 per tornare al menu: "
    if "!OK!"=="0" exit /b 1
    goto :osservatore_attesa
)
echo  Avvio dell'osservatore nella sua finestra...
start "Osservatore programma originale" cmd /k call "%PY%" ponte\osservatore.py --condivisa "%PONTE%"
call :firewall silenzioso
echo  Avvio dell'interfaccia web ^(collegamento: programma originale^)...
echo  Nell'interfaccia: Connetti, scegliere ordine e rullo, Scansiona rullo;
echo  poi scansionare dal programma originale. STOP per chiudere il rullo.
"%PY%" run.py --rete --porta %PORTA% --driver originale --ponte "%PONTE%" --apri --connetti
exit /b 0


REM ===========================================================================
:automatico
REM  Lo lancia all'accensione l'Utilita' di pianificazione, gia' da
REM  amministratore e senza richieste di Windows. Parte con l'ultimo modo
REM  usato e non fa domande: davanti alla macchina, a quell'ora, non c'e'
REM  nessuno.
REM ===========================================================================
set "AUTO=1"
title Masterflex D1008 - avvio automatico
echo.
echo  Avvio automatico, modo: !MODO_ULTIMO!
if /i "!MODO_ULTIMO!"=="osservatore" goto :osservatore
call :avvia !MODO_ULTIMO! si
exit /b !errorlevel!


REM ===========================================================================
:firmware
REM  Il carrello deve avere la 1.15. Il 2 agosto 2025 il software originale
REM  l'ha riportato da solo alla 1.13 (Capri1FirmwareVersion.ini). Mostra
REM  anche flag e posizioni dall'ultimo backup del Parameter Flash.
REM ===========================================================================
"%PY%" manutenzione.py firmware
if not errorlevel 1 exit /b 0
echo.
echo  Se si hanno i file del firmware 1.15 ^(master e slave .a37^), indicare
echo  la cartella e verranno messi al posto della 1.13. Invio per saltare.
set "F115="
set /p "F115=  Cartella con la 1.15: "
if defined F115 "%PY%" manutenzione.py firmware --usa-1.15 "%F115%"
exit /b 0


REM ===========================================================================
:collaudo
REM ===========================================================================
"%PY%" manutenzione.py verifica
"%PY%" run.py --controllo
echo.
"%PY%" selftest.py
exit /b !errorlevel!


REM ===========================================================================
:diagnosi
REM ===========================================================================
"%PY%" manutenzione.py diagnosi
REM la cartella dei registri, con lo zip da mandare in da-mandare
if exist "%~dp0log" start "" explorer "%~dp0log\da-mandare"
exit /b !errorlevel!


REM ===========================================================================
:installa
REM  Python 3.8.10 a 32 bit se manca (scaricato e controllato), librerie alle
REM  versioni giuste, cartelle, firewall, controllo di driver e SDK.
REM ===========================================================================
echo.
echo  [1/5] Python 3.8 a 32 bit
if defined PY (
    echo        presente: !PY!
) else (
    call :installa_python
    call :trova_python
    if not defined PY (
        echo.
        echo  Python non installato. Si puo' installare a mano:
        echo    %PY_URL%
        echo  spuntando "Add Python 3.8 to PATH" e "tcl/tk and IDLE",
        echo  poi rilanciare questa voce.
        exit /b 1
    )
)
echo.
echo  [2/5] Librerie ^(versioni per CPU senza AVX: numpy 1.22.4^)
"%PY%" manutenzione.py librerie || exit /b 1
"%PY%" manutenzione.py verifica
echo.
echo  [3/5] Cartelle
if not exist "%PONTE%" mkdir "%PONTE%" 2>nul
if exist "%PONTE%" (echo        %PONTE%) else (echo        [ERRORE] %PONTE% non creata: serve "Esegui come amministratore")
"%PY%" manutenzione.py pulisci-vecchio
echo.
echo  [4/5] Firewall ^(porta %PORTA% per il tablet^)
call :firewall
echo.
echo  [5/5] Driver, scheda, SDK e firmware del carrello
"%PY%" manutenzione.py macchina
"%PY%" manutenzione.py firmware
echo.
echo  Fatto. La prima volta: voce 3 ^(simulazione^), e collegarsi dal tablet.
echo.
set "AA="
set /p "AA=  Attivare l'avvio automatico all'accensione? [s/N] "
if /i "!AA!"=="s" call :avvio_automatico
exit /b 0


:installa_python
set "PYEXE=%~dp0pacchetti\python-3.8.10.exe"
if not exist "%PYEXE%" (
    set "PYEXE=%TEMP%\python-3.8.10.exe"
    echo        scarico Python 3.8.10 ^(27 MB^)...
    call :scarica "%PY_URL%" "!PYEXE!"
)
if not exist "%PYEXE%" (
    echo        [ERRORE] download non riuscito ^(su Windows 7 capita: TLS 1.2^).
    echo        Preparare il pacchetto offline su un altro PC ^(voce 11^) oppure
    echo        copiare python-3.8.10.exe nella cartella pacchetti.
    exit /b 1
)
call :sha256 "%PYEXE%"
if /i not "!HASH!"=="%PY_SHA256%" (
    echo        [ERRORE] il file scaricato non e' l'installer atteso.
    echo        atteso:  %PY_SHA256%
    echo        trovato: !HASH!
    del "%PYEXE%" 2>nul
    exit /b 1
)
echo        impronta verificata. Installo per questo utente...
start "" /wait "%PYEXE%" /quiet InstallAllUsers=0 PrependPath=1 Include_tcltk=1 Include_test=0 Include_doc=0 Include_launcher=1 InstallLauncherAllUsers=0 Shortcuts=0
if errorlevel 1 (
    echo        [ERRORE] installazione di Python fallita ^(codice !errorlevel!^).
    echo        Su Windows 7 servono SP1 e l'aggiornamento KB2533623.
    exit /b 1
)
exit /b 0


:scarica
REM  Tre strade, perche' su Windows 7 nessuna funziona sempre: certutil usa le
REM  impostazioni di Internet Explorer (con IE11 il TLS 1.2 c'e'), bitsadmin il
REM  servizio BITS, PowerShell il .NET installato.
del "%~2" 2>nul
certutil -urlcache -split -f "%~1" "%~2" >nul 2>&1
if exist "%~2" exit /b 0
bitsadmin /transfer masterflex /download /priority foreground "%~1" "%~2" >nul 2>&1
if exist "%~2" exit /b 0
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=3072; (New-Object Net.WebClient).DownloadFile('%~1','%~2')" >nul 2>&1
exit /b 0


:sha256
REM  certutil su Windows 7 stampa l'impronta a coppie separate da spazi.
set "HASH="
for /f "skip=1 delims=" %%h in ('certutil -hashfile "%~1" SHA256') do (
    if not defined HASH set "HASH=%%h"
)
if defined HASH set "HASH=!HASH: =!"
exit /b 0


REM ===========================================================================
:aggiorna
REM ===========================================================================
echo.
echo  Da dove prendo la versione nuova?
echo    Invio  = scarica l'ultima da GitHub ^(se il repository e' pubblico^)
echo    oppure = percorso di uno zip scaricato da GitHub o di una cartella
echo.
set "SORG="
set /p "SORG=  Sorgente: "
"%PY%" manutenzione.py aggiorna "!SORG!" || exit /b 1
"%PY%" manutenzione.py librerie
REM  Questo file non si puo' sovrascrivere mentre gira: manutenzione.py ha
REM  scritto la versione nuova accanto. Il blocco fra parentesi viene letto
REM  tutto prima di eseguirlo, quindi lo scambio e l'uscita sono sicuri.
if exist "%~dp0MASTERFLEX.bat.nuovo" (
    move /y "%~dp0MASTERFLEX.bat.nuovo" "%~dp0MASTERFLEX.bat" >nul
    echo.
    echo  Aggiornato. Rilanciare MASTERFLEX.bat.
    pause
    exit
)
exit /b 0


REM ===========================================================================
:avvio_automatico
REM ===========================================================================
REM  All'accensione parte da amministratore senza richieste: lo fa
REM  un'attivita' dell'Utilita' di pianificazione. Crearla chiede
REM  l'amministratore una volta, adesso. Parte sempre con l'ultimo modo usato:
REM  qui si sceglie quello di partenza.
"%PY%" manutenzione.py avvio stato
echo.
echo    1  attiva - completo, motori BLOCCATI ^(consigliato finche' il
echo       problema delle guide non e' chiuso^)
echo    2  attiva - completo, motori attivi
echo    3  attiva - man in the middle ^(programma originale + osservatore^)
echo    4  attiva - simulazione
echo    5  prova adesso, come all'accensione ^(senza riavviare^)
echo    6  disattiva
echo    0  indietro
set "A="
set /p "A=  Scelta: "
set "M="
if "%A%"=="1" set "M=avvia-senza-motori"
if "%A%"=="2" set "M=avvia"
if "%A%"=="3" set "M=osservatore"
if "%A%"=="4" set "M=simulazione"
if defined M (
    "%PY%" manutenzione.py modo !M!
    "%PY%" manutenzione.py avvio on
)
if "%A%"=="5" "%PY%" manutenzione.py avvio adesso
if "%A%"=="6" "%PY%" manutenzione.py avvio off
exit /b 0


REM ===========================================================================
:originale
REM  Spegne o riaccende stampa e densitometro nel programma originale. Non
REM  tocca eseguibili: cambia due voci di configurazione e tiene una copia
REM  .prima-di-noi di ogni file.
REM ===========================================================================
"%PY%" -m filmscan.originale --stato
echo.
echo    1  spegni stampa e densitometro
echo    2  riaccendili
echo    0  indietro
set "O="
set /p "O=  Scelta: "
if "%O%"=="1" "%PY%" -m filmscan.originale --alleggerisci
if "%O%"=="2" "%PY%" -m filmscan.originale --ripristina
echo.
echo  ATTENZIONE: il programma originale e filmscan non vanno usati insieme.
exit /b 0


REM ===========================================================================
:offline
REM  Funziona da qualunque PC con Python e internet, anche moderno: le wheel
REM  si chiedono per Windows 32 bit e Python 3.8.
REM ===========================================================================
set "PYO=%PY%"
if not defined PYO (
    where python >nul 2>&1 && set "PYO=python"
)
if not defined PYO (
    echo  Serve un Python qualunque su questo PC per preparare il pacchetto.
    exit /b 1
)
"%PYO%" manutenzione.py offline
exit /b !errorlevel!


REM ===========================================================================
:disinstalla
REM  Toglie quello che il programma ha messo fuori dalla sua cartella. La
REM  cartella, gli ordini e le scansioni restano: si cancellano a mano.
REM ===========================================================================
"%PY%" manutenzione.py avvio off
del "%USERPROFILE%\Desktop\Interfaccia scansione.url" 2>nul
netsh advfirewall firewall delete rule name="filmscan %PORTA%" >nul 2>&1
if errorlevel 1 (echo  Regola firewall: non tolta ^(serve "Esegui come amministratore"^)) else (echo  Regola firewall tolta.)
echo  Fatto. La cartella del programma e i dati sono rimasti dove sono.
exit /b 0


REM ===========================================================================
:firewall
REM  Senza questa regola il tablet non si collega. Serve l'amministratore.
REM ===========================================================================
netsh advfirewall firewall show rule name="filmscan %PORTA%" >nul 2>&1
if not errorlevel 1 (
    if not "%~1"=="silenzioso" echo        regola gia' presente
    exit /b 0
)
netsh advfirewall firewall add rule name="filmscan %PORTA%" dir=in action=allow protocol=TCP localport=%PORTA% profile=private >nul 2>&1
if errorlevel 1 (
    echo        [nota] regola firewall non creata: serve "Esegui come amministratore".
    echo               Se il tablet non si collega, e' quasi sempre questo.
) else (
    echo        porta %PORTA% aperta sulle reti private
)
exit /b 0


REM ===========================================================================
:trova_python
REM  Cerca Python 3.8 a 32 bit: prima dove lo mette l'installer, poi il
REM  launcher py, poi il PATH. Lascia il percorso completo in PY.
REM ===========================================================================
set "PY="
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python38-32\python.exe"
    "%ProgramFiles%\Python38-32\python.exe"
    "%ProgramFiles(x86)%\Python38-32\python.exe"
    "C:\Python38-32\python.exe"
    "C:\Python38\python.exe"
) do (
    if not defined PY if exist "%%~P" call :prova_python "%%~P"
)
if not defined PY (
    for /f "delims=" %%P in ('py -3.8-32 -c "import sys;print(sys.executable)" 2^>nul') do call :prova_python "%%P"
)
if not defined PY (
    for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)" 2^>nul') do call :prova_python "%%P"
)
exit /b 0

:prova_python
"%~1" -c "import sys,struct;sys.exit(0 if sys.version_info[:2]==(3,8) and struct.calcsize('P')==4 else 1)" >nul 2>&1
if not errorlevel 1 set "PY=%~1"
exit /b 0
