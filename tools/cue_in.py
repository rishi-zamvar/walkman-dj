"""Cue-in (first-downbeat / mix-in) detector, after Zehren et al. (CMJ 2022).

The first cue = first DOWNBEAT where the main groove establishes: sustained RMS energy
(relative to the track's own groove, so builds don't fool it) + drum-onset density
clearing ~the track's groove level. Candidate downbeats come from bpm + a known
beat_this downbeat (phase). Returns (cue_seconds, confidence 0..1) where confidence is
how SHARP the intro->groove transition is (sharp = safe to auto-write; gradual = flag).
"""
import numpy as np
import librosa

E_FRAC = 0.5          # groove bar energy must clear this * median groove energy
MIN_ON = 3            # and this many drum onsets/bar (or half the groove median)
SUSTAIN = 2           # sustained for this many bars


def _feats(path, bpm, phase, search_s=110.0):
    # only the first (search_s + margin) seconds matter for cue-in -> load less, HPSS faster
    y, sr = librosa.load(path, sr=22050, mono=True, duration=search_s + 15)
    dur = len(y) / sr
    bar = 4 * 60.0 / bpm
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    rms_t = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    yp = librosa.effects.percussive(y)
    onsets = librosa.onset.onset_detect(y=yp, sr=sr, hop_length=hop, units="time", backtrack=True)
    k = int(np.floor(-phase / bar))
    cands = []
    while True:
        t = phase + k * bar
        if t > min(dur, search_s):
            break
        if t >= -1e-6:
            cands.append(max(0.0, t))
        k += 1
    cands = np.array(cands)
    e = np.array([rms[(rms_t >= t) & (rms_t < t + bar)].mean()
                  if ((rms_t >= t) & (rms_t < t + bar)).any() else 0.0 for t in cands])
    o = np.array([int(((onsets >= t) & (onsets < t + bar)).sum()) for t in cands])
    return cands, e, o


def detect(path, bpm, phase, search_s=110.0):
    cands, e, o = _feats(path, bpm, phase, search_s)
    if len(cands) == 0:
        return round(phase, 3), 0.0
    med_e = np.median(e[e > 0]) if (e > 0).any() else 0.0
    med_o = np.median(o[o > 0]) if (o > 0).any() else 0.0
    thr_e, thr_o = E_FRAC * med_e, max(MIN_ON, 0.5 * med_o)
    for i, t in enumerate(cands):
        ok = all(i + j < len(cands) and e[i + j] >= thr_e and o[i + j] >= thr_o
                 for j in range(SUSTAIN))
        if ok:
            prev_e = e[i - 1] if i > 0 else e[i] * 0.5     # energy just before the cue
            conf = float(np.clip(1.0 - prev_e / (e[i] + 1e-9), 0.0, 1.0))
            if i == 0:
                conf = 0.5                                  # groove from the top: medium
            return round(float(t), 3), round(conf, 3)
    return round(float(cands[int(np.argmax(e))]), 3), 0.0    # fallback: loudest bar, no confidence


if __name__ == "__main__":
    import sys, os, sqlite3
    c = sqlite3.connect(os.path.expanduser("~/Documents/Projects/walkman-dj/data/library.db"))
    for key, truth, filt in [("Too Good", 8.1, None), ("CAROUSEL", 29.3, None),
                             ("Knock Knock", 14.4, "Funk"), ("Harder, Better", 6.0, None)]:
        rows = c.execute("SELECT canonical_path,artist,first_downbeat,bpm FROM recordings WHERE title LIKE ?",
                         (f"%{key}%",)).fetchall()
        row = next((r for r in rows if filt in (r[1] or "")), None) if filt else (rows[0] if rows else None)
        if not row:
            continue
        cue, conf = detect(row[0], row[3], row[2])
        print(f"{key:16} truth={truth:5.1f}  cue={cue:6.2f}  conf={conf:.2f}  err={abs(cue-truth):.2f}")
