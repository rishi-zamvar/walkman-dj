"""MIR feature extraction. Pure functions over (y, sr)."""
import os
import numpy as np
import librosa
from walkman_dj.camelot import to_camelot


# ---- BPM ----

def correct_bpm(raw: float, prior: tuple[int, int]) -> float:
    lo, hi = prior
    for mult in (0.25, 0.5, 1, 2, 4):
        c = raw * mult
        if lo <= c <= hi:
            return round(c, 1)
    mid = (lo + hi) / 2
    cands = [raw * m for m in (0.25, 0.5, 1, 2, 4)]
    return round(min(cands, key=lambda c: abs(c - mid)), 1)


def bpm_of(y, sr, prior=(70, 180)) -> dict:
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    raw = float(np.atleast_1d(tempo)[0])
    return {"bpm_raw": round(raw, 1), "bpm": correct_bpm(raw, prior),
            "bpm_confidence": 1.0}


# ---- Key (dual profile -> Camelot) ----

_PITCHES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_KRUM_MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KRUM_MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_TEMP_MAJ = np.array([5, 2, 3.5, 2, 4.5, 4, 2, 4.5, 2, 3.5, 1.5, 4])
_TEMP_MIN = np.array([5, 2, 3.5, 4.5, 2, 4, 2, 4.5, 3.5, 2, 1.5, 4])


def _estimate(chroma_mean, maj, min_):
    scores = []
    for i in range(12):
        scores.append((np.corrcoef(np.roll(maj, i), chroma_mean)[0, 1], _PITCHES[i], "major"))
        scores.append((np.corrcoef(np.roll(min_, i), chroma_mean)[0, 1], _PITCHES[i], "minor"))
    return max(scores, key=lambda s: (s[0] if not np.isnan(s[0]) else -1))


def key_of(y, sr) -> dict:
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
    k1 = _estimate(chroma, _KRUM_MAJ, _KRUM_MIN)
    k2 = _estimate(chroma, _TEMP_MAJ, _TEMP_MIN)
    agree = (k1[1], k1[2]) == (k2[1], k2[2])
    c1 = k1[0] if not np.isnan(k1[0]) else 0.0
    c2 = k2[0] if not np.isnan(k2[0]) else 0.0
    conf = float(max(0.0, (c1 + (c2 if agree else 0)) / (2 if agree else 1)))
    pitch, mode = k1[1], k1[2]
    return {"key": pitch, "camelot": to_camelot(pitch, mode),
            "key_confidence": round(min(conf, 1.0), 3), "key_agree": int(agree)}


# ---- Energy / timbre / vocal ----

def energy_timbre_of(y, sr, curve_pts=16) -> dict:
    rms = librosa.feature.rms(y=y)[0]
    energy = float(np.clip(rms.mean() / (np.percentile(rms, 95) + 1e-9), 0, 1))
    idx = np.linspace(0, len(rms) - 1, curve_pts).astype(int)
    env = rms[idx].astype(float)
    ptp = env.max() - env.min()
    env = (env - env.min()) / (ptp + 1e-9)
    cent = librosa.feature.spectral_centroid(y=y, sr=sr)[0].mean()
    brightness = float(np.clip(cent / (sr / 2), 0, 1))
    S = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)
    low = S[freqs < 250].sum()
    bass = float(np.clip(low / (S.sum() + 1e-9), 0, 1))
    if os.environ.get("FAST_ANALYZE"):
        is_vocal = 0                                 # skip 5.8s hpss (is_vocal over-fires + unused by sequencer)
    else:
        harm, perc = librosa.effects.hpss(y)
        is_vocal = int(np.mean(np.abs(harm)) > 1.15 * np.mean(np.abs(perc)))
    dance = float(np.clip(np.mean(librosa.onset.onset_strength(y=y, sr=sr)) / 5.0, 0, 1))
    return {"energy": round(energy, 3),
            "loudness_curve": [round(float(x), 3) for x in env],
            "brightness": round(brightness, 3), "bass_weight": round(bass, 3),
            "is_vocal": is_vocal, "danceability": round(dance, 3),
            "duration": round(len(y) / sr, 1)}


# ---- Structure / mix points ----

def structure_of(y, sr, n_seg=5) -> dict:
    dur = len(y) / sr
    try:
        mfcc = librosa.feature.mfcc(y=y, sr=sr)
        k = min(n_seg, max(2, int(dur // 30) + 1))
        bounds = librosa.segment.agglomerative(mfcc, k)
        times = librosa.frames_to_time(bounds, sr=sr).tolist()
    except Exception:  # noqa: BLE001
        times = [0.0]
    edges = sorted(set([0.0] + [t for t in times if 0 < t < dur] + [dur]))
    rms = librosa.feature.rms(y=y)[0]
    rt = librosa.times_like(rms, sr=sr)
    segs = []
    for i, (s, e) in enumerate(zip(edges[:-1], edges[1:])):
        m = (rt >= s) & (rt < e)
        lvl = float(rms[m].mean()) if m.any() else 0.0
        segs.append({"idx": i, "label": lvl, "start": round(s, 2), "end": round(e, 2)})
    hi = max((s["label"] for s in segs), default=1.0) or 1.0
    for s in segs:
        r = s["label"] / (hi + 1e-9)
        s["label"] = ("drop" if r > 0.8 else "build" if r > 0.5 else
                      "breakdown" if r > 0.25 else ("intro" if s["idx"] == 0 else "outro"))
    thr = 0.3 * rms.max()
    above = np.where(rms > thr)[0]
    mix_in = float(rt[above[0]]) if len(above) else 0.0
    mix_out = float(rt[above[-1]]) if len(above) else dur
    return {"segments": segs, "mix_in": round(mix_in, 2), "mix_out": round(mix_out, 2)}


# ---- Compose ----

def analyze_file(path, prior=(70, 180), on_stage=None) -> dict:
    def stage(s):
        if on_stage:
            on_stage(s)
    try:
        stage("load")
        # Hard load cap: never pull more than 6 min into memory. Real songs fit;
        # this bounds per-worker RAM so more workers stay safe on an 18 GB machine.
        y, sr = librosa.load(path, sr=22050, mono=True, duration=360)
        if len(y) < sr * 5:
            return {"status": "failed", "segments": []}
        out = {"status": "ok"}
        stage("tempo")
        out.update(bpm_of(y, sr, prior))
        stage("key")
        out.update(key_of(y, sr))
        stage("energy")
        out.update(energy_timbre_of(y, sr))
        stage("structure")
        out.update(structure_of(y, sr))
        return out
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "segments": [], "error": str(e)[:200]}
