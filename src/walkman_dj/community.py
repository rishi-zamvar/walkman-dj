"""Louvain community detection with size shaping: split oversize communities at a
higher resolution, park un-mergeable singletons in a 'loose' bin."""
from networkx.algorithms.community import louvain_communities


def _louvain(G, resolution, seed):
    if G.number_of_edges() == 0:
        return [{n} for n in G.nodes()]
    return [set(c) for c in louvain_communities(G, weight="weight",
                                                resolution=resolution, seed=seed)]


def _split_oversize(G, comm, set_max, resolution, seed, depth=0):
    if len(comm) <= set_max or depth >= 4:
        return [comm]
    sub = G.subgraph(comm)
    parts = _louvain(sub, resolution * 1.6, seed)
    if len(parts) <= 1:                     # cannot split further
        return [comm]
    out = []
    for p in parts:
        out.extend(_split_oversize(G, p, set_max, resolution * 1.6, seed, depth + 1))
    return out


def detect(G, set_min=8, set_max=120, resolution=1.0, seed=1):
    """Return (communities, loose_set). communities are size-shaped; tracks that
    can't join a >=set_min community land in loose."""
    comms = []
    for c in _louvain(G, resolution, seed):
        comms.extend(_split_oversize(G, c, set_max, resolution, seed))
    big = [c for c in comms if len(c) >= set_min]
    small = [c for c in comms if len(c) < set_min]
    loose = set()
    for c in small:
        for node in c:
            best, bw = None, -1.0
            for nb in G.neighbors(node):
                w = G[node][nb].get("weight", 0)
                host = next((b for b in big if nb in b), None)
                if host is not None and w > bw:
                    best, bw = host, w
            if best is not None:
                best.add(node)
            else:
                loose.add(node)
    return big, loose
