import random, time
def test_bucketed_candidates_are_subquadratic():
    from walkman_dj.libgraph import candidate_pairs
    rng = random.Random(0)
    tracks = [{"fingerprint": f"t{i}", "bpm": rng.choice([124, 128, 130, 136, 150, 174]) + rng.random(),
               "camelot": f"{rng.randint(1,12)}A"} for i in range(2000)]
    pairs = candidate_pairs(tracks, window_pct=0.06, floor=4.0)
    assert len(pairs) < 2000 * 2000 // 4
    from walkman_dj.distance import bpm_distance, bpm_window
    for i, j in list(pairs)[:500]:
        d = bpm_distance(tracks[i]["bpm"], tracks[j]["bpm"])
        assert d <= bpm_window(tracks[i]["bpm"], tracks[j]["bpm"]) + 1e-9
