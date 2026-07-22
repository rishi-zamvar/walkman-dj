from walkman_dj.family import rescale_energy, learn_envelopes, audio_override
from walkman_dj.genre_store import ensure_schema, get_all


def test_rescale_spreads_compressed_band_to_unit():
    raw = {"a": 0.47, "b": 0.55, "c": 0.60, "d": 0.72}
    en = rescale_energy(raw)
    assert min(en.values()) == 0.0
    assert max(en.values()) == 1.0
    assert en["a"] < en["b"] < en["c"] < en["d"]


def test_rescale_single_track_is_half():
    assert rescale_energy({"a": 0.6}) == {"a": 0.5}


def test_rescale_all_equal_is_half():
    en = rescale_energy({"a": 0.6, "b": 0.6})
    assert en == {"a": 0.5, "b": 0.5}


def _tracks():
    house = [{"bpm": 122 + i, "energy": 0.55, "bass_weight": 0.5, "brightness": 0.5} for i in range(6)]
    rap = [{"bpm": 148 + i, "energy": 0.7, "bass_weight": 0.8, "brightness": 0.3} for i in range(6)]
    return {"HOUSE": house, "RAP": rap}


def test_envelope_keeps_in_family_track():
    env = learn_envelopes(_tracks())
    t = {"bpm": 124, "energy": 0.55, "bass_weight": 0.5, "brightness": 0.5}
    fam, src = audio_override(t, "HOUSE", env)
    assert fam == "HOUSE" and src == "label"


def test_envelope_overrides_mislabeled_track():
    env = learn_envelopes(_tracks())
    t = {"bpm": 150, "energy": 0.7, "bass_weight": 0.8, "brightness": 0.3}
    fam, src = audio_override(t, "HOUSE", env)
    assert fam == "RAP" and src == "audio_override"


def test_assign_persists_only_new(tmp_path):
    db = str(tmp_path / "g.db")
    ensure_schema(db)
    from walkman_dj import family as F
    tracks = [
        {"fingerprint": "fp1", "label_family": "HOUSE", "canon_genre": "tech house",
         "bpm": 124, "energy": 0.55, "bass_weight": 0.5, "brightness": 0.5, "label_conf": 0.9},
        {"fingerprint": "fp2", "label_family": "RAP", "canon_genre": "hip-hop",
         "bpm": 150, "energy": 0.7, "bass_weight": 0.8, "brightness": 0.3, "label_conf": 0.9},
    ]
    n = F.assign_and_persist(db, tracks)
    assert n == 2
    assert F.assign_and_persist(db, tracks) == 0
    rows = get_all(db)
    assert rows["fp1"]["family"] == "HOUSE"
    assert rows["fp2"]["family"] == "RAP"
