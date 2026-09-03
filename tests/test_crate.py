"""Tests for crate — acoustic-fingerprint library dedup. Uses real audio: it picks
two short, clearly-different lake tracks and transcodes one to create a known
same-recording/different-format pair."""
import os, sys, shutil, subprocess, tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import crate  # noqa: E402

LAKE = os.environ.get("CRATE_ROOT", os.path.expanduser("~/Music/MusicLake"))


def _two_distinct_tracks():
    """Find two flac files that are genuinely DIFFERENT recordings (verified by
    fingerprint, not just filename) so the discrimination test is meaningful."""
    cands = []
    seen_title = set()
    for dp, _, fns in os.walk(LAKE):
        if "_dedup-losers" in dp:
            continue
        for fn in fns:
            if fn.lower().endswith(".flac"):
                p = os.path.join(dp, fn)
                try:
                    if not (20 < os.path.getsize(p) / 1e6 < 45):
                        continue
                except OSError:
                    continue
                key = crate._norm(crate.tags(p)[1])       # normalized title
                if key and key not in seen_title:
                    seen_title.add(key)
                    cands.append(p)
    # pick a pair whose fingerprints actually differ (guards against same song / diff title)
    for i in range(len(cands)):
        fi, di = crate.fingerprint(cands[i])
        for j in range(i + 1, min(i + 5, len(cands))):
            fj, dj = crate.fingerprint(cands[j])
            if not crate.same_recording(fi, di, fj, dj):
                return cands[i], cands[j]
    pytest.skip("need two distinct flac recordings in MusicLake")


@pytest.fixture(scope="module")
def tracks():
    return _two_distinct_tracks()


def test_given_same_file_when_fingerprinted_twice_then_identical(tracks):
    a, _ = tracks
    fp1, d1 = crate.fingerprint(a)
    fp2, d2 = crate.fingerprint(a)
    assert fp1 == fp2 and d1 == d2 and len(fp1) > 10


def test_given_different_songs_when_compared_then_not_same_recording(tracks):
    a, b = tracks
    fa, da = crate.fingerprint(a)
    fb, db = crate.fingerprint(b)
    assert not crate.same_recording(fa, da, fb, db)


def test_given_flac_transcoded_to_mp3_when_compared_then_same_recording(tracks, tmp_path):
    a, _ = tracks
    mp3 = str(tmp_path / "copy.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", a, "-c:a", "libmp3lame", "-b:a", "192k", mp3], check=True)
    fa, da = crate.fingerprint(a)
    fb, db = crate.fingerprint(mp3)
    assert crate.same_recording(fa, da, fb, db)


def test_given_flac_and_mp3_when_ranked_then_flac_wins(tracks, tmp_path):
    a, _ = tracks
    mp3 = str(tmp_path / "copy.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", a, "-c:a", "libmp3lame", "-b:a", "192k", mp3], check=True)
    assert crate.quality(a) > crate.quality(mp3)


def test_given_new_song_when_ingested_then_added_and_indexed(tracks, tmp_path):
    a, _ = tracks
    root = tmp_path / "lib"; root.mkdir()
    incoming = str(tmp_path / "song.flac"); shutil.copy(a, incoming)
    lib = crate.CrateLib(str(tmp_path / "db.sqlite"), str(root))
    r = lib.ingest(incoming)
    assert r.action == "added"
    assert os.path.exists(r.canonical) and str(root) in r.canonical
    assert lib.stats()["recordings"] == 1


def test_given_lower_quality_dupe_when_ingested_then_discarded(tracks, tmp_path):
    a, _ = tracks
    root = tmp_path / "lib"; root.mkdir()
    lib = crate.CrateLib(str(tmp_path / "db.sqlite"), str(root))
    first = str(tmp_path / "song.flac"); shutil.copy(a, first)
    lib.ingest(first)
    mp3 = str(tmp_path / "dupe.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", a, "-c:a", "libmp3lame", "-b:a", "192k", mp3], check=True)
    r = lib.ingest(mp3)
    assert r.action == "discarded"
    assert lib.stats()["recordings"] == 1                 # library still holds ONE copy
    assert r.canonical.endswith(".flac")                  # kept the lossless one


def test_given_higher_quality_dupe_when_ingested_then_replaces(tracks, tmp_path):
    a, _ = tracks
    root = tmp_path / "lib"; root.mkdir()
    lib = crate.CrateLib(str(tmp_path / "db.sqlite"), str(root))
    mp3 = str(tmp_path / "song.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", a, "-c:a", "libmp3lame", "-b:a", "192k", mp3], check=True)
    lib.ingest(mp3)
    flac = str(tmp_path / "better.flac"); shutil.copy(a, flac)
    r = lib.ingest(flac)
    assert r.action == "replaced"
    assert lib.stats()["recordings"] == 1
    assert r.canonical.endswith(".flac")


def test_given_indexed_library_when_resolved_then_returns_canonical(tracks, tmp_path):
    a, _ = tracks
    root = tmp_path / "lib"; root.mkdir()
    lib = crate.CrateLib(str(tmp_path / "db.sqlite"), str(root))
    incoming = str(tmp_path / "song.flac"); shutil.copy(a, incoming)
    canonical = lib.ingest(incoming).canonical
    artist, title, _ = crate.tags(canonical)
    hit = lib.resolve(f"{artist} {title}")
    assert hit == canonical
