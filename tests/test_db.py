import tempfile, os
from walkman_dj.db import CrateDB
def test_upsert_and_incremental():
    with tempfile.TemporaryDirectory() as d:
        db = CrateDB(os.path.join(d, "t.db")); db.init()
        db.upsert_track(fingerprint="fp1", path="/M/a.mp3", filename="a.mp3",
                        artist="X", title="A", folder="Singles")
        assert db.needs_analysis("fp1") is True
        db.save_features("fp1", {"bpm":124.0,"camelot":"8A","energy":0.7,
                                 "key_confidence":0.8,"status":"ok"})
        assert db.needs_analysis("fp1") is False
        db.upsert_track(fingerprint="fp1", path="/M/moved.mp3", filename="moved.mp3",
                        artist="X", title="A", folder="Singles")
        assert db.needs_analysis("fp1") is False
        row = db.get_track("fp1")
        assert row["path"] == "/M/moved.mp3" and row["bpm"] == 124.0
def test_failed_status_not_reanalyzed_by_default():
    with tempfile.TemporaryDirectory() as d:
        db = CrateDB(os.path.join(d, "t.db")); db.init()
        db.upsert_track(fingerprint="fp2", path="/M/b.wav", filename="b.wav",
                        artist=None, title=None, folder="Singles")
        db.save_features("fp2", {"status":"failed"})
        assert db.needs_analysis("fp2") is False
