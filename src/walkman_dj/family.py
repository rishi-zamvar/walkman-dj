"""Family (coarse-bucket) assignment, per-family energy rescale, audio override.
Reuses genre.resolve_family for the label path; adds structural override + persist."""
import math

from walkman_dj import genre_store


def rescale_energy(raw: dict) -> dict:
    """Map each fingerprint's raw energy to its min-max position in [0,1] within
    the group. Compressed library band (~0.47-0.72) -> full 0..1 so templates bite.
    Degenerate (n<=1 or all-equal) -> 0.5."""
    vals = list(raw.values())
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return {k: 0.5 for k in raw}
    return {k: (v - lo) / (hi - lo) for k, v in raw.items()}


_ENV_KEYS = ("bpm", "energy", "bass_weight", "brightness")


def _mean_std(vals):
    m = sum(vals) / len(vals)
    var = sum((v - m) ** 2 for v in vals) / max(1, len(vals))
    return m, math.sqrt(var) or 1e-6


def learn_envelopes(tracks_by_family: dict) -> dict:
    """Per-family mean/std over BPM+energy+timbre, from confidently-labeled members."""
    env = {}
    for fam, tracks in tracks_by_family.items():
        if not tracks:
            continue
        env[fam] = {k: _mean_std([t.get(k) or 0.0 for t in tracks]) for k in _ENV_KEYS}
    return env


def _mahalanobis_like(track, stats) -> float:
    return math.sqrt(sum(
        ((track.get(k) or 0.0) - stats[k][0]) ** 2 / stats[k][1] ** 2 for k in _ENV_KEYS))


def audio_override(track, label_family, env, margin=1.0):
    """If the track sits far outside its labeled family's envelope AND clearly
    closer to another family's, re-file it. Returns (family, source)."""
    if label_family not in env:
        # no envelope to trust -> keep label
        return (label_family, "label")
    d_label = _mahalanobis_like(track, env[label_family])
    best_fam, best_d = min(((f, _mahalanobis_like(track, s)) for f, s in env.items()),
                           key=lambda x: x[1])
    if best_fam != label_family and best_d + margin < d_label:
        return (best_fam, "audio_override")
    return (label_family, "label")


def assign_and_persist(db_path, tracks, reclassify=False):
    """Assign each track a family (label + audio override) and persist frozen.
    `tracks` carry label_family, canon_genre, label_conf + envelope features.
    Returns count newly written. Envelopes learned from confidently-labeled tracks."""
    genre_store.ensure_schema(db_path)
    confident = [t for t in tracks if (t.get("label_conf") or 0) >= 0.6 and t.get("label_family")]
    by_fam = {}
    for t in confident:
        by_fam.setdefault(t["label_family"], []).append(t)
    env = learn_envelopes(by_fam)
    written = 0
    for t in tracks:
        fp = t["fingerprint"]
        if not reclassify and genre_store.is_assigned(db_path, fp):
            continue
        fam, src = audio_override(t, t.get("label_family"), env)
        genre_store.upsert(db_path, fp, t.get("canon_genre"), fam, src,
                           t.get("label_conf") or 0.0, reclassify=reclassify)
        written += 1
    return written
