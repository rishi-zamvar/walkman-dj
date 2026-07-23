# Set Sequencer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Order genre-scoped playlists from the `walkman_analysis.db` MIR features into harmonically-mixed, energy-templated DJ sets, emitted as Walkman `.m3u8` + an annotated transition sheet.

**Architecture:** Extend the existing pure modules (`camelot.py`, `genre.py`, `sequence.py`) plus four new pure modules (`distance.py`, `templates.py`, `family.py`, `graph.py`) and a CLI (`seqcli.py`). Ordering runs over a **penalty-completed cost matrix** (non-mixable pairs carry a large `HARD_CUT`) so greedy-NN → 2-opt → Held-Karp are always well-defined; a **position-indexed energy template** encodes build-up/build-down. Genre scope reuses the existing coarse-bucket family resolution, persisted to a frozen `track_genre` table for stability.

**Tech Stack:** Python 3.14, `numpy`, `sqlite3`, `tomllib`, `pytest`. **No new dependencies** (no hdbscan, no networkx, no sentence-transformers — graphs are tiny and implemented directly).

**Spec:** `docs/superpowers/specs/2026-07-22-set-sequencer-design.md`

**BUILD STATUS (2026-07-22):** ✅ Phases 1–4 implemented on branch `feat/set-sequencer`, **92 tests green**. Genre now sourced from the real `tracks` metadata via `genre_rules.toml` classification (resolved the Task-15 open risk — `tracks.path` is already device-relative, so m3u8 output is drop-in). 16 fine genre families live on the production DB (`track_genre` persisted, frozen). 12 ordered set sheets generated to `~/Desktop/DJ Sets/`, all under the 10% hard-transition bar. Remaining: push m3u8s to device (needs replug) + Rekordbox ear-acceptance (Task 19 Step 3); Phase 5 deferred.

**Conventions (verified in repo):** tests in `tests/test_*.py`, run with `python -m pytest`; config via `tomllib` (`src/walkman_dj/rules.py`); analyzer entry `python -m walkman_dj.pipeline`; DB helper `walkman_dj.db.CrateDB`; existing set builder `tools/build_djsets.py`.

**Key value objects:** a *track* is a `dict` with keys `fingerprint, bpm, bpm_confidence, camelot, key_confidence, key_agree, energy, bass_weight, brightness, danceability, duration` (as stored in `features`). `energy_n` is the per-family-rescaled energy in `[0,1]`, computed at order time, never stored.

---

## Phase 0 — Finish feature extraction (gate)

### Task 0: Analyze stragglers + new downloads

**Files:** none (operational).

- [ ] **Step 1: Count what's unанализed**

Run: `python3 -c "import sqlite3;c=sqlite3.connect('data/walkman_analysis.db');print(dict(c.execute('select status,count(*) from features group by status')))"`
Expected: shows `pending` + `failed` counts (was 42 + 28) plus any new fingerprints from tonight's downloads.

- [ ] **Step 2: Run the analyzer over everything not `ok`**

Run: `python -m walkman_dj.pipeline --workers 9 --batch 150`
Expected: processes pending/new tracks; final line reports newly-analyzed count.

- [ ] **Step 3: Gate — confirm ≥ 99% analyzed**

Run: `python3 -c "import sqlite3;c=sqlite3.connect('data/walkman_analysis.db');ok=c.execute(\"select count(*) from features where status='ok'\").fetchone()[0];tot=c.execute('select count(*) from features').fetchone()[0];print(ok,tot,round(ok/tot,3))"`
Expected: ratio ≥ 0.99. Remaining `failed` tracks (unreadable audio) are excluded from sequencing by design — do NOT zero-fill.

- [ ] **Step 4: Commit** (no code change; skip if nothing to commit)

---

## Phase 1 — Pure domain + tests

### Task 1: `distance.py` — BPM distance with half/double fold

**Files:**
- Create: `src/walkman_dj/distance.py`
- Test: `tests/test_distance.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_distance.py
import math
from walkman_dj.distance import bpm_distance, bpm_window

def test_bpm_distance_direct():
    assert bpm_distance(128.0, 130.0) == 2.0

def test_bpm_distance_double_time_folds_to_zero():
    # 150 trap over a 75-feel groove is 0 apart, not 75
    assert bpm_distance(150.0, 75.0) == 0.0
    assert bpm_distance(174.0, 87.0) == 0.0

def test_bpm_distance_missing_is_inf():
    assert math.isinf(bpm_distance(0, 128))
    assert math.isinf(bpm_distance(128, None))

def test_bpm_window_floor_and_pct():
    assert bpm_window(60, 60, pct=0.06, floor=4.0) == 4.0      # 0.06*60=3.6 < floor
    assert bpm_window(200, 200, pct=0.06, floor=4.0) == 12.0   # 0.06*200=12 > floor

def test_bpm_window_widen():
    assert bpm_window(200, 200, pct=0.06, floor=4.0, widen=True) == 15.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_distance.py -v`
Expected: FAIL — `ModuleNotFoundError: walkman_dj.distance`

- [ ] **Step 3: Write minimal implementation**

```python
# src/walkman_dj/distance.py
"""Pure tempo/edge math for the sequencer. No I/O."""


def bpm_distance(b1, b2) -> float:
    """Min tempo gap allowing half/double-time equivalence. inf if either missing."""
    if not b1 or not b2:
        return float("inf")
    return min(abs(b1 - x) for x in (b2, b2 * 2, b2 / 2))


def bpm_window(b1, b2, pct=0.06, floor=4.0, widen=False) -> float:
    """Allowed pitch-fader range in BPM: pct of the slower track, floored."""
    w = max(pct * min(b1, b2), floor)
    return w * 1.25 if widen else w
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_distance.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/distance.py tests/test_distance.py
git commit -m "feat(sequencer): bpm_distance with half/double fold + window"
```

---

### Task 2: `distance.py` — pairwise smoothness cost

**Files:**
- Modify: `src/walkman_dj/distance.py`
- Test: `tests/test_distance.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_distance.py
from walkman_dj.distance import smoothness_cost

def _cfg():
    return {"bpm_window_pct": 0.06, "bpm_window_floor": 4.0,
            "bpm_conf_min": 0.6, "w_energy_jump": 0.5,
            "w_unreliable_bpm": 0.2}

def test_smoothness_low_for_close_bpm_and_energy():
    a = {"bpm": 128, "bpm_confidence": 0.9}
    b = {"bpm": 129, "bpm_confidence": 0.9}
    c, hard = smoothness_cost(a, b, 0.5, 0.5, _cfg())
    assert hard is False
    assert c < 0.3

def test_smoothness_hard_when_bpm_out_of_window():
    a = {"bpm": 128, "bpm_confidence": 0.9}
    b = {"bpm": 150, "bpm_confidence": 0.9}   # 22 apart, window ~7.7
    c, hard = smoothness_cost(a, b, 0.5, 0.5, _cfg())
    assert hard is True

def test_smoothness_low_bpm_conf_widens_and_penalizes():
    a = {"bpm": 128, "bpm_confidence": 0.4}   # unreliable
    b = {"bpm": 137, "bpm_confidence": 0.9}   # 9 apart; base window 7.7, widened 9.6 -> fits
    c, hard = smoothness_cost(a, b, 0.5, 0.5, _cfg())
    assert hard is False
    assert c >= 0.2   # includes w_unreliable_bpm
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_distance.py::test_smoothness_hard_when_bpm_out_of_window -v`
Expected: FAIL — `ImportError: cannot import name 'smoothness_cost'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/walkman_dj/distance.py

def smoothness_cost(a, b, energy_n_a, energy_n_b, cfg):
    """Pairwise transition roughness (lower = smoother). Returns (cost, is_hard).
    is_hard=True means BPM out of the (possibly widened) window — the harmonic
    gate is applied separately in graph.py. Callers add HARD_CUT when is_hard."""
    ca = a.get("bpm_confidence") or 0.0
    cb = b.get("bpm_confidence") or 0.0
    widen = ca < cfg["bpm_conf_min"] or cb < cfg["bpm_conf_min"]
    win = bpm_window(a.get("bpm") or 0, b.get("bpm") or 0,
                     cfg["bpm_window_pct"], cfg["bpm_window_floor"], widen)
    dist = bpm_distance(a.get("bpm"), b.get("bpm"))
    if dist > win:
        return (0.0, True)
    cost = dist / win + cfg["w_energy_jump"] * abs(energy_n_a - energy_n_b)
    if widen:
        cost += cfg["w_unreliable_bpm"]
    return (cost, False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_distance.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/distance.py tests/test_distance.py
git commit -m "feat(sequencer): pairwise smoothness_cost with confidence widening"
```

---

### Task 3: `camelot.py` — harmonic edge gate + reliability

**Files:**
- Modify: `src/walkman_dj/camelot.py`
- Test: `tests/test_camelot.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_camelot.py
from walkman_dj.camelot import camelot_edge_ok

def test_same_code_ok():
    assert camelot_edge_ok("8A", "8A") is True

def test_plus_minus_one_same_letter_ok():
    assert camelot_edge_ok("8A", "9A") is True
    assert camelot_edge_ok("8A", "7A") is True

def test_wrap_12_to_1():
    assert camelot_edge_ok("12A", "1A") is True
    assert camelot_edge_ok("1A", "12A") is True

def test_relative_major_minor_ok():
    assert camelot_edge_ok("8A", "8B") is True

def test_two_apart_blocked_unless_boost():
    assert camelot_edge_ok("8A", "10A") is False
    assert camelot_edge_ok("8A", "10A", allow_boost=True) is True

def test_different_number_and_letter_blocked():
    assert camelot_edge_ok("8A", "9B") is False

def test_full_576_matrix_symmetry():
    codes = [f"{n}{l}" for n in range(1, 13) for l in "AB"]
    for x in codes:
        for y in codes:
            assert camelot_edge_ok(x, y) == camelot_edge_ok(y, x)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_camelot.py::test_relative_major_minor_ok -v`
Expected: FAIL — `ImportError: cannot import name 'camelot_edge_ok'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/walkman_dj/camelot.py

def camelot_edge_ok(a: str, b: str, allow_boost: bool = False) -> bool:
    """Harmonic mix gate: same code, ±1 same letter (wrap 12->1), or relative
    major/minor (same number, letter swap). allow_boost also permits +/-2 same letter."""
    if not a or not b:
        return False
    na, la = int(a[:-1]), a[-1]
    nb, lb = int(b[:-1]), b[-1]
    if la == lb:
        step = min((na - nb) % 12, (nb - na) % 12)
        return step <= (2 if allow_boost else 1)
    return na == nb
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_camelot.py -v`
Expected: PASS (all, including the 576-pair symmetry sweep)

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/camelot.py tests/test_camelot.py
git commit -m "feat(sequencer): camelot_edge_ok harmonic gate + boost"
```

---

### Task 4: `genre.py` — canonicalization + token Jaccard

**Files:**
- Modify: `src/walkman_dj/genre.py`
- Test: `tests/test_genre_sim.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_genre_sim.py
from walkman_dj.genre import canon, jaccard

def test_canon_collapses_dnb_aliases():
    assert canon("Drum & Bass") == canon("dnb") == canon("drum n bass") == "drum and bass"

def test_canon_collapses_baile_aliases():
    assert canon("Funk Carioca") == canon("brazilian funk") == "baile funk"

def test_canon_idempotent():
    once = canon("Hip-Hop/Rap")
    assert canon(once) == once

def test_jaccard_partial_overlap():
    assert abs(jaccard("deep house", "tech house") - 1/3) < 1e-9

def test_jaccard_disjoint_zero():
    assert jaccard("deep house", "jungle") == 0.0

def test_jaccard_identical_one():
    assert jaccard("uk garage", "ukg") == 1.0   # canon first -> identical token sets
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_genre_sim.py -v`
Expected: FAIL — `ImportError: cannot import name 'canon'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/walkman_dj/genre.py
import re as _re

# Tier-0 canonicalization: (regex, canonical). Order matters; first match wins.
CANON = [
    (r"d ?& ?b|dnb|drum ?(and|n) ?bass", "drum and bass"),
    (r"funk carioca|brazilian funk|baile ?funk|\bbaile\b", "baile funk"),
    (r"uk ?garage|\bukg\b|2 ?-?step", "uk garage"),
    (r"hip ?-?hop|hiphop|\brap\b", "hip-hop"),
    (r"r ?& ?b|\brnb\b", "rnb"),
    (r"jersey club", "jersey club"),
    (r"amapiano", "amapiano"),
    (r"dembow", "dembow"),
    (r"tech ?house", "tech house"),
    (r"deep ?house", "deep house"),
    (r"afro ?house", "afro house"),
]
_STOP = {"the", "a", "an", "and", "music", "genre"}


def canon(s: str) -> str:
    """Normalize a messy genre string to a canonical form (lowercase, alias-mapped)."""
    if not s:
        return ""
    g = _re.sub(r"[^a-z0-9& /-]+", " ", s.lower()).strip()
    for pat, rep in CANON:
        if _re.search(pat, g):
            return rep
    return " ".join(w for w in _re.split(r"[^a-z0-9]+", g) if w and w not in _STOP)


def _tokens(s: str) -> set:
    return {w for w in _re.split(r"[^a-z0-9]+", canon(s)) if w and w not in _STOP}


def jaccard(g1: str, g2: str) -> float:
    """Token-set Jaccard on canonicalized genre strings."""
    a, b = _tokens(g1), _tokens(g2)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_genre_sim.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/genre.py tests/test_genre_sim.py
git commit -m "feat(sequencer): genre canon + token Jaccard"
```

---

### Task 5: `templates.py` — energy target curves

**Files:**
- Create: `src/walkman_dj/templates.py`
- Test: `tests/test_templates.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_templates.py
import pytest
from walkman_dj.templates import target, TEMPLATES, local_maxima

@pytest.mark.parametrize("name", list(TEMPLATES))
def test_range_bounded(name):
    for i in range(21):
        y = target(name, i / 20)
        assert 0.0 <= y <= 1.0

def test_single_peak_has_one_interior_max():
    xs = [i / 40 for i in range(41)]
    ys = [target("single_peak", x) for x in xs]
    assert len(local_maxima(ys)) == 1

def test_wave_has_at_least_two_maxima():
    xs = [i / 40 for i in range(41)]
    ys = [target("wave", x) for x in xs]
    assert len(local_maxima(ys)) >= 2

def test_monotonic_is_nondecreasing():
    xs = [i / 40 for i in range(41)]
    ys = [target("monotonic", x) for x in xs]
    assert all(ys[i] <= ys[i + 1] + 1e-9 for i in range(len(ys) - 1))

def test_plateau_drop_ends_low():
    assert target("plateau_drop", 1.0) < target("plateau_drop", 0.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_templates.py -v`
Expected: FAIL — `ModuleNotFoundError: walkman_dj.templates`

- [ ] **Step 3: Write minimal implementation**

```python
# src/walkman_dj/templates.py
"""Energy target curves T: position[0,1] -> energy_n[0,1]. Pure."""
import math


def _single_peak(x):
    # rise to a peak ~70% through, then cool
    return math.sin(min(x / 0.7, 1.0) * math.pi / 2) if x <= 0.7 else \
        max(0.0, 1.0 - (x - 0.7) / 0.3 * 0.5)


def _wave(x):
    # two peaks and a valley between: "peaks and valleys"
    return 0.5 + 0.45 * math.sin(2 * math.pi * (x * 2 - 0.25))


def _plateau_drop(x):
    if x < 0.75:
        return 0.6 + 0.4 * min(x / 0.25, 1.0)   # ramp to sustained high
    return max(0.0, 1.0 - (x - 0.75) / 0.25)    # late drop


def _monotonic(x):
    return x


TEMPLATES = {
    "single_peak": _single_peak,
    "wave": _wave,
    "plateau_drop": _plateau_drop,
    "monotonic": _monotonic,
}


def target(name: str, pos: float) -> float:
    """Target energy_n at a normalized position (0=start, 1=end)."""
    y = TEMPLATES[name](max(0.0, min(1.0, pos)))
    return max(0.0, min(1.0, y))


def local_maxima(ys) -> list:
    """Indices of strict interior local maxima (for tests / QA)."""
    return [i for i in range(1, len(ys) - 1) if ys[i] > ys[i - 1] and ys[i] > ys[i + 1]]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_templates.py -v`
Expected: PASS. If `wave`/`single_peak` maxima counts differ, tune the curve constants until the shape tests pass — the tests define the contract.

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/templates.py tests/test_templates.py
git commit -m "feat(sequencer): energy target templates (single_peak/wave/plateau_drop/monotonic)"
```

---

## Phase 2 — Family assignment + persistence

### Task 6: `track_genre` table migration

**Files:**
- Create: `src/walkman_dj/genre_store.py`
- Test: `tests/test_genre_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_genre_store.py
import sqlite3
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_genre_store.py -v`
Expected: FAIL — `ModuleNotFoundError: walkman_dj.genre_store`

- [ ] **Step 3: Write minimal implementation**

```python
# src/walkman_dj/genre_store.py
"""Frozen per-track genre/family persistence for stable playlists."""
import sqlite3

_DDL = """
CREATE TABLE IF NOT EXISTS track_genre (
  fingerprint   TEXT PRIMARY KEY,
  canon_genre   TEXT,
  family        TEXT,
  family_source TEXT,
  confidence    REAL,
  assigned_at   TEXT DEFAULT (datetime('now'))
);
"""


def ensure_schema(db_path):
    con = sqlite3.connect(db_path)
    con.execute(_DDL)
    con.commit()
    con.close()


def is_assigned(db_path, fingerprint) -> bool:
    con = sqlite3.connect(db_path)
    row = con.execute("SELECT 1 FROM track_genre WHERE fingerprint=?", (fingerprint,)).fetchone()
    con.close()
    return row is not None


def upsert(db_path, fingerprint, canon_genre, family, family_source, confidence, reclassify=False):
    """Insert an assignment. Frozen: existing rows are NOT overwritten unless reclassify=True."""
    con = sqlite3.connect(db_path)
    if reclassify:
        con.execute(
            "INSERT INTO track_genre(fingerprint,canon_genre,family,family_source,confidence) "
            "VALUES(?,?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET "
            "canon_genre=excluded.canon_genre, family=excluded.family, "
            "family_source=excluded.family_source, confidence=excluded.confidence",
            (fingerprint, canon_genre, family, family_source, confidence))
    else:
        con.execute(
            "INSERT OR IGNORE INTO track_genre(fingerprint,canon_genre,family,family_source,confidence) "
            "VALUES(?,?,?,?,?)",
            (fingerprint, canon_genre, family, family_source, confidence))
    con.commit()
    con.close()


def get_all(db_path) -> dict:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = {r["fingerprint"]: dict(r)
            for r in con.execute("SELECT * FROM track_genre")}
    con.close()
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_genre_store.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/genre_store.py tests/test_genre_store.py
git commit -m "feat(sequencer): frozen track_genre persistence"
```

---

### Task 7: `family.py` — per-family energy rescale

**Files:**
- Create: `src/walkman_dj/family.py`
- Test: `tests/test_family.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_family.py
from walkman_dj.family import rescale_energy

def test_rescale_spreads_compressed_band_to_unit():
    # raw energies compressed in 0.47..0.72 -> should span ~0..1 by rank
    raw = {"a": 0.47, "b": 0.55, "c": 0.60, "d": 0.72}
    en = rescale_energy(raw)
    assert min(en.values()) == 0.0
    assert max(en.values()) == 1.0
    assert en["a"] < en["b"] < en["c"] < en["d"]

def test_rescale_single_track_is_half():
    assert rescale_energy({"a": 0.6}) == {"a": 0.5}

def test_rescale_all_equal_is_half():
    en = rescale_energy({"a": 0.6, "b": 0.6})
    assert en == {"a": 0.5, "b": 0.5}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_family.py -v`
Expected: FAIL — `ModuleNotFoundError: walkman_dj.family`

- [ ] **Step 3: Write minimal implementation**

```python
# src/walkman_dj/family.py
"""Family (coarse-bucket) assignment, per-family energy rescale, audio override.
Reuses genre.resolve_family for the label path; adds structural override + persist."""


def rescale_energy(raw: dict) -> float:
    """Map each fingerprint's raw energy to its min-max position in [0,1] within
    the group. Compressed library band (~0.47-0.72) -> full 0..1 so templates bite.
    Degenerate (n<=1 or all-equal) -> 0.5."""
    vals = list(raw.values())
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return {k: 0.5 for k in raw}
    return {k: (v - lo) / (hi - lo) for k, v in raw.items()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_family.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/family.py tests/test_family.py
git commit -m "feat(sequencer): per-family energy rescale"
```

---

### Task 8: `family.py` — audio-envelope structural override

**Files:**
- Modify: `src/walkman_dj/family.py`
- Test: `tests/test_family.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_family.py
from walkman_dj.family import learn_envelopes, audio_override

def _tracks():
    # HOUSE cluster ~124 bpm mid energy; RAP cluster ~150 bpm; one mislabeled rap-as-house
    house = [{"bpm": 122 + i, "energy": 0.55, "bass_weight": 0.5, "brightness": 0.5} for i in range(6)]
    rap = [{"bpm": 148 + i, "energy": 0.7, "bass_weight": 0.8, "brightness": 0.3} for i in range(6)]
    return {"HOUSE": house, "RAP": rap}

def test_envelope_keeps_in_family_track():
    env = learn_envelopes(_tracks())
    t = {"bpm": 124, "energy": 0.55, "bass_weight": 0.5, "brightness": 0.5}
    fam, src = audio_override(t, "HOUSE", env)
    assert fam == "HOUSE" and src == "label"

def test_envelope_overrides_mislabeled_track():
    env = learn_envelopes(_tracks())
    # labeled HOUSE but features sit squarely in RAP
    t = {"bpm": 150, "energy": 0.7, "bass_weight": 0.8, "brightness": 0.3}
    fam, src = audio_override(t, "HOUSE", env)
    assert fam == "RAP" and src == "audio_override"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_family.py::test_envelope_overrides_mislabeled_track -v`
Expected: FAIL — `ImportError: cannot import name 'learn_envelopes'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/walkman_dj/family.py
import math

_ENV_KEYS = ("bpm", "energy", "bass_weight", "brightness")


def _mean_std(vals):
    m = sum(vals) / len(vals)
    var = sum((v - m) ** 2 for v in vals) / max(1, len(vals))
    return m, math.sqrt(var) or 1e-6


def learn_envelopes(tracks_by_family: dict) -> dict:
    """Per-family mean/std over BPM+energy+timbre, from confidently-labeled members."""
    env = {}
    for fam, tracks in tracks_by_family.items():
        if not tracks:
            continue
        env[fam] = {k: _mean_std([t.get(k) or 0.0 for t in tracks]) for k in _ENV_KEYS}
    return env


def _mahalanobis_like(track, stats) -> float:
    return math.sqrt(sum(
        ((track.get(k) or 0.0) - stats[k][0]) ** 2 / stats[k][1] ** 2 for k in _ENV_KEYS))


def audio_override(track, label_family, env, margin=1.0):
    """If the track sits far outside its labeled family's envelope AND clearly
    closer to another family's, re-file it. Returns (family, source)."""
    if label_family not in env:
        # no envelope to trust -> keep label
        return (label_family, "label")
    d_label = _mahalanobis_like(track, env[label_family])
    best_fam, best_d = min(((f, _mahalanobis_like(track, s)) for f, s in env.items()),
                           key=lambda x: x[1])
    if best_fam != label_family and best_d + margin < d_label:
        return (best_fam, "audio_override")
    return (label_family, "label")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_family.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/family.py tests/test_family.py
git commit -m "feat(sequencer): audio-envelope structural family override"
```

---

### Task 9: `family.py` — assign + persist pipeline

**Files:**
- Modify: `src/walkman_dj/family.py`
- Test: `tests/test_family.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_family.py
from walkman_dj.genre_store import ensure_schema, get_all

def test_assign_persists_only_new(tmp_path, monkeypatch):
    db = str(tmp_path / "g.db")
    ensure_schema(db)
    from walkman_dj import family as F
    # two tracks, each already carrying a resolved label family + canon genre
    tracks = [
        {"fingerprint": "fp1", "label_family": "HOUSE", "canon_genre": "tech house",
         "bpm": 124, "energy": 0.55, "bass_weight": 0.5, "brightness": 0.5, "label_conf": 0.9},
        {"fingerprint": "fp2", "label_family": "RAP", "canon_genre": "hip-hop",
         "bpm": 150, "energy": 0.7, "bass_weight": 0.8, "brightness": 0.3, "label_conf": 0.9},
    ]
    n = F.assign_and_persist(db, tracks)
    assert n == 2
    # second run adds nothing (frozen)
    assert F.assign_and_persist(db, tracks) == 0
    rows = get_all(db)
    assert rows["fp1"]["family"] == "HOUSE"
    assert rows["fp2"]["family"] == "RAP"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_family.py::test_assign_persists_only_new -v`
Expected: FAIL — `AttributeError: module 'walkman_dj.family' has no attribute 'assign_and_persist'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/walkman_dj/family.py
from walkman_dj import genre_store


def assign_and_persist(db_path, tracks, reclassify=False):
    """Assign each track a family (label + audio override) and persist frozen.
    `tracks` carry label_family, canon_genre, label_conf + envelope features.
    Returns count newly written. Envelopes learned from confidently-labeled tracks."""
    genre_store.ensure_schema(db_path)
    confident = [t for t in tracks if (t.get("label_conf") or 0) >= 0.6 and t.get("label_family")]
    by_fam = {}
    for t in confident:
        by_fam.setdefault(t["label_family"], []).append(t)
    env = learn_envelopes(by_fam)
    written = 0
    for t in tracks:
        fp = t["fingerprint"]
        if not reclassify and genre_store.is_assigned(db_path, fp):
            continue
        fam, src = audio_override(t, t.get("label_family"), env)
        genre_store.upsert(db_path, fp, t.get("canon_genre"), fam, src,
                           t.get("label_conf") or 0.0, reclassify=reclassify)
        written += 1
    return written
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_family.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/family.py tests/test_family.py
git commit -m "feat(sequencer): family assign+persist with frozen stability"
```

---

## Phase 3 — Graph + template-guided ordering

### Task 10: `graph.py` — penalty-completed pairwise cost matrix

**Files:**
- Create: `src/walkman_dj/graph.py`
- Test: `tests/test_graph.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph.py
from walkman_dj.graph import HARD_CUT, pair_matrix

def _cfg():
    return {"bpm_window_pct": 0.06, "bpm_window_floor": 4.0, "bpm_conf_min": 0.6,
            "key_conf_min": 0.55, "w_energy_jump": 0.5, "w_unreliable_bpm": 0.2,
            "w_unreliable_key": 0.3, "allow_energy_boost": False}

def test_compatible_pair_is_soft():
    tr = [{"bpm": 128, "camelot": "8A", "bpm_confidence": 0.9, "key_confidence": 0.9},
          {"bpm": 129, "camelot": "8A", "bpm_confidence": 0.9, "key_confidence": 0.9}]
    m = pair_matrix(tr, {tr[0]["camelot"]: 0.5, 1: 0.5}, [0.5, 0.5], _cfg())
    assert m[0][1] < HARD_CUT

def test_key_clash_is_hard_cut():
    tr = [{"bpm": 128, "camelot": "8A", "bpm_confidence": 0.9, "key_confidence": 0.9},
          {"bpm": 129, "camelot": "3B", "bpm_confidence": 0.9, "key_confidence": 0.9}]
    m = pair_matrix(tr, None, [0.5, 0.5], _cfg())
    assert m[0][1] >= HARD_CUT

def test_bpm_gap_is_hard_cut():
    tr = [{"bpm": 128, "camelot": "8A", "bpm_confidence": 0.9, "key_confidence": 0.9},
          {"bpm": 150, "camelot": "8A", "bpm_confidence": 0.9, "key_confidence": 0.9}]
    m = pair_matrix(tr, None, [0.5, 0.5], _cfg())
    assert m[0][1] >= HARD_CUT

def test_unreliable_key_penalized_not_blocked():
    tr = [{"bpm": 128, "camelot": "8A", "bpm_confidence": 0.9, "key_confidence": 0.3},
          {"bpm": 129, "camelot": "3B", "bpm_confidence": 0.9, "key_confidence": 0.3}]  # would clash
    m = pair_matrix(tr, None, [0.5, 0.5], _cfg())
    assert m[0][1] < HARD_CUT          # allowed because key is unreliable
    assert m[0][1] >= 0.3              # but carries w_unreliable_key
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_graph.py -v`
Expected: FAIL — `ModuleNotFoundError: walkman_dj.graph`

- [ ] **Step 3: Write minimal implementation**

```python
# src/walkman_dj/graph.py
"""Penalty-completed pairwise cost matrix + sparse Dijkstra. Non-mixable pairs
carry HARD_CUT so ordering (greedy/2-opt/held-karp) is always well-defined."""
import heapq
from walkman_dj.camelot import camelot_edge_ok
from walkman_dj.distance import smoothness_cost

HARD_CUT = 1000.0


def _key_reliable(t, cfg):
    return (t.get("key_agree", True) is not False) and (t.get("key_confidence") or 0) >= cfg["key_conf_min"]


def pair_matrix(tracks, _energy_by_key, energy_n, cfg):
    """n x n cost. cost[i][j] = smoothness (+ penalties) if mixable, else HARD_CUT.
    energy_n: list aligned with tracks (rescaled energies)."""
    n = len(tracks)
    m = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            a, b = tracks[i], tracks[j]
            soft, hard_bpm = smoothness_cost(a, b, energy_n[i], energy_n[j], cfg)
            key_ok = camelot_edge_ok(a.get("camelot"), b.get("camelot"), cfg["allow_energy_boost"])
            key_unrel = not (_key_reliable(a, cfg) and _key_reliable(b, cfg))
            if hard_bpm or (not key_ok and not key_unrel):
                m[i][j] = HARD_CUT + soft
                continue
            cost = soft
            if not key_ok and key_unrel:
                cost += cfg["w_unreliable_key"]     # allowed but risky key
            m[i][j] = cost
    return m


def dijkstra(pair, src, dst):
    """Shortest smooth bridge over REAL edges only (HARD_CUT edges excluded)."""
    n = len(pair)
    dist = [float("inf")] * n
    prev = [-1] * n
    dist[src] = 0.0
    pq = [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u]:
            continue
        if u == dst:
            break
        for v in range(n):
            if v == u or pair[u][v] >= HARD_CUT:
                continue
            nd = d + pair[u][v]
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    if dist[dst] == float("inf"):
        return None
    path = []
    x = dst
    while x != -1:
        path.append(x)
        x = prev[x]
    return path[::-1]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_graph.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/graph.py tests/test_graph.py
git commit -m "feat(sequencer): penalty-completed cost matrix + sparse dijkstra"
```

---

### Task 11: `sequence.py` — ordering objective + greedy (template-aware)

**Files:**
- Modify: `src/walkman_dj/sequence.py`
- Test: `tests/test_order.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_order.py
from walkman_dj.sequence import path_cost, greedy_order

def test_path_cost_sums_pairwise_plus_template():
    pair = [[0, 1, 5], [1, 0, 1], [5, 1, 0]]
    en = [0.0, 0.5, 1.0]
    # monotonic template rewards ascending energy order 0->1->2
    c_asc = path_cost([0, 1, 2], pair, en, "monotonic", 1.0)
    c_desc = path_cost([2, 1, 0], pair, en, "monotonic", 1.0)
    assert c_asc < c_desc

def test_greedy_prefers_cheap_edges():
    pair = [[0, 1, 9], [1, 0, 1], [9, 1, 0]]
    order = greedy_order(pair, en=[0.5, 0.5, 0.5], template="monotonic",
                         w_template=0.0, start=0)
    assert order == [0, 1, 2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_order.py::test_path_cost_sums_pairwise_plus_template -v`
Expected: FAIL — `ImportError: cannot import name 'path_cost'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/walkman_dj/sequence.py
from walkman_dj.templates import target as _target


def path_cost(order, pair, energy_n, template, w_template):
    """Total open-path cost: sum of pairwise + per-position template deviation."""
    n = len(order)
    c = sum(pair[order[i]][order[i + 1]] for i in range(n - 1))
    if w_template and n > 1:
        c += sum(w_template * abs(energy_n[order[p]] - _target(template, p / (n - 1)))
                 for p in range(n))
    return c


def greedy_order(pair, en, template, w_template, start=0):
    """Nearest-neighbor on the penalty-completed matrix, biased toward the template
    energy at each slot."""
    n = len(pair)
    unvisited = set(range(n)) - {start}
    order = [start]
    while unvisited:
        cur = order[-1]
        pos = len(order)
        tgt = _target(template, pos / (n - 1)) if (w_template and n > 1) else None
        def _c(k):
            c = pair[cur][k]
            if tgt is not None:
                c += w_template * abs(en[k] - tgt)
            return c
        nxt = min(unvisited, key=_c)
        order.append(nxt)
        unvisited.discard(nxt)
    return order
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_order.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/sequence.py tests/test_order.py
git commit -m "feat(sequencer): template-aware path_cost + greedy_order"
```

---

### Task 12: `sequence.py` — 2-opt improvement

**Files:**
- Modify: `src/walkman_dj/sequence.py`
- Test: `tests/test_order.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_order.py
from walkman_dj.sequence import two_opt

def test_two_opt_never_worsens():
    import random
    rng = random.Random(0)
    n = 8
    pts = [rng.random() for _ in range(n)]
    pair = [[abs(pts[i] - pts[j]) for j in range(n)] for i in range(n)]
    en = [0.5] * n
    start = list(range(n))
    rng.shuffle(start)
    before = path_cost(start, pair, en, "monotonic", 0.0)
    after_order = two_opt(start, pair, en, "monotonic", 0.0)
    after = path_cost(after_order, pair, en, "monotonic", 0.0)
    assert after <= before + 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_order.py::test_two_opt_never_worsens -v`
Expected: FAIL — `ImportError: cannot import name 'two_opt'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/walkman_dj/sequence.py
import time as _time


def two_opt(order, pair, energy_n, template, w_template, max_passes=200, time_budget=2.0):
    """Reverse segments while total (pairwise+template) cost strictly drops."""
    best = list(order)
    bc = path_cost(best, pair, energy_n, template, w_template)
    deadline = _time.monotonic() + time_budget
    improved = True
    passes = 0
    n = len(best)
    while improved and passes < max_passes:
        improved = False
        passes += 1
        for i in range(1, n - 1):
            if _time.monotonic() > deadline:
                return best
            for k in range(i + 1, n):
                cand = best[:i] + best[i:k + 1][::-1] + best[k + 1:]
                cc = path_cost(cand, pair, energy_n, template, w_template)
                if cc < bc - 1e-9:
                    best, bc, improved = cand, cc, True
    return best
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_order.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/sequence.py tests/test_order.py
git commit -m "feat(sequencer): 2-opt improvement on combined objective"
```

---

### Task 13: `sequence.py` — Held-Karp exact for small n

**Files:**
- Modify: `src/walkman_dj/sequence.py`
- Test: `tests/test_order.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_order.py
from walkman_dj.sequence import held_karp

def test_held_karp_optimal_bounds_greedy_plus_2opt():
    import random
    rng = random.Random(1)
    for trial in range(20):
        n = rng.randint(4, 9)
        pts = [rng.random() for _ in range(n)]
        pair = [[abs(pts[i] - pts[j]) for j in range(n)] for i in range(n)]
        en = [0.5] * n
        exact = held_karp(pair, en, "monotonic", 0.0)
        ec = path_cost(exact, pair, en, "monotonic", 0.0)
        g = greedy_order(pair, en, "monotonic", 0.0, start=0)
        g = two_opt(g, pair, en, "monotonic", 0.0)
        gc = path_cost(g, pair, en, "monotonic", 0.0)
        assert ec <= gc + 1e-9                 # exact is optimal
        assert gc <= 1.3 * ec + 1e-9           # heuristic within 1.3x
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_order.py::test_held_karp_optimal_bounds_greedy_plus_2opt -v`
Expected: FAIL — `ImportError: cannot import name 'held_karp'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/walkman_dj/sequence.py

def held_karp(pair, energy_n, template, w_template):
    """Exact minimum-cost open Hamiltonian path via subset DP. Position within the
    path == popcount(mask)-1, so the per-position template term folds in exactly.
    Use only for small n (<= ~12); 2^n * n^2."""
    n = len(pair)
    if n <= 1:
        return list(range(n))
    INF = float("inf")

    def tmpl(idx, pos):
        if not w_template or n <= 1:
            return 0.0
        return w_template * abs(energy_n[idx] - _target(template, pos / (n - 1)))

    dp = [[INF] * n for _ in range(1 << n)]
    par = [[-1] * n for _ in range(1 << n)]
    for j in range(n):
        dp[1 << j][j] = tmpl(j, 0)              # single-node path, position 0
    for mask in range(1 << n):
        pos = bin(mask).count("1")             # nodes already placed
        for j in range(n):
            if not (mask >> j) & 1 or dp[mask][j] == INF:
                continue
            base = dp[mask][j]
            for k in range(n):
                if (mask >> k) & 1:
                    continue
                nm = mask | (1 << k)
                nc = base + pair[j][k] + tmpl(k, pos)   # k lands at slot `pos`
                if nc < dp[nm][k]:
                    dp[nm][k] = nc
                    par[nm][k] = j
    full = (1 << n) - 1
    end = min(range(n), key=lambda j: dp[full][j])
    order = []
    mask, j = full, end
    while j != -1:
        order.append(j)
        pj = par[mask][j]
        mask ^= (1 << j)
        j = pj
    return order[::-1]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_order.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/sequence.py tests/test_order.py
git commit -m "feat(sequencer): exact Held-Karp path with template folding"
```

---

### Task 14: `sequence.py` — `order_family` orchestrator + hard-cut surfacing

**Files:**
- Modify: `src/walkman_dj/sequence.py`
- Test: `tests/test_order.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_order.py
from walkman_dj.sequence import order_family

def _cfg():
    return {"bpm_window_pct": 0.06, "bpm_window_floor": 4.0, "bpm_conf_min": 0.6,
            "key_conf_min": 0.55, "w_energy_jump": 0.5, "w_unreliable_bpm": 0.2,
            "w_unreliable_key": 0.3, "allow_energy_boost": False,
            "exact_tsp_max_n": 12, "w_template": 1.0}

def test_order_family_returns_all_tracks_and_flags_hard():
    tracks = [
        {"fingerprint": "a", "bpm": 128, "camelot": "8A", "energy": 0.40,
         "bpm_confidence": 0.9, "key_confidence": 0.9},
        {"fingerprint": "b", "bpm": 129, "camelot": "8A", "energy": 0.55,
         "bpm_confidence": 0.9, "key_confidence": 0.9},
        {"fingerprint": "c", "bpm": 128, "camelot": "9A", "energy": 0.70,
         "bpm_confidence": 0.9, "key_confidence": 0.9},
        {"fingerprint": "z", "bpm": 175, "camelot": "3B", "energy": 0.72,   # isolated
         "bpm_confidence": 0.9, "key_confidence": 0.9},
    ]
    ordered, hard, isolated = order_family(tracks, _cfg(), template="single_peak")
    assert {t["fingerprint"] for t in ordered} | set(isolated) == {"a", "b", "c", "z"}
    assert "z" in isolated or any(h["to"] == "z" or h["from"] == "z" for h in hard)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_order.py::test_order_family_returns_all_tracks_and_flags_hard -v`
Expected: FAIL — `ImportError: cannot import name 'order_family'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/walkman_dj/sequence.py
from walkman_dj.graph import pair_matrix, HARD_CUT
from walkman_dj.family import rescale_energy


def order_family(tracks, cfg, template="single_peak"):
    """Order one family. Returns (ordered_tracks, hard_transitions, isolated_fps).
    - isolated: tracks with no real (non-HARD_CUT) edge to any other -> appended, not ordered.
    - hard_transitions: residual HARD_CUT steps in the final order (DJ handles manually)."""
    if len(tracks) <= 1:
        return (list(tracks), [], [])
    raw = {t["fingerprint"]: (t.get("energy") or 0.0) for t in tracks}
    en_map = rescale_energy(raw)
    en = [en_map[t["fingerprint"]] for t in tracks]
    pair = pair_matrix(tracks, None, en, cfg)

    # isolated = no real edge in either direction
    keep_idx, iso_idx = [], []
    for i in range(len(tracks)):
        has_edge = any(i != j and pair[i][j] < HARD_CUT for j in range(len(tracks)))
        (keep_idx if has_edge else iso_idx).append(i)

    if len(keep_idx) <= 1:
        ordered = [tracks[i] for i in keep_idx]
        return (ordered, [], [tracks[i]["fingerprint"] for i in iso_idx])

    sub = [[pair[i][j] for j in keep_idx] for i in keep_idx]
    subtr = [tracks[i] for i in keep_idx]
    suben = [en[i] for i in keep_idx]
    w_t = cfg["w_template"]

    # seed at the track closest to template start energy
    start = min(range(len(subtr)), key=lambda k: abs(suben[k] - _target(template, 0.0)))
    if len(subtr) <= cfg["exact_tsp_max_n"]:
        order = held_karp(sub, suben, template, w_t)
    else:
        order = greedy_order(sub, suben, template, w_t, start=start)
        order = two_opt(order, sub, suben, template, w_t)

    ordered = [subtr[i] for i in order]
    hard = []
    for p in range(len(order) - 1):
        if sub[order[p]][order[p + 1]] >= HARD_CUT:
            hard.append({"from": subtr[order[p]]["fingerprint"],
                         "to": subtr[order[p + 1]]["fingerprint"], "pos": p})
    return (ordered, hard, [tracks[i]["fingerprint"] for i in iso_idx])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_order.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/sequence.py tests/test_order.py
git commit -m "feat(sequencer): order_family orchestrator with hard/isolated surfacing"
```

---

### Task 15: `seqcli.py` — CLI → `.m3u8` + annotated `.md`

**Files:**
- Create: `src/walkman_dj/seqcli.py`
- Create: `config/sequencer.toml`
- Test: `tests/test_seqcli.py`

- [ ] **Step 1: Write `config/sequencer.toml`**

```toml
# config/sequencer.toml — all sequencer weights/windows in one place
bpm_window_pct   = 0.06
bpm_window_floor = 4.0
bpm_conf_min     = 0.60
key_conf_min     = 0.55
w_energy_jump    = 0.5
w_unreliable_bpm = 0.2
w_unreliable_key = 0.3
allow_energy_boost = false
exact_tsp_max_n  = 12
w_template       = 1.0
default_template = "single_peak"
w_genre_bridge   = 0.5
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_seqcli.py
from walkman_dj.seqcli import render_m3u8, render_md

def _ordered():
    return [
        {"fingerprint": "a", "path": "/m/A.flac", "title": "A", "bpm": 128,
         "camelot": "8A", "energy": 0.4, "duration": 200},
        {"fingerprint": "b", "path": "/m/B.flac", "title": "B", "bpm": 129,
         "camelot": "8A", "energy": 0.6, "duration": 210},
    ]

def test_render_m3u8_has_extinf_and_relative_paths():
    out = render_m3u8(_ordered(), music_root="/m")
    assert out.startswith("#EXTM3U")
    assert "#EXTINF:200,A" in out
    assert "A.flac" in out and "/m/A.flac" not in out   # relativized

def test_render_md_lists_transitions_and_sections():
    out = render_md(_ordered(), hard=[], isolated=[], template="single_peak")
    assert "single_peak" in out
    assert "8A" in out
    assert "## No clean transition" in out  # section always present (may be empty)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_seqcli.py -v`
Expected: FAIL — `ModuleNotFoundError: walkman_dj.seqcli`

- [ ] **Step 4: Write minimal implementation**

```python
# src/walkman_dj/seqcli.py
"""CLI: order a family / full set / anchor bridge -> .m3u8 + annotated .md."""
import argparse
import os
import tomllib
from walkman_dj.sequence import order_family
from walkman_dj.camelot import camelot_edge_ok

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CFG = os.path.join(ROOT, "config", "sequencer.toml")


def load_cfg(path=DEFAULT_CFG):
    with open(path, "rb") as f:
        return tomllib.load(f)


def render_m3u8(ordered, music_root):
    lines = ["#EXTM3U"]
    for t in ordered:
        dur = int(t.get("duration") or 0)
        title = t.get("title") or t["fingerprint"]
        rel = os.path.relpath(t["path"], music_root)
        lines.append(f"#EXTINF:{dur},{title}")
        lines.append(rel)
    return "\n".join(lines) + "\n"


def render_md(ordered, hard, isolated, template):
    hardset = {(h["from"], h["to"]) for h in hard}
    out = [f"# Set — template: {template}", ""]
    for i, t in enumerate(ordered):
        line = f"{i+1:>2}. **{t.get('title') or t['fingerprint']}** — {t.get('bpm')} BPM · {t.get('camelot')} · E={t.get('energy'):.2f}"
        out.append(line)
        if i + 1 < len(ordered):
            nxt = ordered[i + 1]
            if (t["fingerprint"], nxt["fingerprint"]) in hardset:
                out.append("    - `## Hard transition` — no harmonic/BPM bridge, mix manually")
    out += ["", "## No clean transition", ""]
    if isolated:
        out += [f"- {fp}" for fp in isolated]
    else:
        out.append("_(none)_")
    return "\n".join(out) + "\n"


def _load_family_tracks(db_path, family):
    """Join features + track_genre for one family. Tracks carry path/title from a
    device index side-table if present; here we read features + genre_store."""
    import sqlite3
    from walkman_dj import genre_store
    fams = genre_store.get_all(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = []
    for r in con.execute("SELECT * FROM features WHERE status='ok'"):
        fp = r["fingerprint"]
        if fams.get(fp, {}).get("family") != family:
            continue
        d = dict(r)
        d["path"] = d.get("path") or fp
        d["title"] = d.get("title") or fp
        rows.append(d)
    con.close()
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "walkman_analysis.db"))
    ap.add_argument("--config", default=DEFAULT_CFG)
    ap.add_argument("--family", required=True)
    ap.add_argument("--template", default=None)
    ap.add_argument("--music-root", default="/storage/6439-6561/Music")
    ap.add_argument("--out", required=True, help="output basename (writes .m3u8 + .md)")
    a = ap.parse_args(argv)
    cfg = load_cfg(a.config)
    template = a.template or cfg["default_template"]
    tracks = _load_family_tracks(a.db, a.family)
    ordered, hard, isolated = order_family(tracks, cfg, template=template)
    with open(a.out + ".m3u8", "w") as f:
        f.write(render_m3u8(ordered, a.music_root))
    with open(a.out + ".md", "w") as f:
        f.write(render_md(ordered, hard, isolated, template))
    print(f"{a.family}: {len(ordered)} ordered, {len(hard)} hard, {len(isolated)} isolated")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_seqcli.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Smoke-test on the real DB (manual)**

Run: `python -m walkman_dj.seqcli --family HOUSE --template single_peak --out /tmp/house_set`
Expected: prints `HOUSE: N ordered, ...`; `/tmp/house_set.m3u8` + `.md` exist. (Requires Phase 2 assignment run first; if `track_genre` empty, order is empty — that's the wiring in Task 16.)

- [ ] **Step 7: Commit**

```bash
git add src/walkman_dj/seqcli.py config/sequencer.toml tests/test_seqcli.py
git commit -m "feat(sequencer): CLI -> m3u8 + annotated md"
```

---

### Task 16: Wire family assignment into the pipeline

**Files:**
- Create: `src/walkman_dj/assign_families.py`
- Test: `tests/test_assign_families.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_assign_families.py
from walkman_dj.assign_families import build_track_records

def test_build_records_uses_resolve_family_and_canon():
    feat = {"fingerprint": "fp1", "bpm": 124, "energy": 0.55,
            "bass_weight": 0.5, "brightness": 0.5}
    rec = build_track_records([feat], {"fp1": {"genre": "Tech House", "conf": 0.9, "title": "x"}})
    assert rec[0]["label_family"] in ("tech_house", "HOUSE", "organic_house", "nu_disco")
    assert rec[0]["canon_genre"] == "tech house"
    assert rec[0]["fingerprint"] == "fp1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_assign_families.py -v`
Expected: FAIL — `ModuleNotFoundError: walkman_dj.assign_families`

- [ ] **Step 3: Write minimal implementation**

```python
# src/walkman_dj/assign_families.py
"""Glue: features + external genre signals -> track records -> family.assign_and_persist.
Reuses genre.resolve_family (per-track, structural) + genre.canon."""
import os
from walkman_dj import genre, family

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "walkman_analysis.db")


def build_track_records(features, genre_signals):
    """features: list of feature dicts. genre_signals: {fp: {genre, conf, title}}.
    Returns records with label_family (coarse bucket), canon_genre, label_conf + envelope feats."""
    recs = []
    for f in features:
        fp = f["fingerprint"]
        sig = genre_signals.get(fp, {})
        gstr = sig.get("genre")
        coarse = genre.coarse_of(gstr)
        fam = genre.resolve_family(None, [coarse], f.get("bpm"), sig.get("title"))
        bucket = genre.FAM_COARSE.get(fam, coarse or "DOWNTEMPO")
        recs.append({
            "fingerprint": fp,
            "label_family": bucket,
            "canon_genre": genre.canon(gstr) if gstr else "",
            "label_conf": sig.get("conf") or (0.7 if coarse else 0.3),
            "bpm": f.get("bpm"), "energy": f.get("energy"),
            "bass_weight": f.get("bass_weight"), "brightness": f.get("brightness"),
        })
    return recs


def run(db_path=DB, reclassify=False):
    """Load features + any persisted genre signals, assign, persist frozen."""
    import sqlite3
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    features = [dict(r) for r in con.execute("SELECT * FROM features WHERE status='ok'")]
    signals = {}
    # genre signals live wherever the online enricher wrote them; fall back to empty.
    try:
        for r in con.execute("SELECT fingerprint, genre, confidence, title FROM track_meta"):
            signals[r["fingerprint"]] = {"genre": r["genre"], "conf": r["confidence"], "title": r["title"]}
    except sqlite3.OperationalError:
        pass
    con.close()
    recs = build_track_records(features, signals)
    n = family.assign_and_persist(db_path, recs, reclassify=reclassify)
    print(f"assigned {n} new tracks ({'reclassify' if reclassify else 'frozen'})")
    return n


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--reclassify", action="store_true")
    a = ap.parse_args()
    run(a.db, a.reclassify)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_assign_families.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Run assignment on the real DB (manual)**

Run: `python -m walkman_dj.assign_families`
Expected: `assigned N new tracks (frozen)`. Re-run → `assigned 0 new tracks`. Then re-run the Task 15 smoke test; families now non-empty.

- [ ] **Step 6: Commit**

```bash
git add src/walkman_dj/assign_families.py tests/test_assign_families.py
git commit -m "feat(sequencer): wire per-track family assignment + persist"
```

---

## Phase 4 — Stitching, anchor routing, stability

### Task 17: `sequence.py` — cross-family macro-arc stitch

**Files:**
- Modify: `src/walkman_dj/sequence.py`
- Test: `tests/test_stitch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stitch.py
from walkman_dj.sequence import stitch_families

def test_stitch_orders_families_by_median_energy_ascending():
    fam_orders = {
        "HOT": [{"fingerprint": "h1", "energy": 0.9}, {"fingerprint": "h2", "energy": 0.85}],
        "WARM": [{"fingerprint": "w1", "energy": 0.3}, {"fingerprint": "w2", "energy": 0.35}],
    }
    seq, boundaries = stitch_families(fam_orders)
    assert [t["fingerprint"] for t in seq][:2] == ["w1", "w2"]   # warm first
    assert "WARM" in boundaries and "HOT" in boundaries
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_stitch.py -v`
Expected: FAIL — `ImportError: cannot import name 'stitch_families'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/walkman_dj/sequence.py
import statistics as _stats


def stitch_families(family_orders):
    """Concatenate per-family ordered lists into one macro arc, warm->peak by
    median energy. Returns (sequence, boundaries) where boundaries maps family ->
    start index. (Pivot-edge optimization is Task 18-adjacent; concatenation first.)"""
    def med(tracks):
        return _stats.median([t.get("energy") or 0.0 for t in tracks]) if tracks else 0.0
    ordered_fams = sorted(family_orders, key=lambda f: med(family_orders[f]))
    seq, boundaries = [], {}
    for fam in ordered_fams:
        boundaries[fam] = len(seq)
        seq.extend(family_orders[fam])
    return seq, boundaries
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_stitch.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/sequence.py tests/test_stitch.py
git commit -m "feat(sequencer): cross-family macro-arc stitch"
```

---

### Task 18: `sequence.py` — freeze-and-insert stability

**Files:**
- Modify: `src/walkman_dj/sequence.py`
- Test: `tests/test_stability.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stability.py
from walkman_dj.sequence import insert_into_order, order_family

def _cfg():
    return {"bpm_window_pct": 0.06, "bpm_window_floor": 4.0, "bpm_conf_min": 0.6,
            "key_conf_min": 0.55, "w_energy_jump": 0.5, "w_unreliable_bpm": 0.2,
            "w_unreliable_key": 0.3, "allow_energy_boost": False,
            "exact_tsp_max_n": 12, "w_template": 1.0}

def _mk(fp, bpm, cam, e):
    return {"fingerprint": fp, "bpm": bpm, "camelot": cam, "energy": e,
            "bpm_confidence": 0.9, "key_confidence": 0.9}

def test_insert_preserves_existing_relative_order():
    existing = [_mk("a", 126, "8A", 0.3), _mk("b", 128, "8A", 0.5), _mk("c", 130, "9A", 0.7)]
    ordered, _, _ = order_family(existing, _cfg(), template="single_peak")
    base_fps = [t["fingerprint"] for t in ordered]
    newcomers = [_mk("d", 129, "8A", 0.6)]
    merged = insert_into_order(ordered, newcomers, _cfg(), template="single_peak")
    merged_fps = [t["fingerprint"] for t in merged]
    # every existing track present, and their relative order unchanged
    assert set(base_fps) | {"d"} == set(merged_fps)
    filtered = [fp for fp in merged_fps if fp in base_fps]
    assert filtered == base_fps
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_stability.py -v`
Expected: FAIL — `ImportError: cannot import name 'insert_into_order'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/walkman_dj/sequence.py
from walkman_dj.graph import pair_matrix as _pm, HARD_CUT as _HC


def insert_into_order(existing_ordered, newcomers, cfg, template="single_peak"):
    """Stability: keep the frozen order of existing tracks; splice each newcomer
    into its lowest-cost gap. Does NOT re-solve the whole set."""
    seq = list(existing_ordered)
    for nt in newcomers:
        best_pos, best_delta = len(seq), float("inf")
        for pos in range(len(seq) + 1):
            trial = seq[:pos] + [nt] + seq[pos:]
            raw = {t["fingerprint"]: (t.get("energy") or 0.0) for t in trial}
            en_map = rescale_energy(raw)
            en = [en_map[t["fingerprint"]] for t in trial]
            pair = _pm(trial, None, en, cfg)
            cost = path_cost(list(range(len(trial))), pair, en, template, cfg["w_template"])
            if cost < best_delta:
                best_delta, best_pos = cost, pos
        seq = seq[:best_pos] + [nt] + seq[best_pos:]
    return seq
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_stability.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Add anchor-routing CLI mode (Mode B)**

Add to `seqcli.py` `main()` an `--from FP --to FP` branch that calls `graph.dijkstra` over the family pair-matrix and writes the bridge as `.m3u8`. Test:

```python
# append to tests/test_graph.py
from walkman_dj.graph import dijkstra
def test_dijkstra_bridges_via_real_edges():
    pair = [[0, 1, HARD_CUT, HARD_CUT],
            [1, 0, 1, HARD_CUT],
            [HARD_CUT, 1, 0, 1],
            [HARD_CUT, HARD_CUT, 1, 0]]
    assert dijkstra(pair, 0, 3) == [0, 1, 2, 3]
```

Run: `python -m pytest tests/test_graph.py::test_dijkstra_bridges_via_real_edges -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/walkman_dj/sequence.py src/walkman_dj/seqcli.py tests/test_stability.py tests/test_graph.py
git commit -m "feat(sequencer): freeze-and-insert stability + Mode B anchor routing"
```

---

### Task 19: Full-suite green + acceptance harness

**Files:**
- Create: `tests/test_sequencer_snapshot.py`

- [ ] **Step 1: Write a snapshot smoke test over a sanitized fixture**

```python
# tests/test_sequencer_snapshot.py
from walkman_dj.sequence import order_family

def _cfg():
    return {"bpm_window_pct": 0.06, "bpm_window_floor": 4.0, "bpm_conf_min": 0.6,
            "key_conf_min": 0.55, "w_energy_jump": 0.5, "w_unreliable_bpm": 0.2,
            "w_unreliable_key": 0.3, "allow_energy_boost": False,
            "exact_tsp_max_n": 12, "w_template": 1.0}

def test_wave_template_rises_and_falls():
    # a family that CAN be arranged into a wave should show >=1 local max interior
    import random
    rng = random.Random(3)
    tracks = [{"fingerprint": f"t{i}", "bpm": 128 + rng.randint(-2, 2),
               "camelot": "8A", "energy": rng.random(),
               "bpm_confidence": 0.9, "key_confidence": 0.9} for i in range(14)]
    ordered, hard, iso = order_family(tracks, _cfg(), template="wave")
    es = [t["energy"] for t in ordered]
    maxima = [i for i in range(1, len(es) - 1) if es[i] > es[i-1] and es[i] > es[i+1]]
    assert len(maxima) >= 1
    assert len(iso) == 0   # all same key/bpm -> fully connected
```

- [ ] **Step 2: Run the whole suite**

Run: `python -m pytest -q`
Expected: all green (existing + new). If pre-existing tests break, reconcile — new modules must not change old behavior.

- [ ] **Step 3: Manual ear-acceptance checklist (record results in the PR)**

1. `python -m walkman_dj.assign_families` then order 3 known families (`HOUSE`, `BASS`, `BAILE`) with `--template wave` for BAILE, `single_peak` for others.
2. Import each `.m3u8` into Rekordbox; confirm pitch-fader-only mixability start→finish.
3. Confirm the `wave` BAILE set visibly rises **and** falls in the waveform overview.
4. Confirm `## No clean transition` holds < 10% of each family's tracks.

- [ ] **Step 4: Commit**

```bash
git add tests/test_sequencer_snapshot.py
git commit -m "test(sequencer): wave-shape snapshot + acceptance harness"
```

---

## Phase 5 — Later (not planned in detail)

Deferred until Phases 1–4 are ear-accepted. Outline only:

- Use `mix_in`/`mix_out` cue points + `loudness_curve` tails to compute **actual
  overlap compatibility** (does A's outro loudness/where it ends line up with B's
  intro), refining edge weights beyond BPM+key.
- Segment-aware transition suggestions (which section of A to bring B in under).
- Optional intra-family sub-clustering (the existing `tech_house/organic_house/nu_disco`
  split) for naming very large families and multi-segment sets.

---

## Self-review notes (author)

- **Spec coverage:** §0 grounding → Tasks 0/6/16; §1 features → Tasks 1–2,7; §2 family → Tasks 6–9,16; §3 graph+ordering → Tasks 10–14; templates §3.5 → Task 5,11,13; §4 stitch/stability/Mode B → Tasks 17–18; §5 tests → every task + Task 19. Covered.
- **No new deps:** confirmed — only `numpy`(already used elsewhere; this plan uses pure lists), `sqlite3`, `tomllib`, `pytest`.
- **Type consistency:** `order_family(tracks, cfg, template)` → `(ordered, hard, isolated)`; `path_cost(order, pair, energy_n, template, w_template)`; `pair_matrix(tracks, _energy_by_key, energy_n, cfg)`; `held_karp/greedy_order/two_opt` share `(…, energy_n, template, w_template)`. Consistent across tasks.
- **Open risk:** `seqcli._load_family_tracks` assumes `features` rows carry `path`/`title`; if the device path lives in a side table, Task 15 Step 6 smoke test will show empty titles — resolve by joining the device index when wiring, not a code change to the ordering core.
