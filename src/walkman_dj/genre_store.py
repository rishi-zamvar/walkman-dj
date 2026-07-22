"""Frozen per-track genre/family persistence for stable playlists."""
import sqlite3

_DDL = """
CREATE TABLE IF NOT EXISTS track_genre (
  fingerprint   TEXT PRIMARY KEY,
  canon_genre   TEXT,
  family        TEXT,
  family_source TEXT,
  confidence    REAL,
  assigned_at   TEXT DEFAULT (datetime('now'))
);
"""


def ensure_schema(db_path):
    con = sqlite3.connect(db_path)
    con.execute(_DDL)
    con.commit()
    con.close()


def is_assigned(db_path, fingerprint) -> bool:
    con = sqlite3.connect(db_path)
    row = con.execute("SELECT 1 FROM track_genre WHERE fingerprint=?", (fingerprint,)).fetchone()
    con.close()
    return row is not None


def upsert(db_path, fingerprint, canon_genre, family, family_source, confidence, reclassify=False):
    """Insert an assignment. Frozen: existing rows are NOT overwritten unless reclassify=True."""
    con = sqlite3.connect(db_path)
    if reclassify:
        con.execute(
            "INSERT INTO track_genre(fingerprint,canon_genre,family,family_source,confidence) "
            "VALUES(?,?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET "
            "canon_genre=excluded.canon_genre, family=excluded.family, "
            "family_source=excluded.family_source, confidence=excluded.confidence",
            (fingerprint, canon_genre, family, family_source, confidence))
    else:
        con.execute(
            "INSERT OR IGNORE INTO track_genre(fingerprint,canon_genre,family,family_source,confidence) "
            "VALUES(?,?,?,?,?)",
            (fingerprint, canon_genre, family, family_source, confidence))
    con.commit()
    con.close()


def get_all(db_path) -> dict:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = {r["fingerprint"]: dict(r)
            for r in con.execute("SELECT * FROM track_genre")}
    con.close()
    return rows
