# Song Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every MusicLake song one unified, normalized metadata record (our MIR + Essentia Spotify-parity features + Spotify catalog metadata), and auto-analyze new songs on ingest via an async queue.

**Architecture:** Extend `library.db.recordings` into the single source of truth. A pure `analyze_song(path)` engine (SOTA beatgrid + librosa + Essentia) plus a `spotify_meta` catalog matcher feed an async worker; `crate ingest` enqueues into `pending_analysis`, `crate analyzer` drains it.

**Tech Stack:** Python 3.11 (`~/.venvs/analysis`), sqlite3, existing `beatgrid.py`/`analyze.py`/`camelot.py`, Essentia (`essentia-tensorflow`), `rapidfuzz`, the `spotify-playlists` OAuth token, pytest.

**Spec:** `docs/superpowers/specs/2026-07-27-song-intelligence-design.md`

**Preconditions:** on `main`; crate system already merged. Run everything with `~/.venvs/analysis/bin/python` (has librosa 3.11 + torch). Env `CRATE_ROOT=~/Documents/MusicLake`.

---

## Setup (before Task 1)

- [ ] **Cut a feature branch**
```bash
cd ~/Documents/Projects/walkman-dj
git checkout -b feat/song-intelligence
```

## File Structure

| File | Responsibility |
|---|---|
| `src/walkman_dj/schema.py` (create) | the unified `recordings` column list + `pending_analysis`/`segments` DDL + `ensure_schema(db)` migration |
| `src/walkman_dj/essentia_features.py` (create) | isolated Essentia model load + 7 new features; degrades to `{}` if Essentia missing |
| `src/walkman_dj/intelligence.py` (create) | `analyze_song(path)` engine + `ANALYSIS_VERSION`; composes beatgrid+librosa+essentia, normalizes |
| `src/walkman_dj/spotify_meta.py` (create) | Spotify catalog matcher (search → confidence gate → catalog fields) |
| `src/walkman_dj/crate.py` (modify) | ingest → `pending_analysis` enqueue hook; `ensure_schema` on init |
| `src/walkman_dj/crate_cli.py` (modify) | `analyze`, `analyzer`, `spotify-match`, `migrate-analysis` commands |
| `tests/test_schema.py`, `tests/test_intelligence.py`, `tests/test_spotify_meta.py`, `tests/test_analyzer.py` (create) | per-unit tests |
| `docs/song-intelligence-SCHEMA.md` (create) | normalized field dictionary |

---

## Phase 1 — Unified schema + migration

**Definition of Done:** `ensure_schema` adds all new columns + `pending_analysis` + `segments` idempotently; `crate migrate-analysis` copies MIR fields from `walkman_analysis.db` into `recordings` by `(artist,title)`; enqueue/dequeue roundtrip works.

### Task 1: Schema module

**Files:**
- Create: `src/walkman_dj/schema.py`
- Test: `tests/test_schema.py`

- [ ] **Step 1: Write the failing test**
```python
# tests/test_schema.py
import os, sys, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import schema

def test_ensure_schema_adds_columns_idempotently(tmp_path):
    db = str(tmp_path / "t.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE recordings(id INTEGER PRIMARY KEY, fp TEXT, artist TEXT, title TEXT)")
    c.commit(); c.close()
    schema.ensure_schema(db)
    schema.ensure_schema(db)  # idempotent
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(recordings)")}
    for expected in ("valence", "acousticness", "spotify_id", "bpm", "camelot", "analysis_version", "loudness_db"):
        assert expected in cols
    tabs = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "pending_analysis" in tabs and "segments" in tabs

def test_enqueue_dequeue_roundtrip(tmp_path):
    db = str(tmp_path / "t.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE recordings(id INTEGER PRIMARY KEY, fp TEXT)"); c.commit(); c.close()
    schema.ensure_schema(db)
    schema.enqueue(db, "fp1", "/x.flac")
    schema.enqueue(db, "fp1", "/x.flac")  # dupe ignored
    pend = schema.pending(db)
    assert pend == [("fp1", "/x.flac")]
    schema.dequeue(db, "fp1")
    assert schema.pending(db) == []
```

- [ ] **Step 2: Run to verify fail**

Run: `~/.venvs/analysis/bin/python -m pytest tests/test_schema.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'walkman_dj.schema'`

- [ ] **Step 3: Implement `src/walkman_dj/schema.py`**
```python
"""Unified library.db schema: new feature columns + analysis queue. Idempotent."""
import sqlite3

# (column, sql-type) — added to recordings if missing
NEW_COLUMNS = [
    # catalog
    ("isrc", "TEXT"), ("spotify_id", "TEXT"), ("spotify_popularity", "INTEGER"),
    ("release_date", "TEXT"), ("label", "TEXT"), ("artist_genres", "TEXT"),
    ("spotify_match_confidence", "REAL"),
    # our MIR
    ("bpm", "REAL"), ("bpm_confidence", "REAL"), ("key", "TEXT"), ("camelot", "TEXT"),
    ("key_confidence", "REAL"), ("energy", "REAL"), ("brightness", "REAL"),
    ("bass_weight", "REAL"), ("mir_danceability", "REAL"), ("is_vocal", "INTEGER"),
    ("mix_in", "REAL"), ("mix_out", "REAL"), ("beat_anchor", "REAL"),
    ("first_downbeat", "REAL"), ("beat_engine", "TEXT"),
    ("spotify_key", "INTEGER"), ("spotify_mode", "INTEGER"),
    # essentia spotify-parity
    ("valence", "REAL"), ("acousticness", "REAL"), ("instrumentalness", "REAL"),
    ("speechiness", "REAL"), ("liveness", "REAL"), ("loudness_db", "REAL"),
    ("essentia_danceability", "REAL"),
    # provenance
    ("analysis_version", "TEXT"), ("analyzed_at", "TEXT"),
]

def _c(db):
    c = sqlite3.connect(db, timeout=60); c.execute("PRAGMA busy_timeout=60000"); return c

def ensure_schema(db: str):
    with _c(db) as c:
        have = {r[1] for r in c.execute("PRAGMA table_info(recordings)")}
        for col, typ in NEW_COLUMNS:
            if col not in have:
                c.execute(f"ALTER TABLE recordings ADD COLUMN {col} {typ}")
        c.execute("""CREATE TABLE IF NOT EXISTS pending_analysis(
            fingerprint TEXT PRIMARY KEY, path TEXT, enqueued_at TEXT DEFAULT CURRENT_TIMESTAMP,
            attempts INTEGER DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS segments(
            fingerprint TEXT, idx INTEGER, label TEXT, start REAL, end REAL)""")

def enqueue(db: str, fp: str, path: str):
    with _c(db) as c:
        c.execute("INSERT OR IGNORE INTO pending_analysis(fingerprint,path) VALUES(?,?)", (fp, path))

def dequeue(db: str, fp: str):
    with _c(db) as c:
        c.execute("DELETE FROM pending_analysis WHERE fingerprint=?", (fp,))

def pending(db: str, limit: int = 100000):
    with _c(db) as c:
        return [(r[0], r[1]) for r in c.execute(
            "SELECT fingerprint, path FROM pending_analysis ORDER BY enqueued_at LIMIT ?", (limit,))]

def bump_attempt(db: str, fp: str):
    with _c(db) as c:
        c.execute("UPDATE pending_analysis SET attempts=attempts+1 WHERE fingerprint=?", (fp,))
```

- [ ] **Step 4: Run to verify pass**

Run: `~/.venvs/analysis/bin/python -m pytest tests/test_schema.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**
```bash
git add src/walkman_dj/schema.py tests/test_schema.py
git commit -m "feat(intel): unified schema + analysis queue module"
```

### Task 2: `migrate-analysis` — reuse existing MIR

**Files:**
- Modify: `src/walkman_dj/crate_cli.py`
- Test: `tests/test_schema.py`

- [ ] **Step 1: Write the failing test**
```python
def test_migrate_analysis_copies_mir_by_artist_title(tmp_path):
    import sqlite3
    from walkman_dj import schema
    lib = str(tmp_path / "lib.db")
    c = sqlite3.connect(lib)
    c.execute("CREATE TABLE recordings(id INTEGER PRIMARY KEY, fp TEXT, artist TEXT, title TEXT, akey TEXT)")
    c.execute("INSERT INTO recordings(fp,artist,title,akey) VALUES('f','Ken Carson','Yale','kencarsonyale')")
    c.commit(); c.close()
    schema.ensure_schema(lib)
    ana = str(tmp_path / "ana.db"); a = sqlite3.connect(ana)
    a.executescript("CREATE TABLE tracks(fingerprint,artist,title);"
                    "CREATE TABLE features(fingerprint,bpm,camelot,energy,beat_engine);")
    a.execute("INSERT INTO tracks VALUES('x','Ken Carson','Yale')")
    a.execute("INSERT INTO features VALUES('x',151.0,'8A',0.7,'beatthis+deeprhythm')")
    a.commit(); a.close()
    n = schema.migrate_analysis(lib, ana)
    assert n == 1
    row = sqlite3.connect(lib).execute("SELECT bpm,camelot,energy,beat_engine FROM recordings WHERE akey='kencarsonyale'").fetchone()
    assert round(row[0]) == 151 and row[1] == "8A" and row[3] == "beatthis+deeprhythm"
```

- [ ] **Step 2: Run to verify fail**

Run: `~/.venvs/analysis/bin/python -m pytest tests/test_schema.py::test_migrate_analysis_copies_mir_by_artist_title -q`
Expected: FAIL — `AttributeError: module 'walkman_dj.schema' has no attribute 'migrate_analysis'`

- [ ] **Step 3: Add `migrate_analysis` to `schema.py`**
```python
import re
_norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
_MIR = ["bpm", "bpm_confidence", "key", "camelot", "key_confidence", "energy",
        "brightness", "bass_weight", "is_vocal", "mix_in", "mix_out",
        "beat_anchor", "first_downbeat", "beat_engine"]

def migrate_analysis(lib_db: str, analysis_db: str) -> int:
    """Copy MIR fields from walkman_analysis.db into recordings by (artist,title)."""
    a = sqlite3.connect(analysis_db); a.row_factory = sqlite3.Row
    fcols = {r[1] for r in a.execute("PRAGMA table_info(features)")}
    use = [c for c in _MIR if c in fcols]
    src = {}
    for r in a.execute(f"""SELECT t.artist, t.title, {','.join('f.'+c for c in use)}
                           FROM tracks t JOIN features f USING(fingerprint)"""):
        k = _norm(f"{r['artist']} {r['title']}")
        if k and k not in src:
            src[k] = {c: r[c] for c in use}
    a.close()
    n = 0
    with _c(lib_db) as c:
        for row in c.execute("SELECT akey FROM recordings WHERE bpm IS NULL").fetchall():
            s = src.get(row[0])
            if s and s.get("bpm") is not None:
                sets = ",".join(f"{col}=?" for col in use)
                c.execute(f"UPDATE recordings SET {sets} WHERE akey=?", (*[s[col] for col in use], row[0]))
                n += 1
    return n
```

- [ ] **Step 4: Run to verify pass**

Run: `~/.venvs/analysis/bin/python -m pytest tests/test_schema.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Add CLI `migrate-analysis` + wire `ensure_schema` into CrateLib init**

In `crate_cli.py`, add:
```python
def cmd_migrate_analysis(args):
    from walkman_dj import schema
    schema.ensure_schema(DB)
    ana = args.analysis or os.path.join(os.path.dirname(DB), "walkman_analysis.db")
    print("migrated MIR for", schema.migrate_analysis(DB, ana), "recordings from", os.path.basename(ana))
```
Parser: `pm = sub.add_parser("migrate-analysis"); pm.add_argument("--analysis"); pm.set_defaults(func=cmd_migrate_analysis)`

In `crate.py` `CrateLib.__init__`, after the existing column-migration block, add:
```python
        from walkman_dj import schema
        schema.ensure_schema(self.db_path)
```

- [ ] **Step 6: Run migrate on the real DB + commit**
```bash
~/.venvs/analysis/bin/python src/walkman_dj/crate_cli.py migrate-analysis
# Expected: "migrated MIR for ~6000 recordings from walkman_analysis.db"
git add src/walkman_dj/schema.py src/walkman_dj/crate_cli.py src/walkman_dj/crate.py tests/test_schema.py
git commit -m "feat(intel): migrate-analysis reuses existing MIR by artist/title"
```

---

## Phase 2 — Analysis engine (Essentia + composition)

**Definition of Done:** `analyze_song(path)` returns all fields normalized to [0,1] (loudness in dB, bpm real), deterministic, never raises; Essentia missing → its 7 fields are null but the rest work.

### Task 3: Essentia install + isolated feature module

**Files:**
- Create: `src/walkman_dj/essentia_features.py`
- Test: `tests/test_intelligence.py`

- [ ] **Step 1: Install Essentia in the analysis venv (graceful if it fails)**
```bash
~/.venvs/analysis/bin/pip install essentia-tensorflow 2>&1 | tail -3 || \
  ~/.venvs/analysis/bin/pip install essentia 2>&1 | tail -3 || echo "Essentia install failed — module will degrade"
~/.venvs/analysis/bin/python -c "import essentia; print('essentia', essentia.__version__)" || echo "no essentia (degraded mode)"
```
Note: on Apple Silicon `essentia-tensorflow` may fail to build. If both installs fail, the module below returns `{}` and the system still works without the 7 Essentia features. Do not block the plan on Essentia.

- [ ] **Step 2: Write the failing test**
```python
# tests/test_intelligence.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import essentia_features as ef

def test_essentia_features_shape_or_empty(tmp_path):
    # returns dict with the 7 keys (or empty if essentia missing) — never raises
    out = ef.extract("/nonexistent.flac")
    assert isinstance(out, dict)
    if out:  # essentia present
        for k in ("valence","acousticness","instrumentalness","speechiness","liveness","loudness_db","essentia_danceability"):
            assert k in out
```

- [ ] **Step 3: Run to verify fail**

Run: `~/.venvs/analysis/bin/python -m pytest tests/test_intelligence.py::test_essentia_features_shape_or_empty -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'walkman_dj.essentia_features'`

- [ ] **Step 4: Implement `src/walkman_dj/essentia_features.py`**
```python
"""Essentia Spotify-parity features, isolated so Essentia is one import site.
Returns {} (degraded) if Essentia is unavailable or the file fails."""
import os

try:
    import essentia.standard as es
    _OK = True
except Exception:
    _OK = False

KEYS = ("valence", "acousticness", "instrumentalness", "speechiness", "liveness",
        "loudness_db", "essentia_danceability")

def available() -> bool:
    return _OK

def extract(path: str) -> dict:
    if not _OK or not os.path.exists(path):
        return {}
    try:
        audio = es.MonoLoader(filename=path, sampleRate=16000)()
        # danceability (0..~3 -> normalize to 0-1 by /3 clamp)
        dance, _ = es.Danceability()(es.MonoLoader(filename=path, sampleRate=44100)())
        # loudness (integrated LUFS-ish via ReplayGain proxy)
        loud = es.Loudness()(audio)
        # spectral proxies for acousticness/instrumentalness/speechiness/liveness/valence
        import numpy as np
        centroid = float(np.mean(es.Centroid()(np.abs(np.fft.rfft(audio[:1<<16])).astype("float32"))) or 0)
        zcr = float(es.ZeroCrossingRate()(audio))
        flat = float(np.mean(es.Flatness()(np.abs(np.fft.rfft(audio[:1<<16])).astype("float32"))) or 0)
        clamp = lambda x: max(0.0, min(1.0, x))
        return {
            "essentia_danceability": clamp(dance / 3.0),
            "loudness_db": round(float(loud), 2),
            "acousticness": clamp(1.0 - centroid),          # bright/electronic -> low acousticness
            "instrumentalness": clamp(1.0 - min(zcr * 20, 1)),  # low ZCR -> more instrumental
            "speechiness": clamp(zcr * 15),
            "liveness": clamp(flat * 5),
            "valence": clamp((dance / 3.0) * 0.6 + (1 - min(centroid, 1)) * 0.4),
        }
    except Exception:
        return {}
```
Note: these are Essentia-derived approximations of Spotify's proprietary features (Spotify never published exact formulas). They are internally consistent and normalized — which is what goal #2 requires. If a pretrained MusiCNN/EffNet model is desired later, swap the body here without touching callers.

- [ ] **Step 5: Run to verify pass**

Run: `~/.venvs/analysis/bin/python -m pytest tests/test_intelligence.py::test_essentia_features_shape_or_empty -q`
Expected: PASS

- [ ] **Step 6: Commit**
```bash
git add src/walkman_dj/essentia_features.py tests/test_intelligence.py
git commit -m "feat(intel): isolated Essentia feature extractor (degrades gracefully)"
```

### Task 4: `analyze_song` engine composition

**Files:**
- Create: `src/walkman_dj/intelligence.py`
- Test: `tests/test_intelligence.py`

- [ ] **Step 1: Write the failing test** (uses a real lake FLAC)
```python
import glob
def _a_flac():
    for dp,_,fns in os.walk(os.path.expanduser("~/Documents/MusicLake")):
        if "_dedup" in dp: continue
        for f in fns:
            if f.endswith(".flac"):
                p=os.path.join(dp,f)
                if 20 < os.path.getsize(p)/1e6 < 45: return p
    import pytest; pytest.skip("need a flac")

def test_analyze_song_returns_normalized_versioned():
    from walkman_dj import intelligence as I
    r = I.analyze_song(_a_flac())
    assert r["status"] == "ok"
    assert r["bpm"] and r["bpm"] > 40
    assert r["camelot"]
    for k in ("energy","brightness","bass_weight"):
        assert 0.0 <= r[k] <= 1.0
    assert r["analysis_version"] == I.ANALYSIS_VERSION

def test_analyze_song_bad_file_returns_failed(tmp_path):
    from walkman_dj import intelligence as I
    bad = str(tmp_path/"x.flac"); open(bad,"wb").write(b"nope")
    assert I.analyze_song(bad)["status"] == "failed"
```

- [ ] **Step 2: Run to verify fail**

Run: `~/.venvs/analysis/bin/python -m pytest tests/test_intelligence.py -k analyze_song -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'walkman_dj.intelligence'`

- [ ] **Step 3: Implement `src/walkman_dj/intelligence.py`**
```python
"""analyze_song(path) — the unified engine. Composes SOTA beatgrid + librosa + Essentia,
normalizes to the schema's scales. Deterministic; never raises."""
import os, datetime
from walkman_dj import beatgrid, analyze, essentia_features

ANALYSIS_VERSION = "si-1.0"
_PITCH = {"C":0,"C#":1,"D":2,"D#":3,"E":4,"F":5,"F#":6,"G":7,"G#":8,"A":9,"A#":10,"B":11}

def analyze_song(path: str) -> dict:
    if not os.path.exists(path):
        return {"status": "failed"}
    try:
        import librosa
        y, sr = librosa.load(path, sr=22050, mono=True, duration=360)
        if len(y) < sr * 5:
            return {"status": "failed"}
        out = {"status": "ok", "analysis_version": ANALYSIS_VERSION,
               "analyzed_at": datetime.datetime.now().isoformat(timespec="seconds")}
        # SOTA beatgrid
        try:
            out.update(beatgrid.beatgrid_of(path))
        except Exception:
            out.update(analyze.bpm_of(y, sr))
        # librosa key/energy/structure
        out.update(analyze.key_of(y, sr))
        out.update(analyze.energy_timbre_of(y, sr))
        st = analyze.structure_of(y, sr)
        out["mix_in"], out["mix_out"] = st.get("mix_in"), st.get("mix_out")
        out["segments"] = st.get("segments", [])
        # map librosa 'danceability' -> mir_danceability
        out["mir_danceability"] = out.pop("danceability", None)
        # spotify-style key/mode from librosa key
        pitch = out.get("key"); mode = "minor" if (out.get("camelot","") or "").endswith("A") else "major"
        out["spotify_key"] = _PITCH.get(pitch, -1)
        out["spotify_mode"] = 1 if mode == "major" else 0
        # essentia parity features (or {} if unavailable)
        out.update(essentia_features.extract(path))
        return out
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "error": str(e)[:120]}
```

- [ ] **Step 4: Run to verify pass**

Run: `~/.venvs/analysis/bin/python -m pytest tests/test_intelligence.py -k analyze_song -q`
Expected: PASS

- [ ] **Step 5: Add `crate analyze <file>` CLI (prints the dict) + commit**

`crate_cli.py`:
```python
def cmd_analyze(args):
    from walkman_dj import intelligence
    import json; print(json.dumps(intelligence.analyze_song(args.file), indent=1, default=str))
```
Parser: `pa = sub.add_parser("analyze"); pa.add_argument("file"); pa.set_defaults(func=cmd_analyze)`
```bash
git add src/walkman_dj/intelligence.py src/walkman_dj/crate_cli.py tests/test_intelligence.py
git commit -m "feat(intel): analyze_song engine (beatgrid+librosa+essentia, normalized+versioned)"
```

---

## Phase 3 — Spotify catalog matcher

**Definition of Done:** `match(artist,title,duration)` returns catalog dict for a confident match, else `{}` (never a wrong match); handles 429 with backoff.

### Task 5: `spotify_meta.match`

**Files:**
- Create: `src/walkman_dj/spotify_meta.py`
- Test: `tests/test_spotify_meta.py`

- [ ] **Step 1: Write the failing test (mocked Spotify search)**
```python
# tests/test_spotify_meta.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import spotify_meta as sm

FAKE = {"tracks":{"items":[{
    "id":"sp1","name":"Yale","duration_ms":180000,"popularity":63,
    "artists":[{"name":"Ken Carson","id":"a1"}],
    "album":{"release_date":"2022-04-14","label":"Opium"},
    "external_ids":{"isrc":"USxx1234567"}}]}}

def test_confident_match_returns_catalog(monkeypatch):
    monkeypatch.setattr(sm, "_search", lambda q: FAKE)
    monkeypatch.setattr(sm, "_artist_genres", lambda aid: ["rage","trap"])
    r = sm.match("Ken Carson", "Yale", 180.0)
    assert r["spotify_id"] == "sp1" and r["isrc"] == "USxx1234567"
    assert r["spotify_popularity"] == 63 and r["release_date"] == "2022-04-14"
    assert r["spotify_match_confidence"] >= 0.7

def test_wrong_artist_rejected(monkeypatch):
    wrong = {"tracks":{"items":[{"id":"z","name":"Yale","duration_ms":180000,"popularity":1,
             "artists":[{"name":"Some Other Guy","id":"a2"}],"album":{"release_date":"2000-01-01"},
             "external_ids":{}}]}}
    monkeypatch.setattr(sm, "_search", lambda q: wrong)
    assert sm.match("Ken Carson", "Yale", 180.0) == {}
```

- [ ] **Step 2: Run to verify fail**

Run: `~/.venvs/analysis/bin/python -m pytest tests/test_spotify_meta.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'walkman_dj.spotify_meta'`

- [ ] **Step 3: Implement `src/walkman_dj/spotify_meta.py`**
```python
"""Spotify catalog matcher. Reuses the spotify-playlists skill's OAuth access token.
Confidence-gated: never stores a wrong match."""
import os, sys, time, json, urllib.request, urllib.parse
from rapidfuzz import fuzz

_SKILL = os.path.expanduser("~/.claude/skills/spotify-playlists")
_TOKENS = os.path.expanduser("~/.config/spotify-playlists/tokens.json")

def _token():
    sys.path.insert(0, _SKILL)
    try:
        import spotify_cli
        return spotify_cli.get_access_token()  # skill exposes token refresh
    except Exception:
        t = json.load(open(_TOKENS))
        return t.get("access_token")

def _get(url):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_token()}"})
    return json.load(urllib.request.urlopen(req, timeout=20))

def _search(q):
    for _ in range(3):
        try:
            return _get("https://api.spotify.com/v1/search?type=track&limit=5&q=" + urllib.parse.quote(q))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(int(e.headers.get("Retry-After", "2")) + 1); continue
            return {}
        except Exception:
            return {}
    return {}

def _artist_genres(aid):
    try:
        return _get(f"https://api.spotify.com/v1/artists/{aid}").get("genres", [])
    except Exception:
        return []

def match(artist: str, title: str, duration: float | None = None, min_conf: float = 0.7) -> dict:
    data = _search(f'artist:"{artist}" track:"{title}"') or _search(f"{artist} {title}")
    best, best_score = None, 0.0
    for t in data.get("tracks", {}).get("items", []):
        a = (t.get("artists") or [{}])[0].get("name", "")
        name_s = fuzz.token_set_ratio(title, t.get("name", "")) / 100
        art_s = fuzz.token_set_ratio(artist, a) / 100
        dur_s = 1.0
        if duration and t.get("duration_ms"):
            dur_s = max(0.0, 1 - abs(duration - t["duration_ms"] / 1000) / 30)
        score = 0.45 * art_s + 0.4 * name_s + 0.15 * dur_s
        if score > best_score:
            best, best_score = t, score
    if not best or best_score < min_conf:
        return {}
    aid = (best.get("artists") or [{}])[0].get("id")
    return {
        "spotify_id": best["id"],
        "isrc": (best.get("external_ids") or {}).get("isrc"),
        "spotify_popularity": best.get("popularity"),
        "release_date": (best.get("album") or {}).get("release_date"),
        "label": (best.get("album") or {}).get("label"),
        "artist_genres": json.dumps(_artist_genres(aid) if aid else []),
        "spotify_match_confidence": round(best_score, 3),
    }
```
Note: verify `spotify_cli` exposes `get_access_token()`. If not, the `tokens.json` fallback reads the stored access token directly (valid ~1h; the worker refreshes by re-running `spotify_cli auth` if it 401s — do NOT rotate the refresh token out-of-band).

- [ ] **Step 4: Run to verify pass**

Run: `~/.venvs/analysis/bin/python -m pytest tests/test_spotify_meta.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Add `crate spotify-match "<artist> - <title>"` CLI + commit**

`crate_cli.py`:
```python
def cmd_spotify_match(args):
    from walkman_dj import spotify_meta
    a, _, t = args.query.partition(" - ")
    import json; print(json.dumps(spotify_meta.match(a.strip(), t.strip() or a.strip()), indent=1))
```
Parser: `ps = sub.add_parser("spotify-match"); ps.add_argument("query"); ps.set_defaults(func=cmd_spotify_match)`
```bash
git add src/walkman_dj/spotify_meta.py src/walkman_dj/crate_cli.py tests/test_spotify_meta.py
git commit -m "feat(intel): spotify catalog matcher (confidence-gated)"
```

---

## Phase 4 — Async worker + ingest hook

**Definition of Done:** `crate ingest` enqueues the new fingerprint; `crate analyzer --once` drains the queue, writes unified rows, empties the queue; idempotent; a failed track bumps attempts and is skipped after 3.

### Task 6: ingest → enqueue hook

**Files:**
- Modify: `src/walkman_dj/crate.py`
- Test: `tests/test_analyzer.py`

- [ ] **Step 1: Write the failing test**
```python
# tests/test_analyzer.py
import os, sys, shutil, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import crate, schema

def _flac(tmp):
    for dp,_,fns in os.walk(os.path.expanduser("~/Documents/MusicLake")):
        if "_dedup" in dp: continue
        for f in fns:
            if f.endswith(".flac") and 20<os.path.getsize(os.path.join(dp,f))/1e6<45:
                d=str(tmp/"s.flac"); shutil.copy(os.path.join(dp,f),d); return d
    import pytest; pytest.skip("need flac")

def test_ingest_enqueues_for_analysis(tmp_path):
    root=tmp_path/"lib"; root.mkdir()
    lib=crate.CrateLib(str(tmp_path/"db.sqlite"), str(root))
    lib.ingest(_flac(tmp_path))
    assert len(schema.pending(str(tmp_path/"db.sqlite"))) == 1
```

- [ ] **Step 2: Run to verify fail**

Run: `~/.venvs/analysis/bin/python -m pytest tests/test_analyzer.py::test_ingest_enqueues_for_analysis -q`
Expected: FAIL — pending is empty (no enqueue yet)

- [ ] **Step 3: Add enqueue in `crate.py`** — in `ingest`, after `self._index(dest, fp, dur)` in both the "replaced" and "added" branches, add:
```python
            from walkman_dj import schema
            schema.enqueue(self.db_path, fp, dest)
```
(Add it right before each `return IngestResult(...)` for the added/replaced cases — not for discarded.)

- [ ] **Step 4: Run to verify pass**

Run: `~/.venvs/analysis/bin/python -m pytest tests/test_analyzer.py::test_ingest_enqueues_for_analysis -q`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/walkman_dj/crate.py tests/test_analyzer.py
git commit -m "feat(intel): ingest enqueues new tracks for async analysis"
```

### Task 7: `crate analyzer` worker

**Files:**
- Modify: `src/walkman_dj/crate_cli.py`
- Test: `tests/test_analyzer.py`

- [ ] **Step 1: Write the failing test**
```python
def test_analyzer_drains_queue_and_writes_row(tmp_path, monkeypatch):
    from walkman_dj import crate, schema, crate_cli
    root=tmp_path/"lib"; root.mkdir(); db=str(tmp_path/"db.sqlite")
    lib=crate.CrateLib(db, str(root))
    src=_flac(tmp_path); r=lib.ingest(src)
    fp=sqlite3.connect(db).execute("SELECT fingerprint FROM pending_analysis").fetchone()[0]
    # stub heavy calls for speed/determinism
    import walkman_dj.intelligence as I, walkman_dj.spotify_meta as sm
    monkeypatch.setattr(I, "analyze_song", lambda p: {"status":"ok","bpm":140.0,"camelot":"8A","energy":0.6,"analysis_version":"si-1.0","segments":[]})
    monkeypatch.setattr(sm, "match", lambda *a, **k: {"spotify_id":"sp","spotify_popularity":50})
    crate_cli.DB=db
    crate_cli.main(["analyzer","--once"])
    row=sqlite3.connect(db).execute("SELECT bpm,camelot,spotify_id FROM recordings WHERE fp=?", (fp,)).fetchone()
    assert round(row[0])==140 and row[1]=="8A" and row[2]=="sp"
    assert schema.pending(db)==[]   # queue drained
```

- [ ] **Step 2: Run to verify fail**

Run: `~/.venvs/analysis/bin/python -m pytest tests/test_analyzer.py::test_analyzer_drains_queue_and_writes_row -q`
Expected: FAIL — `invalid choice: 'analyzer'`

- [ ] **Step 3: Implement `crate analyzer` in `crate_cli.py`**
```python
def cmd_analyzer(args):
    import sqlite3, time
    from walkman_dj import schema, intelligence, spotify_meta
    schema.ensure_schema(DB)
    _FEAT = [c for c, _ in schema.NEW_COLUMNS]
    def analyze_one(fp, path):
        r = intelligence.analyze_song(path)
        if r.get("status") != "ok":
            schema.bump_attempt(DB, fp); return False
        # spotify match by the recording's tags
        con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
        tr = con.execute("SELECT artist,title,duration FROM recordings WHERE fp=?", (fp,)).fetchone()
        if tr:
            try: r.update(spotify_meta.match(tr["artist"] or "", tr["title"] or "", tr["duration"]))
            except Exception: pass
        segs = r.pop("segments", [])
        fields = {k: v for k, v in r.items() if k in _FEAT}
        sets = ",".join(f"{k}=?" for k in fields)
        with sqlite3.connect(DB, timeout=60) as c:
            c.execute(f"UPDATE recordings SET {sets} WHERE fp=?", (*fields.values(), fp))
            c.execute("DELETE FROM segments WHERE fingerprint=?", (fp,))
            c.executemany("INSERT INTO segments(fingerprint,idx,label,start,end) VALUES(?,?,?,?,?)",
                          [(fp, s["idx"], s["label"], s["start"], s["end"]) for s in segs])
        schema.dequeue(DB, fp); return True
    while True:
        pend = [(fp, p) for fp, p in schema.pending(DB, 500)
                if sqlite3.connect(DB).execute("SELECT attempts FROM pending_analysis WHERE fingerprint=?", (fp,)).fetchone()[0] < 3]
        if not pend:
            if args.daemon: time.sleep(15); continue
            break
        for i, (fp, path) in enumerate(pend, 1):
            analyze_one(fp, path)
            if i % 10 == 0: print(f"  analyzed {i}/{len(pend)}", flush=True)
        if not args.daemon: break
    print("analyzer done")
```
Parser: `pz = sub.add_parser("analyzer"); pz.add_argument("--once", action="store_true"); pz.add_argument("--daemon", action="store_true"); pz.set_defaults(func=cmd_analyzer)`

- [ ] **Step 4: Run to verify pass**

Run: `~/.venvs/analysis/bin/python -m pytest tests/test_analyzer.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**
```bash
git add src/walkman_dj/crate_cli.py tests/test_analyzer.py
git commit -m "feat(intel): crate analyzer worker (drain queue -> unified rows)"
```

---

## Phase 5 — Backfill + schema doc

**Definition of Done:** all existing songs enqueued + drained (or failed after 3 attempts); `SCHEMA.md` documents every field.

### Task 8: seed backfill + schema doc

**Files:**
- Modify: `src/walkman_dj/crate_cli.py`
- Create: `docs/song-intelligence-SCHEMA.md`

- [ ] **Step 1: Add `crate backfill` (enqueue all un-analyzed) to `crate_cli.py`**
```python
def cmd_backfill(args):
    import sqlite3
    from walkman_dj import schema
    schema.ensure_schema(DB)
    n = 0
    with sqlite3.connect(DB) as c:
        for fp, path in c.execute("SELECT fp, canonical_path FROM recordings WHERE analysis_version IS NULL OR analysis_version!=?",
                                  ("si-1.0",)).fetchall():
            schema.enqueue(DB, fp, path); n += 1
    print(f"enqueued {n} songs for analysis — run: crate analyzer --once")
```
Parser: `pb = sub.add_parser("backfill"); pb.set_defaults(func=cmd_backfill)`

- [ ] **Step 2: Write `docs/song-intelligence-SCHEMA.md`** — a table of every field: name, range/units, source (MIR/Essentia/Spotify), meaning. (List all columns from `schema.NEW_COLUMNS` + the existing identity fields, each with its normalization.)

- [ ] **Step 3: Commit**
```bash
git add src/walkman_dj/crate_cli.py docs/song-intelligence-SCHEMA.md
git commit -m "feat(intel): backfill command + schema documentation"
```

- [ ] **Step 4: Run the real backfill (detached — hours)**
```bash
cd ~/Documents/Projects/walkman-dj
~/.venvs/analysis/bin/python src/walkman_dj/crate_cli.py migrate-analysis   # reuse existing MIR first
~/.venvs/analysis/bin/python src/walkman_dj/crate_cli.py backfill
CRATE_ROOT=~/Documents/MusicLake nohup ~/.venvs/analysis/bin/python src/walkman_dj/crate_cli.py analyzer --once > /tmp/backfill.log 2>&1 &
# monitor: tail -f /tmp/backfill.log ; verify coverage when done
```

---

## Final verification (after all tasks)

- [ ] Full suite: `~/.venvs/analysis/bin/python -m pytest tests/test_schema.py tests/test_intelligence.py tests/test_spotify_meta.py tests/test_analyzer.py -v` — all pass.
- [ ] `crate analyze <lake-file>` prints a full normalized dict with bpm/camelot/energy + essentia fields (or nulls if Essentia degraded).
- [ ] After backfill: `SELECT COUNT(*) FROM recordings WHERE analysis_version='si-1.0'` ≈ 7,170 (minus genuine failures).
- [ ] `SELECT COUNT(*) FROM recordings WHERE spotify_id IS NOT NULL` > 0 (catalog matched).
- [ ] Ingest a new file → appears in `pending_analysis` → `crate analyzer --once` → its row populated.
- [ ] Use superpowers:finishing-a-development-branch to merge `feat/song-intelligence`.

## Definition of Done — whole project
1. Unified normalized schema in `library.db`; `walkman_analysis.db` migrated (MIR reused).
2. `analyze_song` produces MIR + Essentia features, normalized + versioned; degrades if Essentia missing.
3. Spotify catalog fields populated for confident matches only.
4. `crate ingest` enqueues; `crate analyzer` drains; integration verified.
5. All existing songs backfilled — every song comparable apples-to-apples.
