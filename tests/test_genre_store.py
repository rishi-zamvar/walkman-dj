from walkman_dj.genre_store import ensure_schema, upsert, get_all, is_assigned


def _db(tmp_path):
    return str(tmp_path / "g.db")


def test_schema_and_upsert_roundtrip(tmp_path):
    db = _db(tmp_path)
    ensure_schema(db)
    upsert(db, "fp1", "tech house", "HOUSE", "label", 0.9)
    rows = get_all(db)
    assert rows["fp1"]["family"] == "HOUSE"
    assert rows["fp1"]["canon_genre"] == "tech house"
    assert rows["fp1"]["family_source"] == "label"


def test_frozen_no_overwrite(tmp_path):
    db = _db(tmp_path)
    ensure_schema(db)
    upsert(db, "fp1", "tech house", "HOUSE", "label", 0.9)
    upsert(db, "fp1", "rap", "RAP", "label", 0.9)         # frozen: ignored
    assert get_all(db)["fp1"]["family"] == "HOUSE"


def test_reclassify_overwrites(tmp_path):
    db = _db(tmp_path)
    ensure_schema(db)
    upsert(db, "fp1", "tech house", "HOUSE", "label", 0.9)
    upsert(db, "fp1", "rap", "RAP", "audio_override", 0.9, reclassify=True)
    assert get_all(db)["fp1"]["family"] == "RAP"


def test_is_assigned(tmp_path):
    db = _db(tmp_path)
    ensure_schema(db)
    assert is_assigned(db, "fp1") is False
    upsert(db, "fp1", "tech house", "HOUSE", "label", 0.9)
    assert is_assigned(db, "fp1") is True
