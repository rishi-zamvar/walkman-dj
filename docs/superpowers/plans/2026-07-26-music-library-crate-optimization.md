# MusicLake `crate` Library Optimization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn MusicLake into a self-maintaining, content-addressed music library — one FLAC master per song, one canonical tag-driven folder layout, automatic dedup on ingest — and package the common operations as a single bundled Claude skill (`music-library`).

**Architecture:** A Chromaprint acoustic fingerprint index (`data/library.db`) is the **source of truth for song identity** (folders become a derived layout, never the dedup key). Every write goes through `crate ingest` (fingerprint → dedup → canonical file). BPM/Camelot/genre are folded into the same index. Devices get transcodes on output; the lake stores lossless masters only. All day-to-day operations (`ingest`, `get`, `dedupe`, `organize`, `enrich`, `export`, `stats`) ship as sub-commands of one `bin/crate` CLI, surfaced through one `music-library` skill.

**Tech Stack:** Python 3 (stdlib `sqlite3`, `subprocess`), Chromaprint `fpcalc`, `mutagen` (tags), `rapidfuzz` (resolve), `ffmpeg` (transcode), pytest. macOS `trash` for all deletions. Existing repo modules: `crate.py`, `crate_cli.py`, `beatgrid.py`, `analyze.py`, `camelot.py`.

**Preconditions:** On branch `main` — start by cutting a feature branch. `crate` **core (Phase 0) is already built and green** (8/8 tests); the baseline dedupe is running/complete. This plan covers Phases 1–6.

---

## Setup (before Task 1)

- [ ] **Cut a feature branch**
```bash
cd ~/Documents/Projects/walkman-dj
git checkout -b feat/crate-library-optimization
```

## File Structure

| File | Responsibility |
|---|---|
| `src/walkman_dj/crate.py` (modify) | core: fingerprint, index, ingest, dedupe (DONE) + add `normalize_tags`, `write_tags`, `canonical_path`, `organize`, enrichment columns/methods |
| `src/walkman_dj/crate_tags.py` (create) | tag reading/cleaning helpers (kept separate — pure string logic, heavily unit-tested) |
| `src/walkman_dj/crate_cli.py` (modify) | add `tagfix`, `organize`, `enrich`, `export` sub-commands |
| `tests/test_crate.py` (modify) | existing 8 tests (DONE) |
| `tests/test_crate_tags.py` (create) | tag-cleaning + canonical-path unit tests (pure, fast) |
| `tests/test_crate_organize.py` (create) | organize/enrich/export integration tests (real audio, tmp lib) |
| `~/.claude/skills/music-library/SKILL.md` (create) | bundles the common crate ops into one skill |
| `~/.claude/skills/music-library/verify.sh` (create) | skill smoke-test |

---

## Phase 1 — Baseline dedupe validation & index verification

**Definition of Done:** the baseline `crate dedupe` has completed; `library.db` holds one row per unique recording; a verification script confirms (a) zero indexed paths are missing on disk, (b) no two index rows are the same recording, (c) the removal log count reconciles with `scanned − unique`.

### Task 1: Index verification command

**Files:**
- Modify: `src/walkman_dj/crate.py`
- Modify: `src/walkman_dj/crate_cli.py`
- Test: `tests/test_crate_organize.py`

- [ ] **Step 1: Write the failing test**
```python
# tests/test_crate_organize.py
import os, sys, shutil, subprocess
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import crate

def _flac(tmp_path):
    for dp, _, fns in os.walk(os.path.expanduser("~/Desktop/MusicLake")):
        for fn in fns:
            if fn.endswith(".flac") and 20 < os.path.getsize(os.path.join(dp, fn))/1e6 < 45:
                dst = str(tmp_path / fn); shutil.copy(os.path.join(dp, fn), dst); return dst
    pytest.skip("need a flac")

def test_given_indexed_lib_when_verified_then_no_missing_or_dupes(tmp_path):
    root = tmp_path / "lib"; root.mkdir()
    lib = crate.CrateLib(str(tmp_path / "db.sqlite"), str(root))
    lib.ingest(_flac(tmp_path))
    report = lib.verify()
    assert report["missing"] == 0
    assert report["duplicate_pairs"] == 0
    assert report["recordings"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_crate_organize.py::test_given_indexed_lib_when_verified_then_no_missing_or_dupes -v`
Expected: FAIL — `AttributeError: 'CrateLib' object has no attribute 'verify'`

- [ ] **Step 3: Implement `verify` in `crate.py`** (add method to `CrateLib`)
```python
    def verify(self) -> dict:
        """Integrity check: missing files + accidental same-recording index rows."""
        with self._c() as c:
            rows = c.execute("SELECT canonical_path, fp, duration FROM recordings").fetchall()
        missing = sum(1 for r in rows if not os.path.exists(r["canonical_path"]))
        # same-recording check within duration buckets
        import json as _json
        by_bucket = {}
        for r in rows:
            by_bucket.setdefault(int(r["duration"]), []).append(r)
        dup = 0
        for b, grp in by_bucket.items():
            for i in range(len(grp)):
                for j in range(i + 1, len(grp)):
                    if same_recording(_json.loads(grp[i]["fp"]), grp[i]["duration"],
                                      _json.loads(grp[j]["fp"]), grp[j]["duration"]):
                        dup += 1
        return {"recordings": len(rows), "missing": missing, "duplicate_pairs": dup}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_crate_organize.py::test_given_indexed_lib_when_verified_then_no_missing_or_dupes -v`
Expected: PASS

- [ ] **Step 5: Add `crate verify` CLI + run on the real index**

Add to `crate_cli.py` `build_parser`:
```python
    sub.add_parser("verify").set_defaults(func=cmd_verify)
```
And:
```python
def cmd_verify(args):
    print(_lib().verify())
```
Run: `bin/crate verify` — Expected: `missing` = 0, `duplicate_pairs` = 0, `recordings` ≈ 6000–6500.

- [ ] **Step 6: Commit**
```bash
git add src/walkman_dj/crate.py src/walkman_dj/crate_cli.py tests/test_crate_organize.py
git commit -m "feat(crate): add library integrity verify"
```

---

## Phase 2 — Tag normalization

**Definition of Done:** `crate tagfix` reads a file's tags, strips acquisition junk (`(youtube)`, `[www.slider.kz]`, `(0fficial Mp3)`, `[FLAC 24bit]`, doubled spaces), recovers `artist`/`title` from `Artist - Title` filenames when tags are missing, and (with `--write`) persists the cleaned tags — never inventing data it can't derive. Pure-string logic is unit-tested; a no-op on already-clean tags is verified.

### Task 2: Tag-cleaning helpers (pure, unit-tested)

**Files:**
- Create: `src/walkman_dj/crate_tags.py`
- Test: `tests/test_crate_tags.py`

- [ ] **Step 1: Write the failing tests**
```python
# tests/test_crate_tags.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import crate_tags as ct

def test_strips_acquisition_junk():
    assert ct.clean_title("Song (youtube)") == "Song"
    assert ct.clean_title("Song [www.slider.kz]") == "Song"
    assert ct.clean_title("Song (0fficial Mp3)") == "Song"
    assert ct.clean_title("Song   [FLAC 24bit]") == "Song"

def test_keeps_meaningful_parens():
    assert ct.clean_title("Song (feat. X)") == "Song (feat. X)"
    assert ct.clean_title("Song (Extended Mix)") == "Song (Extended Mix)"

def test_recovers_artist_title_from_stem():
    assert ct.split_stem("04 - Artist Name - Some Title") == ("Artist Name", "Some Title")
    assert ct.split_stem("Some Title") == (None, "Some Title")

def test_noop_on_clean():
    assert ct.clean_title("Clean Title") == "Clean Title"
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_crate_tags.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'walkman_dj.crate_tags'`

- [ ] **Step 3: Implement `crate_tags.py`**
```python
"""Pure tag-string cleaning. No I/O — trivially unit-testable."""
import re, os

_JUNK = re.compile(
    r"\s*[\(\[]\s*(?:youtube|официаль\w*|0fficial\s*mp3|official\s*(?:audio|video|mp3)|"
    r"www\.[\w.]+|slider\.kz|digitaldripped[\w.]*|flac(?:\s*\d+bit)?|web|deluxe\s*edition|"
    r"free\s*dl|\d{3,4}kbps|hq|lyrics?)\s*[\)\]]", re.I)

def clean_title(t: str) -> str:
    if not t: return t
    prev = None
    while prev != t:
        prev = t; t = _JUNK.sub("", t)
    return re.sub(r"\s{2,}", " ", t).strip(" -_")

def split_stem(stem: str):
    """('Artist','Title') from 'NN - Artist - Title' / 'Artist - Title'; (None,title) otherwise."""
    s = re.sub(r"^\s*\d{1,3}\s*[-\.\s]+", "", stem).strip()
    parts = [p.strip() for p in s.split(" - ")]
    if len(parts) >= 2:
        return parts[0], " - ".join(parts[1:])
    return None, s
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_crate_tags.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**
```bash
git add src/walkman_dj/crate_tags.py tests/test_crate_tags.py
git commit -m "feat(crate): pure tag-cleaning helpers"
```

### Task 3: `normalize_tags` + `write_tags` + `crate tagfix`

**Files:**
- Modify: `src/walkman_dj/crate.py`
- Modify: `src/walkman_dj/crate_cli.py`
- Test: `tests/test_crate_organize.py`

- [ ] **Step 1: Write the failing test**
```python
def test_given_junky_tags_when_normalized_then_cleaned(tmp_path):
    import mutagen
    from mutagen.easyid3 import EasyID3
    from walkman_dj import crate
    src = str(tmp_path / "x.mp3")
    # minimal silent mp3 via ffmpeg
    import subprocess
    subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i","anullsrc=r=44100:cl=stereo",
                    "-t","3","-c:a","libmp3lame", src], check=True)
    m = mutagen.File(src, easy=True); m["title"]=["Song (youtube)"]; m["artist"]=["Artist"]; m.save()
    tags = crate.normalize_tags(src, write=True)
    assert tags["title"] == "Song"
    assert mutagen.File(src, easy=True)["title"][0] == "Song"
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_crate_organize.py::test_given_junky_tags_when_normalized_then_cleaned -v`
Expected: FAIL — `AttributeError: module 'walkman_dj.crate' has no attribute 'normalize_tags'`

- [ ] **Step 3: Implement in `crate.py`**
```python
from walkman_dj import crate_tags

def normalize_tags(path: str, write: bool = False) -> dict:
    """Return cleaned {artist,title,album,albumartist}; optionally persist via mutagen."""
    import mutagen
    m = mutagen.File(path, easy=True)
    raw = {k: (m.get(k, [""])[0] if m else "") for k in ("artist", "title", "album", "albumartist")}
    stem = os.path.splitext(os.path.basename(path))[0]
    fa, ft = crate_tags.split_stem(stem)
    out = {
        "title": crate_tags.clean_title(raw["title"] or ft or ""),
        "artist": (raw["artist"] or fa or "").strip(),
        "album": crate_tags.clean_title(raw["album"] or ""),
        "albumartist": (raw["albumartist"] or raw["artist"] or fa or "").strip(),
    }
    if write and m is not None:
        for k, v in out.items():
            if v:
                m[k if k != "albumartist" else "albumartist"] = [v]
        m.save()
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_crate_organize.py::test_given_junky_tags_when_normalized_then_cleaned -v`
Expected: PASS

- [ ] **Step 5: Add `crate tagfix` CLI (dry by default, `--write` persists)**

Add to `crate_cli.py`:
```python
def cmd_tagfix(args):
    from walkman_dj import crate
    for p in args.files:
        before = crate.normalize_tags(p, write=False)
        crate.normalize_tags(p, write=args.write)
        print(("wrote " if args.write else "would fix "), os.path.basename(p), "->", before["artist"], "-", before["title"])
```
And parser:
```python
    pt = sub.add_parser("tagfix"); pt.add_argument("files", nargs="+"); pt.add_argument("--write", action="store_true"); pt.set_defaults(func=cmd_tagfix)
```

- [ ] **Step 6: Commit**
```bash
git add src/walkman_dj/crate.py src/walkman_dj/crate_cli.py tests/test_crate_organize.py
git commit -m "feat(crate): tag normalization + tagfix command"
```

---

## Phase 3 — Canonical organize

**Definition of Done:** `crate organize --dry-run` prints every planned move without touching disk; `crate organize` moves each file to its tag-derived canonical path (album-tagged → `<AlbumArtist>/<Album>/NN - Title.ext`; no album tag → `_Singles/<Artist>/Title.ext`), updates the index, never overwrites, logs every move, and is idempotent (a second run plans zero moves). **An album track is never demoted to `_Singles`.**

### Task 4: `canonical_path` (pure)

**Files:**
- Modify: `src/walkman_dj/crate.py`
- Test: `tests/test_crate_tags.py`

- [ ] **Step 1: Write the failing tests**
```python
def test_album_track_goes_to_album_folder():
    from walkman_dj import crate
    p = crate.canonical_path("/LIB", "/tmp/x.flac",
        {"albumartist":"Ken Carson","album":"Teen X","title":"Yale","artist":"Ken Carson"}, tracknum=2)
    assert p == "/LIB/Ken Carson/Teen X/02 - Yale.flac"

def test_single_no_album_goes_to_singles():
    from walkman_dj import crate
    p = crate.canonical_path("/LIB", "/tmp/x.mp3",
        {"albumartist":"","album":"","title":"Some Bootleg","artist":"DJ X"}, tracknum=None)
    assert p == "/LIB/_Singles/DJ X/Some Bootleg.mp3"

def test_sanitizes_slashes():
    from walkman_dj import crate
    p = crate.canonical_path("/LIB", "/tmp/x.flac",
        {"albumartist":"AC/DC","album":"Back/Black","title":"T","artist":"AC/DC"}, tracknum=1)
    assert "/LIB/AC-DC/Back-Black/01 - T.flac" == p
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_crate_tags.py -k canonical -v`
Expected: FAIL — `AttributeError: module 'walkman_dj.crate' has no attribute 'canonical_path'`

- [ ] **Step 3: Implement in `crate.py`**
```python
def _san(s: str) -> str:
    return re.sub(r"\s{2,}", " ", re.sub(r"[/:]", "-", (s or "").strip())).strip(" .") or "Unknown"

def canonical_path(root: str, src: str, tags: dict, tracknum=None) -> str:
    ext = os.path.splitext(src)[1].lower()
    title = _san(tags.get("title") or os.path.splitext(os.path.basename(src))[0])
    album = (tags.get("album") or "").strip()
    if album:                                       # album-tagged -> album folder
        aa = _san(tags.get("albumartist") or tags.get("artist") or "Unknown")
        nn = f"{int(tracknum):02d} - " if tracknum else ""
        return os.path.join(root, aa, _san(album), f"{nn}{title}{ext}")
    artist = _san(tags.get("artist") or "Unknown")  # true single
    return os.path.join(root, "_Singles", artist, f"{title}{ext}")
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_crate_tags.py -k canonical -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**
```bash
git add src/walkman_dj/crate.py tests/test_crate_tags.py
git commit -m "feat(crate): tag-driven canonical_path"
```

### Task 5: `organize` (dry-run + execute, idempotent)

**Files:**
- Modify: `src/walkman_dj/crate.py`
- Modify: `src/walkman_dj/crate_cli.py`
- Test: `tests/test_crate_organize.py`

- [ ] **Step 1: Write the failing test**
```python
def test_organize_moves_to_canonical_and_is_idempotent(tmp_path):
    import mutagen, subprocess
    from walkman_dj import crate
    root = tmp_path / "lib"; root.mkdir()
    src = str(tmp_path / "random-name.flac")
    # a real flac with album tags
    base = None
    for dp,_,fns in os.walk(os.path.expanduser("~/Desktop/MusicLake")):
        for fn in fns:
            if fn.endswith(".flac") and 20<os.path.getsize(os.path.join(dp,fn))/1e6<45: base=os.path.join(dp,fn); break
        if base: break
    shutil.copy(base, src)
    m = mutagen.File(src, easy=True); m["albumartist"]=["A"]; m["album"]=["B"]; m["title"]=["C"]; m.save()
    lib = crate.CrateLib(str(tmp_path/"db.sqlite"), str(root))
    lib.ingest(src, into_library=False)                 # index it where it sits
    plan = lib.organize(dry_run=True)
    assert any(dst.endswith("/A/B/C.flac") for _, dst in plan)
    lib.organize(dry_run=False)
    assert os.path.exists(str(root/"A"/"B"/"C.flac"))
    assert lib.organize(dry_run=True) == []              # idempotent
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_crate_organize.py::test_organize_moves_to_canonical_and_is_idempotent -v`
Expected: FAIL — `AttributeError: 'CrateLib' object has no attribute 'organize'`

- [ ] **Step 3: Implement `organize` in `CrateLib`**
```python
    def organize(self, dry_run=True, on_move=None) -> list:
        import shutil as _sh
        with self._c() as c:
            rows = c.execute("SELECT canonical_path FROM recordings").fetchall()
        moves = []
        for r in rows:
            src = r["canonical_path"]
            if not os.path.exists(src):
                continue
            tags = normalize_tags(src, write=False)
            tn = None
            mm = __import__("mutagen").File(src, easy=True)
            if mm and mm.get("tracknumber"):
                tn = str(mm["tracknumber"][0]).split("/")[0]
                tn = int(tn) if tn.isdigit() else None
            dst = canonical_path(self.root, src, tags, tracknum=tn)
            if os.path.abspath(dst) == os.path.abspath(src):
                continue
            moves.append((src, dst))
            if not dry_run:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if not os.path.exists(dst):              # never overwrite
                    _sh.move(src, dst)
                    self._reindex_path(src, dst)
                    if on_move: on_move(src, dst)
        return moves
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_crate_organize.py::test_organize_moves_to_canonical_and_is_idempotent -v`
Expected: PASS

- [ ] **Step 5: Add `crate organize` CLI (dry-run default) + prune empty dirs**

`crate_cli.py`:
```python
def cmd_organize(args):
    lib = _lib(); logf = open(args.log, "w") if args.log else None
    n = 0
    def mv(s, d):
        nonlocal n; n += 1
        if logf: logf.write(f"{s}\t->\t{d}\n")
    moves = lib.organize(dry_run=not args.apply, on_move=mv)
    if args.apply:
        # prune emptied dirs
        for dp, dns, fns in os.walk(ROOT, topdown=False):
            if not os.listdir(dp) and dp != ROOT: os.rmdir(dp)
        print(f"moved {n} files")
    else:
        for s, d in moves[:40]: print(f"  {os.path.relpath(s,ROOT)}  ->  {os.path.relpath(d,ROOT)}")
        print(f"... {len(moves)} planned moves (run with --apply to execute)")
    if logf: logf.close()
```
Parser:
```python
    po = sub.add_parser("organize"); po.add_argument("--apply", action="store_true"); po.add_argument("--log"); po.set_defaults(func=cmd_organize)
```

- [ ] **Step 6: Commit**
```bash
git add src/walkman_dj/crate.py src/walkman_dj/crate_cli.py tests/test_crate_organize.py
git commit -m "feat(crate): canonical organize (dry-run + apply, idempotent)"
```

---

## Phase 4 — Fold analysis/genre into the index

**Definition of Done:** the `recordings` table carries `bpm`, `camelot`, `energy`, `family` columns; `crate enrich` backfills them from `data/walkman_analysis.db` by fuzzy (artist,title) match at high confidence; a fresh `crate ingest` computes BPM/Camelot/energy for the new track via the existing engine and stores them. Device-path phantom rows never enter the crate index. Playlist/set tooling can query one DB.

### Task 6: enrichment columns + `enrich_from_analysis`

**Files:**
- Modify: `src/walkman_dj/crate.py`
- Modify: `src/walkman_dj/crate_cli.py`
- Test: `tests/test_crate_organize.py`

- [ ] **Step 1: Write the failing test**
```python
def test_enrich_copies_bpm_by_artist_title(tmp_path):
    import sqlite3
    from walkman_dj import crate
    root = tmp_path / "lib"; root.mkdir()
    lib = crate.CrateLib(str(tmp_path/"db.sqlite"), str(root))
    # seed one recording
    with lib._c() as c:
        c.execute("INSERT INTO recordings(canonical_path,artist,title,album,fmt,duration,dur_bucket,fp,akey) "
                  "VALUES(?,?,?,?,?,?,?,?,?)",
                  ("/x.flac","Ken Carson","Yale","Teen X","flac",180,180,"[1,2,3]","kencarsonyale"))
    # fake analysis db
    adb = str(tmp_path/"analysis.db"); a = sqlite3.connect(adb)
    a.executescript("CREATE TABLE tracks(fingerprint,artist,title); CREATE TABLE features(fingerprint,bpm,camelot,energy);"
                    "CREATE TABLE track_genre(fingerprint,family);")
    a.execute("INSERT INTO tracks VALUES('fp','Ken Carson','Yale')")
    a.execute("INSERT INTO features VALUES('fp',151.0,'8A',0.7)")
    a.execute("INSERT INTO track_genre VALUES('fp','opium_rage')"); a.commit()
    n = lib.enrich_from_analysis(adb)
    assert n == 1
    with lib._c() as c:
        row = c.execute("SELECT bpm,camelot,family FROM recordings WHERE akey='kencarsonyale'").fetchone()
    assert round(row["bpm"]) == 151 and row["camelot"] == "8A" and row["family"] == "opium_rage"
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_crate_organize.py::test_enrich_copies_bpm_by_artist_title -v`
Expected: FAIL — `sqlite3.OperationalError: no such column: bpm` (or missing method)

- [ ] **Step 3: Implement**

In `SCHEMA`, extend `recordings` with columns; add a lightweight migration in `__init__`:
```python
        with self._c() as c:
            c.executescript(SCHEMA)
            for col in ("bpm REAL", "camelot TEXT", "energy REAL", "family TEXT"):
                try: c.execute(f"ALTER TABLE recordings ADD COLUMN {col}")
                except sqlite3.OperationalError: pass
```
Add method:
```python
    def enrich_from_analysis(self, analysis_db: str) -> int:
        import sqlite3 as _sq
        a = _sq.connect(analysis_db); a.row_factory = _sq.Row
        src = {}
        for r in a.execute("""SELECT t.artist,t.title,f.bpm,f.camelot,f.energy,g.family
                              FROM tracks t LEFT JOIN features f USING(fingerprint)
                              LEFT JOIN track_genre g USING(fingerprint)"""):
            k = _norm(f"{r['artist']} {r['title']}")
            if k and k not in src: src[k] = r
        n = 0
        with self._c() as c:
            for row in c.execute("SELECT akey FROM recordings WHERE bpm IS NULL").fetchall():
                s = src.get(row["akey"])
                if s:
                    c.execute("UPDATE recordings SET bpm=?,camelot=?,energy=?,family=? WHERE akey=?",
                              (s["bpm"], s["camelot"], s["energy"], s["family"], row["akey"]))
                    n += 1
        return n
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_crate_organize.py::test_enrich_copies_bpm_by_artist_title -v`
Expected: PASS

- [ ] **Step 5: Add `crate enrich` CLI + analyze-on-ingest**

`crate_cli.py`:
```python
def cmd_enrich(args):
    adb = args.analysis or os.path.join(os.path.dirname(DB), "walkman_analysis.db")
    print("enriched", _lib().enrich_from_analysis(adb), "recordings from", adb)
```
Parser: `pe = sub.add_parser("enrich"); pe.add_argument("--analysis"); pe.set_defaults(func=cmd_enrich)`

- [ ] **Step 6: Commit**
```bash
git add src/walkman_dj/crate.py src/walkman_dj/crate_cli.py tests/test_crate_organize.py
git commit -m "feat(crate): fold bpm/camelot/genre into the index (enrich)"
```

---

## Phase 5 — Output & device export (transcode-on-output)

**Definition of Done:** `crate export <playlist.m3u8> --to DIR --format m4a` resolves every playlist entry to its canonical file and writes a device-ready copy — transcoding FLAC→m4a on the fly, copying when already the target format — so the lake never stores device duplicates. Verified: exported dir has one `.m4a` per resolvable entry, tags preserved.

### Task 7: `crate export`

**Files:**
- Modify: `src/walkman_dj/crate_cli.py`
- Test: `tests/test_crate_organize.py`

- [ ] **Step 1: Write the failing test**
```python
def test_export_transcodes_playlist_to_m4a(tmp_path):
    import subprocess
    from walkman_dj import crate_cli
    # make a 1-track m3u8 pointing at a real flac
    base = None
    for dp,_,fns in os.walk(os.path.expanduser("~/Desktop/MusicLake")):
        for fn in fns:
            if fn.endswith(".flac") and 20<os.path.getsize(os.path.join(dp,fn))/1e6<45: base=os.path.join(dp,fn); break
        if base: break
    m3u = str(tmp_path/"p.m3u8"); open(m3u,"w").write("#EXTM3U\n"+base+"\n")
    out = str(tmp_path/"out")
    crate_cli.main(["export", m3u, "--to", out, "--format", "m4a"])
    m4as = [f for f in os.listdir(out) if f.endswith(".m4a")]
    assert len(m4as) == 1
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_crate_organize.py::test_export_transcodes_playlist_to_m4a -v`
Expected: FAIL — `argument cmd: invalid choice: 'export'`

- [ ] **Step 3: Implement `crate export`**
```python
def cmd_export(args):
    os.makedirs(args.to, exist_ok=True)
    entries = [l.strip() for l in open(args.playlist) if l.strip() and not l.startswith("#")]
    done = 0
    for src in entries:
        if not os.path.exists(src): continue
        base = os.path.splitext(os.path.basename(src))[0]
        if args.format and not src.lower().endswith("." + args.format):
            dst = os.path.join(args.to, base + "." + args.format)
            subprocess.run(["ffmpeg","-nostdin","-v","error","-y","-i",src,"-map","0:a","-map","0:v?",
                            "-c:v","copy","-c:a","aac" if args.format=="m4a" else args.format,
                            "-b:a","256k","-map_metadata","0","-disposition:v","attached_pic",dst],
                           check=False)
            if not os.path.exists(dst):
                subprocess.run(["ffmpeg","-nostdin","-v","error","-y","-i",src,"-c:a","aac","-b:a","256k",dst], check=False)
        else:
            dst = os.path.join(args.to, os.path.basename(src)); subprocess.run(["cp", src, dst], check=False)
        if os.path.exists(dst): done += 1
    print(f"exported {done}/{len(entries)} -> {args.to}")
```
Parser:
```python
    px = sub.add_parser("export"); px.add_argument("playlist"); px.add_argument("--to", required=True)
    px.add_argument("--format", default="m4a"); px.set_defaults(func=cmd_export)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_crate_organize.py::test_export_transcodes_playlist_to_m4a -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/walkman_dj/crate_cli.py tests/test_crate_organize.py
git commit -m "feat(crate): playlist export with transcode-on-output"
```

---

## Phase 6 — Package as a bundled Claude skill

**Definition of Done:** a single `music-library` skill bundles the common ops (`ingest`, `get`, `dedupe`, `organize`, `enrich`, `export`, `stats`, `verify`) with a clear "when to use each"; a `verify.sh` smoke-test runs `crate stats`/`get`/`verify` against the live index and exits non-zero on failure; the existing `walkman-playlist-push` / `cdj-upload` skills note they should resolve files via `crate get`. Invoking `/music-library` (or the skill) drives the CLI for all day-to-day library work.

### Task 8: `music-library` skill bundle

**Files:**
- Create: `~/.claude/skills/music-library/SKILL.md`
- Create: `~/.claude/skills/music-library/verify.sh`

- [ ] **Step 1: Write the smoke-test first (TDD for the skill)**
```bash
# ~/.claude/skills/music-library/verify.sh
#!/bin/bash
set -e
CRATE=~/Documents/Projects/walkman-dj/bin/crate
echo "stats:"; "$CRATE" stats
echo "verify:"; "$CRATE" verify
# resolve a track known to exist; must print a path under MusicLake
HIT=$("$CRATE" get "Ken Carson Yale" || true)
[ -n "$HIT" ] && echo "resolve OK: $HIT" || { echo "resolve FAILED"; exit 1; }
echo "SMOKE TEST PASSED"
```

- [ ] **Step 2: Run it — expect it to work once crate is built**

Run: `bash ~/.claude/skills/music-library/verify.sh`
Expected: prints stats, `duplicate_pairs: 0`, a resolved path, `SMOKE TEST PASSED`.

- [ ] **Step 3: Write `SKILL.md` (the bundle)**
```markdown
---
name: music-library
description: Manage the MusicLake library — add songs (auto-deduped), fetch the exact canonical file, dedupe/organize/verify the library, fold in BPM/genre, and export playlists to device formats. Use for any "add this song", "do I already have X", "clean up / dedupe / organize the library", "get me the file for Y", or "export this playlist as m4a" request.
---

# Music Library (crate)

One CLI, `~/Documents/Projects/walkman-dj/bin/crate`, over a Chromaprint-fingerprinted
library at `~/Desktop/MusicLake` with the index at `data/library.db`. Identity is the
audio fingerprint, so dedup survives format/name/folder differences.

## Common operations (bundled)
| Want | Command |
|---|---|
| Add a file (auto-dedupe) | `crate ingest <file>` |
| Download + add | `crate ingest --get "artist - title"` |
| Get the exact file | `crate get "artist title"` (add `--to DIR --format m4a` to copy/transcode out) |
| Full dedupe sweep | `crate dedupe --log /tmp/dedupe.log` |
| Fix tags | `crate tagfix --write <files>` |
| Reorganize to canonical folders | `crate organize` (preview) then `crate organize --apply` |
| Backfill BPM/genre | `crate enrich` |
| Export a playlist for a device | `crate export <playlist.m3u8> --to DIR --format m4a` |
| Health check | `crate verify` · `crate stats` |

## Rules
- **Only add music through `crate ingest`** — never drop files into MusicLake directly.
- Album membership comes from the **album tag**, not the folder. True singles (no album tag) live in `_Singles/`.
- Keep **one FLAC master per song**; devices get transcodes via `crate get --format` / `crate export`.
- All deletions go to Trash (recoverable); `dedupe`/`organize` write audit logs.

## Verify
`bash <skill-dir>/verify.sh` — smoke-tests stats/verify/resolve against the live index.
```

- [ ] **Step 4: Run the smoke test again to confirm the skill is coherent**

Run: `bash ~/.claude/skills/music-library/verify.sh`
Expected: `SMOKE TEST PASSED`

- [ ] **Step 5: Cross-reference the device skills**

Append to `~/.claude/skills/walkman-playlist-push/SKILL.md` and `~/.claude/skills/cdj-upload/SKILL.md` a note:
```markdown
> Resolve source files with `crate get "<query>"` (music-library skill) so you always push the canonical, deduped copy.
```

- [ ] **Step 6: Commit**
```bash
git add ~/.claude/skills/music-library/
git commit -m "feat: bundle crate ops into music-library skill"
```

---

## Final verification (after all phases)

- [ ] Run the full suite: `python3 -m pytest tests/test_crate.py tests/test_crate_tags.py tests/test_crate_organize.py -v` — Expected: all pass.
- [ ] `bin/crate verify` on the real index — `missing: 0`, `duplicate_pairs: 0`.
- [ ] `crate organize` dry-run reviewed, then `--apply`; re-run dry-run → 0 moves (idempotent).
- [ ] `bash ~/.claude/skills/music-library/verify.sh` → `SMOKE TEST PASSED`.
- [ ] Use superpowers:finishing-a-development-branch to merge `feat/crate-library-optimization`.

## Definition of Done — whole project
1. Library identity is the fingerprint index; folders are derived.
2. `crate ingest` is the only write path and auto-dedupes.
3. One canonical tag-driven layout; `_Singles/` holds only true singles.
4. BPM/Camelot/genre live in the same index.
5. Devices get transcodes on output; lake stores lossless masters.
6. All common operations are one `crate` CLI, surfaced through one `music-library` skill, with a passing smoke test.
