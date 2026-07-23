# Emergent Set Formation — Whole-Library Community Graph (design)

Evolution of the Set Sequencer. Instead of ordering fixed, capped genre playlists,
build ONE transition graph over the entire analyzed library and let **DJ sets emerge
as communities** in that graph — then order within each. Motivated by an empirical
finding: the capped 70-track "Baile Funk" playlist ordered to 12–19 hard cuts, but
the *full* 89-track baile pool ordered to **1 hard cut** (median node degree 44).
The cuts were an input-sparsity artifact of the caps, not an ordering failure.

Locked decisions (2026-07-22):
1. **Grouping = community detection** (Louvain on the transition graph, resolution-tuned).
2. **Genre = soft-weighted edges** — cross-genre pairs carry a Jaccard-based penalty, so
   communities emerge genre-coherent but a killer cross-genre pivot can still bridge.
3. **Set size targeted ~40–120** — tune resolution, split oversize communities, park
   singletons in a "Loose" bin.

```
all analyzed tracks ─► libgraph (BPM-bucketed, genre-weighted affinity edges)
                       ─► community (Louvain + size post-process) ─► named communities
                       ─► emergent (order_family + template per community) ─► ordered sets
                       ─► deploy_emergent (device m3u8) + tune_sets (eval/sweep) + graph_viz
```

## Why community detection over fixed genres
A capped playlist is a sparse subgraph → forced hard cuts. The full library graph is
dense (median degree 44 in baile). Communities are the naturally-connected dense
regions — sets where almost every track has a smooth neighbor. Ordering a community
therefore yields ≤1 hard cut *by construction* (the only cut is bridging internal
sub-bands, e.g. a double-time tail).

## Components

### `libgraph.py` — whole-library sparse graph
- Nodes = analyzed on-device tracks (`status='ok'` + a `track_genre` family).
- **Candidate generation is BPM-bucketed** (never all-pairs): tracks sorted by BPM;
  for each track, candidates come from windowed lookups around its BPM *and* its
  half/double equivalents (2×, ½×), via bisect on the sorted BPM array. This turns
  12M naive pairs into ~n·(local neighbourhood).
- Edge iff `camelot_edge_ok` (with reliability handling) AND `bpm_distance ≤ window`.
- Edge **weight = affinity** (Louvain wants high=strong), `affinity = 1/(1+cost)` where
  `cost = smoothness_cost + w_genre_bridge·(1 − genre_jaccard)`. Same-genre pairs →
  cost lower → affinity higher → they clump; cross-genre pairs weaker but present.
- Returns a `networkx.Graph` with node attrs (family, bpm, energy, camelot).

### `community.py` — Louvain + size shaping
- `louvain_communities(G, weight='weight', resolution=r, seed=s)` (deterministic via seed).
- **Split oversize** (> `set_max`): recurse Louvain on the induced subgraph at
  `resolution·1.6` until ≤ `set_max` or no further split.
- **Park tiny** (< `set_min`): merge each into the community holding its highest-affinity
  neighbour; unbridgeable leftovers → a single "Loose / Unsorted" community.
- Returns `list[set[node]]`.

### `setname.py` — factual set names
Name = nice genre label (dominant family, via the existing GENRE map) + median BPM band
+ energy descriptor. e.g. `Baile Funk · 132 · Peak`, `Deep House · 124 · Warm`.

### `emergent.py` — orchestrator
Load tracks(+family) → `libgraph.build` → `community.detect` → per community:
`sequence.order_family` with a template chosen by the community (wave for club-breaks,
single_peak default) → `{name, ordered, hard, isolated}`.

### `overnight/tune_sets.py` — the "fine-tune the algorithm" harness
Sweep `(resolution, w_genre_bridge, bpm_window_pct)`; for each config report: #sets,
size p50/p90, **% of sets with ≤1 hard cut**, total isolated, genre-purity per set
(dominant-family share). Recommends the config maximizing ≤1-cut sets subject to size
in [set_min, set_max]. This is where "fine-tuning" happens, measured not eyeballed.

### `overnight/deploy_emergent.py` — device deploy
Backup, then write each ordered community to a device `.m3u8` (derived name), count-safe.
Replaces the 18 fixed-genre crates (backed up); the 30 vibe/cultural/discovery lists
are untouched. Fully reversible from the backup.

## Efficiency & determinism
- Graph build target: < ~30s on the M-series for ~5k nodes via BPM bucketing (Phase 0
  spike benchmarks it; fall back to numpy vectorization if needed).
- Louvain seeded → deterministic runs. Cross-run *stability* under library growth is
  explicitly deferred (quality first); re-cluster is a periodic batch op, sets aren't
  frozen yet. Noted as future work.

## Reuse (no rewrites)
`graph.camelot_edge_ok` / `smoothness_cost` / `HARD_CUT`, `genre.jaccard`/`canon`,
`sequence.order_family` / templates, `family.rescale_energy`, `genre_store` (family +
canon per track), the GENRE name map from `overnight/rebuild.py`. Only genuinely new
code: candidate-gen graph build, Louvain wrapper + size shaping, naming, eval sweep.

## Non-goals
Frozen cross-run set stability; re-analysis (features already done); the vibe/cultural
/discovery multi-family playlists (kept as-is). Phase 5 of the base sequencer
(mix_in/out overlap) still deferred.
