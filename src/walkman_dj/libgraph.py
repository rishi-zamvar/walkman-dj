"""Whole-library transition graph: BPM-bucketed candidate generation (never
all-pairs) + genre-weighted affinity edges, for community detection."""
import bisect
import networkx as nx
from walkman_dj.camelot import camelot_edge_ok
from walkman_dj.distance import bpm_distance, bpm_window, smoothness_cost
from walkman_dj.genre import jaccard
from walkman_dj.family import rescale_energy


def candidate_pairs(tracks, window_pct=0.06, floor=4.0):
    """Set of (i,j) index pairs whose BPMs are within the pitch window, including
    half/double-time equivalence — via bisect on a sorted BPM index, so it is
    ~O(n·neighbourhood), not O(n²). i<j guaranteed."""
    n = len(tracks)
    idx = sorted(range(n), key=lambda k: tracks[k].get("bpm") or 0.0)
    bpms = [tracks[k].get("bpm") or 0.0 for k in idx]
    out = set()
    for pos in range(n):
        i = idx[pos]
        b = bpms[pos]
        if not b:
            continue
        for center in (b, b * 2, b / 2):
            if center <= 0:
                continue
            win = bpm_window(center, center, window_pct, floor) + 1e-6
            lo = bisect.bisect_left(bpms, center - win)
            hi = bisect.bisect_right(bpms, center + win)
            for q in range(lo, hi):
                j = idx[q]
                if j == i:
                    continue
                if bpm_distance(b, tracks[j].get("bpm")) <= max(
                        bpm_window(b, tracks[j].get("bpm") or 0, window_pct, floor),
                        bpm_window(tracks[j].get("bpm") or 0, b, window_pct, floor)):
                    out.add((i, j) if i < j else (j, i))
    return out


def _key_reliable(t, cfg):
    return (t.get("key_agree", True) is not False) and (t.get("key_confidence") or 0) >= cfg["key_conf_min"]


def build_graph(tracks, cfg):
    """networkx.Graph over tracks. Edge iff harmonic + BPM compatible; weight is
    affinity = 1/(1+cost), cost = smoothness + w_genre_bridge*(1 - genre_jaccard).
    Higher weight = stronger tie (Louvain maximises intra-community weight)."""
    raw = {t["fingerprint"]: (t.get("energy") or 0.0) for t in tracks}
    en = rescale_energy(raw)
    G = nx.Graph()
    for t in tracks:
        G.add_node(t["fingerprint"], family=t.get("family"), bpm=t.get("bpm"),
                   energy=t.get("energy"), camelot=t.get("camelot"),
                   canon=t.get("canon_genre"))
    for i, j in candidate_pairs(tracks, cfg["bpm_window_pct"], cfg["bpm_window_floor"]):
        a, b = tracks[i], tracks[j]
        key_ok = camelot_edge_ok(a.get("camelot"), b.get("camelot"), cfg["allow_energy_boost"])
        key_unrel = not (_key_reliable(a, cfg) and _key_reliable(b, cfg))
        if not key_ok and not key_unrel:
            continue
        soft, hard_bpm = smoothness_cost(a, b, en[a["fingerprint"]], en[b["fingerprint"]], cfg)
        if hard_bpm:
            continue
        cost = soft
        if not key_ok and key_unrel:
            cost += cfg["w_unreliable_key"]
        cost += cfg["w_genre_bridge"] * (1.0 - jaccard(a.get("canon_genre") or "",
                                                        b.get("canon_genre") or ""))
        G.add_edge(a["fingerprint"], b["fingerprint"], weight=1.0 / (1.0 + cost), cost=cost)
    return G
