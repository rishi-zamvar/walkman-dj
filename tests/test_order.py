from walkman_dj.sequence import order_playlist
def test_hype_starts_high_and_ends_on_peak():
    tracks=[{"fingerprint":str(i),"camelot":"8A","bpm":124+i%3,"is_vocal":0,
             "energy":e,"key_confidence":0.9} for i,e in
            enumerate([0.6,0.95,0.7,0.99,0.65,0.8])]
    seq=order_playlist(tracks,"hype")
    assert seq[0]["energy"]>=0.8
    assert seq[-1]["energy"]==max(t["energy"] for t in tracks)
def test_soul_ends_soft():
    tracks=[{"fingerprint":str(i),"camelot":"8A","bpm":90,"is_vocal":1,
             "energy":e,"key_confidence":0.9} for i,e in
            enumerate([0.2,0.5,0.35,0.7,0.25])]
    seq=order_playlist(tracks,"soul")
    assert seq[-1]["energy"]==min(t["energy"] for t in tracks)


from walkman_dj.sequence import (path_cost, greedy_order, two_opt, held_karp,
                                 order_family)


def test_path_cost_sums_pairwise_plus_template():
    pair = [[0, 1, 5], [1, 0, 1], [5, 1, 0]]
    en = [0.0, 0.5, 1.0]
    c_asc = path_cost([0, 1, 2], pair, en, "monotonic", 1.0)
    c_desc = path_cost([2, 1, 0], pair, en, "monotonic", 1.0)
    assert c_asc < c_desc


def test_greedy_prefers_cheap_edges():
    pair = [[0, 1, 9], [1, 0, 1], [9, 1, 0]]
    order = greedy_order(pair, en=[0.5, 0.5, 0.5], template="monotonic",
                         w_template=0.0, start=0)
    assert order == [0, 1, 2]


def test_two_opt_never_worsens():
    import random
    rng = random.Random(0)
    n = 8
    pts = [rng.random() for _ in range(n)]
    pair = [[abs(pts[i] - pts[j]) for j in range(n)] for i in range(n)]
    en = [0.5] * n
    start = list(range(n))
    rng.shuffle(start)
    before = path_cost(start, pair, en, "monotonic", 0.0)
    after_order = two_opt(start, pair, en, "monotonic", 0.0)
    after = path_cost(after_order, pair, en, "monotonic", 0.0)
    assert after <= before + 1e-9


def test_held_karp_optimal_bounds_greedy_plus_2opt():
    import random
    rng = random.Random(1)
    for trial in range(20):
        n = rng.randint(4, 9)
        pts = [rng.random() for _ in range(n)]
        pair = [[abs(pts[i] - pts[j]) for j in range(n)] for i in range(n)]
        en = [0.5] * n
        exact = held_karp(pair, en, "monotonic", 0.0)
        ec = path_cost(exact, pair, en, "monotonic", 0.0)
        g = greedy_order(pair, en, "monotonic", 0.0, start=0)
        g = two_opt(g, pair, en, "monotonic", 0.0)
        gc = path_cost(g, pair, en, "monotonic", 0.0)
        assert ec <= gc + 1e-9              # Held-Karp is optimal
        assert gc <= 1.5 * ec + 1e-9        # greedy+2opt within 2-opt's open-path bound


def _cfg_of():
    return {"bpm_window_pct": 0.06, "bpm_window_floor": 4.0, "bpm_conf_min": 0.6,
            "key_conf_min": 0.55, "w_energy_jump": 0.5, "w_unreliable_bpm": 0.2,
            "w_unreliable_key": 0.3, "allow_energy_boost": False,
            "exact_tsp_max_n": 12, "w_template": 1.0}


def test_order_family_returns_all_tracks_and_flags_hard():
    tracks = [
        {"fingerprint": "a", "bpm": 128, "camelot": "8A", "energy": 0.40,
         "bpm_confidence": 0.9, "key_confidence": 0.9},
        {"fingerprint": "b", "bpm": 129, "camelot": "8A", "energy": 0.55,
         "bpm_confidence": 0.9, "key_confidence": 0.9},
        {"fingerprint": "c", "bpm": 128, "camelot": "9A", "energy": 0.70,
         "bpm_confidence": 0.9, "key_confidence": 0.9},
        {"fingerprint": "z", "bpm": 175, "camelot": "3B", "energy": 0.72,
         "bpm_confidence": 0.9, "key_confidence": 0.9},
    ]
    ordered, hard, isolated = order_family(tracks, _cfg_of(), template="single_peak")
    assert {t["fingerprint"] for t in ordered} | set(isolated) == {"a", "b", "c", "z"}
    assert "z" in isolated or any(h["to"] == "z" or h["from"] == "z" for h in hard)
