import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import genre_label as G

AUDIO = {"genre": "Jersey Club", "subgenre": "Jersey Club", "genre_confidence": 0.4}


def test_remix_prefers_audio_over_tag():
    r = G.decide("Pop", AUDIO, remix=True)
    assert r["genre"] == "Jersey Club" and r["genre_source"] == "audio"


def test_specific_tag_wins_when_not_remix():
    r = G.decide("Hip-Hop", AUDIO, remix=False)
    assert r["genre"] == "Hip-Hop" and r["genre_source"] == "tag"


def test_generic_tag_refined_by_audio():
    r = G.decide("Electronic", AUDIO, remix=False)
    assert r["genre"] == "Jersey Club" and r["genre_source"] == "audio"


def test_no_tag_uses_audio():
    r = G.decide(None, AUDIO, remix=False)
    assert r["genre"] == "Jersey Club" and r["genre_source"] == "audio"


def test_low_conf_audio_falls_back_to_generic_tag():
    weak = {"genre": "House", "subgenre": "House", "genre_confidence": 0.02}
    r = G.decide("Electronic", weak, remix=False)
    assert r["genre"] == "Electronic" and r["genre_source"] == "tag"


def test_nothing_returns_other():
    r = G.decide(None, {}, remix=False)
    assert r["genre"] == "Other"


def test_is_remix_detection():
    assert G.is_remix("Song (Salute Remix)")
    assert G.is_remix("track (VIP).flac")
    assert G.is_remix("Artist - Tune (Bootleg)")
    assert not G.is_remix("Just A Regular Song")


def test_label_track_reads_tag_and_remix(tmp_path):
    # tag genre 'Pop', filename marks remix -> audio wins
    out = G.label_track("/x/Some Song (Jersey Remix).flac",
                        {"genre": ["Pop"], "title": ["Some Song (Jersey Remix)"]}, AUDIO)
    assert out["genre"] == "Jersey Club" and out["genre_source"] == "audio"
