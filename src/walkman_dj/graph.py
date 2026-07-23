"""Penalty-completed pairwise cost matrix + sparse Dijkstra. Non-mixable pairs
carry HARD_CUT so ordering (greedy/2-opt/held-karp) is always well-defined."""
import heapq
from walkman_dj.camelot import camelot_edge_ok
from walkman_dj.distance import smoothness_cost

HARD_CUT = 1000.0


def _key_reliable(t, cfg):
    return (t.get("key_agree", True) is not False) and (t.get("key_confidence") or 0) >= cfg["key_conf_min"]


def pair_matrix(tracks, _energy_by_key, energy_n, cfg):
    """n x n cost. cost[i][j] = smoothness (+ penalties) if mixable, else HARD_CUT.
    energy_n: list aligned with tracks (rescaled energies)."""
    n = len(tracks)
    m = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            a, b = tracks[i], tracks[j]
            soft, hard_bpm = smoothness_cost(a, b, energy_n[i], energy_n[j], cfg)
            key_ok = camelot_edge_ok(a.get("camelot"), b.get("camelot"), cfg["allow_energy_boost"])
            key_unrel = not (_key_reliable(a, cfg) and _key_reliable(b, cfg))
            if hard_bpm or (not key_ok and not key_unrel):
                m[i][j] = HARD_CUT + soft
                continue
            cost = soft
            if not key_ok and key_unrel:
                cost += cfg["w_unreliable_key"]     # allowed but risky key
            m[i][j] = cost
    return m


def dijkstra(pair, src, dst):
    """Shortest smooth bridge over REAL edges only (HARD_CUT edges excluded)."""
    n = len(pair)
    dist = [float("inf")] * n
    prev = [-1] * n
    dist[src] = 0.0
    pq = [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u]:
            continue
        if u == dst:
            break
        for v in range(n):
            if v == u or pair[u][v] >= HARD_CUT:
                continue
            nd = d + pair[u][v]
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    if dist[dst] == float("inf"):
        return None
    path = []
    x = dst
    while x != -1:
        path.append(x)
        x = prev[x]
    return path[::-1]
