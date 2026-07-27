"""Unified library.db schema: new feature columns + analysis queue + MIR migration. Idempotent."""
import re
import sqlite3

# (column, sql-type) — added to recordings if missing
NEW_COLUMNS = [
    # catalog
    ("isrc", "TEXT"), ("spotify_id", "TEXT"), ("spotify_popularity", "INTEGER"),
    ("release_date", "TEXT"), ("label", "TEXT"), ("artist_genres", "TEXT"),
    ("spotify_match_confidence", "REAL"),
    # our MIR
    ("bpm", "REAL"), ("bpm_confidence", "REAL"), ("key", "TEXT"), ("camelot", "TEXT"),
    ("key_confidence", "REAL"), ("energy", "REAL"), ("brightness", "REAL"),
    ("bass_weight", "REAL"), ("mir_danceability", "REAL"), ("is_vocal", "INTEGER"),
    ("mix_in", "REAL"), ("mix_out", "REAL"), ("beat_anchor", "REAL"),
    ("first_downbeat", "REAL"), ("beat_engine", "TEXT"),
    ("spotify_key", "INTEGER"), ("spotify_mode", "INTEGER"),
    # essentia spotify-parity
    ("valence", "REAL"), ("acousticness", "REAL"), ("instrumentalness", "REAL"),
    ("speechiness", "REAL"), ("liveness", "REAL"), ("loudness_db", "REAL"),
    ("essentia_danceability", "REAL"),
    # provenance
    ("analysis_version", "TEXT"), ("analyzed_at", "TEXT"),
]

_norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
_MIR = ["bpm", "bpm_confidence", "key", "camelot", "key_confidence", "energy",
        "brightness", "bass_weight", "is_vocal", "mix_in", "mix_out",
        "beat_anchor", "first_downbeat", "beat_engine"]


def _c(db):
    c = sqlite3.connect(db, timeout=60)
    c.execute("PRAGMA busy_timeout=60000")
    return c


def ensure_schema(db: str):
    with _c(db) as c:
        have = {r[1] for r in c.execute("PRAGMA table_info(recordings)")}
        for col, typ in NEW_COLUMNS:
            if col not in have:
                c.execute(f"ALTER TABLE recordings ADD COLUMN {col} {typ}")
        c.execute("""CREATE TABLE IF NOT EXISTS pending_analysis(
            fingerprint TEXT PRIMARY KEY, path TEXT, enqueued_at TEXT DEFAULT CURRENT_TIMESTAMP,
            attempts INTEGER DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS segments(
            fingerprint TEXT, idx INTEGER, label TEXT, start REAL, end REAL)""")


def enqueue(db: str, fp: str, path: str):
    with _c(db) as c:
        c.execute("INSERT OR IGNORE INTO pending_analysis(fingerprint,path) VALUES(?,?)", (fp, path))


def dequeue(db: str, fp: str):
    with _c(db) as c:
        c.execute("DELETE FROM pending_analysis WHERE fingerprint=?", (fp,))


def pending(db: str, limit: int = 100000):
    with _c(db) as c:
        return [(r[0], r[1]) for r in c.execute(
            "SELECT fingerprint, path FROM pending_analysis ORDER BY enqueued_at LIMIT ?", (limit,))]


def bump_attempt(db: str, fp: str):
    with _c(db) as c:
        c.execute("UPDATE pending_analysis SET attempts=attempts+1 WHERE fingerprint=?", (fp,))


def migrate_analysis(lib_db: str, analysis_db: str) -> int:
    """Copy MIR fields from walkman_analysis.db into recordings by (artist,title)."""
    a = sqlite3.connect(analysis_db); a.row_factory = sqlite3.Row
    fcols = {r[1] for r in a.execute("PRAGMA table_info(features)")}
    use = [col for col in _MIR if col in fcols]
    src = {}
    for r in a.execute(f"""SELECT t.artist, t.title, {','.join('f.'+col for col in use)}
                           FROM tracks t JOIN features f USING(fingerprint)"""):
        k = _norm(f"{r['artist']} {r['title']}")
        if k and k not in src:
            src[k] = {col: r[col] for col in use}
    a.close()
    n = 0
    with _c(lib_db) as c:
        for row in c.execute("SELECT akey FROM recordings WHERE bpm IS NULL").fetchall():
            s = src.get(row[0])
            if s and s.get("bpm") is not None:
                sets = ",".join(f"{col}=?" for col in use)
                c.execute(f"UPDATE recordings SET {sets} WHERE akey=?", (*[s[col] for col in use], row[0]))
                n += 1
    return n
