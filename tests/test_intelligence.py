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


def _write_wav(path, freq, sr=44100, secs=5):
    import numpy as np, wave, struct
    t = np.linspace(0, secs, sr * secs, endpoint=False)
    x = (0.3 * np.sin(2 * np.pi * freq * t)).astype("float32")
    w = wave.open(path, "w"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes((x * 32767).astype("<i2").tobytes()); w.close()


def test_acousticness_spreads_bright_below_dark(tmp_path):
    if not ef.available():
        import pytest; pytest.skip("essentia not installed")
    bright = str(tmp_path / "bright.wav"); _write_wav(bright, 8000)
    dark = str(tmp_path / "dark.wav"); _write_wav(dark, 120)
    ab = ef.extract(bright).get("acousticness")
    ad = ef.extract(dark).get("acousticness")
    if ab is not None and ad is not None:
        assert ab < ad                      # bright/electronic reads less acoustic
        assert ab < 0.5                      # not pinned high anymore


def test_analyze_song_bad_file_returns_failed(tmp_path):
    from walkman_dj import intelligence as I
    bad = str(tmp_path / "x.flac"); open(bad, "wb").write(b"nope")
    assert I.analyze_song(bad)["status"] == "failed"
