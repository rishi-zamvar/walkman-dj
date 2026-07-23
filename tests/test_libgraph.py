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
