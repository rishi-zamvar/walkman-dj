import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "overnight"))
from tune_sets import score_config

def _sets():
    return [{"n": 40, "hard": [], "ordered": [1] * 40, "name": "A"},
            {"n": 80, "hard": [{"pos": 3}], "ordered": [1] * 80, "name": "B"},
            {"n": 5, "hard": [{"pos": 1}, {"pos": 2}], "ordered": [1] * 5, "name": "C"}]

def test_score_rewards_clean_in_range_sets():
    s = score_config(_sets(), set_min=8, set_max=120)
    assert s["n_sets"] == 3
    assert abs(s["pct_clean"] - 2 / 3) < 1e-9   # two of three have <=1 hard cut
    assert s["p50_size"] > 0
