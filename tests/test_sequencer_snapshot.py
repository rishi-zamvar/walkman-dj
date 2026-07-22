from walkman_dj.sequence import order_family


def _cfg():
    return {"bpm_window_pct": 0.06, "bpm_window_floor": 4.0, "bpm_conf_min": 0.6,
            "key_conf_min": 0.55, "w_energy_jump": 0.5, "w_unreliable_bpm": 0.2,
            "w_unreliable_key": 0.3, "allow_energy_boost": False,
            "exact_tsp_max_n": 12, "w_template": 1.0}


def test_wave_template_rises_and_falls():
    import random
    rng = random.Random(3)
    tracks = [{"fingerprint": f"t{i}", "bpm": 128 + rng.randint(-2, 2),
               "camelot": "8A", "energy": rng.random(),
               "bpm_confidence": 0.9, "key_confidence": 0.9} for i in range(14)]
    ordered, hard, iso = order_family(tracks, _cfg(), template="wave")
    es = [t["energy"] for t in ordered]
    maxima = [i for i in range(1, len(es) - 1) if es[i] > es[i - 1] and es[i] > es[i + 1]]
    assert len(maxima) >= 1
    assert len(iso) == 0   # all same key, close bpm -> fully connected
