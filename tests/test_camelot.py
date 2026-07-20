from walkman_dj.camelot import to_camelot, compatible
def test_a_minor_is_8a():
    assert to_camelot("A", "minor") == "8A"
def test_c_major_is_8b():
    assert to_camelot("C", "major") == "8B"
def test_compatible_includes_pm1_and_relative():
    assert set(compatible("8A")) == {"8A", "7A", "9A", "8B"}
def test_wrap_around():
    assert set(compatible("1A")) == {"1A", "12A", "2A", "1B"}
