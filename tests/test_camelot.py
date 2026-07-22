from walkman_dj.camelot import to_camelot, compatible, camelot_edge_ok
def test_a_minor_is_8a():
    assert to_camelot("A", "minor") == "8A"
def test_c_major_is_8b():
    assert to_camelot("C", "major") == "8B"
def test_compatible_includes_pm1_and_relative():
    assert set(compatible("8A")) == {"8A", "7A", "9A", "8B"}
def test_wrap_around():
    assert set(compatible("1A")) == {"1A", "12A", "2A", "1B"}


def test_edge_same_code_ok():
    assert camelot_edge_ok("8A", "8A") is True


def test_edge_plus_minus_one_same_letter_ok():
    assert camelot_edge_ok("8A", "9A") is True
    assert camelot_edge_ok("8A", "7A") is True


def test_edge_wrap_12_to_1():
    assert camelot_edge_ok("12A", "1A") is True
    assert camelot_edge_ok("1A", "12A") is True


def test_edge_relative_major_minor_ok():
    assert camelot_edge_ok("8A", "8B") is True


def test_edge_two_apart_blocked_unless_boost():
    assert camelot_edge_ok("8A", "10A") is False
    assert camelot_edge_ok("8A", "10A", allow_boost=True) is True


def test_edge_different_number_and_letter_blocked():
    assert camelot_edge_ok("8A", "9B") is False


def test_edge_full_576_matrix_symmetry():
    codes = [f"{n}{l}" for n in range(1, 13) for l in "AB"]
    for x in codes:
        for y in codes:
            assert camelot_edge_ok(x, y) == camelot_edge_ok(y, x)
