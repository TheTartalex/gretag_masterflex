# Strumenti

Script di servizio per le macchine, che non appartengono né al driver né a
filmscan. Una sottocartella per strumento, ognuna con il suo `LEGGIMI.txt`
per chi lo lancia sulla macchina.

| Strumento | Cosa fa | Stato |
|---|---|---|
| `diagnostica/raccogli-diagnosi.bat` | raccoglie in un `.txt` lo stato di driver, scheda e SDK | da importare dalla cartella di lavoro locale |
| `correggi-numpy/` | sostituisce numpy 1.24.4 con la 1.22.4 sulle CPU senza AVX ([decisione 0003](../documentazione/decisioni/0003-numpy-1.22.4-cpu-senza-avx.md)) | da importare: solo gli script, **senza** le wheel |

L'output delle diagnosi non va nel repository: contiene percorsi e nomi
delle macchine. Va allegato alla sessione o al problema su GitHub.
