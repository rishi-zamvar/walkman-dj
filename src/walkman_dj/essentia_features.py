"""Essentia Spotify-parity features, isolated so Essentia is one import site.
Returns {} (degraded) if Essentia is unavailable or the file fails.

These are Essentia-derived approximations of Spotify's proprietary audio features
(Spotify never published exact formulas). They are internally consistent and
normalized to [0,1] (loudness in dB) — which is what apples-to-apples comparison needs.
"""
import os

try:
    import essentia.standard as es
    import numpy as np
    _OK = True
except Exception:
    _OK = False

KEYS = ("valence", "acousticness", "instrumentalness", "speechiness", "liveness",
        "loudness_db", "essentia_danceability")


def available() -> bool:
    return _OK


def _clamp(x):
    return max(0.0, min(1.0, float(x)))


def extract(path: str) -> dict:
    if not _OK or not os.path.exists(path):
        return {}
    try:
        audio = es.MonoLoader(filename=path, sampleRate=44100)()
        if len(audio) < 44100 * 3:
            return {}
        dance, _ = es.Danceability()(audio)
        # real dBFS loudness from RMS (typical music ~ -6 to -25 dBFS)
        rms = float(np.sqrt(np.mean(np.square(audio)))) + 1e-9
        loudness_db = round(20.0 * float(np.log10(rms)), 2)
        # spectral centroid normalized to Nyquist (0..1): bright/electronic -> high
        spec = np.abs(np.fft.rfft(audio[: 1 << 17].astype("float32")))
        freqs = np.linspace(0, 22050, spec.size)
        centroid = float((freqs * spec).sum() / (spec.sum() + 1e-9)) / 22050.0
        zcr = float(es.ZeroCrossingRate()(audio))          # music ~0.03-0.12, speechy higher
        flat = float(es.Flatness()(spec)) if spec.size else 0.0  # 0..1, noise/live -> higher
        d = _clamp(dance / 3.0)
        return {
            "essentia_danceability": d,
            "loudness_db": loudness_db,
            "acousticness": _clamp(1.0 - centroid * 2.0),          # bright -> low acousticness
            "instrumentalness": _clamp(1.0 - zcr * 6.0),           # low ZCR -> instrumental
            "speechiness": _clamp((zcr - 0.03) * 6.0),             # elevated ZCR -> speech
            "liveness": _clamp(flat * 2.5),                        # spectral flatness proxy
            "valence": _clamp(d * 0.6 + (1 - centroid) * 0.4),
        }
    except Exception:
        return {}
