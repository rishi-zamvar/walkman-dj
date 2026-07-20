"""Within-family micro-genre split by a measured axis (deterministic thresholds).

Each splittable family declares (axis, threshold). Tracks on/above the threshold
get the '_rage' (high) label, below get '_melodic' (low). The is_vocal flag is
intentionally NOT used — it over-fires ~96% and can't discriminate. Display
names for each sub-family live in pipeline.DISPLAY.
"""

# family -> (axis, threshold).  hi side -> _rage, lo side -> _melodic
SPLIT_RULES = {
    "opium_rage":   ("energy", 0.55),   # Hard / Melodic
    "atlanta_trap": ("energy", 0.55),   # Hard / Melodic
    "melodic_trap": ("energy", 0.55),   # Hard / Soft
    "toronto":      ("energy", 0.55),   # Uptempo / Late Night
    "alt_rnb":      ("bpm",    92.0),   # Upbeat / Bedroom
    "lyrical_soul": ("energy", 0.55),   # Bars / Mellow
    "tech_house":   ("bpm",    127.0),  # Peak / Deep
}


def split_family(family, tracks) -> dict:
    """Map fingerprint -> sub-family label. Non-splittable families pass through."""
    rule = SPLIT_RULES.get(family)
    out = {}
    for t in tracks:
        fp = t["fingerprint"]
        if not rule:
            out[fp] = family
            continue
        axis, thr = rule
        default = 0.5 if axis == "energy" else 0.0
        val = t.get(axis)
        val = default if val is None else val
        out[fp] = f"{family}_rage" if val >= thr else f"{family}_melodic"
    return out
