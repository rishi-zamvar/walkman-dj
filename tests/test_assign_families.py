from walkman_dj.assign_families import build_track_records


def test_build_records_uses_resolve_family_and_canon():
    feat = {"fingerprint": "fp1", "bpm": 124, "energy": 0.55,
            "bass_weight": 0.5, "brightness": 0.5}
    rec = build_track_records([feat], {"fp1": {"genre": "Tech House", "conf": 0.9, "title": "x"}})
    assert rec[0]["label_family"] in ("tech_house", "HOUSE", "organic_house", "nu_disco")
    assert rec[0]["canon_genre"] == "tech house"
    assert rec[0]["fingerprint"] == "fp1"
