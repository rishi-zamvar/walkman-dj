import os, sys, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import enrich, schema


def test_merge_accuracy_ranked_and_normalised():
    sources = {
        "discogs": {"genre": "UK Garage", "subgenre": "Speed Garage", "year": 2001,
                    "label": "Locked On", "catalog_no": "LOX1"},
        "mb": {"genre": "electronic", "label": "MB Label", "release_date": "2001-05-01",
               "isrc": "GBABC0100001", "country": "GB"},
        "lastfm": {"tags": ["garage", "bass", "dance"]},
    }
    up, prov = enrich.merge(sources)
    assert up["genre"] == "UK Garage" and prov["genre"] == "discogs"   # discogs beats mb
    assert up["label"] == "MB Label" and prov["label"] == "mb"         # mb beats discogs for label
    assert up["release_date"] == "2001-05-01" and up["isrc"] == "GBABC0100001"
    assert up["year"] == 2001 and up["catalog_no"] == "LOX1" and up["country"] == "GB"
    assert "garage" in up["mood_tags"]


def test_merge_never_emits_rekordbox_fields():
    # even if a source somehow returns bpm/key, merge must drop them
    sources = {"discogs": {"genre": "House", "bpm": 128, "camelot": "8A"}}
    up, _ = enrich.merge(sources)
    assert "bpm" not in up and "camelot" not in up and up["genre"] == "House"


def test_lookups_degrade_without_keys(monkeypatch):
    monkeypatch.setattr(enrich.keys, "get", lambda n: None)
    assert enrich.discogs("a", "b") == {}
    assert enrich.lastfm("a", "b") == {}
    # identify with no acoustid key + no network mock -> {} (search returns {})
    monkeypatch.setattr(enrich, "_get", lambda *a, **k: {})
    assert enrich.identify("/x.flac", "a", "b") == {}


def test_enrich_one_skips_empty_artist(monkeypatch):
    # blank-artist tracks can't be reliably identified -> skip, no garbage
    called = []
    monkeypatch.setattr(enrich, "identify", lambda *a: called.append(1) or {})
    up, prov, mbid = enrich.enrich_one("/x.flac", "", "Cn Tower")
    assert up == {} and mbid is None and not called   # identify never even ran


def test_identify_mb_fallback_gates_wrong_artist(monkeypatch):
    monkeypatch.setattr(enrich.keys, "get", lambda n: None)   # no acoustid
    # MB returns a recording by a different artist -> must be rejected
    monkeypatch.setattr(enrich, "_get", lambda *a, **k: {"recordings": [
        {"id": "x", "title": "Cn Tower", "artist-credit": [{"name": "Some 1977 Band"}]}]})
    assert enrich.identify("/x.flac", "Drake", "Cn Tower") == {}


def test_merge_sets_genre_source(monkeypatch):
    up, prov = enrich.merge({"discogs": {"genre": "UK Garage"}})
    assert up["genre"] == "UK Garage" and up["genre_source"] == "discogs"


def test_enrich_one_composes(monkeypatch):
    monkeypatch.setattr(enrich, "identify", lambda p, a, t: {"mbid": "m1", "artist": a, "title": t})
    monkeypatch.setattr(enrich, "mb", lambda m: {"label": "L", "release_date": "2000-01-01"})
    monkeypatch.setattr(enrich, "discogs", lambda a, t: {"genre": "UK Garage", "subgenre": "Bassline"})
    monkeypatch.setattr(enrich, "lastfm", lambda a, t: {})
    up, prov, mbid = enrich.enrich_one("/x.flac", "DJ Q", "Yeah")
    assert up["genre"] == "UK Garage" and up["label"] == "L" and mbid == "m1"
    assert up["mbid"] == "m1" and prov["mbid"] == "acoustid"


def test_discogs_rejects_wrong_artist(monkeypatch):
    monkeypatch.setattr(enrich.keys, "get", lambda n: "tok")
    # search returns a hit whose artist ("Various") doesn't match ours -> rejected
    monkeypatch.setattr(enrich, "_get", lambda *a, **k: {"results": [
        {"title": "Various - Broken Hearted", "year": 2005, "style": ["Rock & Roll"]}]})
    assert enrich.discogs("Interplanetary Criminal", "Where's My Money") == {}


def test_discogs_accepts_matching_artist(monkeypatch):
    monkeypatch.setattr(enrich.keys, "get", lambda n: "tok")
    monkeypatch.setattr(enrich, "_get", lambda *a, **k: {"results": [
        {"title": "DJ Q - Ineffable", "year": 2014, "style": ["UK Garage"],
         "genre": ["Electronic"], "label": ["Local Action"], "catno": "LOCLP003"}]})
    r = enrich.discogs("DJ Q", "Ineffable")
    assert r["genre"] == "UK Garage" and r["year"] == 2014 and r["label"] == "Local Action"


def test_mb_parses_recording(monkeypatch):
    fake = {"genres": [{"name": "garage", "count": 5}, {"name": "house", "count": 1}],
            "isrcs": ["GBTEST0100001"],
            "releases": [{"date": "1999-09-09", "country": "GB",
                          "label-info": [{"label": {"name": "Locked On"}}]}]}
    monkeypatch.setattr(enrich, "_get", lambda *a, **k: fake)
    r = enrich.mb("mbid1")
    assert r["genre"] == "garage" and r["isrc"] == "GBTEST0100001"
    assert r["label"] == "Locked On" and r["release_date"] == "1999-09-09" and r["country"] == "GB"
