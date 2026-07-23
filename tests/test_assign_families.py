from walkman_dj.assign_families import family_for, build_track_records


def _rules():
    return {"ukg_bass": {"folders": [], "artist_keywords": ["interplanetary criminal"],
                         "filename_keywords": []},
            "tech_house": {"folders": ["some house album"], "artist_keywords": [],
                           "filename_keywords": []}}


def test_family_for_uses_rules_artist_keyword():
    fam, conf = family_for(_rules(), folder="", filename="track.flac",
                           artist="Interplanetary Criminal", bpm=134)
    assert fam == "ukg_bass"
    assert conf >= 0.6


def test_family_for_uses_rules_folder():
    fam, conf = family_for(_rules(), folder="some house album", filename="x.flac",
                           artist="Unknown", bpm=126)
    assert fam == "tech_house"


def test_family_for_falls_back_to_tempo_bucket():
    fam, conf = family_for(_rules(), folder="", filename="mystery.flac",
                           artist="Nobody", bpm=126)
    assert conf < 0.6            # no rule hit -> low confidence
    assert fam                    # some bucket assigned


def test_build_records_carries_features_and_canon():
    rows = [{"fingerprint": "fp1", "folder": "", "filename": "t.flac",
             "artist": "Interplanetary Criminal", "bpm": 134, "energy": 0.6,
             "bass_weight": 0.7, "brightness": 0.4}]
    rec = build_track_records(rows, _rules())
    assert rec[0]["fingerprint"] == "fp1"
    assert rec[0]["label_family"] == "ukg_bass"
    assert rec[0]["energy"] == 0.6
