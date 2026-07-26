"""Integration tests for crate organize/enrich/export/verify — use real audio + tmp libs."""
import os, sys, shutil, subprocess
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import crate  # noqa: E402

LAKE = os.path.expanduser("~/Desktop/MusicLake")


def _a_flac(tmp_path, name="song.flac"):
    for dp, _, fns in os.walk(LAKE):
        if "_dedup" in dp:
            continue
        for fn in fns:
            if fn.endswith(".flac"):
                p = os.path.join(dp, fn)
                try:
                    if 20 < os.path.getsize(p) / 1e6 < 45:
                        dst = str(tmp_path / name); shutil.copy(p, dst); return dst
                except OSError:
                    pass
    pytest.skip("need a flac in MusicLake")


def test_given_indexed_lib_when_verified_then_no_missing_or_dupes(tmp_path):
    root = tmp_path / "lib"; root.mkdir()
    lib = crate.CrateLib(str(tmp_path / "db.sqlite"), str(root))
    lib.ingest(_a_flac(tmp_path))
    report = lib.verify()
    assert report["missing"] == 0
    assert report["duplicate_pairs"] == 0
    assert report["recordings"] == 1


def test_organize_moves_to_canonical_and_is_idempotent(tmp_path):
    import mutagen
    root = tmp_path / "lib"; root.mkdir()
    src = _a_flac(tmp_path, name="random-name.flac")
    m = mutagen.File(src, easy=True); m["albumartist"] = ["A"]; m["album"] = ["B"]; m["title"] = ["C"]
    if m.get("tracknumber"):
        del m["tracknumber"]
    m.save()
    lib = crate.CrateLib(str(tmp_path / "db.sqlite"), str(root))
    lib.ingest(src, into_library=False)                 # index where it sits
    plan = lib.organize(dry_run=True)
    assert any(dst.endswith("/A/B/C.flac") for _, dst in plan)
    lib.organize(dry_run=False)
    assert os.path.exists(str(root / "A" / "B" / "C.flac"))
    assert lib.organize(dry_run=True) == []             # idempotent


def test_given_junky_tags_when_normalized_then_cleaned(tmp_path):
    import mutagen
    src = str(tmp_path / "x.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "anullsrc=r=44100:cl=stereo", "-t", "3", "-c:a", "libmp3lame", src], check=True)
    m = mutagen.File(src, easy=True); m["title"] = ["Song (youtube)"]; m["artist"] = ["Artist"]; m.save()
    tags = crate.normalize_tags(src, write=True)
    assert tags["title"] == "Song"
    assert mutagen.File(src, easy=True)["title"][0] == "Song"
