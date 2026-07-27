import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import genre_audio as ga


def _a_flac():
    for dp, _, fns in os.walk(os.path.expanduser("~/Documents/MusicLake")):
        if "_dedup" in dp:
            continue
        for f in fns:
            if f.endswith(".flac") and 20 < os.path.getsize(os.path.join(dp, f)) / 1e6 < 45:
                return os.path.join(dp, f)
    import pytest
    pytest.skip("need a flac")


def test_predict_bad_file_empty():
    assert ga.predict("/nonexistent.flac") == {}


def test_predict_real_track():
    if not ga.available():
        import pytest
        pytest.skip("models/essentia missing")
    out = ga.predict(_a_flac())
    if out:
        assert out["genre"]
        assert 0.0 <= out["genre_confidence"] <= 1.0
        assert "subgenre" in out
