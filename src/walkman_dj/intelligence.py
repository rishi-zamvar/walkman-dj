"""analyze_song(path) — the unified engine. Composes SOTA beatgrid + librosa + Essentia,
normalizes to the schema's scales. Deterministic; never raises."""
import os
import datetime
from walkman_dj import beatgrid, analyze, essentia_features

ANALYSIS_VERSION = "si-1.0"
_PITCH = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6,
          "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}


def analyze_song(path: str) -> dict:
    if not os.path.exists(path):
        return {"status": "failed"}
    try:
        import librosa
        y, sr = librosa.load(path, sr=22050, mono=True, duration=360)
        if len(y) < sr * 5:
            return {"status": "failed"}
        out = {"status": "ok", "analysis_version": ANALYSIS_VERSION,
               "analyzed_at": datetime.datetime.now().isoformat(timespec="seconds")}
        # SOTA beatgrid (falls back to librosa bpm internally)
        try:
            out.update(beatgrid.beatgrid_of(path))
        except Exception:
            out.update(analyze.bpm_of(y, sr))
        # librosa key / energy / structure
        out.update(analyze.key_of(y, sr))
        out.update(analyze.energy_timbre_of(y, sr))
        st = analyze.structure_of(y, sr)
        out["mix_in"], out["mix_out"] = st.get("mix_in"), st.get("mix_out")
        out["segments"] = st.get("segments", [])
        # librosa 'danceability' -> mir_danceability (schema name)
        out["mir_danceability"] = out.pop("danceability", None)
        # spotify-style key/mode from librosa key + camelot A/B
        pitch = out.get("key")
        is_major = not (out.get("camelot", "") or "").endswith("A")
        out["spotify_key"] = _PITCH.get(pitch, -1)
        out["spotify_mode"] = 1 if is_major else 0
        # essentia parity features (or {} if unavailable)
        out.update(essentia_features.extract(path))
        return out
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "error": str(e)[:120]}
