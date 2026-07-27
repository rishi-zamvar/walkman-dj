import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import essentia_features as ef


def _a_flac():
    for dp, _, fns in os.walk(os.path.expanduser("~/Documents/MusicLake")):
        if "_dedup" in dp:
            continue
        for f in fns:
            if f.endswith(".flac"):
                p = os.path.join(dp, f)
                if 20 < os.path.getsize(p) / 1e6 < 45:
                    return p
    import pytest
    pytest.skip("need a flac")


def test_essentia_features_shape_or_empty():
    # returns dict with the 7 keys (or empty if essentia missing) — never raises
    out = ef.extract("/nonexistent.flac")
    assert isinstance(out, dict)
    assert out == {}  # nonexistent file -> empty


def test_essentia_features_on_real_track():
    if not ef.available():
        import pytest
        pytest.skip("essentia not installed")
    out = ef.extract(_a_flac())
    if out:
        for k in ef.KEYS:
            assert k in out
        for k in ("valence", "acousticness", "instrumentalness", "speechiness", "liveness", "essentia_danceability"):
            assert 0.0 <= out[k] <= 1.0


def test_analyze_song_returns_normalized_versioned():
    from walkman_dj import intelligence as I
    r = I.analyze_song(_a_flac())
    assert r["status"] == "ok"
    assert r["bpm"] and r["bpm"] > 40
    assert r["camelot"]
    for k in ("energy", "brightness", "bass_weight"):
        assert 0.0 <= r[k] <= 1.0
    assert r["analysis_version"] == I.ANALYSIS_VERSION


def test_analyze_song_bad_file_returns_failed(tmp_path):
    from walkman_dj import intelligence as I
    bad = str(tmp_path / "x.flac"); open(bad, "wb").write(b"nope")
    assert I.analyze_song(bad)["status"] == "failed"
