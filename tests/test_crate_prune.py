"""Tests for crate prune/rescan — index<->disk reconciliation. Real audio from the lake."""
import os, sys, shutil, unicodedata
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import crate  # noqa: E402

LAKE = os.environ.get("CRATE_ROOT", os.path.expanduser("~/Music/MusicLake"))


def _distinct_flacs(n=3):
    """n flacs that are genuinely different recordings (fingerprint-verified)."""
    cands = []
    for dp, _, fns in os.walk(LAKE):
        if "_dedup" in dp:
            continue
        for fn in fns:
            if fn.endswith(".flac"):
                p = os.path.join(dp, fn)
                try:
                    if 15 < os.path.getsize(p) / 1e6 < 45:
                        cands.append(p)
                except OSError:
                    pass
        if len(cands) > 25:
            break
    out, seen = [], []
    for p in cands:
        try:
            fp, dur = crate.fingerprint(p)
        except Exception:
            continue
        if all(not crate.same_recording(fp, dur, f, d) for f, d in seen):
            seen.append((fp, dur))
            out.append(p)
            if len(out) == n:
                return out
    pytest.skip(f"need {n} distinct flacs in {LAKE}")


@pytest.fixture(scope="module")
def songs():
    return _distinct_flacs(3)


def _lib(tmp_path, songs, count=2):
    root = tmp_path / "lake"
    root.mkdir()
    lib = crate.CrateLib(str(tmp_path / "db.sqlite"), str(root))
    paths = []
    for i, src in enumerate(songs[:count]):
        d = root / f"Artist{i}"
        d.mkdir()
        p = d / f"track{i}.flac"
        shutil.copy(src, p)
        lib.ingest(str(p), into_library=False)
        paths.append(str(p))
    return lib, paths


def test_given_moved_file_when_pruned_then_row_is_relinked_not_deleted(tmp_path, songs):
    lib, (a, b) = _lib(tmp_path, songs)
    moved = os.path.join(os.path.dirname(a), "renamed.flac")
    os.rename(a, moved)

    dry = lib.prune(apply=False)
    assert dry["missing"] == 1
    assert dry["relinked"] == [(a, moved)]
    assert dry["deleted"] == []
    with lib._c() as c:                                  # dry run changed nothing
        assert c.execute("SELECT COUNT(*) FROM recordings WHERE canonical_path=?", (a,)).fetchone()[0] == 1

    lib.prune(apply=True)
    with lib._c() as c:
        assert c.execute("SELECT COUNT(*) FROM recordings WHERE canonical_path=?", (a,)).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM recordings WHERE canonical_path=?", (moved,)).fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 2


def test_given_deleted_file_when_pruned_then_row_and_analysis_rows_go(tmp_path, songs):
    lib, (a, b) = _lib(tmp_path, songs)
    with lib._c() as c:
        rid, fp = c.execute("SELECT id, fp FROM recordings WHERE canonical_path=?", (b,)).fetchone()
        c.execute("INSERT INTO ml_features(id, vec) VALUES(?,'[]')", (rid,))
        c.execute("INSERT INTO track_tags(id, source, tag, weight) VALUES(?,'t','x',1.0)", (rid,))
        c.execute("INSERT INTO segments(fingerprint, idx, label, start, end) VALUES(?,0,'intro',0,1)", (fp,))
        c.execute("INSERT INTO cue_in(path, cue, conf) VALUES(?,0.5,1.0)", (b,))
        c.commit()
    os.remove(b)

    r = lib.prune(apply=True)
    assert r["deleted"] == [b] and r["relinked"] == []
    with lib._c() as c:
        assert c.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 1
        for q, arg in (("SELECT COUNT(*) FROM ml_features WHERE id=?", rid),
                       ("SELECT COUNT(*) FROM track_tags WHERE id=?", rid),
                       ("SELECT COUNT(*) FROM segments WHERE fingerprint=?", fp),
                       ("SELECT COUNT(*) FROM cue_in WHERE path=?", b),
                       ("SELECT COUNT(*) FROM pending_analysis WHERE path=?", b)):
            assert c.execute(q, (arg,)).fetchone()[0] == 0, q


def test_given_case_and_unicode_variant_paths_when_pruned_then_nothing_is_missing(tmp_path, songs):
    """The phantom-drift bug: APFS is case-insensitive and mixes NFC/NFD, so a row
    stored in a different case/normal form must NOT count as a missing file."""
    lib, (a, b) = _lib(tmp_path, songs)
    with lib._c() as c:
        c.execute("UPDATE recordings SET canonical_path=? WHERE canonical_path=?",
                  (unicodedata.normalize("NFD", a.upper()), a))
        c.commit()
    assert lib.prune(apply=False)["missing"] == 0


def test_given_unindexed_file_when_rescanned_then_it_is_indexed(tmp_path, songs):
    lib, (a, b) = _lib(tmp_path, songs)
    extra = os.path.join(lib.root, "Artist0", "unindexed.flac")
    shutil.copy(songs[2], extra)

    dry = lib.rescan(apply=False)
    assert dry["unindexed"] == 1 and dry["added"] == [extra] and dry["dupes"] == []
    with lib._c() as c:                                  # dry run changed nothing
        assert c.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 2

    lib.rescan(apply=True)
    with lib._c() as c:
        assert c.execute("SELECT COUNT(*) FROM recordings WHERE canonical_path=?", (extra,)).fetchone()[0] == 1
    assert lib.rescan(apply=True)["unindexed"] == 0      # idempotent


def test_given_duplicate_copy_when_rescanned_then_reported_not_indexed_or_trashed(tmp_path, songs):
    lib, (a, b) = _lib(tmp_path, songs)
    dupe = os.path.join(lib.root, "Artist0", "copy.flac")
    shutil.copy(a, dupe)

    r = lib.rescan(apply=True)
    assert r["added"] == [] and r["dupes"] == [(dupe, a)]
    assert os.path.exists(dupe)                          # rescan never trashes
    with lib._c() as c:
        assert c.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 2


def test_given_case_colliding_rows_when_pruned_then_stale_one_is_dropped(tmp_path, songs):
    """APFS lets two rows claim one file via a case-variant path — UNIQUE doesn't catch
    it. Prune keeps the row whose fingerprint matches the file."""
    lib, (a, b) = _lib(tmp_path, songs)
    with lib._c() as c:                       # a second row for `a`, different case + wrong audio
        fp_b, dur_b = c.execute("SELECT fp, duration FROM recordings WHERE canonical_path=?", (b,)).fetchone()
        c.execute("""INSERT INTO recordings(canonical_path, artist, title, fmt, duration, dur_bucket, fp, akey)
                     VALUES(?,'x','x','flac',?,?,?,'x')""",
                  (a.upper(), dur_b, int(dur_b), fp_b))
        c.commit()
        assert c.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 3

    r = lib.prune(apply=True)
    assert r["deleted"] == [a.upper()] and r["collisions"] == []
    with lib._c() as c:
        assert c.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM recordings WHERE canonical_path=?", (a,)).fetchone()[0] == 1
    assert os.path.exists(a)                  # prune never touches audio
