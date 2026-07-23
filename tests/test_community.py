import networkx as nx
from walkman_dj.community import detect

def _two_cliques():
    G = nx.Graph()
    A = [f"a{i}" for i in range(10)]
    B = [f"b{i}" for i in range(10)]
    for grp in (A, B):
        for i in range(len(grp)):
            for j in range(i + 1, len(grp)):
                G.add_edge(grp[i], grp[j], weight=1.0)
    G.add_edge("a0", "b0", weight=0.05)   # one weak bridge
    return G, A, B

def test_detect_recovers_two_communities():
    G, A, B = _two_cliques()
    comms, loose = detect(G, set_min=3, set_max=50, resolution=1.0, seed=1)
    labels = [set(c) for c in comms]
    assert set(A) in labels and set(B) in labels
    assert loose == set()

def test_singletons_go_to_loose():
    G = nx.Graph()
    G.add_edge("a0", "a1", weight=1.0)
    G.add_node("x")                        # isolated node
    comms, loose = detect(G, set_min=2, set_max=50, resolution=1.0, seed=1)
    assert "x" in loose

def test_oversize_is_split():
    G = nx.complete_graph([f"n{i}" for i in range(40)])
    for u, v in G.edges():
        G[u][v]["weight"] = 1.0
    comms, loose = detect(G, set_min=3, set_max=15, resolution=1.0, seed=1)
    assert all(len(c) <= 15 for c in comms)
