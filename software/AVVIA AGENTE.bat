@echo off
REM  Avvia solo l'agente dello scanner (motori attivi), se non gira gia'.
REM  Per i collaudi, o se la finestra "Agente scanner" si e' chiusa.
cd /d "%~dp0"
call MASTERFLEX.bat agente
timeout /t 8 >nul
