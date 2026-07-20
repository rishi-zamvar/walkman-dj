"""SQLite crate-DB: single source of truth. Identity = content fingerprint."""
import sqlite3
import json

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks(
  fingerprint TEXT PRIMARY KEY, path TEXT, filename TEXT,
  artist TEXT, title TEXT, folder TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS features(
  fingerprint TEXT PRIMARY KEY REFERENCES tracks(fingerprint),
  status TEXT DEFAULT 'pending',
  bpm REAL, bpm_raw REAL, bpm_confidence REAL,
  key TEXT, camelot TEXT, key_confidence REAL, key_agree INTEGER,
  energy REAL, loudness_curve TEXT, brightness REAL, bass_weight REAL,
  is_vocal INTEGER, danceability REAL, mix_in REAL, mix_out REAL, duration REAL);
CREATE TABLE IF NOT EXISTS segments(
  fingerprint TEXT, idx INTEGER, label TEXT, start REAL, end REAL);
CREATE TABLE IF NOT EXISTS playlists(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE, arc TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS playlist_tracks(
  playlist_id INTEGER, position INTEGER, fingerprint TEXT, transition_score REAL);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, note TEXT);
"""

# columns that live on the features table (guards save_features against bad keys)
_FEATURE_COLS = {"status", "bpm", "bpm_raw", "bpm_confidence", "key", "camelot",
                 "key_confidence", "key_agree", "energy", "loudness_curve",
                 "brightness", "bass_weight", "is_vocal", "danceability",
                 "mix_in", "mix_out", "duration"}


class CrateDB:
    def __init__(self, path):
        self.path = path

    def _c(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def init(self):
        with self._c() as c:
            c.executescript(SCHEMA)

    def upsert_track(self, *, fingerprint, path, filename, artist, title, folder):
        with self._c() as c:
            c.execute(
                """INSERT INTO tracks(fingerprint,path,filename,artist,title,folder)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(fingerprint) DO UPDATE SET
                     path=excluded.path, filename=excluded.filename,
                     artist=excluded.artist, title=excluded.title,
                     folder=excluded.folder, updated_at=CURRENT_TIMESTAMP""",
                (fingerprint, path, filename, artist, title, folder))
            c.execute("INSERT OR IGNORE INTO features(fingerprint) VALUES(?)",
                      (fingerprint,))

    def needs_analysis(self, fp) -> bool:
        with self._c() as c:
            r = c.execute("SELECT status FROM features WHERE fingerprint=?",
                          (fp,)).fetchone()
            return (r is None) or (r["status"] == "pending")

    def save_features(self, fp, feat: dict):
        feat = {k: v for k, v in feat.items() if k in _FEATURE_COLS}
        if "loudness_curve" in feat and not isinstance(feat["loudness_curve"], str):
            feat["loudness_curve"] = json.dumps(feat["loudness_curve"])
        cols = ",".join(f"{k}=?" for k in feat)
        with self._c() as c:
            c.execute(f"UPDATE features SET {cols} WHERE fingerprint=?",
                      (*feat.values(), fp))

    def save_segments(self, fp, segments):
        with self._c() as c:
            c.execute("DELETE FROM segments WHERE fingerprint=?", (fp,))
            c.executemany(
                "INSERT INTO segments(fingerprint,idx,label,start,end) VALUES(?,?,?,?,?)",
                [(fp, s["idx"], s["label"], s["start"], s["end"]) for s in segments])

    def get_track(self, fp) -> dict:
        with self._c() as c:
            r = c.execute("""SELECT t.*, f.* FROM tracks t JOIN features f
                             USING(fingerprint) WHERE t.fingerprint=?""",
                          (fp,)).fetchone()
            return dict(r) if r else None

    def all_ok(self) -> list[dict]:
        with self._c() as c:
            return [dict(r) for r in c.execute(
                "SELECT t.*, f.* FROM tracks t JOIN features f USING(fingerprint) "
                "WHERE f.status='ok'")]

    def counts(self) -> dict:
        with self._c() as c:
            ok = c.execute("SELECT COUNT(*) FROM features WHERE status='ok'").fetchone()[0]
            failed = c.execute("SELECT COUNT(*) FROM features WHERE status='failed'").fetchone()[0]
            pending = c.execute("SELECT COUNT(*) FROM features WHERE status='pending'").fetchone()[0]
            return {"ok": ok, "failed": failed, "pending": pending}
