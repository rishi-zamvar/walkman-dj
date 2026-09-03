"""Integration tests for crate organize/enrich/export/verify — use real audio + tmp libs."""
import os, sys, shutil, subprocess
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import crate  # noqa: E402

LAKE = os.environ.get("CRATE_ROOT", os.path.expanduser("~/Music/MusicLake"))


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


def test_export_transcodes_playlist_to_m4a(tmp_path):
    from walkman_dj import crate_cli
    base = _a_flac(tmp_path, name="track.flac")
    m3u = str(tmp_path / "p.m3u8"); open(m3u, "w").write("#EXTM3U\n" + base + "\n")
    out = str(tmp_path / "out")
    crate_cli.main(["export", m3u, "--to", out, "--format", "m4a"])
    assert len([f for f in os.listdir(out) if f.endswith(".m4a")]) == 1


def test_enrich_copies_bpm_by_artist_title(tmp_path):
    import sqlite3
    root = tmp_path / "lib"; root.mkdir()
    lib = crate.CrateLib(str(tmp_path / "db.sqlite"), str(root))
    with lib._c() as c:
        c.execute("INSERT INTO recordings(canonical_path,artist,title,album,fmt,duration,dur_bucket,fp,akey) "
                  "VALUES(?,?,?,?,?,?,?,?,?)",
                  ("/x.flac", "Ken Carson", "Yale", "Teen X", "flac", 180, 180, "[1,2,3]", "kencarsonyale"))
    adb = str(tmp_path / "analysis.db"); a = sqlite3.connect(adb)
    a.executescript("CREATE TABLE tracks(fingerprint,artist,title); CREATE TABLE features(fingerprint,bpm,camelot,energy);"
                    "CREATE TABLE track_genre(fingerprint,family);")
    a.execute("INSERT INTO tracks VALUES('fp','Ken Carson','Yale')")
    a.execute("INSERT INTO features VALUES('fp',151.0,'8A',0.7)")
    a.execute("INSERT INTO track_genre VALUES('fp','opium_rage')"); a.commit(); a.close()
    n = lib.enrich_from_analysis(adb)
    assert n == 1
    with lib._c() as c:
        row = c.execute("SELECT bpm,camelot,family FROM recordings WHERE akey='kencarsonyale'").fetchone()
    assert round(row["bpm"]) == 151 and row["camelot"] == "8A" and row["family"] == "opium_rage"


def test_ingest_files_album_track_into_nested_canonical_layout(tmp_path):
    import mutagen
    root = tmp_path / "lib"; root.mkdir()
    src = _a_flac(tmp_path, name="random.flac")
    m = mutagen.File(src, easy=True)
    m["albumartist"] = ["Some Artist"]; m["album"] = ["Some Album"]; m["title"] = ["A Track"]; m["tracknumber"] = ["3"]
    m.save()
    lib = crate.CrateLib(str(tmp_path / "db.sqlite"), str(root))
    r = lib.ingest(src)
    # must be nested AlbumArtist/Album/NN - Title, NOT flat "Artist - Album"
    assert r.canonical.endswith("/Some Artist/Some Album/03 - A Track.flac"), r.canonical
    assert " - Some Album/" not in r.canonical


def test_normalize_tags_survives_corrupt_file(tmp_path):
    # a file with .flac extension that is NOT a valid FLAC (mislabeled container)
    bad = str(tmp_path / "10 - Artist - Song.flac")
    with open(bad, "wb") as f:
        f.write(b"ID3\x03\x00\x00\x00\x00\x00\x00not really flac")
    tags = crate.normalize_tags(bad, write=False)     # must not raise
    assert tags["title"] == "Song" and tags["artist"] == "Artist"


def test_given_junky_tags_when_normalized_then_cleaned(tmp_path):
    import mutagen
    src = str(tmp_path / "x.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "anullsrc=r=44100:cl=stereo", "-t", "3", "-c:a", "libmp3lame", src], check=True)
    m = mutagen.File(src, easy=True); m["title"] = ["Song (youtube)"]; m["artist"] = ["Artist"]; m.save()
    tags = crate.normalize_tags(src, write=True)
    assert tags["title"] == "Song"
    assert mutagen.File(src, easy=True)["title"][0] == "Song"
