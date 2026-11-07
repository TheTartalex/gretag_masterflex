@echo off
REM Trascinare qui sopra uno o piu' grezzi .npz: accanto a ciascuno
REM escono i TIFF a 16 bit del negativo (e dell'infrarosso), da aprire
REM con Photoshop, GIMP o altri. Vedi grezzo_in_tiff.py.
call "%~dp0MASTERFLEX.bat" tiff %*
