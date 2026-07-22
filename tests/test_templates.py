import pytest
from walkman_dj.templates import target, TEMPLATES, local_maxima


@pytest.mark.parametrize("name", list(TEMPLATES))
def test_range_bounded(name):
    for i in range(21):
        y = target(name, i / 20)
        assert 0.0 <= y <= 1.0


def test_single_peak_has_one_interior_max():
    xs = [i / 40 for i in range(41)]
    ys = [target("single_peak", x) for x in xs]
    assert len(local_maxima(ys)) == 1


def test_wave_has_at_least_two_maxima():
    xs = [i / 40 for i in range(41)]
    ys = [target("wave", x) for x in xs]
    assert len(local_maxima(ys)) >= 2


def test_monotonic_is_nondecreasing():
    xs = [i / 40 for i in range(41)]
    ys = [target("monotonic", x) for x in xs]
    assert all(ys[i] <= ys[i + 1] + 1e-9 for i in range(len(ys) - 1))


def test_plateau_drop_ends_low():
    assert target("plateau_drop", 1.0) < target("plateau_drop", 0.5)
