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
