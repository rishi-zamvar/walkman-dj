"""Arc assignment + harmonic/energy transition scoring + ordering."""
import numpy as np
from walkman_dj.camelot import compatible

# NOTE: vocal weight kept low — the is_vocal detector over-fires (~92-98% of
# tracks read as vocal, including instrumentals), so it's an unreliable
# discriminator. BPM / key / energy carry the sequencing.
_WEIGHTS = {
    "hype":    {"arc": 0.25, "bpm": 0.40, "key": 0.30, "vocal": 0.05},
    "soul":    {"arc": 0.35, "bpm": 0.20, "key": 0.40, "vocal": 0.05},
    "journey": {"arc": 0.35, "bpm": 0.30, "key": 0.30, "vocal": 0.05},
}


def assign_arc(energies) -> str:
    m = float(np.median(list(energies)))
    if m >= 0.7:
        return "hype"
    if m <= 0.4:
        return "soul"
    return "journey"


def transition_score(a, b, w) -> float:
    key_ok = (b.get("camelot") in compatible(a["camelot"])
              if a.get("camelot") and b.get("camelot") else False)
    conf = min(a.get("key_confidence", 0.5) or 0.5, b.get("key_confidence", 0.5) or 0.5)
    key = (1.0 if key_ok else 0.2) * (0.5 + 0.5 * conf)
    bpm = max(0.0, 1.0 - abs((a.get("bpm") or 0) - (b.get("bpm") or 0)) / 8.0)
    arc = 1.0 - abs((a.get("energy") or 0) - (b.get("energy") or 0))
    vocal = 0.0 if (a.get("is_vocal") and b.get("is_vocal")) else 1.0
    return w["key"] * key + w["bpm"] * bpm + w["arc"] * arc + w["vocal"] * vocal


def order_playlist(tracks, arc) -> list[dict]:
    if len(tracks) <= 2:
        return list(tracks)
    w = _WEIGHTS[arc]
    pool = list(tracks)
    peak = max(pool, key=lambda t: t["energy"])
    soft = min(pool, key=lambda t: t["energy"])
    if arc == "hype":
        start = peak
    elif arc == "soul":
        start = soft
    else:
        start = min(pool, key=lambda t: t["energy"])
    pool.remove(start)
    seq = [start]
    while pool:
        cur = seq[-1]
        nxt = max(pool, key=lambda t: transition_score(cur, t, w))
        pool.remove(nxt)
        seq.append(nxt)
    # endpoint pinning
    if arc == "hype" and peak in seq:
        seq.remove(peak)
        seq.append(peak)                       # peak finish
    if arc == "soul":
        if soft in seq:
            seq.remove(soft)
            seq.append(soft)                   # afterglow (softest) last
        if peak in seq:
            seq.remove(peak)
            seq.insert(max(1, int(len(seq) * 0.75)), peak)   # late climax
    return seq
