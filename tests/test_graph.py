from walkman_dj.graph import HARD_CUT, pair_matrix, dijkstra


def _cfg():
    return {"bpm_window_pct": 0.06, "bpm_window_floor": 4.0, "bpm_conf_min": 0.6,
            "key_conf_min": 0.55, "w_energy_jump": 0.5, "w_unreliable_bpm": 0.2,
            "w_unreliable_key": 0.3, "allow_energy_boost": False}


def test_compatible_pair_is_soft():
    tr = [{"bpm": 128, "camelot": "8A", "bpm_confidence": 0.9, "key_confidence": 0.9},
          {"bpm": 129, "camelot": "8A", "bpm_confidence": 0.9, "key_confidence": 0.9}]
    m = pair_matrix(tr, None, [0.5, 0.5], _cfg())
    assert m[0][1] < HARD_CUT


def test_key_clash_is_hard_cut():
    tr = [{"bpm": 128, "camelot": "8A", "bpm_confidence": 0.9, "key_confidence": 0.9},
          {"bpm": 129, "camelot": "3B", "bpm_confidence": 0.9, "key_confidence": 0.9}]
    m = pair_matrix(tr, None, [0.5, 0.5], _cfg())
    assert m[0][1] >= HARD_CUT


def test_bpm_gap_is_hard_cut():
    tr = [{"bpm": 128, "camelot": "8A", "bpm_confidence": 0.9, "key_confidence": 0.9},
          {"bpm": 150, "camelot": "8A", "bpm_confidence": 0.9, "key_confidence": 0.9}]
    m = pair_matrix(tr, None, [0.5, 0.5], _cfg())
    assert m[0][1] >= HARD_CUT


def test_unreliable_key_penalized_not_blocked():
    tr = [{"bpm": 128, "camelot": "8A", "bpm_confidence": 0.9, "key_confidence": 0.3},
          {"bpm": 129, "camelot": "3B", "bpm_confidence": 0.9, "key_confidence": 0.3}]
    m = pair_matrix(tr, None, [0.5, 0.5], _cfg())
    assert m[0][1] < HARD_CUT
    assert m[0][1] >= 0.3


def test_dijkstra_bridges_via_real_edges():
    pair = [[0, 1, HARD_CUT, HARD_CUT],
            [1, 0, 1, HARD_CUT],
            [HARD_CUT, 1, 0, 1],
            [HARD_CUT, HARD_CUT, 1, 0]]
    assert dijkstra(pair, 0, 3) == [0, 1, 2, 3]
