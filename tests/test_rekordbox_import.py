import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import rekordbox_import as ri


def test_camelot_passthrough():
    assert ri.to_camelot("4A") == "4A"
    assert ri.to_camelot("12B") == "12B"


def test_musical_to_camelot():
    assert ri.to_camelot("Am") == "8A"
    assert ri.to_camelot("C") == "8B"
    assert ri.to_camelot("F#m") == "11A"
    assert ri.to_camelot("Ebm") == "2A"


def test_unknown_or_missing():
    assert ri.to_camelot(None) is None
    assert ri.to_camelot("") is None
    assert ri.to_camelot("nonsense") is None
