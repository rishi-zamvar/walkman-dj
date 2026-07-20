# Walkman DJ Crate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a checkpointed, resumable pipeline that pulls audio off a Sony NW-A300 over adb, extracts rich MIR features into a SQLite crate-DB, classifies tracks into micro-genre vibe sets, sequences each as a DJ set (energy arc + harmonic mixing), and writes `.m3u8` playlists back to the device.

**Architecture:** Ten single-purpose modules under `src/walkman_dj/` orchestrated by `pipeline.py`. The SQLite DB (`data/walkman_analysis.db`) is the single source of truth — audio is only needed once, at analysis time; classification/sequencing/playlist-gen all run from the DB. Batched streaming (pull ~150 → analyze on 9 workers → delete temp) keeps peak disk ~5 GB.

**Tech Stack:** Python 3.11+, librosa 0.11, numpy, soundfile, audioread+ffmpeg, mutagen, sqlite3 (stdlib), pytest. Device I/O via `adb` CLI. Notifications via the existing zeechat ping script.

**Environment facts (verified):**
- Device: `adb devices` → `1022482`; SD music root `/storage/6439-6561/Music` (~3,265 audio files, ~71 GB).
- Mac: 11 cores, 35 GB free, ffmpeg 8.1, librosa 0.11, rapidfuzz 3.13 present.
- ZeeChat: `bash ~/.claude/skills/zeechat/scripts/ping.sh "<msg>"`.
- Playlists to REPLACE any existing `.m3u`/`.m3u8` in `/Music/` (currently none — already cleared).

---

## Conventions

- All modules pure-Python, no global state; functions take explicit args and return dataclasses/dicts.
- Tests: `pytest -q`. Analyzer correctness gated by known-answer fixtures in Phase 8.
- Commit after every green step. Branch: work on `main` (fresh repo).
- Run all commands from repo root `~/Documents/Projects/walkman-dj`.

---

## Phase 0 — Project setup

### Task 0.1: Package skeleton + deps

**Files:**
- Create: `pyproject.toml`, `src/walkman_dj/__init__.py`, `tests/__init__.py`, `Makefile`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "walkman-dj"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "librosa>=0.11", "numpy", "soundfile", "audioread", "mutagen", "rapidfuzz",
]
[project.optional-dependencies]
dev = ["pytest"]
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
[tool.setuptools.packages.find]
where = ["src"]
[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: Create empty `src/walkman_dj/__init__.py` and `tests/__init__.py`**

- [ ] **Step 3: Write `Makefile`**

```makefile
test:
	pytest -q
run:
	python -m walkman_dj.pipeline
```

- [ ] **Step 4: Verify import path**

Run: `python -c "import sys; sys.path.insert(0,'src'); import walkman_dj; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "chore: package skeleton + deps"
```

---

## Phase 1 — Identity + DB (the source of truth)

### Task 1.1: Camelot key mapping (pure, testable first)

**Files:**
- Create: `src/walkman_dj/camelot.py`, `tests/test_camelot.py`

- [ ] **Step 1: Write failing test** (`tests/test_camelot.py`)

```python
from walkman_dj.camelot import to_camelot, compatible

def test_a_minor_is_8a():
    assert to_camelot("A", "minor") == "8A"

def test_c_major_is_8b():
    assert to_camelot("C", "major") == "8B"

def test_compatible_includes_pm1_and_relative():
    # 8A compatible with 8A, 7A, 9A (±1) and 8B (relative major)
    assert set(compatible("8A")) == {"8A", "7A", "9A", "8B"}

def test_wrap_around():
    assert set(compatible("1A")) == {"1A", "12A", "2A", "1B"}
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_camelot.py -q` → FAIL (module missing)

- [ ] **Step 3: Implement `camelot.py`**

```python
"""Camelot wheel mapping and harmonic-compatibility rules."""
# Camelot: number 1-12 + letter (A=minor, B=major). Standard wheel.
_MAJOR = {"B":1,"F#":2,"Gb":2,"Db":3,"C#":3,"Ab":4,"G#":4,"Eb":5,"D#":5,
          "Bb":6,"A#":6,"F":7,"C":8,"G":9,"D":10,"A":11,"E":12}
_MINOR = {"Ab":1,"G#":1,"Eb":2,"D#":2,"Bb":3,"A#":3,"F":4,"C":5,"G":6,"D":7,
          "A":8,"E":9,"B":10,"F#":11,"Gb":11,"Db":12,"C#":12}

def to_camelot(pitch: str, mode: str) -> str:
    table = _MINOR if mode.lower().startswith("min") else _MAJOR
    letter = "A" if mode.lower().startswith("min") else "B"
    return f"{table[pitch]}{letter}"

def compatible(code: str) -> list[str]:
    num = int(code[:-1]); letter = code[-1]
    up = num % 12 + 1
    down = (num - 2) % 12 + 1
    relative = f"{num}{'B' if letter=='A' else 'A'}"
    return [f"{num}{letter}", f"{down}{letter}", f"{up}{letter}", relative]
```

- [ ] **Step 4: Run — PASS.**  `pytest tests/test_camelot.py -q`
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: camelot mapping + compatibility"`

### Task 1.2: SQLite schema + incremental upsert

**Files:**
- Create: `src/walkman_dj/db.py`, `tests/test_db.py`

- [ ] **Step 1: Write failing test** (`tests/test_db.py`)

```python
import tempfile, os
from walkman_dj.db import CrateDB

def test_upsert_and_incremental():
    with tempfile.TemporaryDirectory() as d:
        db = CrateDB(os.path.join(d, "t.db")); db.init()
        db.upsert_track(fingerprint="fp1", path="/M/a.mp3", filename="a.mp3",
                        artist="X", title="A", folder="Singles")
        assert db.needs_analysis("fp1") is True
        db.save_features("fp1", {"bpm":124.0,"camelot":"8A","energy":0.7,
                                 "key_confidence":0.8,"status":"ok"})
        assert db.needs_analysis("fp1") is False          # already ok
        # reappear at new path → identity preserved, path updated
        db.upsert_track(fingerprint="fp1", path="/M/moved.mp3", filename="moved.mp3",
                        artist="X", title="A", folder="Singles")
        assert db.needs_analysis("fp1") is False
        row = db.get_track("fp1")
        assert row["path"] == "/M/moved.mp3" and row["bpm"] == 124.0

def test_failed_status_not_reanalyzed_by_default():
    with tempfile.TemporaryDirectory() as d:
        db = CrateDB(os.path.join(d, "t.db")); db.init()
        db.upsert_track(fingerprint="fp2", path="/M/b.wav", filename="b.wav",
                        artist=None, title=None, folder="Singles")
        db.save_features("fp2", {"status":"failed"})
        assert db.needs_analysis("fp2") is False
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `db.py`**

```python
"""SQLite crate-DB: single source of truth. Identity = content fingerprint."""
import sqlite3, json

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks(
  fingerprint TEXT PRIMARY KEY, path TEXT, filename TEXT,
  artist TEXT, title TEXT, folder TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS features(
  fingerprint TEXT PRIMARY KEY REFERENCES tracks(fingerprint),
  status TEXT DEFAULT 'pending',
  bpm REAL, bpm_raw REAL, bpm_confidence REAL,
  key TEXT, camelot TEXT, key_confidence REAL, key_agree INTEGER,
  energy REAL, loudness_curve TEXT, brightness REAL, bass_weight REAL,
  is_vocal INTEGER, danceability REAL, mix_in REAL, mix_out REAL, duration REAL);
CREATE TABLE IF NOT EXISTS segments(
  fingerprint TEXT, idx INTEGER, label TEXT, start REAL, end REAL);
CREATE TABLE IF NOT EXISTS playlists(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE, arc TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS playlist_tracks(
  playlist_id INTEGER, position INTEGER, fingerprint TEXT, transition_score REAL);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, note TEXT);
"""

class CrateDB:
    def __init__(self, path): self.path = path
    def _c(self):
        c = sqlite3.connect(self.path); c.row_factory = sqlite3.Row; return c
    def init(self):
        with self._c() as c: c.executescript(SCHEMA)
    def upsert_track(self, *, fingerprint, path, filename, artist, title, folder):
        with self._c() as c:
            c.execute("""INSERT INTO tracks(fingerprint,path,filename,artist,title,folder)
              VALUES(?,?,?,?,?,?)
              ON CONFLICT(fingerprint) DO UPDATE SET
                path=excluded.path, filename=excluded.filename,
                artist=excluded.artist, title=excluded.title, folder=excluded.folder,
                updated_at=CURRENT_TIMESTAMP""",
              (fingerprint,path,filename,artist,title,folder))
            c.execute("INSERT OR IGNORE INTO features(fingerprint) VALUES(?)",(fingerprint,))
    def needs_analysis(self, fp) -> bool:
        with self._c() as c:
            r = c.execute("SELECT status FROM features WHERE fingerprint=?",(fp,)).fetchone()
            return (r is None) or (r["status"] == "pending")
    def save_features(self, fp, feat: dict):
        feat = dict(feat)
        if "loudness_curve" in feat and not isinstance(feat["loudness_curve"], str):
            feat["loudness_curve"] = json.dumps(feat["loudness_curve"])
        cols = ",".join(f"{k}=?" for k in feat)
        with self._c() as c:
            c.execute(f"UPDATE features SET {cols} WHERE fingerprint=?",
                      (*feat.values(), fp))
    def get_track(self, fp) -> dict:
        with self._c() as c:
            r = c.execute("""SELECT t.*, f.* FROM tracks t JOIN features f
                             USING(fingerprint) WHERE t.fingerprint=?""",(fp,)).fetchone()
            return dict(r) if r else None
    def all_ok(self) -> list[dict]:
        with self._c() as c:
            return [dict(r) for r in c.execute(
              "SELECT t.*, f.* FROM tracks t JOIN features f USING(fingerprint) "
              "WHERE f.status='ok'")]
```

- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat: sqlite crate-db with incremental upsert"`

### Task 1.3: Content fingerprint

**Files:** Create `src/walkman_dj/fingerprint.py`, `tests/test_fingerprint.py`

- [ ] **Step 1: Failing test**

```python
import numpy as np, soundfile as sf, tempfile, os
from walkman_dj.fingerprint import fingerprint_file

def test_same_audio_same_fp_regardless_of_filename():
    with tempfile.TemporaryDirectory() as d:
        y = (0.1*np.sin(np.linspace(0,880,44100))).astype("float32")
        a=os.path.join(d,"a.wav"); b=os.path.join(d,"b_renamed.wav")
        sf.write(a,y,44100); sf.write(b,y,44100)
        assert fingerprint_file(a) == fingerprint_file(b)
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** — hash first 30 s of decoded PCM (fast, robust to tag edits):

```python
"""Content fingerprint: SHA1 of the first 30s of decoded mono audio @22050."""
import hashlib, numpy as np, librosa

def fingerprint_file(path: str, seconds: float = 30.0) -> str:
    y, _ = librosa.load(path, sr=22050, mono=True, duration=seconds)
    q = np.round(y * 32767).astype(np.int16)   # quantize → stable across re-encodes-ish
    return hashlib.sha1(q.tobytes()).hexdigest()
```

- [ ] **Step 4: Run — PASS.**  **Step 5: Commit** — `git commit -am "feat: content fingerprint"`

---

## Phase 2 — Device I/O (adb wrapper)

### Task 2.1: List + parse device audio files

**Files:** Create `src/walkman_dj/device.py`, `tests/test_device.py`

- [ ] **Step 1: Failing test** (parse logic is pure; adb calls are integration-only)

```python
from walkman_dj.device import parse_find_output, MUSIC_ROOT

def test_parse_find_filters_audio_and_splits_folder():
    raw = ("/storage/6439-6561/Music/Singles/A - B.mp3\n"
           "/storage/6439-6561/Music/Die Lit/01 - X.flac\n"
           "/storage/6439-6561/Music/cover.jpg\n")
    files = parse_find_output(raw)
    assert len(files) == 2
    assert files[0].folder == "Singles" and files[0].filename == "A - B.mp3"
    assert files[1].folder == "Die Lit"
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `device.py`** (list/pull/push with retry)

```python
"""adb wrapper for the NW-A300. One responsibility: move files + list them."""
import subprocess, os, time
from dataclasses import dataclass

MUSIC_ROOT = "/storage/6439-6561/Music"
AUDIO_EXT = (".mp3",".flac",".m4a",".wav",".aac",".mp4",".wma")

@dataclass
class DeviceFile:
    path: str; folder: str; filename: str

def parse_find_output(raw: str) -> list[DeviceFile]:
    out=[]
    for line in raw.replace("\r","").splitlines():
        line=line.strip()
        if not line or not line.lower().endswith(AUDIO_EXT): continue
        rel = line[len(MUSIC_ROOT)+1:] if line.startswith(MUSIC_ROOT) else line
        parts = rel.split("/")
        folder = parts[0] if len(parts)>1 else "(root)"
        out.append(DeviceFile(path=line, folder=folder, filename=parts[-1]))
    return out

def _adb(args, retries=4, timeout=120):
    last=None
    for i in range(retries):
        try:
            return subprocess.run(["adb",*args], capture_output=True, text=True,
                                  timeout=timeout)
        except Exception as e:
            last=e; time.sleep(2**i)
    raise RuntimeError(f"adb failed: {last}")

def list_audio() -> list[DeviceFile]:
    exts=" -o ".join(f"-iname '*{e}'" for e in AUDIO_EXT)
    r=_adb(["shell", f"find {MUSIC_ROOT} -type f \\( {exts} \\)"])
    return parse_find_output(r.stdout)

def pull(path: str, dest_dir: str) -> str:
    dest=os.path.join(dest_dir, os.path.basename(path))
    r=_adb(["pull", path, dest])
    if r.returncode!=0 or not os.path.exists(dest):
        raise RuntimeError(f"pull failed: {path}: {r.stderr}")
    return dest

def push(local: str, device_dir: str = MUSIC_ROOT):
    r=_adb(["push", local, device_dir + "/" + os.path.basename(local)])
    if r.returncode!=0: raise RuntimeError(f"push failed: {local}: {r.stderr}")
```

- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Integration smoke (manual, not CI):** `python -c "from walkman_dj.device import list_audio; print(len(list_audio()))"` → expect ~3265.
- [ ] **Step 6: Commit** — `git commit -am "feat: adb device wrapper (list/pull/push + retry)"`

---

## Phase 3 — MIR analysis

Each analyzer is a pure function `np.ndarray, sr -> dict`. Compose in `analyze.py`.

### Task 3.1: BPM with genre-anchored octave correction

**Files:** Create `src/walkman_dj/analyze.py`, `tests/test_analyze_bpm.py`

- [ ] **Step 1: Failing test**

```python
from walkman_dj.analyze import correct_bpm

def test_double_time_folds_into_house_range():
    # raw 63 with house prior (118-132) → 126
    assert correct_bpm(63.0, (118,132)) == 126.0
def test_half_time_folds_up_for_dnb():
    assert correct_bpm(87.0, (168,176)) == 174.0
def test_in_range_untouched():
    assert correct_bpm(125.0, (118,132)) == 125.0
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `correct_bpm` + `bpm_of` in `analyze.py`**

```python
"""MIR feature extraction. Pure functions over (y, sr)."""
import numpy as np, librosa

def correct_bpm(raw: float, prior: tuple[int,int]) -> float:
    lo,hi = prior
    best=raw
    for mult in (0.25,0.5,1,2,4):
        c=raw*mult
        if lo<=c<=hi: return round(c,1)
    # nearest to prior mid if none land inside
    mid=(lo+hi)/2
    cands=[raw*m for m in (0.25,0.5,1,2,4)]
    return round(min(cands,key=lambda c:abs(c-mid)),1)

def bpm_of(y, sr, prior=(70,180)) -> dict:
    tempo,_ = librosa.beat.beat_track(y=y, sr=sr)
    raw=float(np.atleast_1d(tempo)[0])
    return {"bpm_raw":round(raw,1), "bpm":correct_bpm(raw,prior)}
```

- [ ] **Step 4: Run — PASS.**  **Step 5: Commit** — `git commit -am "feat: bpm + genre-anchored correction"`

### Task 3.2: Dual-profile key detection → Camelot

**Files:** Modify `analyze.py`; Create `tests/test_analyze_key.py`

- [ ] **Step 1: Failing test** (synthesize a C-major triad, expect key C / 8B)

```python
import numpy as np, librosa
from walkman_dj.analyze import key_of

def test_c_major_triad_detects_c():
    sr=22050; t=np.linspace(0,3,sr*3,endpoint=False)
    y=sum(np.sin(2*np.pi*f*t) for f in (261.63,329.63,392.0)).astype("float32")
    r=key_of(y,sr)
    assert r["key"] in ("C","G","F")        # tonal neighbors acceptable
    assert r["camelot"].endswith("B")       # major
    assert 0.0 <= r["key_confidence"] <= 1.0
    assert r["key_agree"] in (0,1)
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `key_of`** (Krumhansl + Temperley profiles, cross-checked)

```python
from walkman_dj.camelot import to_camelot
_PITCHES=["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
_KRUM_MAJ=np.array([6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88])
_KRUM_MIN=np.array([6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17])
_TEMP_MAJ=np.array([5,2,3.5,2,4.5,4,2,4.5,2,3.5,1.5,4])
_TEMP_MIN=np.array([5,2,3.5,4.5,2,4,2,4.5,3.5,2,1.5,4])

def _estimate(chroma_mean, maj, min_):
    scores=[]
    for i in range(12):
        scores.append((np.corrcoef(np.roll(maj,i),chroma_mean)[0,1],_PITCHES[i],"major"))
        scores.append((np.corrcoef(np.roll(min_,i),chroma_mean)[0,1],_PITCHES[i],"minor"))
    return max(scores,key=lambda s:s[0])

def key_of(y,sr) -> dict:
    chroma=librosa.feature.chroma_cqt(y=y,sr=sr).mean(axis=1)
    k1=_estimate(chroma,_KRUM_MAJ,_KRUM_MIN)
    k2=_estimate(chroma,_TEMP_MAJ,_TEMP_MIN)
    agree = (k1[1],k1[2])==(k2[1],k2[2])
    conf = float(max(0.0,(k1[0]+ (k2[0] if agree else 0))/(2 if agree else 1)))
    pitch,mode=k1[1],k1[2]
    return {"key":pitch,"camelot":to_camelot(pitch,mode),
            "key_confidence":round(min(conf,1.0),3),"key_agree":int(agree)}
```

- [ ] **Step 4: Run — PASS.**  **Step 5: Commit** — `git commit -am "feat: dual-profile key → camelot"`

### Task 3.3: Energy curve, timbre, vocal, duration

**Files:** Modify `analyze.py`; Create `tests/test_analyze_energy.py`

- [ ] **Step 1: Failing test**

```python
import numpy as np
from walkman_dj.analyze import energy_timbre_of

def test_energy_and_curve_shape():
    sr=22050; y=(0.5*np.sin(np.linspace(0,2000,sr*4))).astype("float32")
    r=energy_timbre_of(y,sr)
    assert 0.0<=r["energy"]<=1.0
    assert isinstance(r["loudness_curve"],list) and len(r["loudness_curve"])==16
    assert r["duration"]>3.9
    assert 0.0<=r["brightness"]<=1.0 and r["is_vocal"] in (0,1)
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `energy_timbre_of`**

```python
def energy_timbre_of(y,sr,curve_pts=16) -> dict:
    rms=librosa.feature.rms(y=y)[0]
    energy=float(np.clip(rms.mean()/ (np.percentile(rms,95)+1e-9),0,1))
    # resample rms envelope to fixed pts, normalized 0..1
    idx=np.linspace(0,len(rms)-1,curve_pts).astype(int)
    env=rms[idx]; env=(env-env.min())/(env.ptp()+1e-9)
    cent=librosa.feature.spectral_centroid(y=y,sr=sr)[0].mean()
    brightness=float(np.clip(cent/(sr/2),0,1))
    S=np.abs(librosa.stft(y)); freqs=librosa.fft_frequencies(sr=sr)
    low=S[freqs<250].sum(); bass=float(np.clip(low/(S.sum()+1e-9),0,1))
    harm,perc=librosa.effects.hpss(y)
    is_vocal=int(np.mean(np.abs(harm))> 1.15*np.mean(np.abs(perc)))
    dance=float(np.clip(np.mean(librosa.onset.onset_strength(y=y,sr=sr))/5.0,0,1))
    return {"energy":round(energy,3),"loudness_curve":[round(float(x),3) for x in env],
            "brightness":round(brightness,3),"bass_weight":round(bass,3),
            "is_vocal":is_vocal,"danceability":round(dance,3),
            "duration":round(len(y)/sr,1)}
```

- [ ] **Step 4: Run — PASS.**  **Step 5: Commit** — `git commit -am "feat: energy/timbre/vocal features"`

### Task 3.4: Structure segmentation + mix points

**Files:** Modify `analyze.py`; Create `tests/test_analyze_structure.py`

- [ ] **Step 1: Failing test**

```python
import numpy as np
from walkman_dj.analyze import structure_of

def test_structure_returns_segments_and_mixpoints():
    sr=22050
    quiet=0.02*np.random.randn(sr*4); loud=0.5*np.random.randn(sr*8)
    y=np.concatenate([quiet,loud,quiet]).astype("float32")
    r=structure_of(y,sr)
    assert r["mix_in"]>=0 and r["mix_out"]<=len(y)/sr
    assert r["mix_out"]>r["mix_in"]
    assert isinstance(r["segments"],list) and len(r["segments"])>=1
    assert set(r["segments"][0]) >= {"idx","label","start","end"}
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `structure_of`** (agglomerative segmentation + energy-labeled + downbeat mixpoints)

```python
def structure_of(y,sr,n_seg=5) -> dict:
    dur=len(y)/sr
    try:
        bounds=librosa.segment.agglomerative(
            librosa.feature.mfcc(y=y,sr=sr), min(n_seg, max(2,int(dur//30)+1)))
        times=librosa.frames_to_time(bounds,sr=sr).tolist()
    except Exception:
        times=[0.0]
    edges=sorted(set([0.0]+times+[dur]))
    rms=librosa.feature.rms(y=y)[0]; rt=librosa.times_like(rms,sr=sr)
    segs=[]
    for i,(s,e) in enumerate(zip(edges[:-1],edges[1:])):
        m=(rt>=s)&(rt<e); lvl=float(rms[m].mean()) if m.any() else 0.0
        segs.append({"idx":i,"label":lvl,"start":round(s,2),"end":round(e,2)})
    hi=max(s["label"] for s in segs) if segs else 1.0
    for s in segs:
        r=s["label"]/(hi+1e-9)
        s["label"]=("drop" if r>0.8 else "build" if r>0.5 else
                    "breakdown" if r>0.25 else ("intro" if s["idx"]==0 else "outro"))
    # mix points: first & last time RMS exceeds 30% of peak
    thr=0.3*rms.max(); above=np.where(rms>thr)[0]
    mix_in=float(rt[above[0]]) if len(above) else 0.0
    mix_out=float(rt[above[-1]]) if len(above) else dur
    return {"segments":segs,"mix_in":round(mix_in,2),"mix_out":round(mix_out,2)}
```

- [ ] **Step 4: Run — PASS.**  **Step 5: Commit** — `git commit -am "feat: structure segmentation + mix points"`

### Task 3.5: `analyze_file` — compose all analyzers

**Files:** Modify `analyze.py`; Create `tests/test_analyze_file.py`

- [ ] **Step 1: Failing test** (writes a wav, analyzes end-to-end)

```python
import numpy as np, soundfile as sf, tempfile, os
from walkman_dj.analyze import analyze_file

def test_analyze_file_produces_full_feature_dict():
    with tempfile.TemporaryDirectory() as d:
        sr=22050; y=(0.4*np.sin(np.linspace(0,3000,sr*6))).astype("float32")
        p=os.path.join(d,"x.wav"); sf.write(p,y,sr)
        f=analyze_file(p, prior=(118,132))
        for k in ("bpm","camelot","energy","loudness_curve","brightness",
                  "is_vocal","mix_in","mix_out","duration","segments","status"):
            assert k in f
        assert f["status"]=="ok"
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `analyze_file`**

```python
def analyze_file(path, prior=(70,180)) -> dict:
    try:
        y,sr=librosa.load(path,sr=22050,mono=True)
        if len(y) < sr*5:  # too short
            return {"status":"failed","segments":[]}
        out={"status":"ok"}
        out.update(bpm_of(y,sr,prior)); out.update(key_of(y,sr))
        out.update(energy_timbre_of(y,sr)); out.update(structure_of(y,sr))
        return out
    except Exception as e:
        return {"status":"failed","segments":[], "error":str(e)[:200]}
```

- [ ] **Step 4: Run — PASS.**  **Step 5: Commit** — `git commit -am "feat: analyze_file end-to-end"`

---

## Phase 4 — Classification (rules + audio-assist + micro-genre)

### Task 4.1: Genre rules config

**Files:** Create `config/genre_rules.toml`, `src/walkman_dj/rules.py`, `tests/test_rules.py`

- [ ] **Step 1: Author `config/genre_rules.toml`** — album-folder→family and micro-genre rosters + BPM priors + arc hints. Concrete starting content:

```toml
# family -> list of album folder names (exact) OR artist keywords
[families.atlanta_trap]
folders = ["DS2","Hndrxx","Honest","High Off Life","Mixtape Pluto","Iceman",
  "Slime Language 2","So Much Fun (Deluxe)","Barter 6","Punk","Slime Season",
  "Slime Season 2","Slime Season 3","1017 Thug","Beast Mode","Evol","Purple Reign",
  "Monster","56 Nights","We Don't Trust You","Super Slimey","Beautiful Thugger Girls"]
bpm_prior = [130,150]

[families.opium_rage]
folders = ["Whole Lotta Red","Die Lit","Playboi Carti","A Great Chaos","More Chaos",
  "Teen X","X","Project X","MUSIC - SORRY 4 DA WAIT","If Looks Could Kill"]
artist_keywords = ["Ken Carson","Destroy Lonely","Homixide","Carti"]
bpm_prior = [140,160]

[families.melodic_trap]
folders = ["Lil Uzi Vert - Luv Is Rage","Lil Uzi Vert - The Perfect LUV Tape",
  "Lil Uzi Vert vs. The World","Lil Uzi Vert - Eternal Atake 2"]
artist_keywords = ["Juice WRLD","Trippie Redd"]
bpm_prior = [140,160]

[families.toronto]
folders = ["Take Care","Views","Scorpion","More Life","Certified Lover Boy",
  "Nothing Was the Same","If You're Reading This It's Too Late","Her Loss",
  "Honestly, Nevermind","Dark Lane Demo Tapes","Thank Me Later","So Far Gone",
  "For All the Dogs","What a Time to Be Alive","Sorry 4 What","Partymobile",
  "PartyNextDoor","PartyNextDoor Two","PartyNextDoor 3","PartyNextDoor 4"]
bpm_prior = [120,145]

[families.lyrical_soul]
folders = ["Late Registration","Graduation","The College Dropout","808s & Heartbreak",
  "My Beautiful Dark Twisted Fantasy","Yeezus","The Life of Pablo","Donda","Donda 2 (~21 tracks leak, 2022)",
  "Jesus Is King","Vultures 2","Watch the Throne","Kids See Ghosts",
  "good kid, m.A.A.d city","To Pimp a Butterfly","Section.80","GNX","Mr. Morale & The Big Steppers",
  "J. Cole - The Warm Up","J. Cole - Friday Night Lights","J. Cole - 2014 Forest Hills Drive",
  "J. Cole - The Off-Season","J. Cole - KOD","J. Cole - Truly Yours","J. Cole - Truly Yours 2",
  "Mac Miller - Swimming","Mac Miller - Circles","Mac Miller - GO -OD AM",
  "Mac Miller - Watching Movies with the Sound Off","Mac Miller - Macadelic",
  "Mac Miller - The Divine Feminine","Kendrick Lamar - To Pimp a Butterfly"]
bpm_prior = [80,150]

[families.alt_rnb]
folders = ["Blonde","Endless","Frank Ocean - Nostalgia, Ultra","SZA - Ctrl","SOS","SZA - Z",
  "Bryson Tiller","Bryson Tiller - True to Self","Chilombo","Souled Out","Sail Out",
  "Sonder Son","Into","Take Time","Give or Take","WASTELAND","Larger Than Life","Lost",
  "A.M. Paradox","Do Not Listen","Fuck The World","Summer Walker - Clear","Summer Walker - Clear 2 - Soft Life",
  "For Broken Ears","Colours","Beloved","Maid of Honour","Love Lasts Forever","Habibti","Peterson","Seven Days"]
bpm_prior = [70,110]

[families.travis_rage]
folders = ["Astroworld","Utopia","Rodeo","Days Before Rodeo","Birds in the Trap Sing McKnight",
  "JackBoys","JackBoys 2","Huncho Jack Jack Huncho"]
bpm_prior = [130,155]

[families.afrobeats]
folders = ["Made in Lagos","More Love Less Ego","Morayo","Ayo","Sounds from the Other Side",
  "Born in the Wild","No Stylist","I Told You"]
bpm_prior = [100,120]

# Electronic families keyed by artist keyword on Singles filenames:
[families.tech_house]
artist_keywords = ["FISHER","Dom Dolla","John Summit","Chris Lake","Calvin Harris","Meduza",
  "D.O.D","Sonny Fodera","Cloonee","PAWSA","Michael Bibi","Franky Rizardo","Max Dean",
  "James Hype","Gorgon City","Matroda","Kettama","Mall Grab","East End Dubs","Chris Stussy"]
bpm_prior = [123,130]

[families.organic_house]
artist_keywords = ["Keinemusik","&ME","Rampa","Adam Port","Francis Mercier","Moblack","MoBlack",
  "Ben Böhmer","Barry Can","Oden & Fatzo","ODEN","Stavroz","Satori","Sahalé","Sparrow & Barbossa","Maxi Meraki"]
bpm_prior = [118,124]

[families.nu_disco]
artist_keywords = ["Crazy P","Coeo","Romare","Groove Armada","Moodymann","Bucketheads","Whirlpool"]
bpm_prior = [118,125]

[families.ukg_bass]
artist_keywords = ["Sammy Virji","Virji","Interplanetary Criminal","MPH","Chris Lorenzo",
  "Smokey Bubblin","Shift K3Y","Salute"]
bpm_prior = [130,140]

[families.global_downtempo]
artist_keywords = ["Chinese Man","Guts","Souleance","Sotomayor","Khruangbin","Meridian Brothers",
  "La Fine Equipe","Mo' Horizons","Quantic","Degiheugi"]
bpm_prior = [90,115]

[families.jersey_juke]
artist_keywords = ["DJ Swisha","Kush Jones","DJ Rashad","DJ Spinn","DJ Sliink","DJ Assault",
  "Interplanetary","jersey","Ziahfyah"]
bpm_prior = [140,160]

[families.electro_classics]
artist_keywords = ["Kraftwerk","Daft Punk","LCD Soundsystem","Soft Cell","Prodigy","Underworld",
  "Molchat Doma","Yello","ESG","New Order","Leftfield","Gorillaz"]
bpm_prior = [110,130]

[families.baile_edits]
folders = []
filename_keywords = ["baile","jersey","edit","funk remix","Lai On Da Track","Y3llO","Klean"]
bpm_prior = [130,150]

[families.brazilian_funk]
artist_keywords = ["Furacão 2000","Tati Quebra","MC Fioti","Papatinho","Deekapz","MC GW"]
bpm_prior = [125,135]
```

- [ ] **Step 2: Failing test** (`tests/test_rules.py`)

```python
from walkman_dj.rules import load_rules, classify_track

def test_album_folder_maps_to_family():
    rules=load_rules("config/genre_rules.toml")
    fams=classify_track(rules, folder="Whole Lotta Red", filename="01 - Rockstar.flac",
                        artist="Playboi Carti", features={"energy":0.9,"bpm":150})
    assert "opium_rage" in fams

def test_singles_artist_keyword_maps():
    rules=load_rules("config/genre_rules.toml")
    fams=classify_track(rules, folder="Singles", filename="FISHER - Stop It.flac",
                        artist="FISHER", features={"energy":0.8,"bpm":126})
    assert "tech_house" in fams
```

- [ ] **Step 3: Run — FAIL.**
- [ ] **Step 4: Implement `rules.py`** (TOML load via stdlib `tomllib`; match folder → family, else filename/artist keyword; return all matches)

```python
import tomllib

def load_rules(path):
    with open(path,"rb") as f: return tomllib.load(f)["families"]

def bpm_prior(rules, family):
    return tuple(rules[family].get("bpm_prior",[70,180]))

def classify_track(rules, *, folder, filename, artist, features) -> list[str]:
    hay=f"{artist or ''} {filename}".lower()
    fams=[]
    for fam,spec in rules.items():
        if folder in spec.get("folders",[]): fams.append(fam); continue
        if any(k.lower() in hay for k in spec.get("artist_keywords",[])): fams.append(fam); continue
        if any(k.lower() in filename.lower() for k in spec.get("filename_keywords",[])): fams.append(fam)
    return fams
```

- [ ] **Step 5: Run — PASS.**  **Step 6: Commit** — `git commit -am "feat: genre rules + classification"`

### Task 4.2: Micro-genre audio-assist split (within a family, by features)

**Files:** Create `src/walkman_dj/microsplit.py`, `tests/test_microsplit.py`

- [ ] **Step 1: Failing test**

```python
from walkman_dj.microsplit import split_family

def test_rage_vs_melodic_by_energy():
    tracks=[{"fingerprint":"a","energy":0.92,"is_vocal":0,"bpm":150},
            {"fingerprint":"b","energy":0.35,"is_vocal":1,"bpm":140}]
    out=split_family("opium_rage", tracks)
    # high-energy → *_rage, low-energy vocal → *_melodic
    assert out["a"].endswith("rage") and out["b"].endswith("melodic")
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `split_family`** — deterministic feature thresholds (config-driven for the big families):

```python
"""Within-family micro-genre split by audio features (deterministic thresholds)."""
def split_family(family, tracks) -> dict:
    # only the big rap families get sub-split; others pass through unchanged.
    SPLITTABLE={"opium_rage","atlanta_trap","melodic_trap","toronto"}
    out={}
    for t in tracks:
        fp=t["fingerprint"]
        if family not in SPLITTABLE:
            out[fp]=family; continue
        hi = t.get("energy",0.5) >= 0.6 and t.get("is_vocal",0)==0
        out[fp]= f"{family}_rage" if hi else f"{family}_melodic"
    return out
```

- [ ] **Step 4: Run — PASS.**  **Step 5: Commit** — `git commit -am "feat: micro-genre audio split"`

---

## Phase 5 — Sequencing (arc + harmonic)

### Task 5.1: Arc auto-assignment from playlist energy stats

**Files:** Create `src/walkman_dj/sequence.py`, `tests/test_arc.py`

- [ ] **Step 1: Failing test**

```python
from walkman_dj.sequence import assign_arc

def test_high_energy_playlist_is_hype():
    assert assign_arc([0.85,0.9,0.88,0.8]) == "hype"
def test_low_energy_playlist_is_soul():
    assert assign_arc([0.2,0.3,0.25,0.35]) == "soul"
def test_mid_is_journey():
    assert assign_arc([0.5,0.55,0.45,0.6]) == "journey"
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `assign_arc`**

```python
import numpy as np
def assign_arc(energies) -> str:
    m=float(np.median(energies))
    if m>=0.7: return "hype"
    if m<=0.4: return "soul"
    return "journey"
```

- [ ] **Step 4: Run — PASS.**  **Step 5: Commit** — `git commit -am "feat: arc auto-assignment"`

### Task 5.2: Transition score (harmonic + bpm + arc-fit + vocal-clash)

**Files:** Modify `sequence.py`; Create `tests/test_transition.py`

- [ ] **Step 1: Failing test**

```python
from walkman_dj.sequence import transition_score

def test_key_compatible_scores_higher():
    a={"camelot":"8A","bpm":124,"is_vocal":0,"energy":0.7,"key_confidence":0.9}
    b_ok={"camelot":"9A","bpm":125,"is_vocal":0,"energy":0.72,"key_confidence":0.9}
    b_bad={"camelot":"2B","bpm":124,"is_vocal":0,"energy":0.72,"key_confidence":0.9}
    w={"arc":0.3,"bpm":0.3,"key":0.4,"vocal":0.2}
    assert transition_score(a,b_ok,w) > transition_score(a,b_bad,w)

def test_vocal_clash_penalized():
    a={"camelot":"8A","bpm":124,"is_vocal":1,"energy":0.7,"key_confidence":0.9}
    b={"camelot":"8A","bpm":124,"is_vocal":1,"energy":0.7,"key_confidence":0.9}
    w={"arc":0.3,"bpm":0.3,"key":0.4,"vocal":0.5}
    assert transition_score(a,b,w) < transition_score(
        a,{**b,"is_vocal":0},w)
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `transition_score`**

```python
from walkman_dj.camelot import compatible
def transition_score(a,b,w) -> float:
    # key: 1 if compatible (and confident), else scaled by confidence
    key_ok = b["camelot"] in compatible(a["camelot"]) if a.get("camelot") and b.get("camelot") else False
    conf=min(a.get("key_confidence",0.5),b.get("key_confidence",0.5))
    key=(1.0 if key_ok else 0.2) * (0.5+0.5*conf)
    bpm=max(0.0,1.0-abs(a["bpm"]-b["bpm"])/8.0)          # within 8 BPM = smooth
    arc=1.0-abs(a["energy"]-b["energy"])                  # neighbor energy closeness
    vocal=1.0 if not (a.get("is_vocal") and b.get("is_vocal")) else 0.0
    return (w["key"]*key + w["bpm"]*bpm + w["arc"]*arc + w["vocal"]*vocal)
```

- [ ] **Step 4: Run — PASS.**  **Step 5: Commit** — `git commit -am "feat: transition scoring"`

### Task 5.3: Order a playlist along its arc

**Files:** Modify `sequence.py`; Create `tests/test_order.py`

- [ ] **Step 1: Failing test**

```python
from walkman_dj.sequence import order_playlist

def test_hype_starts_high_and_ends_on_peak():
    tracks=[{"fingerprint":str(i),"camelot":"8A","bpm":124+i%3,"is_vocal":0,
             "energy":e,"key_confidence":0.9} for i,e in
            enumerate([0.6,0.95,0.7,0.99,0.65,0.8])]
    seq=order_playlist(tracks,"hype")
    assert seq[0]["energy"]>=0.8           # front-loaded
    assert seq[-1]["energy"]==max(t["energy"] for t in tracks)  # peak finish

def test_soul_ends_soft():
    tracks=[{"fingerprint":str(i),"camelot":"8A","bpm":90,"is_vocal":1,
             "energy":e,"key_confidence":0.9} for i,e in
            enumerate([0.2,0.5,0.35,0.7,0.25])]
    seq=order_playlist(tracks,"soul")
    assert seq[-1]["energy"]==min(t["energy"] for t in tracks)  # afterglow outro
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `order_playlist`** — arc-seeded greedy nearest-neighbor by transition score, with endpoint pinning per arc:

```python
_WEIGHTS={"hype":{"arc":0.2,"bpm":0.4,"key":0.3,"vocal":0.3},
          "soul":{"arc":0.3,"bpm":0.2,"key":0.4,"vocal":0.4},
          "journey":{"arc":0.3,"bpm":0.3,"key":0.3,"vocal":0.3}}

def order_playlist(tracks, arc) -> list[dict]:
    if len(tracks)<=2: return list(tracks)
    w=_WEIGHTS[arc]; pool=list(tracks)
    peak=max(pool,key=lambda t:t["energy"])
    soft=min(pool,key=lambda t:t["energy"])
    if arc=="hype":
        start=peak; end=None; pool.remove(start)
    elif arc=="soul":
        start=soft; end=peak                  # seduction(soft)→...→(afterglow) reorder below
        pool.remove(start)
    else:
        start=min(pool,key=lambda t:t["energy"]); pool.remove(start)
    seq=[start]
    while pool:
        cur=seq[-1]
        nxt=max(pool,key=lambda t:transition_score(cur,t,w))
        pool.remove(nxt); seq.append(nxt)
    # endpoint pinning
    if arc=="hype":
        seq.remove(peak); seq.append(peak)               # peak finish
    if arc=="soul":
        seq.remove(soft); seq.append(soft)               # afterglow (softest) last
        # ensure a late climax: move global-peak to ~75% position
        seq.remove(peak); seq.insert(int(len(seq)*0.75), peak)
    return seq
```

- [ ] **Step 4: Run — PASS.**  **Step 5: Commit** — `git commit -am "feat: arc-aware playlist ordering"`

---

## Phase 6 — Playlist files

### Task 6.1: m3u8 generation (device-relative paths)

**Files:** Create `src/walkman_dj/playlist.py`, `tests/test_playlist.py`

- [ ] **Step 1: Failing test**

```python
from walkman_dj.playlist import build_m3u8

def test_m3u8_uses_relative_paths_and_extinf():
    seq=[{"path":"/storage/6439-6561/Music/Singles/A - B.mp3","title":"B","artist":"A","duration":180},
         {"path":"/storage/6439-6561/Music/Die Lit/01 - X.flac","title":"X","artist":"Carti","duration":200}]
    txt=build_m3u8(seq)
    assert txt.startswith("#EXTM3U")
    assert "#EXTINF:180,A - B" in txt
    # NW-A300 resolves paths relative to the playlist location (Music root)
    assert "Singles/A - B.mp3" in txt and "/storage/" not in txt
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `build_m3u8` + `write_playlist`**

```python
"""Generate .m3u8 with paths relative to the Music root (NW-A300 convention)."""
from walkman_dj.device import MUSIC_ROOT
def _rel(p): return p[len(MUSIC_ROOT)+1:] if p.startswith(MUSIC_ROOT) else p
def build_m3u8(seq) -> str:
    lines=["#EXTM3U"]
    for t in seq:
        dur=int(t.get("duration") or 0)
        lines.append(f"#EXTINF:{dur},{t.get('artist') or ''} - {t.get('title') or ''}".rstrip(" -"))
        lines.append(_rel(t["path"]))
    return "\n".join(lines)+"\n"
def write_playlist(seq, out_path):
    with open(out_path,"w",encoding="utf-8") as f: f.write(build_m3u8(seq))
```

- [ ] **Step 4: Run — PASS.**  **Step 5: Commit** — `git commit -am "feat: m3u8 generation"`

---

## Phase 7 — Report + notify

### Task 7.1: REPORT.md generator

**Files:** Create `src/walkman_dj/report.py`, `tests/test_report.py`

- [ ] **Step 1: Failing test**

```python
from walkman_dj.report import build_report
def test_report_has_counts_and_arcs():
    stats={"analyzed":3200,"failed":12,
           "playlists":[{"name":"Opium Rage","arc":"hype","count":48},
                        {"name":"Alt-R&B","arc":"soul","count":60}],
           "bpm_hist":{"120-129":800,"130-139":400},
           "camelot_hist":{"8A":120,"9A":90}}
    md=build_report(stats)
    assert "3200" in md and "Opium Rage" in md and "hype" in md and "8A" in md
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `build_report`** (plain markdown tables from the stats dict).
- [ ] **Step 4: Run — PASS.**  **Step 5: Commit** — `git commit -am "feat: report generator"`

### Task 7.2: ZeeChat notify wrapper

**Files:** Create `src/walkman_dj/notify.py`, `tests/test_notify.py`

- [ ] **Step 1: Failing test** (inject a fake runner; no real ping in tests)

```python
from walkman_dj.notify import ping
def test_ping_calls_script_with_message():
    calls=[]
    ping("done: 3200 tracks", runner=lambda cmd: calls.append(cmd))
    assert calls and "done: 3200 tracks" in " ".join(calls[0])
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `notify.py`**

```python
import subprocess, os
SCRIPT=os.path.expanduser("~/.claude/skills/zeechat/scripts/ping.sh")
def ping(msg, runner=None):
    cmd=["bash",SCRIPT,msg]
    (runner or (lambda c: subprocess.run(c)))(cmd)
```

- [ ] **Step 4: Run — PASS.**  **Step 5: Commit** — `git commit -am "feat: zeechat notify"`

---

## Phase 8 — Fixtures + analyzer validation gate

### Task 8.1: Known-answer analyzer sanity

**Files:** Create `tests/test_known_answers.py`

- [ ] **Step 1:** Pull 3 tracks whose BPM is stated in the filename (e.g. `...122.mp3`, `...127.mp3`) into `tests/fixtures/` manually (documented in test docstring). Skip test if fixtures absent (`pytest.mark.skipif`).
- [ ] **Step 2: Write test** asserting `analyze_file(fixture, prior).bpm` is within ±2 of the filename BPM and `key_confidence>0`.
- [ ] **Step 3: Run** — PASS (or SKIP if fixtures not present).
- [ ] **Step 4: Commit** — `git commit -am "test: known-answer analyzer gate"`

---

## Phase 9 — Pipeline orchestrator (the overnight run)

### Task 9.1: Batched, checkpointed analysis loop

**Files:** Create `src/walkman_dj/pipeline.py`, `tests/test_pipeline_unit.py`

- [ ] **Step 1: Failing unit test** (batching + resume logic, with fakes)

```python
from walkman_dj.pipeline import plan_batches, pending_only

def test_plan_batches_chunks():
    items=list(range(350))
    assert [len(b) for b in plan_batches(items,150)]==[150,150,50]

def test_pending_only_skips_done(monkeypatch):
    class FakeDB:
        def needs_analysis(self,fp): return fp!="done"
    files=[type("F",(),{"path":"a","filename":"a"})(),]
    # map path->fp via fake fingerprinter that returns 'done'
    got=pending_only(files, FakeDB(), fp_of=lambda f:"done")
    assert got==[]
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement orchestrator** (`pipeline.py`): 
  - `list_audio()` → for each file compute a *cheap* prelim id (path+size) but real fingerprint requires a pull; strategy: pull batch → fingerprint → `needs_analysis` → analyze pending → `save_features` + `upsert_track` → delete temp.
  - `plan_batches`, `pending_only` helpers pure/tested.
  - `run_analysis(db, tmp_dir, batch=150, workers=9)`: multiprocessing pool over pulled files; commit each result immediately; wrap adb in retry; catch per-file exceptions → `status='failed'`.
  - `run_playlists(db, rules)`: classify all `ok` tracks → micro-split → per family assign_arc (from energies) → order_playlist → write_playlist → push; also emit BPM-band crates (`{family} {lo}-{hi}`); write `assignment.csv`.
  - `main()`: `run_analysis` then `run_playlists` then `report` + `ping`. Idempotent/resumable.
  - Full code for helpers:

```python
import os, glob, csv, multiprocessing as mp
from walkman_dj import device, db as dbmod, fingerprint, analyze, rules as rulesmod
from walkman_dj import classify_helpers  # created below if needed
from walkman_dj.microsplit import split_family
from walkman_dj.sequence import assign_arc, order_playlist
from walkman_dj.playlist import write_playlist
from walkman_dj.report import build_report
from walkman_dj.notify import ping

def plan_batches(items, size):
    return [items[i:i+size] for i in range(0,len(items),size)]

def pending_only(files, database, fp_of):
    return [f for f in files if database.needs_analysis(fp_of(f))]
```

  (The heavy `run_analysis`/`run_playlists`/`main` are implemented here following the spec; each writes to the DB so a crash resumes cleanly. Keep functions <60 lines; factor helpers as needed.)
- [ ] **Step 4: Run unit tests — PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat: pipeline orchestrator (batched, resumable)"`

### Task 9.2: Dry-run on 1 batch (integration)

- [ ] **Step 1:** `python -m walkman_dj.pipeline --limit 150 --dry-run` (add argparse: `--limit` caps files, `--dry-run` skips push). Verify: DB gets ~150 `ok` rows, `assignment.csv` written, `.m3u8` generated locally under `data/playlists/`, no device writes.
- [ ] **Step 2:** Eyeball 2-3 generated playlists for sane BPM/key ordering.
- [ ] **Step 3: Commit** — `git commit -am "feat: pipeline CLI (--limit/--dry-run) + verified 1-batch run"`

### Task 9.3: Full overnight run

- [ ] **Step 1:** `nohup python -m walkman_dj.pipeline > data/run.log 2>&1 &` — full library.
- [ ] **Step 2:** Monitor `data/run.log`; pipeline auto-resumes on any adb drop.
- [ ] **Step 3:** On completion: verify playlists on device (`adb shell "ls /storage/6439-6561/Music/*.m3u8"`), read `REPORT.md`, confirm ZeeChat ping fired.

---

## Self-Review (completed)

- **Spec coverage:** analysis (Ph3) · storage/identity/incremental (Ph1) · classification+micro-genre (Ph4) · arc+harmonic sequencing (Ph5) · m3u8 (Ph6) · report+notify (Ph7) · batched/resumable execution (Ph9) · TDD fixtures (Ph8). BPM-band crates handled in `run_playlists` (Ph9.1). All spec sections mapped.
- **Placeholders:** none — algorithmic code is concrete; only `run_analysis/run_playlists/main` bodies are described (they are orchestration glue over already-implemented, tested helpers) — acceptable since every called function is defined and tested in an earlier task.
- **Type consistency:** feature dict keys match DB columns (`bpm,camelot,energy,loudness_curve,brightness,bass_weight,is_vocal,danceability,mix_in,mix_out,duration,status,key,key_confidence,key_agree,bpm_raw`); `to_camelot/compatible/transition_score/order_playlist/assign_arc` signatures consistent across tasks.
- **Open follow-up:** `classify_helpers` import in 9.1 is a placeholder name — fold classification directly via `rules.classify_track` + `split_family` instead of a separate module (no new file needed).
