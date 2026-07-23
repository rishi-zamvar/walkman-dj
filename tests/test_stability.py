from walkman_dj.sequence import insert_into_order, order_family


def _cfg():
    return {"bpm_window_pct": 0.06, "bpm_window_floor": 4.0, "bpm_conf_min": 0.6,
            "key_conf_min": 0.55, "w_energy_jump": 0.5, "w_unreliable_bpm": 0.2,
            "w_unreliable_key": 0.3, "allow_energy_boost": False,
            "exact_tsp_max_n": 12, "w_template": 1.0}


def _mk(fp, bpm, cam, e):
    return {"fingerprint": fp, "bpm": bpm, "camelot": cam, "energy": e,
            "bpm_confidence": 0.9, "key_confidence": 0.9}


def test_insert_preserves_existing_relative_order():
    existing = [_mk("a", 126, "8A", 0.3), _mk("b", 128, "8A", 0.5), _mk("c", 130, "9A", 0.7)]
    ordered, _, _ = order_family(existing, _cfg(), template="single_peak")
    base_fps = [t["fingerprint"] for t in ordered]
    newcomers = [_mk("d", 129, "8A", 0.6)]
    merged = insert_into_order(ordered, newcomers, _cfg(), template="single_peak")
    merged_fps = [t["fingerprint"] for t in merged]
    assert set(base_fps) | {"d"} == set(merged_fps)
    filtered = [fp for fp in merged_fps if fp in base_fps]
    assert filtered == base_fps
