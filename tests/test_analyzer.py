import os, sys, shutil, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import crate, schema


def _flac(tmp):
    for dp, _, fns in os.walk(os.path.expanduser("~/Documents/MusicLake")):
        if "_dedup" in dp:
            continue
        for f in fns:
            if f.endswith(".flac") and 20 < os.path.getsize(os.path.join(dp, f)) / 1e6 < 45:
                d = str(tmp / "s.flac"); shutil.copy(os.path.join(dp, f), d); return d
    import pytest
    pytest.skip("need flac")


def test_ingest_enqueues_for_analysis(tmp_path):
    root = tmp_path / "lib"; root.mkdir()
    db = str(tmp_path / "db.sqlite")
    lib = crate.CrateLib(db, str(root))
    lib.ingest(_flac(tmp_path))
    assert len(schema.pending(db)) == 1


def test_analyzer_drains_queue_and_writes_row(tmp_path, monkeypatch):
    from walkman_dj import crate_cli
    root = tmp_path / "lib"; root.mkdir()
    db = str(tmp_path / "db.sqlite")
    lib = crate.CrateLib(db, str(root))
    lib.ingest(_flac(tmp_path))
    fp = sqlite3.connect(db).execute("SELECT fingerprint FROM pending_analysis").fetchone()[0]
    import walkman_dj.intelligence as I
    import walkman_dj.spotify_meta as sm
    monkeypatch.setattr(I, "analyze_song", lambda p: {
        "status": "ok", "bpm": 140.0, "camelot": "8A", "energy": 0.6,
        "analysis_version": "si-1.0", "segments": []})
    monkeypatch.setattr(sm, "match", lambda *a, **k: {"spotify_id": "sp", "spotify_popularity": 50})
    crate_cli.DB = db
    crate_cli.main(["analyzer", "--once"])
    row = sqlite3.connect(db).execute("SELECT bpm,camelot,spotify_id FROM recordings WHERE fp=?", (fp,)).fetchone()
    assert round(row[0]) == 140 and row[1] == "8A" and row[2] == "sp"
    assert schema.pending(db) == []
