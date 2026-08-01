"""Code-standardized tempo stability — does the BPM drift within a track?

Rekordbox's per-beat BPM can't tell us: 'normal' analysis forces one constant BPM
regardless of the real tempo. This re-derives stability from the audio itself, the
same way for every track, so a constant/variable classification is consistent
independent of how Rekordbox analysed it.

tempo_cv(path, ref_bpm): mean % deviation of per-window tempo from the KNOWN bpm.
  Anchoring the octave-fold to the trusted (Rekordbox) bpm avoids librosa's per-window
  half/double-tempo errors, which a self-referential median can't correct.
  <1  = rock-solid constant tempo (clean beatmatch)
  <2  = constant enough for mixing
  >3  = drifts (live/jazzy/variable) — hard to beatmatch
"""
_MULTS = (1, 2, 0.5, 1.5, 2 / 3, 3, 1 / 3, 4 / 3, 3 / 4)


def tempo_cv(path, ref_bpm, nwin=10, sr=22050):
    """Mean % tempo deviation from ref_bpm across windows, or None if unanalysable."""
    if not ref_bpm:
        return None
    try:
        import librosa
        import numpy as np
        y, _ = librosa.load(path, sr=sr, mono=True, duration=300)
    except Exception:
        return None
    if len(y) < sr * 20:
        return None
    seg = len(y) // nwin
    devs = []
    for i in range(nwin):
        w = y[i * seg:(i + 1) * seg]
        if len(w) < sr * 4:
            continue
        try:
            t = float(librosa.feature.tempo(y=w, sr=sr, aggregate=np.median)[0])
        except Exception:
            continue
        folded = min((t * m for m in _MULTS), key=lambda x: abs(x - ref_bpm))  # fold to grid near ref
        devs.append(abs(folded - ref_bpm) / ref_bpm)
    if len(devs) < 4:
        return None
    return round(100 * sum(devs) / len(devs), 2)
