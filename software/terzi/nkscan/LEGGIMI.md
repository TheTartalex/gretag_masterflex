# nkscan (terze parti)

`filmscan/ice_aperto.py` è la traduzione in Python e numpy della pulizia
della polvere con l'infrarosso di
[nkscan](https://github.com/activexray/nkscan), file `src/dust.rs`
(novembre 2026). nkscan è distribuito sotto licenza MIT oppure Apache-2.0, a
scelta: qui si usa la MIT, il cui testo è in `LICENSE-MIT`.

Il motore "aperto" del Digital ICE è la traduzione senza aggiunte; il motore
"filmscan" la usa con le nostre migliorie (allineamento dell'IR, zone dense).
Vedi la [decisione 0012](../../../documentazione/decisioni/0012-digital-ice-aperto.md).
