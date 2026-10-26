# Strumenti

Script di servizio che non appartengono né al driver né a filmscan. Una
sottocartella per strumento, ognuna con il suo `LEGGIMI.txt` per chi lo lancia
sulla macchina.

Al momento è vuota. I due strumenti previsti sono entrati in
`software/MASTERFLEX.bat` il 26/10/2026
([decisione 0005](../documentazione/decisioni/0005-masterflex-bat-unico.md)):

| Era | Ora |
|---|---|
| `diagnostica/raccogli-diagnosi.bat` | voce 6, *Diagnosi* (`manutenzione.py diagnosi`) |
| `numpy/correggi-numpy/` | voce 7, *Installa / ripara*: sostituisce numpy 1.24.4 con la 1.22.4 |

Gli strumenti originali Gretag del carrello (Compad, SensorTest,
SelfTestInterface) restano nel pacchetto proprietario `CapriFirmware`, fuori
dal repository: `MASTERFLEX.bat` li lancia dalla voce 4 → 5.

L'output delle diagnosi non va nel repository: contiene percorsi e nomi
delle macchine. Va allegato alla sessione o al problema su GitHub.
