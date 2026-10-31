# -*- coding: utf-8 -*-
"""
Gestione ordini per cliente.

SQLite dalla libreria standard: nessun server, nessuna dipendenza, un file
solo da copiare per il backup. Su una macchina di laboratorio e' esattamente
quello che serve.

Modello dei dati, volutamente minimo:

    cliente  ->  ordine  ->  rullo  ->  fotogramma

Un cliente porta uno o piu' ordini; un ordine contiene uno o piu' rulli; ogni
rullo ha i suoi fotogrammi. Le impostazioni di scansione stanno sul rullo, non
sull'ordine, perche' e' il rullo ad avere un formato e una pellicola.

I file NON vengono messi nel database: si salvano i percorsi. Un database che
contiene immagini diventa ingestibile e impossibile da riparare, e comunque i
file servono anche fuori dal programma.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS clienti (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nome        TEXT NOT NULL,
    telefono    TEXT DEFAULT '',
    email       TEXT DEFAULT '',
    note        TEXT DEFAULT '',
    creato      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clienti_nome ON clienti(nome);

CREATE TABLE IF NOT EXISTS ordini (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id  INTEGER NOT NULL REFERENCES clienti(id) ON DELETE CASCADE,
    codice      TEXT UNIQUE NOT NULL,
    stato       TEXT NOT NULL DEFAULT 'aperto',
    note        TEXT DEFAULT '',
    creato      REAL NOT NULL,
    chiuso      REAL
);
CREATE INDEX IF NOT EXISTS idx_ordini_cliente ON ordini(cliente_id);
CREATE INDEX IF NOT EXISTS idx_ordini_stato ON ordini(stato);

CREATE TABLE IF NOT EXISTS rulli (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ordine_id   INTEGER NOT NULL REFERENCES ordini(id) ON DELETE CASCADE,
    etichetta   TEXT NOT NULL,
    formato     TEXT NOT NULL,
    preset      TEXT NOT NULL,
    profilo     TEXT NOT NULL DEFAULT 'c41',
    specchiato  INTEGER NOT NULL DEFAULT 0,
    conf_specchio INTEGER NOT NULL DEFAULT 0,
    attesi      INTEGER NOT NULL DEFAULT 36,
    stato       TEXT NOT NULL DEFAULT 'da_scansionare',
    creato      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rulli_ordine ON rulli(ordine_id);

CREATE TABLE IF NOT EXISTS fotogrammi (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rullo_id    INTEGER NOT NULL REFERENCES rulli(id) ON DELETE CASCADE,
    numero      INTEGER NOT NULL,
    file_raw    TEXT DEFAULT '',
    file_out    TEXT DEFAULT '',
    file_maschera TEXT DEFAULT '',
    rotazione   INTEGER NOT NULL DEFAULT 0,
    stato       TEXT NOT NULL DEFAULT 'ok',
    avvisi      TEXT DEFAULT '',
    creato      REAL NOT NULL,
    UNIQUE(rullo_id, numero)
);
CREATE INDEX IF NOT EXISTS idx_fotogrammi_rullo ON fotogrammi(rullo_id);
"""

STATI_ORDINE = ("aperto", "in_lavorazione", "completato", "consegnato", "annullato")
STATI_RULLO = ("da_scansionare", "in_corso", "completato", "problema")


# --------------------------------------------------------------------------

@dataclass
class Cliente:
    id: int
    nome: str
    telefono: str = ""
    email: str = ""
    note: str = ""
    creato: float = 0.0


@dataclass
class Ordine:
    id: int
    cliente_id: int
    codice: str
    stato: str = "aperto"
    note: str = ""
    creato: float = 0.0
    chiuso: Optional[float] = None
    cliente_nome: str = ""
    rulli: int = 0
    fotogrammi: int = 0


@dataclass
class Rullo:
    id: int
    ordine_id: int
    etichetta: str
    formato: str
    preset: str
    profilo: str = "c41"
    specchiato: bool = False
    conf_specchio: bool = False
    attesi: int = 0        # 0 = automatico: si conta scansionando
    stato: str = "da_scansionare"
    creato: float = 0.0
    scansionati: int = 0
    # Digital ICE: pulizia a infrarosso, scelta rullo per rullo
    ice: bool = False
    # il codice DX letto dalla macchina ("DX 115-3"), vuoto se non letto
    dx: str = ""
    # sviluppo in stop: 0 normale, +1/+2/+3 forzato, -1 tirato
    sviluppo: int = 0


# --------------------------------------------------------------------------

class Archivio:
    """Accesso al database degli ordini.

    Thread-safe per l'uso previsto: l'interfaccia web serve piu' richieste, e
    ognuna apre e chiude la propria connessione. SQLite gestisce il resto.
    Niente connessioni condivise fra thread: e' la strada piu' breve per un
    'database is locked' a meta' di un rullo.
    """

    def __init__(self, path: str = "./ordini.db"):
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            # Colonne aggiunte dopo: un archivio gia' esistente le riceve qui,
            # con il valore di serie, senza toccare il resto.
            for tabella, colonna, tipo in (
                    ("rulli", "ice", "INTEGER NOT NULL DEFAULT 0"),
                    ("rulli", "dx", "TEXT DEFAULT ''"),
                    ("rulli", "sviluppo", "INTEGER NOT NULL DEFAULT 0"),
                    ("fotogrammi", "numero_bordo", "TEXT DEFAULT ''"),
                    # 1 = da stampare, -1 = scartato, 0 = niente
                    ("fotogrammi", "voto", "INTEGER NOT NULL DEFAULT 0"),
                    # il negativo lineare per altri programmi (TIFF o DNG)
                    ("fotogrammi", "file_negativo", "TEXT DEFAULT ''")):
                presenti = [r[1] for r in c.execute(
                    "PRAGMA table_info(%s)" % tabella).fetchall()]
                if colonna not in presenti:
                    c.execute("ALTER TABLE %s ADD COLUMN %s %s"
                              % (tabella, colonna, tipo))

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # --- clienti ---

    def cliente_crea(self, nome: str, telefono: str = "", email: str = "",
                     note: str = "") -> int:
        nome = nome.strip()
        if not nome:
            raise ValueError("il nome del cliente non puo' essere vuoto")
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO clienti (nome, telefono, email, note, creato) "
                "VALUES (?,?,?,?,?)", (nome, telefono, email, note, time.time()))
            return int(cur.lastrowid)

    def cliente_trova(self, testo: str = "", limite: int = 50) -> List[Cliente]:
        """Ricerca per nome, parziale e senza distinzione di maiuscole."""
        with self._conn() as c:
            if testo.strip():
                rows = c.execute(
                    "SELECT * FROM clienti WHERE nome LIKE ? "
                    "ORDER BY nome LIMIT ?",
                    ("%" + testo.strip() + "%", limite)).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM clienti ORDER BY creato DESC LIMIT ?",
                    (limite,)).fetchall()
        return [Cliente(**dict(r)) for r in rows]

    def cliente_o_crea(self, nome: str) -> int:
        """Ritorna l'id del cliente con quel nome esatto, creandolo se manca.

        Comodo al banco: si digita il nome e si va avanti. La corrispondenza e'
        sul nome esatto, non parziale: 'Rossi' e 'Rossi Mario' restano due
        clienti distinti, che e' quasi sempre quello che si vuole.
        """
        nome = nome.strip()
        with self._conn() as c:
            row = c.execute("SELECT id FROM clienti WHERE nome = ?", (nome,)).fetchone()
            if row:
                return int(row["id"])
        return self.cliente_crea(nome)

    def cliente_elimina(self, cliente_id: int) -> int:
        """Elimina un cliente e, a cascata, i suoi ordini, rulli e fotogrammi.

        Ritorna quanti ordini se ne sono andati con lui: e' il numero da
        mostrare PRIMA di chiedere conferma, perche' "elimina cliente" sembra
        una riga sola e invece puo' essere il lavoro di un anno.

        I file su disco restano, come sempre.
        """
        with self._conn() as c:
            n = c.execute("SELECT COUNT(*) FROM ordini WHERE cliente_id = ?",
                          (int(cliente_id),)).fetchone()[0]
            c.execute("DELETE FROM clienti WHERE id = ?", (int(cliente_id),))
        return int(n)

    # --- ordini ---

    def cliente(self, cliente_id: int) -> Optional[Cliente]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM clienti WHERE id = ?", (cliente_id,)).fetchone()
        return Cliente(**dict(r)) if r else None

    def cliente_rinomina(self, cliente_id: int, nome: str = "",
                         telefono: Optional[str] = None,
                         email: Optional[str] = None,
                         note: Optional[str] = None) -> None:
        """Cambia i dati di un cliente. Vale per tutti i suoi ordini.

        Il nome di un cliente si sbaglia a scriverlo piu' spesso di quanto si
        creda - specie sotto dettatura al banco - e correggerlo non deve voler
        dire rifare l'ordine.
        """
        campi, valori = [], []
        for nome_campo, valore in (("nome", nome or None), ("telefono", telefono),
                                   ("email", email), ("note", note)):
            if valore is not None:
                campi.append("%s = ?" % nome_campo)
                valori.append(valore)
        if not campi:
            return
        valori.append(int(cliente_id))
        with self._conn() as c:
            c.execute("UPDATE clienti SET %s WHERE id = ?" % ", ".join(campi), valori)

    def ordine_rinomina(self, ordine_id: int, cliente: str = "",
                        note: Optional[str] = None) -> None:
        """Sposta l'ordine su un altro cliente (creandolo se serve) e/o
        cambia la sua nota.

        Attenzione alla differenza: qui si cambia A CHI e' intestato l'ordine.
        Per correggere il nome di un cliente che resta lo stesso si usa
        cliente_rinomina, altrimenti si finisce con due schede per la stessa
        persona.
        """
        with self._conn() as c:
            if cliente:
                cid = self.cliente_o_crea(cliente)
                c.execute("UPDATE ordini SET cliente_id = ? WHERE id = ?",
                          (cid, int(ordine_id)))
            if note is not None:
                c.execute("UPDATE ordini SET note = ? WHERE id = ?",
                          (note, int(ordine_id)))

    def _nuovo_codice(self) -> str:
        """Codice progressivo leggibile: anno piu' contatore, es. 2026-0042."""
        anno = time.strftime("%Y")
        with self._conn() as c:
            row = c.execute(
                "SELECT codice FROM ordini WHERE codice LIKE ? "
                "ORDER BY id DESC LIMIT 1", (anno + "-%",)).fetchone()
        n = 1
        if row:
            try:
                n = int(str(row["codice"]).split("-")[1]) + 1
            except (IndexError, ValueError):
                n = 1
        return "%s-%04d" % (anno, n)

    def ordine_crea(self, cliente: str, note: str = "") -> Ordine:
        cliente_id = self.cliente_o_crea(cliente)
        codice = self._nuovo_codice()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO ordini (cliente_id, codice, stato, note, creato) "
                "VALUES (?,?,?,?,?)",
                (cliente_id, codice, "aperto", note, time.time()))
            oid = int(cur.lastrowid)
        return self.ordine(oid)

    def ordine(self, ordine_id: int) -> Optional[Ordine]:
        with self._conn() as c:
            row = c.execute(
                "SELECT o.*, cl.nome AS cliente_nome, "
                "  (SELECT COUNT(*) FROM rulli r WHERE r.ordine_id = o.id) AS rulli, "
                "  (SELECT COUNT(*) FROM fotogrammi f JOIN rulli r2 ON f.rullo_id = r2.id "
                "   WHERE r2.ordine_id = o.id) AS fotogrammi "
                "FROM ordini o JOIN clienti cl ON cl.id = o.cliente_id "
                "WHERE o.id = ?", (ordine_id,)).fetchone()
        return Ordine(**dict(row)) if row else None

    def ordini(self, cliente: str = "", stato: str = "",
               limite: int = 100) -> List[Ordine]:
        sql = ("SELECT o.*, cl.nome AS cliente_nome, "
               "  (SELECT COUNT(*) FROM rulli r WHERE r.ordine_id = o.id) AS rulli, "
               "  (SELECT COUNT(*) FROM fotogrammi f JOIN rulli r2 ON f.rullo_id = r2.id "
               "   WHERE r2.ordine_id = o.id) AS fotogrammi "
               "FROM ordini o JOIN clienti cl ON cl.id = o.cliente_id WHERE 1=1")
        args: List[object] = []
        if cliente.strip():
            sql += " AND cl.nome LIKE ?"
            args.append("%" + cliente.strip() + "%")
        if stato:
            sql += " AND o.stato = ?"
            args.append(stato)
        sql += " ORDER BY o.creato DESC LIMIT ?"
        args.append(limite)
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [Ordine(**dict(r)) for r in rows]

    def ordine_stato(self, ordine_id: int, stato: str) -> None:
        if stato not in STATI_ORDINE:
            raise ValueError("stato non valido: %s" % stato)
        chiuso = time.time() if stato in ("completato", "consegnato") else None
        with self._conn() as c:
            c.execute("UPDATE ordini SET stato = ?, chiuso = ? WHERE id = ?",
                      (stato, chiuso, ordine_id))

    def ordine_elimina(self, ordine_id: int) -> None:
        """Elimina ordine, rulli e fotogrammi. I FILE SU DISCO RESTANO:
        il database tiene i percorsi, non le immagini, e cancellare il lavoro
        di un cliente per un click sbagliato non deve essere possibile."""
        with self._conn() as c:
            c.execute("DELETE FROM ordini WHERE id = ?", (ordine_id,))

    # --- rulli ---

    def rullo_crea(self, ordine_id: int, etichetta: str, formato: str,
                   preset: str, profilo: str = "c41", attesi: int = 0,
                   ice: bool = False, sviluppo: int = 0) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO rulli (ordine_id, etichetta, formato, preset, "
                "profilo, attesi, stato, creato, ice, sviluppo) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ordine_id, etichetta, formato, preset, profilo, attesi,
                 "da_scansionare", time.time(), 1 if ice else 0,
                 max(-1, min(3, int(sviluppo or 0)))))
            return int(cur.lastrowid)

    def rulli(self, ordine_id: int) -> List[Rullo]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT r.*, (SELECT COUNT(*) FROM fotogrammi f "
                "  WHERE f.rullo_id = r.id) AS scansionati "
                "FROM rulli r WHERE r.ordine_id = ? ORDER BY r.id", (ordine_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["specchiato"] = bool(d["specchiato"])
            d["conf_specchio"] = bool(d["conf_specchio"])
            d["ice"] = bool(d.get("ice", 0))
            d["dx"] = d.get("dx") or ""
            d["sviluppo"] = int(d.get("sviluppo") or 0)
            out.append(Rullo(**d))
        return out

    def rullo(self, rullo_id: int) -> Optional[Rullo]:
        with self._conn() as c:
            row = c.execute(
                "SELECT r.*, (SELECT COUNT(*) FROM fotogrammi f "
                "  WHERE f.rullo_id = r.id) AS scansionati "
                "FROM rulli r WHERE r.id = ?", (rullo_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["specchiato"] = bool(d["specchiato"])
        d["conf_specchio"] = bool(d["conf_specchio"])
        d["ice"] = bool(d.get("ice", 0))
        d["dx"] = d.get("dx") or ""
        d["sviluppo"] = int(d.get("sviluppo") or 0)
        return Rullo(**d)

    def rullo_specchiatura(self, rullo_id: int, specchiato: bool,
                           confermata: bool = True) -> None:
        """Registra il verso di caricamento della pellicola per questo rullo.

        E' una proprieta' del rullo intero: si decide una volta, guardando le
        scritte sul bordo del primo fotogramma, e vale per tutti gli altri.
        """
        with self._conn() as c:
            c.execute("UPDATE rulli SET specchiato = ?, conf_specchio = ? WHERE id = ?",
                      (1 if specchiato else 0, 1 if confermata else 0, rullo_id))

    def rullo_dx(self, rullo_id: int, dx: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE rulli SET dx = ? WHERE id = ?", (dx or "", rullo_id))

    def rullo_profilo(self, rullo_id: int, profilo: str) -> None:
        """Il profilo di inversione corretto dal codice DX."""
        with self._conn() as c:
            c.execute("UPDATE rulli SET profilo = ? WHERE id = ?", (profilo, rullo_id))

    def ordine_aggiungi_nota(self, ordine_id: int, riga: str) -> None:
        """Aggiunge una riga alla nota dell'ordine (senza ripeterla)."""
        o = self.ordine(ordine_id)
        if o is None or not riga:
            return
        nota = (o.note or "").strip()
        if riga in nota:
            return
        with self._conn() as c:
            c.execute("UPDATE ordini SET note = ? WHERE id = ?",
                      ((nota + "\n" if nota else "") + riga, int(ordine_id)))

    def rullo_stato(self, rullo_id: int, stato: str) -> None:
        if stato not in STATI_RULLO:
            raise ValueError("stato non valido: %s" % stato)
        with self._conn() as c:
            c.execute("UPDATE rulli SET stato = ? WHERE id = ?", (stato, rullo_id))

    def rullo_elimina(self, rullo_id: int) -> None:
        """Elimina un rullo e i suoi fotogrammi dal database.

        I FILE SU DISCO RESTANO. Chi vuole anche quelli passa da consegne.libera,
        che li cancella e lo dice; qui si tolgono le righe, non il lavoro.
        """
        with self._conn() as c:
            c.execute("DELETE FROM rulli WHERE id = ?", (int(rullo_id),))

    # --- fotogrammi ---

    def fotogramma_registra(self, rullo_id: int, numero: int,
                            file_out: str = "", file_raw: str = "",
                            file_maschera: str = "", rotazione: int = 0,
                            stato: str = "ok", avvisi: str = "",
                            numero_bordo: str = "",
                            file_negativo: str = "") -> int:
        """Registra un fotogramma scansionato. Se esiste gia', lo aggiorna:
        riscansionare un fotogramma venuto male e' normale e non deve creare
        doppioni. Il voto resta quello dato; il negativo lineare resta se
        questa volta non se n'e' fatto uno nuovo."""
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO fotogrammi (rullo_id, numero, file_raw, file_out, "
                "file_maschera, rotazione, stato, avvisi, creato, numero_bordo, "
                "file_negativo) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(rullo_id, numero) DO UPDATE SET "
                "  file_raw=excluded.file_raw, file_out=excluded.file_out, "
                "  file_maschera=excluded.file_maschera, "
                "  rotazione=excluded.rotazione, stato=excluded.stato, "
                "  avvisi=excluded.avvisi, creato=excluded.creato, "
                "  numero_bordo=excluded.numero_bordo, "
                "  file_negativo=CASE WHEN excluded.file_negativo != '' "
                "    THEN excluded.file_negativo ELSE fotogrammi.file_negativo END",
                (rullo_id, numero, file_raw, file_out, file_maschera,
                 rotazione, stato, avvisi, time.time(), numero_bordo or "",
                 file_negativo or ""))
            return int(cur.lastrowid)

    def fotogramma_voto(self, fotogramma_id: int, voto: int) -> None:
        """Da stampare (1), scartato (-1) o niente (0)."""
        voto = 1 if voto > 0 else (-1 if voto < 0 else 0)
        with self._conn() as c:
            c.execute("UPDATE fotogrammi SET voto = ? WHERE id = ?",
                      (voto, int(fotogramma_id)))

    def fotogrammi_rinumera(self, nuovi: List[Dict[str, object]]) -> None:
        """Cambia numero e file a piu' fotogrammi insieme (riordino). In due
        passi, con numeri negativi in mezzo: il vincolo UNIQUE(rullo,
        numero) non permette di scambiarli direttamente."""
        with self._conn() as c:
            for f in nuovi:
                c.execute("UPDATE fotogrammi SET numero = ? WHERE id = ?",
                          (-int(f["id"]), int(f["id"])))
            for f in nuovi:
                c.execute("UPDATE fotogrammi SET numero = ?, file_raw = ?, "
                          "file_out = ?, file_maschera = ?, file_negativo = ? "
                          "WHERE id = ?",
                          (int(f["numero"]), f.get("file_raw") or "",
                           f.get("file_out") or "", f.get("file_maschera") or "",
                           f.get("file_negativo") or "", int(f["id"])))

    def fotogrammi(self, rullo_id: int) -> List[Dict[str, object]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM fotogrammi WHERE rullo_id = ? ORDER BY numero",
                (rullo_id,)).fetchall()
        return [dict(r) for r in rows]

    def fotogrammi_per_id(self, fotogramma_id: int) -> List[Dict[str, object]]:
        """Un fotogramma solo, cercato per id. Ritorna una lista (vuota o di uno)
        cosi' chi chiama non deve gestire il None."""
        if not fotogramma_id:
            return []
        with self._conn() as c:
            r = c.execute("SELECT * FROM fotogrammi WHERE id = ?",
                          (int(fotogramma_id),)).fetchone()
        return [dict(r)] if r else []

    def fotogramma_scorda_file(self, fotogramma_id: int,
                               quali=("file_raw", "file_out",
                                      "file_maschera", "file_negativo")) -> None:
        """Azzera i percorsi dei file che non ci sono piu'.

        Serve dopo una cancellazione, ed e' la meta' che mancava: finche' il
        database continua a indicare un file che sul disco non esiste,
        l'archivio conta fotogrammi che non ci sono, le miniature si rompono
        una per una e chi guarda conclude, giustamente, che il pulsante
        "elimina" non ha fatto niente.

        La riga del fotogramma resta: sapere che quel fotogramma e' esistito
        e' esattamente l'informazione che si vuole conservare.
        """
        campi = [q for q in quali
                 if q in ("file_raw", "file_out", "file_maschera", "file_negativo")]
        if not campi:
            return
        sql = "UPDATE fotogrammi SET %s WHERE id = ?" % ", ".join(
            "%s = ''" % c for c in campi)
        with self._conn() as c:
            c.execute(sql, (int(fotogramma_id),))

    def fotogramma_ruota(self, fotogramma_id: int, gradi: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE fotogrammi SET rotazione = ? WHERE id = ?",
                      (int(gradi) % 360, fotogramma_id))

    # --- riepiloghi ---

    def riepilogo(self) -> Dict[str, int]:
        with self._conn() as c:
            def uno(sql: str, args=()) -> int:
                row = c.execute(sql, args).fetchone()
                return int(row[0]) if row else 0
            return {
                "clienti": uno("SELECT COUNT(*) FROM clienti"),
                "ordini_aperti": uno(
                    "SELECT COUNT(*) FROM ordini WHERE stato IN ('aperto','in_lavorazione')"),
                "ordini_totali": uno("SELECT COUNT(*) FROM ordini"),
                "rulli_da_fare": uno(
                    "SELECT COUNT(*) FROM rulli WHERE stato = 'da_scansionare'"),
                "fotogrammi": uno("SELECT COUNT(*) FROM fotogrammi"),
            }

    def cartella_ordine(self, base: str, ordine_id: int) -> str:
        """Percorso di destinazione per un ordine: base/cliente/codice.

        Il nome del cliente viene ripulito dai caratteri che Windows non accetta
        nei nomi di cartella. Non e' pedanteria: un cliente che si chiama
        'Rossi & C.' o una data scritta con le barre bloccano il salvataggio a
        meta' rullo.
        """
        o = self.ordine(ordine_id)
        if o is None:
            raise ValueError("ordine inesistente: %s" % ordine_id)
        vietati = '<>:"/\\|?*'
        nome = "".join("_" if ch in vietati else ch for ch in o.cliente_nome).strip()
        nome = nome.rstrip(". ") or "cliente"
        path = os.path.join(base, nome, o.codice)
        os.makedirs(path, exist_ok=True)
        return path
