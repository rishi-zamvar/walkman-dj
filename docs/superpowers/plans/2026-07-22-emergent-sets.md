# Emergent Set Formation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fixed, capped genre playlists with DJ sets that emerge as communities in one whole-library transition graph, ordered to ≤1 hard cut each.

**Architecture:** BPM-bucketed genre-weighted affinity graph over all analyzed tracks → Louvain communities (resolution-tuned, oversize-split, singletons-parked) → per-community template ordering (reuse `order_family`) → device deploy + an eval/tuning sweep harness.

**Tech Stack:** Python 3.14, `networkx` 3.5 (`louvain_communities`, already installed — declare it), `numpy`, `sqlite3`, `pytest`. No other new deps.

**Spec:** `docs/superpowers/specs/2026-07-22-emergent-sets-design.md`
**Builds on:** `feat/set-sequencer` branch (Set Sequencer Phases 1–4 already shipped: `graph.py`, `sequence.py`, `genre.py`, `templates.py`, `family.py`, `genre_store.py`, `seqcli.py`).

**Grounding (verified):** networkx 3.5 has `networkx.algorithms.community.louvain_communities`. 4,911 analyzed tracks → 12M naive pairs (must bucket, not all-pairs). Full baile pool already orders to 1 hard cut / 1 isolated at median degree 44 — the target is reproducing that library-wide.

---

## Phase 0 — Dependency + graph-build spike

### Task 0: Declare networkx, benchmark candidate generation

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/test_libgraph_scale.py` (a fast spike test, not a benchmark in CI)

- [ ] **Step 1: Declare the dep**

Edit `pyproject.toml` `dependencies` to add `"networkx>=3.4"` (louvain_communities needs ≥3.0; 3.5 present).

Run: `python3 -c "import networkx; from networkx.algorithms.community import louvain_communities; print(networkx.__version__)"`
Expected: prints `3.5`.

- [ ] **Step 2: Spike test — bucketed candidate gen beats all-pairs**

```python
# tests/test_libgraph_scale.py
import random, time
def test_bucketed_candidates_are_subquadratic():
    from walkman_dj.libgraph import candidate_pairs
    rng = random.Random(0)
    tracks = [{"fingerprint": f"t{i}", "bpm": rng.choice([124, 128, 130, 136, 150, 174]) + rng.random(),
               "camelot": f"{rng.randint(1,12)}A"} for i in range(2000)]
    pairs = candidate_pairs(tracks, window_pct=0.06, floor=4.0)
    # far below the 2M all-pairs; only BPM-near (or half/double) pairs survive
    assert len(pairs) < 2000 * 2000 // 4
    # every returned pair is genuinely BPM-close (incl half/double)
    from walkman_dj.distance import bpm_distance, bpm_window
    for i, j in list(pairs)[:500]:
        d = bpm_distance(tracks[i]["bpm"], tracks[j]["bpm"])
        assert d <= bpm_window(tracks[i]["bpm"], tracks[j]["bpm"]) + 1e-9
```

- [ ] **Step 3: Run (fails — libgraph not built yet)**

Run: `python3 -m pytest tests/test_libgraph_scale.py -v`
Expected: FAIL — `ModuleNotFoundError: walkman_dj.libgraph`. (Implemented in Task 1; this test rides along.)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml tests/test_libgraph_scale.py
git commit -m "chore(emergent): declare networkx dep + graph-scale spike test"
```

---

## Phase 1 — Whole-library affinity graph

### Task 1: `libgraph.py` — BPM-bucketed candidate pairs

**Files:**
- Create: `src/walkman_dj/libgraph.py`
- Test: `tests/test_libgraph.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_libgraph.py
from walkman_dj.libgraph import candidate_pairs

def test_candidates_include_bpm_neighbors():
    tr = [{"fingerprint": "a", "bpm": 128, "camelot": "8A"},
          {"fingerprint": "b", "bpm": 130, "camelot": "8A"},
          {"fingerprint": "c", "bpm": 200, "camelot": "8A"}]
    p = candidate_pairs(tr, window_pct=0.06, floor=4.0)
    assert (0, 1) in p or (1, 0) in p        # 128~130 close
    assert (0, 2) not in p and (2, 0) not in p  # 128 vs 200 far (and not half/double)

def test_candidates_fold_half_double():
    tr = [{"fingerprint": "a", "bpm": 150, "camelot": "8A"},
          {"fingerprint": "b", "bpm": 75, "camelot": "8A"}]   # 150 == 2x75
    p = candidate_pairs(tr, window_pct=0.06, floor=4.0)
    assert (0, 1) in p or (1, 0) in p
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_libgraph.py::test_candidates_fold_half_double -v`
Expected: FAIL — `ImportError: cannot import name 'candidate_pairs'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/walkman_dj/libgraph.py
"""Whole-library transition graph: BPM-bucketed candidate generation (never
all-pairs) + genre-weighted affinity edges, for community detection."""
import bisect
import networkx as nx
from walkman_dj.camelot import camelot_edge_ok
from walkman_dj.distance import bpm_distance, bpm_window, smoothness_cost
from walkman_dj.genre import jaccard
from walkman_dj.family import rescale_energy


def candidate_pairs(tracks, window_pct=0.06, floor=4.0):
    """Set of (i,j) index pairs whose BPMs are within the pitch window, including
    half/double-time equivalence — via bisect on a sorted BPM index, so it is
    ~O(n·neighbourhood), not O(n²). i<j guaranteed."""
    n = len(tracks)
    idx = sorted(range(n), key=lambda k: tracks[k].get("bpm") or 0.0)
    bpms = [tracks[k].get("bpm") or 0.0 for k in idx]
    out = set()
    for pos in range(n):
        i = idx[pos]
        b = bpms[pos]
        if not b:
            continue
        # widest window this track could ever match (use its own bpm for the bound)
        for center in (b, b * 2, b / 2):
            if center <= 0:
                continue
            win = bpm_window(center, center, window_pct, floor) + 1e-6
            lo = bisect.bisect_left(bpms, center - win)
            hi = bisect.bisect_right(bpms, center + win)
            for q in range(lo, hi):
                j = idx[q]
                if j == i:
                    continue
                if bpm_distance(b, tracks[j].get("bpm")) <= max(
                        bpm_window(b, tracks[j].get("bpm") or 0, window_pct, floor),
                        bpm_window(tracks[j].get("bpm") or 0, b, window_pct, floor)):
                    out.add((i, j) if i < j else (j, i))
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_libgraph.py tests/test_libgraph_scale.py -v`
Expected: PASS (3 passed — the two here + the scale spike from Task 0).

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/libgraph.py tests/test_libgraph.py
git commit -m "feat(emergent): BPM-bucketed candidate pair generation"
```

---

### Task 2: `libgraph.py` — build the genre-weighted affinity graph

**Files:**
- Modify: `src/walkman_dj/libgraph.py`
- Test: `tests/test_libgraph.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_libgraph.py
from walkman_dj.libgraph import build_graph

def _cfg():
    return {"bpm_window_pct": 0.06, "bpm_window_floor": 4.0, "bpm_conf_min": 0.6,
            "key_conf_min": 0.55, "w_energy_jump": 0.5, "w_unreliable_bpm": 0.2,
            "w_unreliable_key": 0.3, "allow_energy_boost": False, "w_genre_bridge": 0.5}

def _t(fp, bpm, cam, e, genre):
    return {"fingerprint": fp, "bpm": bpm, "camelot": cam, "energy": e,
            "bpm_confidence": 0.9, "key_confidence": 0.9, "canon_genre": genre,
            "family": genre.upper()}

def test_graph_connects_compatible_same_genre_strongly():
    tr = [_t("a", 128, "8A", 0.5, "tech house"), _t("b", 129, "8A", 0.5, "tech house"),
          _t("c", 128, "8A", 0.5, "hip-hop")]
    G = build_graph(tr, _cfg())
    assert G.has_edge("a", "b") and G.has_edge("a", "c")
    # same-genre edge weight (affinity) higher than cross-genre
    assert G["a"]["b"]["weight"] > G["a"]["c"]["weight"]

def test_graph_no_edge_when_key_clashes():
    tr = [_t("a", 128, "8A", 0.5, "tech house"), _t("b", 129, "3B", 0.5, "tech house")]
    G = build_graph(tr, _cfg())
    assert not G.has_edge("a", "b")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_libgraph.py::test_graph_connects_compatible_same_genre_strongly -v`
Expected: FAIL — `ImportError: cannot import name 'build_graph'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/walkman_dj/libgraph.py
def _key_reliable(t, cfg):
    return (t.get("key_agree", True) is not False) and (t.get("key_confidence") or 0) >= cfg["key_conf_min"]


def build_graph(tracks, cfg):
    """networkx.Graph over tracks. Edge iff harmonic + BPM compatible; weight is
    affinity = 1/(1+cost), cost = smoothness + w_genre_bridge*(1 - genre_jaccard).
    Higher weight = stronger tie (Louvain maximises intra-community weight)."""
    raw = {t["fingerprint"]: (t.get("energy") or 0.0) for t in tracks}
    en = rescale_energy(raw)
    G = nx.Graph()
    for t in tracks:
        G.add_node(t["fingerprint"], family=t.get("family"), bpm=t.get("bpm"),
                   energy=t.get("energy"), camelot=t.get("camelot"),
                   canon=t.get("canon_genre"))
    for i, j in candidate_pairs(tracks, cfg["bpm_window_pct"], cfg["bpm_window_floor"]):
        a, b = tracks[i], tracks[j]
        key_ok = camelot_edge_ok(a.get("camelot"), b.get("camelot"), cfg["allow_energy_boost"])
        key_unrel = not (_key_reliable(a, cfg) and _key_reliable(b, cfg))
        if not key_ok and not key_unrel:
            continue                              # hard key clash -> no edge
        soft, hard_bpm = smoothness_cost(a, b, en[a["fingerprint"]], en[b["fingerprint"]], cfg)
        if hard_bpm:
            continue
        cost = soft
        if not key_ok and key_unrel:
            cost += cfg["w_unreliable_key"]
        cost += cfg["w_genre_bridge"] * (1.0 - jaccard(a.get("canon_genre") or "",
                                                        b.get("canon_genre") or ""))
        G.add_edge(a["fingerprint"], b["fingerprint"], weight=1.0 / (1.0 + cost), cost=cost)
    return G
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_libgraph.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/libgraph.py tests/test_libgraph.py
git commit -m "feat(emergent): genre-weighted affinity graph over the library"
```

---

## Phase 2 — Community detection + size shaping

### Task 3: `community.py` — Louvain + oversize split + park singletons

**Files:**
- Create: `src/walkman_dj/community.py`
- Test: `tests/test_community.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_community.py
import networkx as nx
from walkman_dj.community import detect

def _two_cliques():
    G = nx.Graph()
    A = [f"a{i}" for i in range(10)]
    B = [f"b{i}" for i in range(10)]
    for grp in (A, B):
        for i in range(len(grp)):
            for j in range(i + 1, len(grp)):
                G.add_edge(grp[i], grp[j], weight=1.0)
    G.add_edge("a0", "b0", weight=0.05)   # one weak bridge
    return G, A, B

def test_detect_recovers_two_communities():
    G, A, B = _two_cliques()
    comms, loose = detect(G, set_min=3, set_max=50, resolution=1.0, seed=1)
    labels = [set(c) for c in comms]
    assert set(A) in labels and set(B) in labels
    assert loose == set()

def test_singletons_go_to_loose():
    G = nx.Graph()
    G.add_edge("a0", "a1", weight=1.0)
    G.add_node("x")                        # isolated node
    comms, loose = detect(G, set_min=2, set_max=50, resolution=1.0, seed=1)
    assert "x" in loose

def test_oversize_is_split():
    G = nx.complete_graph([f"n{i}" for i in range(40)])
    for u, v in G.edges():
        G[u][v]["weight"] = 1.0
    comms, loose = detect(G, set_min=3, set_max=15, resolution=1.0, seed=1)
    assert all(len(c) <= 15 for c in comms)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_community.py -v`
Expected: FAIL — `ModuleNotFoundError: walkman_dj.community`

- [ ] **Step 3: Write minimal implementation**

```python
# src/walkman_dj/community.py
"""Louvain community detection with size shaping: split oversize communities at a
higher resolution, park un-mergeable singletons in a 'loose' bin."""
from networkx.algorithms.community import louvain_communities


def _louvain(G, resolution, seed):
    if G.number_of_edges() == 0:
        return [{n} for n in G.nodes()]
    return [set(c) for c in louvain_communities(G, weight="weight",
                                                resolution=resolution, seed=seed)]


def _split_oversize(G, comm, set_max, resolution, seed, depth=0):
    if len(comm) <= set_max or depth >= 4:
        return [comm]
    sub = G.subgraph(comm)
    parts = _louvain(sub, resolution * 1.6, seed)
    if len(parts) <= 1:                     # cannot split further
        return [comm]
    out = []
    for p in parts:
        out.extend(_split_oversize(G, p, set_max, resolution * 1.6, seed, depth + 1))
    return out


def detect(G, set_min=8, set_max=120, resolution=1.0, seed=1):
    """Return (communities, loose_set). communities are size-shaped; tracks that
    can't join a >=set_min community land in loose."""
    comms = []
    for c in _louvain(G, resolution, seed):
        comms.extend(_split_oversize(G, c, set_max, resolution, seed))
    big = [c for c in comms if len(c) >= set_min]
    small = [c for c in comms if len(c) < set_min]
    loose = set()
    for c in small:
        for node in c:
            # attach to the big community holding this node's strongest neighbour
            best, bw = None, -1.0
            for nb in G.neighbors(node):
                w = G[node][nb].get("weight", 0)
                host = next((b for b in big if nb in b), None)
                if host is not None and w > bw:
                    best, bw = host, w
            if best is not None:
                best.add(node)
            else:
                loose.add(node)
    return big, loose
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_community.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/community.py tests/test_community.py
git commit -m "feat(emergent): Louvain communities with size shaping + loose bin"
```

---

## Phase 3 — Naming, orchestration, ordering

### Task 4: `setname.py` — factual set names

**Files:**
- Create: `src/walkman_dj/setname.py`
- Test: `tests/test_setname.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setname.py
from walkman_dj.setname import name_set

def test_name_uses_dominant_family_bpm_energy():
    tracks = [{"family": "baile_edits", "bpm": 131, "energy": 0.7},
              {"family": "baile_edits", "bpm": 133, "energy": 0.72},
              {"family": "ukg_bass", "bpm": 132, "energy": 0.68}]
    nm = name_set(tracks)
    assert "Baile" in nm            # dominant family -> nice label
    assert "132" in nm or "131" in nm or "133" in nm  # median-ish bpm band
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_setname.py -v`
Expected: FAIL — `ModuleNotFoundError: walkman_dj.setname`

- [ ] **Step 3: Write minimal implementation**

```python
# src/walkman_dj/setname.py
"""Factual name for an emergent set: dominant genre + median BPM + energy tier."""
import statistics
from collections import Counter

# fine family -> nice label (mirrors overnight/rebuild.py GENRE names)
NICE = {
    "opium_rage": "Opium & Rage", "atlanta_trap": "Atlanta Trap", "travis_rage": "Travis Scott",
    "melodic_trap": "Melodic Trap", "toronto": "Toronto Nights", "lyrical_soul": "Lyrical Hip-Hop",
    "alt_rnb": "Alt R&B", "afrobeats": "Afrobeats", "tech_house": "Tech House",
    "organic_house": "Organic & Afro House", "nu_disco": "Nu-Disco & Funk", "baile_edits": "Baile Funk",
    "ukg_bass": "UK Garage", "jersey_juke": "Jersey Club", "electro_classics": "Electro",
    "global_downtempo": "Downtempo", "rap": "Hip-Hop", "pop": "Pop",
}


def name_set(tracks):
    fams = Counter(t.get("family") for t in tracks if t.get("family"))
    fam = fams.most_common(1)[0][0] if fams else "mixed"
    label = NICE.get(fam, fam.replace("_", " ").title())
    bpms = [t["bpm"] for t in tracks if t.get("bpm")]
    med = int(statistics.median(bpms)) if bpms else 0
    es = [t.get("energy") or 0 for t in tracks]
    me = statistics.median(es) if es else 0
    tier = "Peak" if me >= 0.6 else ("Warm" if me >= 0.45 else "Deep")
    return f"{label} · {med} · {tier}"
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_setname.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/setname.py tests/test_setname.py
git commit -m "feat(emergent): factual set naming"
```

---

### Task 5: `emergent.py` — orchestrator (graph → communities → ordered sets)

**Files:**
- Create: `src/walkman_dj/emergent.py`
- Test: `tests/test_emergent.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_emergent.py
from walkman_dj.emergent import build_sets

def _cfg():
    return {"bpm_window_pct": 0.06, "bpm_window_floor": 4.0, "bpm_conf_min": 0.6,
            "key_conf_min": 0.55, "w_energy_jump": 0.5, "w_unreliable_bpm": 0.2,
            "w_unreliable_key": 0.3, "allow_energy_boost": False, "w_genre_bridge": 0.5,
            "w_template": 1.0, "exact_tsp_max_n": 12, "community_resolution": 1.0,
            "set_min": 3, "set_max": 60, "louvain_seed": 1, "default_template": "single_peak"}

def _mk(fp, bpm, cam, e, g):
    return {"fingerprint": fp, "bpm": bpm, "camelot": cam, "energy": e,
            "bpm_confidence": 0.9, "key_confidence": 0.9, "canon_genre": g, "family": g}

def test_build_sets_groups_two_genres_and_orders():
    tracks = []
    # a compatible house cluster ~128 and a baile cluster ~172, weakly bridged
    for i in range(8):
        tracks.append(_mk(f"h{i}", 128 + (i % 3), "8A", 0.4 + 0.02 * i, "tech house"))
    for i in range(8):
        tracks.append(_mk(f"b{i}", 172 + (i % 3), "8A", 0.6 + 0.02 * i, "baile funk"))
    sets = build_sets(tracks, _cfg())
    assert len(sets) >= 2
    for s in sets:
        assert "name" in s and s["ordered"]
        # each community is internally near-connected -> <=1 hard cut
        assert len(s["hard"]) <= 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_emergent.py -v`
Expected: FAIL — `ModuleNotFoundError: walkman_dj.emergent`

- [ ] **Step 3: Write minimal implementation**

```python
# src/walkman_dj/emergent.py
"""Orchestrate emergent sets: whole-library graph -> communities -> ordered sets."""
from walkman_dj import libgraph, community, setname
from walkman_dj.sequence import order_family

_CLUB_BREAKS = {"baile_edits", "jersey_juke", "ukg_bass"}


def _template_for(tracks, cfg):
    from collections import Counter
    fam = Counter(t.get("family") for t in tracks).most_common(1)
    if fam and fam[0][0] in _CLUB_BREAKS:
        return "wave"
    return cfg.get("default_template", "single_peak")


def build_sets(tracks, cfg):
    """Return list of {name, ordered, hard, isolated, family} emergent sets."""
    by_fp = {t["fingerprint"]: t for t in tracks}
    G = libgraph.build_graph(tracks, cfg)
    comms, loose = community.detect(
        G, set_min=cfg["set_min"], set_max=cfg["set_max"],
        resolution=cfg["community_resolution"], seed=cfg["louvain_seed"])
    sets = []
    for c in comms:
        ct = [by_fp[fp] for fp in c if fp in by_fp]
        if len(ct) < 2:
            continue
        tmpl = _template_for(ct, cfg)
        ordered, hard, isolated = order_family(ct, cfg, template=tmpl)
        sets.append({"name": setname.name_set(ct), "ordered": ordered,
                     "hard": hard, "isolated": isolated, "template": tmpl,
                     "n": len(ordered)})
    if loose:
        lt = [by_fp[fp] for fp in loose if fp in by_fp]
        sets.append({"name": "Loose · Unsorted", "ordered": lt, "hard": [],
                     "isolated": list(loose), "template": None, "n": len(lt)})
    sets.sort(key=lambda s: -s["n"])
    return sets
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_emergent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/walkman_dj/emergent.py tests/test_emergent.py
git commit -m "feat(emergent): orchestrator graph->communities->ordered sets"
```

---

## Phase 4 — Tuning harness (the "fine-tune the algorithm" step)

### Task 6: `overnight/tune_sets.py` — sweep + metrics

**Files:**
- Create: `overnight/tune_sets.py`
- Test: `tests/test_tune_sets.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tune_sets.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "overnight"))
from tune_sets import score_config

def _sets():
    return [{"n": 40, "hard": [], "ordered": [1] * 40, "family": "x"},
            {"n": 80, "hard": [{"pos": 3}], "ordered": [1] * 80, "family": "y"},
            {"n": 5, "hard": [{"pos": 1}, {"pos": 2}], "ordered": [1] * 5, "family": "z"}]

def test_score_rewards_clean_in_range_sets():
    s = score_config(_sets(), set_min=8, set_max=120)
    assert s["n_sets"] == 3
    assert 0 <= s["pct_clean"] <= 1
    # two of three sets have <=1 hard cut
    assert abs(s["pct_clean"] - 2 / 3) < 1e-9
    assert s["p50_size"] in (40, 80, 5) or s["p50_size"] > 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_tune_sets.py -v`
Expected: FAIL — `ModuleNotFoundError: tune_sets`

- [ ] **Step 3: Write minimal implementation**

```python
# overnight/tune_sets.py
"""Fine-tune emergent-set formation: sweep (resolution, w_genre_bridge, bpm window),
report set-size distribution + %-of-sets with <=1 hard cut + genre purity, and
recommend the config that maximises clean in-range sets. Metrics, not eyeballing."""
import os, sys, statistics
sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/src")
from walkman_dj import seqcli, emergent, genre_store
import sqlite3

DB = "/Users/rishizamvar/Documents/Projects/walkman-dj/data/walkman_analysis.db"


def score_config(sets, set_min, set_max):
    real = [s for s in sets if s.get("name", "") != "Loose · Unsorted"] if sets and "name" in sets[0] else sets
    n = len(real)
    clean = sum(1 for s in real if len(s.get("hard", [])) <= 1)
    sizes = [s["n"] for s in real] or [0]
    in_range = sum(1 for z in sizes if set_min <= z <= set_max)
    sizes_sorted = sorted(sizes)
    return {"n_sets": n, "pct_clean": (clean / n) if n else 0.0,
            "p50_size": sizes_sorted[len(sizes_sorted) // 2],
            "p90_size": sizes_sorted[min(len(sizes_sorted) - 1, int(len(sizes_sorted) * 0.9))],
            "pct_in_range": (in_range / n) if n else 0.0}


def load_tracks(db=DB):
    fams = genre_store.get_all(db)
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    tr = []
    for r in con.execute("SELECT * FROM features WHERE status='ok'"):
        d = dict(r); fp = d["fingerprint"]
        g = fams.get(fp)
        if not g:
            continue
        d["family"] = g["family"]; d["canon_genre"] = g["canon_genre"]
        tr.append(d)
    con.close()
    return tr


def main():
    base = seqcli.load_cfg()
    tracks = load_tracks()
    print(f"tuning over {len(tracks)} tracks\n")
    print(f"{'res':>5} {'genreW':>7} {'winPct':>7} {'sets':>5} {'p50':>5} {'p90':>5} {'clean%':>7} {'inRange%':>9}")
    best = None
    for res in (0.8, 1.0, 1.3, 1.7):
        for gw in (0.3, 0.6, 1.0):
            cfg = dict(base, community_resolution=res, w_genre_bridge=gw,
                       set_min=base.get("set_min", 25), set_max=base.get("set_max", 120),
                       louvain_seed=base.get("louvain_seed", 1))
            sets = emergent.build_sets(tracks, cfg)
            sc = score_config(sets, cfg["set_min"], cfg["set_max"])
            print(f"{res:>5} {gw:>7} {base['bpm_window_pct']:>7} {sc['n_sets']:>5} "
                  f"{sc['p50_size']:>5} {sc['p90_size']:>5} {sc['pct_clean']*100:>6.0f}% {sc['pct_in_range']*100:>8.0f}%")
            key = (sc["pct_clean"], sc["pct_in_range"])
            if best is None or key > best[0]:
                best = (key, res, gw)
    print(f"\nRECOMMEND resolution={best[1]}  w_genre_bridge={best[2]}  "
          f"(clean={best[0][0]*100:.0f}% in-range={best[0][1]*100:.0f}%)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_tune_sets.py -v`
Expected: PASS

- [ ] **Step 5: Run the real sweep (manual) + set config**

Run: `python3 overnight/tune_sets.py`
Expected: prints the sweep table + a RECOMMEND line. Copy the recommended `community_resolution` / `w_genre_bridge` into `config/sequencer.toml` (add `set_min=25`, `set_max=120`, `louvain_seed=1`).

- [ ] **Step 6: Commit**

```bash
git add overnight/tune_sets.py tests/test_tune_sets.py config/sequencer.toml
git commit -m "feat(emergent): tuning sweep harness + tuned config"
```

---

## Phase 5 — Deploy + visualize

### Task 7: `overnight/deploy_emergent.py` — write emergent sets to device

**Files:**
- Create: `overnight/deploy_emergent.py`
- Test: `tests/test_deploy_emergent.py`

- [ ] **Step 1: Write the failing test** (pure m3u8 rendering + device-path resolution, no adb)

```python
# tests/test_deploy_emergent.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "overnight"))
from deploy_emergent import render_set_m3u8

def test_render_uses_device_paths_and_extinf():
    ordered = [{"path": "/storage/6439-6561/Music/A/01 - x.flac", "title": "x", "duration": 200},
               {"path": "/storage/6439-6561/Music/B/02 - y.mp3", "title": "y", "duration": 190}]
    out = render_set_m3u8(ordered, "/storage/6439-6561/Music")
    assert out.startswith("#EXTM3U")
    assert "#EXTINF:200,x" in out
    assert "A/01 - x.flac" in out and "/storage" not in out.split("\n", 2)[2]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_deploy_emergent.py -v`
Expected: FAIL — `ModuleNotFoundError: deploy_emergent`

- [ ] **Step 3: Write minimal implementation**

```python
# overnight/deploy_emergent.py
"""Deploy emergent community sets to the Walkman. Loads analyzed on-device tracks,
builds emergent sets, writes one ordered .m3u8 per set (device-relative paths),
backs up + removes the superseded fixed-genre crates. Reuses deploy_sets helpers."""
import os, sys, re, subprocess, datetime, sqlite3
sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/src")
sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/overnight")
from walkman_dj import seqcli, emergent, genre_store
import deploy_sets

DEV = "/storage/6439-6561/Music"
DB = "/Users/rishizamvar/Documents/Projects/walkman-dj/data/walkman_analysis.db"
BACKUP = os.path.expanduser(f"~/Desktop/walkman-emergent-backup-{datetime.date.today()}")
_safe = lambda s: re.sub(r'[/:]', "-", s).strip()


def render_set_m3u8(ordered, music_root):
    lines = ["#EXTM3U"]
    for t in ordered:
        lines.append(f"#EXTINF:{int(t.get('duration') or 0)},{t.get('title') or ''}")
        lines.append(os.path.relpath(t["path"], music_root))
    return "\n".join(lines) + "\n"


def load_ondevice_tracks():
    """Analyzed tracks that are on the device now, with family + path/title, matched
    by filename to the current device file set (path in DB may be stale)."""
    feat = deploy_sets.features_by_filename()          # normalized-filename -> feature row (has fingerprint)
    fams = genre_store.get_all(DB)
    devrel = _device_rel_by_name()
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    meta = {r["fingerprint"]: dict(r) for r in con.execute("SELECT * FROM tracks")}
    con.close()
    out = []
    for key, d in feat.items():
        fp = d["fingerprint"]
        g = fams.get(fp)
        if not g:
            continue
        cur = devrel.get(key)                          # current device rel-path by filename
        if not cur:
            continue                                   # analyzed but not currently on device
        t = dict(d)
        t["family"] = g["family"]; t["canon_genre"] = g["canon_genre"]
        t["path"] = f"{DEV}/{cur}"
        t["title"] = (meta.get(fp) or {}).get("title") or os.path.splitext(os.path.basename(cur))[0]
        out.append(t)
    return out


def _device_rel_by_name():
    r = subprocess.run(["adb", "shell", f"cd {DEV} && find . -type f "
                        r"\( -iname '*.flac' -o -iname '*.mp3' -o -iname '*.m4a' -o -iname '*.wav' \)"],
                       capture_output=True, text=True, timeout=180)
    out = {}
    for l in r.stdout.replace("\r", "").splitlines():
        p = l.strip()
        p = p[2:] if p.startswith("./") else p
        if p:
            out[deploy_sets._norm(os.path.splitext(os.path.basename(p))[0])] = p
    return out


def main(push=True):
    os.makedirs(BACKUP, exist_ok=True)
    for remote in deploy_sets.list_playlists():
        base = remote.rsplit("/", 1)[-1]
        subprocess.run(["adb", "pull", remote, os.path.join(BACKUP, base)], capture_output=True)
    print(f"backed up existing playlists -> {BACKUP}")
    cfg = seqcli.load_cfg()
    tracks = load_ondevice_tracks()
    print(f"{len(tracks)} on-device analyzed tracks")
    sets = emergent.build_sets(tracks, cfg)
    print(f"\n{'set':40} {'n':>4} {'hard':>5}  push")
    for s in sets:
        if s["n"] < 2:
            continue
        name = _safe(s["name"])
        m3u = render_set_m3u8(s["ordered"], DEV)
        local = f"/tmp/em_{deploy_sets._norm(name)}.m3u8"
        open(local, "w").write(m3u)
        pushed = ""
        if push:
            pr = subprocess.run(["adb", "push", local, f"{DEV}/{name}.m3u8"], capture_output=True, text=True)
            pushed = "pushed" if ("pushed" in pr.stdout + pr.stderr or "1 file" in pr.stdout + pr.stderr) else "?"
        print(f"{s['name'][:40]:40} {s['n']:>4} {len(s['hard']):>5}  {pushed}")
    # remove superseded fixed-genre crates (backed up above)
    if push:
        for nm in deploy_sets.GENRE_PLAYLISTS:
            subprocess.run(["adb", "shell", f'rm "{DEV}/{nm}.m3u8"'], capture_output=True)
    print("DEPLOY_EMERGENT DONE")


if __name__ == "__main__":
    main(push="--dry-run" not in sys.argv)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_deploy_emergent.py -v`
Expected: PASS

- [ ] **Step 5: Dry-run on the real library (manual, needs device)**

Run: `python3 overnight/deploy_emergent.py --dry-run`
Expected: prints each emergent set with n + hard-cut count; **verify the vast majority have hard ≤ 1** and sizes fall ~25–120. If not, re-tune (Task 6) before pushing.

- [ ] **Step 6: Commit**

```bash
git add overnight/deploy_emergent.py tests/test_deploy_emergent.py
git commit -m "feat(emergent): deploy emergent community sets to device"
```

---

### Task 8: Extend `graph_viz.py` for emergent sets + full-suite green

**Files:**
- Modify: `overnight/graph_viz.py`
- Test: full suite

- [ ] **Step 1: Add an emergent-sets entrypoint**

Add `graph_viz.py --emergent` that builds sets via `emergent.build_sets(deploy_emergent.load_ondevice_tracks(), cfg)` and renders one node/edge graph per set (reuse the existing `_svg` — it already takes an ordered track list + cfg). Output `~/Desktop/DJ Emergent Graphs.html`.

```python
# in overnight/graph_viz.py main(), branch on "--emergent" in sys.argv:
#   import deploy_emergent, emergent
#   sets = emergent.build_sets(deploy_emergent.load_ondevice_tracks(), seqcli.load_cfg())
#   secs = [_svg(s["name"], s["ordered"], cfg) for s in sets if s["n"] >= 2]
#   ...write ~/Desktop/DJ Emergent Graphs.html
```

- [ ] **Step 2: Run the whole suite**

Run: `python3 -m pytest -q`
Expected: all green (Set Sequencer + emergent). Reconcile any regressions.

- [ ] **Step 3: Render + open (manual)**

Run: `python3 overnight/graph_viz.py --emergent && open ~/Desktop/"DJ Emergent Graphs.html"`
Expected: one graph per emergent set; hard-cut counts visibly ≤1 for most.

- [ ] **Step 4: Commit**

```bash
git add overnight/graph_viz.py
git commit -m "feat(emergent): node/edge graphs for emergent sets"
```

---

## Acceptance

1. `tune_sets.py` reports a config where **≥90% of emergent sets have ≤1 hard cut** and p50 size ∈ [25,120].
2. `deploy_emergent.py --dry-run` shows the baile-equivalent set at **≤1 hard cut** (the original goal).
3. Ear test: load 3 emergent sets in Rekordbox — mixable pitch-fader-only start→finish.
4. Every analyzed on-device track lands in exactly one set or the explicit "Loose · Unsorted" bin (nothing silently dropped).

## Self-review notes (author)
- **Spec coverage:** libgraph (Tasks 1–2), community+size shaping (Task 3), naming (Task 4), orchestrator/ordering (Task 5), tuning sweep (Task 6), deploy (Task 7), viz (Task 8). Covered.
- **Reuse:** `graph.camelot_edge_ok`/`smoothness_cost`, `genre.jaccard`, `sequence.order_family`, `family.rescale_energy`, `genre_store`, `deploy_sets` helpers. Only new algorithmic code is candidate-gen + Louvain wrapper + naming + sweep.
- **Type consistency:** `build_graph(tracks,cfg)->nx.Graph`; `detect(G,set_min,set_max,resolution,seed)->(list[set],set)`; `build_sets(tracks,cfg)->list[{name,ordered,hard,isolated,template,n}]`. Consistent across tasks.
- **Deferred (called out, not silent):** cross-run set stability under library growth (Louvain seeded but re-cluster reshuffles); revisit if sets need to be frozen.
