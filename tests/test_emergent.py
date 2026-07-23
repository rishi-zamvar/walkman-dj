from walkman_dj.emergent import build_sets


def _cfg():
    return {"bpm_window_pct": 0.06, "bpm_window_floor": 4.0, "bpm_conf_min": 0.6,
            "key_conf_min": 0.55, "w_energy_jump": 0.5, "w_unreliable_bpm": 0.2,
            "w_unreliable_key": 0.3, "allow_energy_boost": False, "w_genre_bridge": 0.5,
            "w_template": 1.0, "exact_tsp_max_n": 12, "community_resolution": 1.0,
            "set_min": 3, "set_max": 60, "louvain_seed": 1, "default_template": "single_peak"}


def _mk(fp, bpm, cam, e, g):
    return {"fingerprint": fp, "bpm": bpm, "camelot": cam, "energy": e,
            "bpm_confidence": 0.9, "key_confidence": 0.9, "canon_genre": g, "family": g}


def test_build_sets_groups_two_genres_and_orders():
    tracks = []
    for i in range(8):
        tracks.append(_mk(f"h{i}", 128 + (i % 3), "8A", 0.4 + 0.02 * i, "tech house"))
    for i in range(8):
        tracks.append(_mk(f"b{i}", 172 + (i % 3), "8A", 0.6 + 0.02 * i, "baile funk"))
    sets = build_sets(tracks, _cfg())
    assert len(sets) >= 2
    for s in sets:
        assert "name" in s and s["ordered"]
        assert len(s["hard"]) <= 1
