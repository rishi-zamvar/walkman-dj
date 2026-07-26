"""Pure unit tests for tag cleaning + canonical path (fast, no I/O)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import crate_tags as ct
from walkman_dj import crate


# ---- tag cleaning ----

def test_strips_acquisition_junk():
    assert ct.clean_title("Song (youtube)") == "Song"
    assert ct.clean_title("Song [www.slider.kz]") == "Song"
    assert ct.clean_title("Song (0fficial Mp3)") == "Song"
    assert ct.clean_title("Song   [FLAC 24bit]") == "Song"


def test_keeps_meaningful_parens():
    assert ct.clean_title("Song (feat. X)") == "Song (feat. X)"
    assert ct.clean_title("Song (Extended Mix)") == "Song (Extended Mix)"


def test_recovers_artist_title_from_stem():
    assert ct.split_stem("04 - Artist Name - Some Title") == ("Artist Name", "Some Title")
    assert ct.split_stem("Some Title") == (None, "Some Title")


def test_noop_on_clean():
    assert ct.clean_title("Clean Title") == "Clean Title"


# ---- canonical path ----

def test_album_track_goes_to_album_folder():
    p = crate.canonical_path("/LIB", "/tmp/x.flac",
        {"albumartist": "Ken Carson", "album": "Teen X", "title": "Yale", "artist": "Ken Carson"}, tracknum=2)
    assert p == "/LIB/Ken Carson/Teen X/02 - Yale.flac"


def test_single_no_album_goes_to_singles():
    p = crate.canonical_path("/LIB", "/tmp/x.mp3",
        {"albumartist": "", "album": "", "title": "Some Bootleg", "artist": "DJ X"}, tracknum=None)
    assert p == "/LIB/_Singles/DJ X/Some Bootleg.mp3"


def test_sanitizes_slashes():
    p = crate.canonical_path("/LIB", "/tmp/x.flac",
        {"albumartist": "AC/DC", "album": "Back/Black", "title": "T", "artist": "AC/DC"}, tracknum=1)
    assert p == "/LIB/AC-DC/Back-Black/01 - T.flac"
