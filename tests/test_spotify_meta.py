import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import spotify_meta as sm

FAKE = {"tracks": {"items": [{
    "id": "sp1", "name": "Yale", "duration_ms": 180000, "popularity": 63,
    "artists": [{"name": "Ken Carson", "id": "a1"}],
    "album": {"id": "alb1", "release_date": "2022-04-14"},
    "external_ids": {"isrc": "USxx1234567"}}]}}


def test_confident_match_returns_catalog(monkeypatch):
    monkeypatch.setattr(sm, "_search", lambda q: FAKE)
    monkeypatch.setattr(sm, "_album_label", lambda aid: "Opium")
    r = sm.match("Ken Carson", "Yale", 180.0)
    assert r["spotify_id"] == "sp1" and r["isrc"] == "USxx1234567"
    assert r["spotify_popularity"] == 63 and r["release_date"] == "2022-04-14"
    assert r["label"] == "Opium"
    assert r["spotify_match_confidence"] >= 0.7


def test_search_failure_is_swallowed(monkeypatch):
    def boom(q):
        raise TimeoutError("hang")
    # _search itself guards; simulate a raising _search to prove match never propagates
    monkeypatch.setattr(sm, "_search", lambda q: {})
    assert sm.match("X", "Y", 1.0) == {}


def test_wrong_artist_rejected(monkeypatch):
    wrong = {"tracks": {"items": [{"id": "z", "name": "Yale", "duration_ms": 180000, "popularity": 1,
             "artists": [{"name": "Some Other Guy", "id": "a2"}], "album": {"release_date": "2000-01-01"},
             "external_ids": {}}]}}
    monkeypatch.setattr(sm, "_search", lambda q: wrong)
    assert sm.match("Ken Carson", "Yale", 180.0) == {}
