# 0004 — Compilare il driver su Linux con MinGW-w64

- Data: 2026-10-25
- Stato: Accettata

## Contesto

Per i driver di Windows 7 la strada ufficiale è il WDK 7.1. Sul PC di
sviluppo c'è solo il Windows Kits 10, e il WDK 7.1 non si trova facilmente.
Le sessioni di lavoro con Claude girano invece in un container Linux.

## Decisione

Il driver e `ektest` si compilano con **MinGW-w64 i686** (`build.sh`). Dopo
il link uno script Python con `pefile` imposta il flag `WDM_DRIVER` e
ricalcola il checksum PE. `sources` e `makefile` per il WDK 7.1 restano come
alternativa, ma non sono la strada provata.

## Conseguenze

- Si compila ovunque ci sia Linux, anche in CI, senza licenze.
- Il binario usa solo istruzioni Pentium di base (`-march=i586 -mno-sse
  -mno-mmx`) e solo API del kernel NT presenti in Windows 7. Da ricontrollare
  a ogni cambio di compilatore.
- Dal 26/10/2026 la compilazione è riproducibile (`--no-insert-timestamp`).
  `confronta.py` controlla che il pacchetto venga dai sorgenti.
