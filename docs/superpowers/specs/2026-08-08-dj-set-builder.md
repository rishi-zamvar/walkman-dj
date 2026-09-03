# DJ Set Builder — design outline

Turn the library into **proper, DJ-mixable ~50-track sets**: per-genre buckets →
metadata-rich transition graph (subgenre-aware) → vibe clusters → smooth paths.
Every set flows with **0 hard jumps** and is sized toward **50 tracks** (curate down,
back-fill up). Output = device-native `.m3u8` per set + local `.md` sheet.

## Data
`data/library.db recordings`, orderable rows (`bpm` + `camelot` present). Features used:
`genre, subgenre, bpm, bpm_confidence, camelot, key_confidence, energy, valence,
brightness, bass_weight, mir_danceability, acousticness, is_vocal, duration, canonical_path`.

## Pipeline

### 1. Genre buckets
Group every orderable track by **main genre** (Trap, Hip Hop, R&B, House, UK Garage,
Drum & Bass, …). Drop junk buckets (`Other`, `Alternative`). Each bucket is processed
independently — no cross-genre edges (genre-pure sets).

### 2. Transitionability graph  (nodes = tracks, edges = "can you mix A→B smoothly")
For every candidate pair, add an edge **iff** it clears the two hard gates, weighted by a
blend of soft factors (weight ∈ [0,1], higher = smoother):

- **Key gate (harmonic):** `camelot_edge_ok(a,b)` — same code, ±1 on the wheel, or
  relative major/minor. No edge otherwise. Soft term scaled by `key_confidence`.
- **BPM gate (halftime-aware):** effective Δ = `min(|a−b|, |a−2b|, |2a−b|)` so a 70 and a
  140 track count as mixable. Must sit inside `bpm_window` (genre-tuned). Soft = `1 − Δ/window`.
- **Energy:** `1 − |Δenergy|` — smooth energy moves.
- **Valence (mood):** `1 − |Δvalence|`.
- **Subgenre affinity:** `1.0` same subgenre · `0.6` same subgenre-**family** (curated map,
  e.g. {Rage, Melodic Trap, Atlanta Trap} ⊂ trap-family) · `0.3` otherwise. ← factors subgenre.
- **Timbre (where present):** brightness / bass_weight / danceability proximity (secondary).

`weight = Σ wᵢ·featureᵢ`, weights genre-tuned (below).

### 3. Vibe clusters  (community detection)
Louvain (`community.detect`) on the weighted graph, resolution tuned so communities land
near the **target set size (~50)**; oversize communities are split, undersize merged into
their best-connected neighbor. Because subgenre + energy + valence are baked into the edge
weight, communities come out **vibe- and subgenre-coherent** on their own.

### 4. Paths through clusters  (the set)
Each cluster → `order_family()` (penalty-completed TSP-path + energy template) = a spun
sequence with a real energy arc. `order_clean()` guarantees **0 hard jumps** by parking any
track that can't harmonically bridge.

### 5. Target 50  (curate down / back-fill up)
- Clustering already aims ~50 (`set_min≈38, set_max≈62`).
- **Short set** (< ~45 after parking) → **add back** the highest-edge-weight tracks from the
  loose bin / adjacent clusters until ~50 — only tracks that mix cleanly (never a hard cut).
- **Over ~62** → community detection already split it.

### 6. Naming
`<Genre> - <dominant subgenre>` from the cluster's modal subgenre (`Trap - Rage`,
`House - Deep Rollers`, `UK Garage - 2-step`). Collisions get an energy tag (Peak / Dark /
Sunset / Late-Night from mean energy·valence).

## Per-genre tuning  (recorded to `config/dj_genre_tuning.toml`)
| family | bpm_window | key weight | energy template | notes |
|---|---|---|---|---|
| House / Tech / Deep House | tight (5%) | high | single_peak | harmonic mixing critical, 4/4 build |
| Techno | very tight | high | monotonic | driving, key-locked |
| UK Garage / 2-step | 6% | high | wave | swing, harmonic |
| Drum & Bass / Jungle | 5% @170 | high | single_peak | tight tempo, high energy |
| Trap / Hip Hop / Drill | loose (halftime) | low | wave | BPM ambiguous, energy-led |
| R&B / Soul | loose | low | single_peak (gentle) | mood-led, key soft |
| Afrobeats / Amapiano / Baile / Latin | 6% | med | wave | groove-led |

## Output & reproducibility
- Device `.m3u8` (real on-device paths, 0 hard jumps) pushed per set; local `.md` sheets in
  `~/Desktop/DJ Sets/`.
- Whole pipeline packaged as reproducible **skills** (`walkman-dj-sets` + genre-validation),
  re-runnable when the library changes.
