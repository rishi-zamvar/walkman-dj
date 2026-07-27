import os, sys, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import schema


def test_ensure_schema_adds_columns_idempotently(tmp_path):
    db = str(tmp_path / "t.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE recordings(id INTEGER PRIMARY KEY, fp TEXT, artist TEXT, title TEXT)")
    c.commit(); c.close()
    schema.ensure_schema(db)
    schema.ensure_schema(db)  # idempotent
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(recordings)")}
    for expected in ("valence", "acousticness", "spotify_id", "bpm", "camelot", "analysis_version", "loudness_db"):
        assert expected in cols
    tabs = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "pending_analysis" in tabs and "segments" in tabs


def test_enqueue_dequeue_roundtrip(tmp_path):
    db = str(tmp_path / "t.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE recordings(id INTEGER PRIMARY KEY, fp TEXT)"); c.commit(); c.close()
    schema.ensure_schema(db)
    schema.enqueue(db, "fp1", "/x.flac")
    schema.enqueue(db, "fp1", "/x.flac")  # dupe ignored
    pend = schema.pending(db)
    assert pend == [("fp1", "/x.flac")]
    schema.dequeue(db, "fp1")
    assert schema.pending(db) == []


def test_migrate_analysis_copies_mir_by_artist_title(tmp_path):
    lib = str(tmp_path / "lib.db")
    c = sqlite3.connect(lib)
    c.execute("CREATE TABLE recordings(id INTEGER PRIMARY KEY, fp TEXT, artist TEXT, title TEXT, akey TEXT)")
    c.execute("INSERT INTO recordings(fp,artist,title,akey) VALUES('f','Ken Carson','Yale','kencarsonyale')")
    c.commit(); c.close()
    schema.ensure_schema(lib)
    ana = str(tmp_path / "ana.db"); a = sqlite3.connect(ana)
    a.executescript("CREATE TABLE tracks(fingerprint,artist,title);"
                    "CREATE TABLE features(fingerprint,bpm,camelot,energy,beat_engine);")
    a.execute("INSERT INTO tracks VALUES('x','Ken Carson','Yale')")
    a.execute("INSERT INTO features VALUES('x',151.0,'8A',0.7,'beatthis+deeprhythm')")
    a.commit(); a.close()
    n = schema.migrate_analysis(lib, ana)
    assert n == 1
    row = sqlite3.connect(lib).execute(
        "SELECT bpm,camelot,energy,beat_engine FROM recordings WHERE akey='kencarsonyale'").fetchone()
    assert round(row[0]) == 151 and row[1] == "8A" and row[3] == "beatthis+deeprhythm"
