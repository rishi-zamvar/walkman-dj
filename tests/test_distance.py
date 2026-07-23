import math
from walkman_dj.distance import bpm_distance, bpm_window, smoothness_cost


def test_bpm_distance_direct():
    assert bpm_distance(128.0, 130.0) == 2.0


def test_bpm_distance_double_time_folds_to_zero():
    # 150 trap over a 75-feel groove is 0 apart, not 75
    assert bpm_distance(150.0, 75.0) == 0.0
    assert bpm_distance(174.0, 87.0) == 0.0


def test_bpm_distance_missing_is_inf():
    assert math.isinf(bpm_distance(0, 128))
    assert math.isinf(bpm_distance(128, None))


def test_bpm_window_floor_and_pct():
    assert bpm_window(60, 60, pct=0.06, floor=4.0) == 4.0      # 0.06*60=3.6 < floor
    assert bpm_window(200, 200, pct=0.06, floor=4.0) == 12.0   # 0.06*200=12 > floor


def test_bpm_window_widen():
    assert bpm_window(200, 200, pct=0.06, floor=4.0, widen=True) == 15.0


def _cfg():
    return {"bpm_window_pct": 0.06, "bpm_window_floor": 4.0,
            "bpm_conf_min": 0.6, "w_energy_jump": 0.5,
            "w_unreliable_bpm": 0.2}


def test_smoothness_low_for_close_bpm_and_energy():
    a = {"bpm": 128, "bpm_confidence": 0.9}
    b = {"bpm": 129, "bpm_confidence": 0.9}
    c, hard = smoothness_cost(a, b, 0.5, 0.5, _cfg())
    assert hard is False
    assert c < 0.3


def test_smoothness_hard_when_bpm_out_of_window():
    a = {"bpm": 128, "bpm_confidence": 0.9}
    b = {"bpm": 150, "bpm_confidence": 0.9}   # 22 apart, window ~7.7
    c, hard = smoothness_cost(a, b, 0.5, 0.5, _cfg())
    assert hard is True


def test_smoothness_low_bpm_conf_widens_and_penalizes():
    a = {"bpm": 128, "bpm_confidence": 0.4}   # unreliable
    b = {"bpm": 137, "bpm_confidence": 0.9}   # 9 apart; base window 7.7, widened 9.6 -> fits
    c, hard = smoothness_cost(a, b, 0.5, 0.5, _cfg())
    assert hard is False
    assert c >= 0.2   # includes w_unreliable_bpm
