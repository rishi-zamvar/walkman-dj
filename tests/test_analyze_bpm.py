from walkman_dj.analyze import correct_bpm
def test_double_time_folds_into_house_range():
    assert correct_bpm(63.0, (118,132)) == 126.0
def test_half_time_folds_up_for_dnb():
    assert correct_bpm(87.0, (168,176)) == 174.0
def test_in_range_untouched():
    assert correct_bpm(125.0, (118,132)) == 125.0
