"""Within-family micro-genre split by audio features (deterministic thresholds)."""

SPLITTABLE = {"opium_rage", "atlanta_trap", "melodic_trap", "toronto"}


def split_family(family, tracks) -> dict:
    """Map fingerprint -> sub-family label. Non-splittable families pass through."""
    out = {}
    for t in tracks:
        fp = t["fingerprint"]
        if family not in SPLITTABLE:
            out[fp] = family
            continue
        hi = t.get("energy", 0.5) >= 0.6 and t.get("is_vocal", 0) == 0
        out[fp] = f"{family}_rage" if hi else f"{family}_melodic"
    return out
