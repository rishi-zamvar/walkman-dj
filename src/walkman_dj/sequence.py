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
    # Calibrated to the real distribution: RMS energy clusters ~0.47-0.72 across
    # families, so 0.7/0.4 never triggered. Tertile-ish split on measured energy.
    m = float(np.median(list(energies)))
    if m >= 0.55:
        return "hype"
    if m <= 0.50:
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


# ---------------------------------------------------------------------------
# Set Sequencer: penalty-completed TSP-path ordering with energy templates.
# ---------------------------------------------------------------------------
import time as _time
import statistics as _stats
from walkman_dj.templates import target as _target
from walkman_dj.graph import pair_matrix as _pm, HARD_CUT as _HC
from walkman_dj.family import rescale_energy


def path_cost(order, pair, energy_n, template, w_template):
    """Total open-path cost: sum of pairwise + per-position template deviation."""
    n = len(order)
    c = sum(pair[order[i]][order[i + 1]] for i in range(n - 1))
    if w_template and n > 1:
        c += sum(w_template * abs(energy_n[order[p]] - _target(template, p / (n - 1)))
                 for p in range(n))
    return c


def greedy_order(pair, en, template, w_template, start=0):
    """Nearest-neighbor on the penalty-completed matrix, biased toward the template
    energy at each slot."""
    n = len(pair)
    unvisited = set(range(n)) - {start}
    order = [start]
    while unvisited:
        cur = order[-1]
        pos = len(order)
        tgt = _target(template, pos / (n - 1)) if (w_template and n > 1) else None

        def _c(k):
            c = pair[cur][k]
            if tgt is not None:
                c += w_template * abs(en[k] - tgt)
            return c

        nxt = min(unvisited, key=_c)
        order.append(nxt)
        unvisited.discard(nxt)
    return order


def two_opt(order, pair, energy_n, template, w_template, max_passes=200, time_budget=2.0):
    """Reverse segments while total (pairwise+template) cost strictly drops."""
    best = list(order)
    bc = path_cost(best, pair, energy_n, template, w_template)
    deadline = _time.monotonic() + time_budget
    improved = True
    passes = 0
    n = len(best)
    while improved and passes < max_passes:
        improved = False
        passes += 1
        for i in range(0, n - 1):      # include position 0 (prefix reversals) for open paths
            if _time.monotonic() > deadline:
                return best
            for k in range(i + 1, n):
                cand = best[:i] + best[i:k + 1][::-1] + best[k + 1:]
                cc = path_cost(cand, pair, energy_n, template, w_template)
                if cc < bc - 1e-9:
                    best, bc, improved = cand, cc, True
    return best


def held_karp(pair, energy_n, template, w_template):
    """Exact minimum-cost open Hamiltonian path via subset DP. Position within the
    path == popcount(mask)-1, so the per-position template term folds in exactly.
    Use only for small n (<= ~12); 2^n * n^2."""
    n = len(pair)
    if n <= 1:
        return list(range(n))
    INF = float("inf")

    def tmpl(idx, pos):
        if not w_template or n <= 1:
            return 0.0
        return w_template * abs(energy_n[idx] - _target(template, pos / (n - 1)))

    dp = [[INF] * n for _ in range(1 << n)]
    par = [[-1] * n for _ in range(1 << n)]
    for j in range(n):
        dp[1 << j][j] = tmpl(j, 0)              # single-node path, position 0
    for mask in range(1 << n):
        pos = bin(mask).count("1")             # nodes already placed
        for j in range(n):
            if not (mask >> j) & 1 or dp[mask][j] == INF:
                continue
            base = dp[mask][j]
            for k in range(n):
                if (mask >> k) & 1:
                    continue
                nm = mask | (1 << k)
                nc = base + pair[j][k] + tmpl(k, pos)   # k lands at slot `pos`
                if nc < dp[nm][k]:
                    dp[nm][k] = nc
                    par[nm][k] = j
    full = (1 << n) - 1
    end = min(range(n), key=lambda j: dp[full][j])
    order = []
    mask, j = full, end
    while j != -1:
        order.append(j)
        pj = par[mask][j]
        mask ^= (1 << j)
        j = pj
    return order[::-1]


def order_family(tracks, cfg, template="single_peak"):
    """Order one family. Returns (ordered_tracks, hard_transitions, isolated_fps).
    - isolated: tracks with no real (non-HARD_CUT) edge to any other -> appended, not ordered.
    - hard_transitions: residual HARD_CUT steps in the final order (DJ handles manually)."""
    if len(tracks) <= 1:
        return (list(tracks), [], [])
    raw = {t["fingerprint"]: (t.get("energy") or 0.0) for t in tracks}
    en_map = rescale_energy(raw)
    en = [en_map[t["fingerprint"]] for t in tracks]
    pair = _pm(tracks, None, en, cfg)

    keep_idx, iso_idx = [], []
    for i in range(len(tracks)):
        has_edge = any(i != j and pair[i][j] < _HC for j in range(len(tracks)))
        (keep_idx if has_edge else iso_idx).append(i)

    if len(keep_idx) <= 1:
        ordered = [tracks[i] for i in keep_idx]
        return (ordered, [], [tracks[i]["fingerprint"] for i in iso_idx])

    sub = [[pair[i][j] for j in keep_idx] for i in keep_idx]
    subtr = [tracks[i] for i in keep_idx]
    suben = [en[i] for i in keep_idx]
    w_t = cfg["w_template"]

    start = min(range(len(subtr)), key=lambda k: abs(suben[k] - _target(template, 0.0)))
    if len(subtr) <= cfg["exact_tsp_max_n"]:
        order = held_karp(sub, suben, template, w_t)
    else:
        order = greedy_order(sub, suben, template, w_t, start=start)
        order = two_opt(order, sub, suben, template, w_t)

    ordered = [subtr[i] for i in order]
    hard = []
    for p in range(len(order) - 1):
        if sub[order[p]][order[p + 1]] >= _HC:
            hard.append({"from": subtr[order[p]]["fingerprint"],
                         "to": subtr[order[p + 1]]["fingerprint"], "pos": p})
    return (ordered, hard, [tracks[i]["fingerprint"] for i in iso_idx])


def stitch_families(family_orders):
    """Concatenate per-family ordered lists into one macro arc, warm->peak by
    median energy. Returns (sequence, boundaries) where boundaries maps family ->
    start index."""
    def med(tracks):
        return _stats.median([t.get("energy") or 0.0 for t in tracks]) if tracks else 0.0
    ordered_fams = sorted(family_orders, key=lambda f: med(family_orders[f]))
    seq, boundaries = [], {}
    for fam in ordered_fams:
        boundaries[fam] = len(seq)
        seq.extend(family_orders[fam])
    return seq, boundaries


def insert_into_order(existing_ordered, newcomers, cfg, template="single_peak"):
    """Stability: keep the frozen order of existing tracks; splice each newcomer
    into its lowest-cost gap. Does NOT re-solve the whole set."""
    seq = list(existing_ordered)
    for nt in newcomers:
        best_pos, best_delta = len(seq), float("inf")
        for pos in range(len(seq) + 1):
            trial = seq[:pos] + [nt] + seq[pos:]
            raw = {t["fingerprint"]: (t.get("energy") or 0.0) for t in trial}
            en_map = rescale_energy(raw)
            en = [en_map[t["fingerprint"]] for t in trial]
            pair = _pm(trial, None, en, cfg)
            cost = path_cost(list(range(len(trial))), pair, en, template, cfg["w_template"])
            if cost < best_delta:
                best_delta, best_pos = cost, pos
        seq = seq[:best_pos] + [nt] + seq[best_pos:]
    return seq
