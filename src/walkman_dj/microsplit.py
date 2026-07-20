"""Within-family micro-genre split by audio features (deterministic thresholds)."""

SPLITTABLE = {"opium_rage", "atlanta_trap", "melodic_trap", "toronto"}


def split_family(family, tracks) -> dict:
    """Map fingerprint -> sub-family label. Non-splittable families pass through.

    Splits on measured energy only. (The is_vocal flag over-fires ~96% and is
    useless as a discriminator, so it is intentionally NOT used here.)
    """
    out = {}
    for t in tracks:
        fp = t["fingerprint"]
        if family not in SPLITTABLE:
            out[fp] = family
            continue
        hi = (t.get("energy") or 0.5) >= 0.55
        out[fp] = f"{family}_rage" if hi else f"{family}_melodic"
    return out
