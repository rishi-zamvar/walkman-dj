"""Pure tempo/edge math for the sequencer. No I/O."""


def bpm_distance(b1, b2) -> float:
    """Min tempo gap allowing half/double-time equivalence. inf if either missing."""
    if not b1 or not b2:
        return float("inf")
    return min(abs(b1 - x) for x in (b2, b2 * 2, b2 / 2))


def bpm_window(b1, b2, pct=0.06, floor=4.0, widen=False) -> float:
    """Allowed pitch-fader range in BPM: pct of the slower track, floored."""
    w = max(pct * min(b1, b2), floor)
    return w * 1.25 if widen else w


def smoothness_cost(a, b, energy_n_a, energy_n_b, cfg):
    """Pairwise transition roughness (lower = smoother). Returns (cost, is_hard).
    is_hard=True means BPM out of the (possibly widened) window — the harmonic
    gate is applied separately in graph.py. Callers add HARD_CUT when is_hard."""
    ca = a.get("bpm_confidence") or 0.0
    cb = b.get("bpm_confidence") or 0.0
    widen = ca < cfg["bpm_conf_min"] or cb < cfg["bpm_conf_min"]
    win = bpm_window(a.get("bpm") or 0, b.get("bpm") or 0,
                     cfg["bpm_window_pct"], cfg["bpm_window_floor"], widen)
    dist = bpm_distance(a.get("bpm"), b.get("bpm"))
    if dist > win:
        return (0.0, True)
    cost = dist / win + cfg["w_energy_jump"] * abs(energy_n_a - energy_n_b)
    if widen:
        cost += cfg["w_unreliable_bpm"]
    return (cost, False)
