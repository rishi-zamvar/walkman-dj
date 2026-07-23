from walkman_dj.libgraph import candidate_pairs

def test_candidates_include_bpm_neighbors():
    tr = [{"fingerprint": "a", "bpm": 128, "camelot": "8A"},
          {"fingerprint": "b", "bpm": 130, "camelot": "8A"},
          {"fingerprint": "c", "bpm": 200, "camelot": "8A"}]
    p = candidate_pairs(tr, window_pct=0.06, floor=4.0)
    assert (0, 1) in p or (1, 0) in p
    assert (0, 2) not in p and (2, 0) not in p

def test_candidates_fold_half_double():
    tr = [{"fingerprint": "a", "bpm": 150, "camelot": "8A"},
          {"fingerprint": "b", "bpm": 75, "camelot": "8A"}]
    p = candidate_pairs(tr, window_pct=0.06, floor=4.0)
    assert (0, 1) in p or (1, 0) in p


from walkman_dj.libgraph import build_graph

def _cfg():
    return {"bpm_window_pct": 0.06, "bpm_window_floor": 4.0, "bpm_conf_min": 0.6,
            "key_conf_min": 0.55, "w_energy_jump": 0.5, "w_unreliable_bpm": 0.2,
            "w_unreliable_key": 0.3, "allow_energy_boost": False, "w_genre_bridge": 0.5}

def _t(fp, bpm, cam, e, genre):
    return {"fingerprint": fp, "bpm": bpm, "camelot": cam, "energy": e,
            "bpm_confidence": 0.9, "key_confidence": 0.9, "canon_genre": genre,
            "family": genre.upper()}

def test_graph_connects_compatible_same_genre_strongly():
    tr = [_t("a", 128, "8A", 0.5, "tech house"), _t("b", 129, "8A", 0.5, "tech house"),
          _t("c", 128, "8A", 0.5, "hip-hop")]
    G = build_graph(tr, _cfg())
    assert G.has_edge("a", "b") and G.has_edge("a", "c")
    assert G["a"]["b"]["weight"] > G["a"]["c"]["weight"]

def test_graph_no_edge_when_key_clashes():
    tr = [_t("a", 128, "8A", 0.5, "tech house"), _t("b", 129, "3B", 0.5, "tech house")]
    G = build_graph(tr, _cfg())
    assert not G.has_edge("a", "b")


def test_hard_genre_scope_drops_cross_family_edges():
    cfg = _cfg()
    cfg["genre_scope"] = "hard"
    tr = [_t("a", 128, "8A", 0.5, "tech house"),   # family "TECH HOUSE"
          _t("b", 129, "8A", 0.5, "hip-hop")]       # family "HIP-HOP"
    G = build_graph(tr, cfg)
    assert not G.has_edge("a", "b")                 # cross-genre edge gated out


def test_soft_scope_keeps_cross_family_edge():
    cfg = _cfg()  # no genre_scope -> soft (default)
    tr = [_t("a", 128, "8A", 0.5, "tech house"),
          _t("b", 129, "8A", 0.5, "hip-hop")]
    G = build_graph(tr, cfg)
    assert G.has_edge("a", "b")
