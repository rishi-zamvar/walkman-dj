"""SOTA beat/BPM: Beat This! (downbeats+beats) + DeepRhythm (octave-correct BPM) +
least-squares constant-tempo snap. Anchor = first downbeat (real beat-1, not mix_in).
Models load once per process (expensive) — reuse the module-level singletons.

Requires the ~/.venvs/analysis venv (torch+beat-this+deeprhythm). MPS if available."""
import numpy as np

_F2B = None
_DR = None


def _engines():
    global _F2B, _DR
    if _F2B is None:
        import torch
        from beat_this.inference import File2Beats
        dev = "mps" if torch.backends.mps.is_available() else "cpu"
        _F2B = File2Beats(device=dev)
        from deeprhythm import DeepRhythmPredictor
        _DR = DeepRhythmPredictor()
    return _F2B, _DR


def constant_grid(beats, downbeats, bpm_hint):
    """Fit beat_time ~ a*index + b over detected beats -> single BPM + phase.
    Snap octave to DeepRhythm's bpm_hint. Anchor = first downbeat."""
    beats = np.asarray(beats, dtype=float)
    if len(beats) < 4:
        return {"bpm": round(float(bpm_hint or 0), 2), "anchor": float(downbeats[0]) if len(downbeats) else 0.0,
                "residual": None, "n_beats": int(len(beats))}
    idx = np.arange(len(beats))
    a, b = np.polyfit(idx, beats, 1)                 # beats ~ a*idx + b
    grid_bpm = 60.0 / a if a > 1e-6 else float(bpm_hint or 0)
    # octave-correct against DeepRhythm
    if bpm_hint:
        for mult in (0.5, 2.0):
            if abs(grid_bpm * mult - bpm_hint) < abs(grid_bpm - bpm_hint):
                grid_bpm *= mult
    resid = float(np.sqrt(np.mean((beats - (a * idx + b)) ** 2)))
    anchor = float(downbeats[0]) if len(downbeats) else float(beats[0])
    return {"bpm": round(grid_bpm, 2), "anchor": round(anchor, 3),
            "residual": round(resid, 4), "n_beats": int(len(beats))}


def beatgrid_of(path, prior=(70, 180)):
    """Full SOTA grid for one file. Returns dict merge-able into features."""
    f2b, dr = _engines()
    try:
        dr_bpm = float(dr.predict(path))
    except Exception:  # noqa: BLE001
        dr_bpm = None
    try:
        beats, downbeats = f2b(path)
    except Exception as e:  # noqa: BLE001
        # Beat This! loader failed — fall back to DeepRhythm-only BPM (or librosa).
        bpm = dr_bpm
        if not bpm:
            try:
                import librosa
                y, sr = librosa.load(path, sr=22050, mono=True, duration=120)
                bpm = float(librosa.beat.beat_track(y=y, sr=sr)[0])
            except Exception:  # noqa: BLE001
                bpm = 0.0
        return {"bpm": round(bpm or 0.0, 2), "bpm_raw": round(bpm or 0.0, 2),
                "bpm_confidence": 0.5 if bpm else 0.0, "beat_anchor": 0.0,
                "first_downbeat": 0.0, "grid_residual": None,
                "beat_engine": "deeprhythm-only" if dr_bpm else "librosa-fallback"}
    g = constant_grid(beats, downbeats, dr_bpm)
    # confidence from fit residual: tight grid (< ~15ms) -> high confidence
    resid = g.get("residual")
    conf = 1.0 if resid is None else float(max(0.0, min(1.0, 1.0 - (resid / 0.05))))
    return {"bpm": g["bpm"], "bpm_raw": round(dr_bpm, 2) if dr_bpm else g["bpm"],
            "bpm_confidence": round(conf, 3), "beat_anchor": g["anchor"],
            "first_downbeat": g["anchor"], "grid_residual": resid,
            "beat_engine": "beatthis+deeprhythm"}
