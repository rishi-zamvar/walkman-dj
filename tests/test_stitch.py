from walkman_dj.sequence import stitch_families


def test_stitch_orders_families_by_median_energy_ascending():
    fam_orders = {
        "HOT": [{"fingerprint": "h1", "energy": 0.9}, {"fingerprint": "h2", "energy": 0.85}],
        "WARM": [{"fingerprint": "w1", "energy": 0.3}, {"fingerprint": "w2", "energy": 0.35}],
    }
    seq, boundaries = stitch_families(fam_orders)
    assert [t["fingerprint"] for t in seq][:2] == ["w1", "w2"]   # warm first
    assert "WARM" in boundaries and "HOT" in boundaries
