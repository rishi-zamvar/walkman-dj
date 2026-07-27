import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import genre_map as gm


def test_normalize_collapses_rap_spellings():
    for s in ["hip hop", "rap", "rap/hip hop", "hip-hop/rap", "Hip-Hop", "hip-hop"]:
        assert gm.normalize(s) == "Hip-Hop"


def test_normalize_known_genres():
    assert gm.normalize("contemporary r&b") == "R&B"
    assert gm.normalize("r & b") == "R&B"
    assert gm.normalize("drum & bass") == "Drum & Bass"
    assert gm.normalize("uk garage") == "UK Garage"
    assert gm.normalize("tech house") == "Tech House"
    assert gm.normalize("reggaeton") == "Reggaeton"


def test_normalize_unknown_returns_none():
    assert gm.normalize("zzznotagenre") is None
    assert gm.normalize("") is None
    assert gm.normalize(None) is None


def test_map_discogs_style_and_parent():
    assert gm.map_discogs("Electronic---Jersey Club") == ("Jersey Club", "Jersey Club")
    g, sub = gm.map_discogs("Hip Hop---Trap")
    assert g == "Trap" and sub == "Trap"
    g, _ = gm.map_discogs("Electronic---Some Unmapped Style")
    assert g == "Electronic"


def test_generic_set():
    assert "Electronic" in gm.GENERIC and "Hip-Hop" not in gm.GENERIC
