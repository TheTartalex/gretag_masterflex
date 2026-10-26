# Verifica di equivalenza con l'originale

Qui si controlla che la traduzione in C faccia **esattamente** quello che
faceva `EKframe.sys`. Il codice macchina originale gira in un emulatore x86
(unicorn). Accanto gira il codice C di questo driver, compilato per Linux. I
due sono collegati allo **stesso** modello simulato dell'hardware e si
confrontano accesso per accesso: lettura o scrittura, registro, valore, attese.

| Script | Cosa confronta | Esito della 2.1 |
|---|---|---|
| `equivalenza_arcnet.py` | inizializzazione COM20020, test RAM, invio e ricezione pacchetti, parola di stato | 105/105 identici |
| `equivalenza_dma.py` | catene di descrittori dopo l'aggancio, troncamento, sequenza di avvio su PLX e FPGA | 7/7 (le differenze attese sono i difetti corretti dell'originale) |

## Cosa serve

- Python 3 con `pefile` e `unicorn` (`pip install pefile unicorn`).
- **Il driver originale** `EKframe.sys` (Eastman Kodak, 2000, 17.079 byte).
  È proprietario e **non sta nel repository**. Va copiato in
  `verifica/originale/EKframe.sys` (cartella esclusa da git), oppure indicato
  con la variabile `EKFRAME_ORIGINALE`.
- Le librerie `libarc.so` e `libdma.so`: il codice di `arcnet.c` e di
  `dma.c`/`hw.c` compilato per Linux con un banco di prova che sostituisce le
  funzioni del kernel (`set_cb`, `tb_setup`, `tb_arm`, `tb_start`, ...).

## Da completare

Il banco di prova in C e lo script che lo compila e lancia tutto (nella
cartella locale si chiamava `verifica-equivalenza/esegui.sh`) **non sono
ancora nel repository**. Senza di loro gli script qui non partono. Quando
arrivano vanno messi in questa cartella e `esegui.sh` va adattato ai percorsi
del repository. Il comando atteso è:

```sh
sh esegui.sh        # deve dare 105/105 e 7/7
```

Va rilanciato a ogni modifica di `arcnet.c`, `dma.c` o `hw.c`.
