# Decisioni

Ogni scelta che orienta il progetto ha qui una scheda breve: il contesto, la
decisione, le conseguenze. Serve a non ridiscutere da capo una cosa già
decisa, e a capire subito cosa rimettere in discussione quando cambia un
fatto.

## Regole

- Un file per decisione: `NNNN-titolo-breve.md`, numerazione progressiva,
  mai riusata.
- Una decisione non si cancella e non si riscrive. Se cambia, se ne scrive
  una nuova e quella vecchia passa a **Superata da NNNN**.
- Stati possibili: **Proposta**, **Accettata**, **Superata da NNNN**,
  **Abbandonata**.

## Modello

```markdown
# NNNN — Titolo

- Data: AAAA-MM-GG
- Stato: Accettata

## Contesto
Quali fatti hanno portato alla scelta, con i riferimenti.

## Decisione
Cosa si fa, in una o due frasi.

## Conseguenze
Cosa diventa più facile, cosa più difficile, cosa resta da verificare.
```

## Elenco

| # | Decisione | Data | Stato |
|---|---|---|---|
| [0001](0001-ponte-sdk-originale.md) | Comandare la macchina attraverso l'SDK originale, con un agente separato | 2026-09-03 | Accettata |
| [0002](0002-driver-pnp-sostitutivo.md) | Scrivere un driver PnP sostitutivo di `EKframe.sys` | 2026-10-25 | Accettata |
| [0003](0003-numpy-1.22.4-cpu-senza-avx.md) | numpy 1.22.4 sulle macchine senza AVX | 2026-10-25 | Accettata |
| [0004](0004-compilazione-mingw-linux.md) | Compilare il driver su Linux con MinGW-w64 | 2026-10-25 | Accettata |
| [0005](0005-masterflex-bat-unico.md) | Un solo script, MASTERFLEX.bat, per tutto quello che si fa sulla macchina | 2026-10-26 | Accettata |
| [0006](0006-firmware-carrello-1.15.md) | Il carrello Capri va tenuto al firmware 1.15 | 2026-10-26 | Accettata, confermata il 28/10 (risolve le guide) |
| [0007](0007-osservatore-programma-originale.md) | Immagini dal programma originale con un osservatore esterno | 2026-10-26 | Proposta |
| [0008](0008-avvio-automatico-e-modi.md) | Avvio da amministratore, chiusura del programma originale, due modi di lavoro | 2026-10-26 | Proposta |
