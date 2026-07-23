# Set Sequencer — Genre-Aware DJ Ordering (revised design)

Revised from the original *CrateDig Set Sequencer* draft after critical review.
Ordering engine that consumes the existing `walkman_analysis.db` MIR feature
table and emits genre-pure, energy-templated, harmonically-mixed playlists as
Walkman `.m3u8` + an annotated `.md` transition sheet.

Four decisions locked with the user (2026-07-22):

1. **Clustering demoted.** Order directly over the genre-*family* graph. No
   HDBSCAN load-bearing partition; optional segmentation only for very large
   families (naming/QA), never as a required stage.
2. **Configurable energy templates.** Ordering targets a curve (single-peak,
   multi-peak "peaks and valleys", plateau-then-drop), not a monotonic climb.
3. **Genre = canon + Jaccard only.** Tier-0 regex canonicalization + Tier-1
   token Jaccard. No MiniLM/PCA embeddings.
4. **Stability is a hard requirement.** Adding tracks nightly must not reshuffle
   existing playlists. Absolute feature scales + freeze-and-reorder + a stability
   acceptance test.

```
features table ─► family assign ─► per-family transition graph ─► template-guided TSP-path ─► ordered .m3u8 + .md
 (walkman_analysis.db)  (canon genre        (Camelot gate + BPM             (greedy-NN → 2-opt →
                         + audio sanity)     window, penalty-completed)      Held-Karp small n)
```

---

## 0. What actually exists (grounding, not assumptions)

The original draft assumed a greenfield `sequencer/` package and a `genre`
column. Neither is true. Build **on** what's here:

| Reality | Source | Consequence for design |
|---|---|---|
| `features` table has **no `genre` column** | `pragma table_info(features)` | Genre is computed on the fly in `overnight/rebuild.py`. **Must persist it** — new `track_genre` table keyed on fingerprint (canon string + family + source + confidence). |
| `src/walkman_dj/camelot.py` (`compatible()`) already exists | repo | Extend, don't replace. Add the ±1/relative-major rule table + reliability handling here. |
| `src/walkman_dj/sequence.py` already does greedy-NN + hype/soul/journey arcs | repo | This **is** Stage 3 Mode A v0. Evolve it: add penalty-completion, 2-opt, Held-Karp, energy templates. |
| `is_vocal` over-fires (~92–98% of tracks read vocal) | `sequence.py` comment | **Drop `is_vocal` from the vibe/feel signal** (draft weighted it 1.0 — wrong). |
| RMS `energy` compressed to ~0.47–0.72 library-wide | `sequence.py` comment | **Per-family min–max / percentile rescale** of energy before templating, else arcs are flat. |
| 4,679 / 4,834 tracks `status='ok'` (analyzed); 42 pending, 28 failed | db | Phase 0 nearly done. Analyze the ~155 stragglers + any new downloads, then gate. |
| `numpy, sklearn, networkx` installed; `hdbscan` **not** | env | Demoting clustering means **zero new deps**. Keep it that way. |

---

## 1. Feature inputs

Per analyzed track (`status='ok'`), from `features` + `segments`:

| Field | Used in | Notes |
|---|---|---|
| `bpm`, `bpm_confidence` | graph | genre-prior corrected; half/double fold at edge time |
| `camelot`, `key_confidence`, `key_agree` | graph | harmonic gate + reliability penalty |
| `energy` | family + template | **rescaled per-family** (§3.1) — raw range is only ~0.25 wide |
| `bass_weight`, `brightness`, `danceability` | family sanity-check | timbre/feel; more reliable than `is_vocal` here |
| `loudness_curve` (16-pt) | template scoring (opt) | shape only; drop brittle single-argmax peak |
| `duration`, fingerprint | bookkeeping | dedup before anything |
| `is_vocal` | **excluded** | detector over-fires; not a usable discriminator |
| `mix_in`, `mix_out`, `segments` | Phase 5 | transition *timing*, not ordering |

Precondition: excluded, not zero-filled. `status != 'ok'` tracks never enter the
graph (zero-fill would collapse them into a fake pocket).

---

## 2. Family assignment (replaces Stage 1 clustering)

### 2.1 Genre canonicalization — Tier 0 (regex)

Config-owned alias map, applied after lowercase+strip. Collapses the noise from
album/artist rules + online lookup. Lives in `genre.py` (already present).

```python
CANON = [
  (r"d ?& ?b|dnb|drum ?(and|n) ?bass", "drum and bass"),
  (r"hip ?-?hop|hiphop|rap",           "hip-hop"),
  (r"funk carioca|brazilian funk|baile","baile funk"),
  (r"r ?& ?b|rnb",                     "rnb"),
  (r"uk ?garage|ukg|2 ?-?step",        "uk garage"),
  (r"amapiano",                        "amapiano"),
  (r"dembow",                          "dembow"),
  # ...config-owned, extend by ear
]
```

### 2.2 Family map (config)

Canon genre → family. Families are the ordering scope (house and rap never
compete for one arc):

```
club_4x4:    [deep house, tech house, techno, afro house, amapiano, organic house]
halftime:    [drum and bass, jungle]
club_breaks: [jersey club, baile funk, uk garage, ghetto house, dembow]
rap_rnb:     [hip-hop, rnb, trap]
latin:       [salsa, bachata]
# ...
```

### 2.3 Audio sanity-check (the structural override you asked for)

The string label is unreliable per-track ("not all Drake songs are rap"). So each
family has an **audio envelope** (BPM band + rescaled-energy band + bass/brightness
centroid), learned from the confidently-labeled members. A track whose label puts
it in family F but whose features sit far outside F's envelope and squarely inside
family G's is **re-filed to G** and flagged `family_source='audio_override'`.
Confidently-labeled + in-envelope → `label`. This is where structural genre wins
over the string, exactly the philosophy the user insisted on.

### 2.4 Persistence + stability

New `track_genre` table: `fingerprint, canon_genre, family, family_source,
confidence, assigned_at`. **Frozen once written.** Re-runs only assign *new*
fingerprints; existing rows are never recomputed unless `--reclassify` is passed.
This is stability requirement half #1 (the other half is §3.5).

---

## 3. Transition graph + ordering

### 3.1 Per-family energy rescale (makes templates real)

Within each family, map `energy` to a percentile rank (or min–max) across that
family's members → `energy_n ∈ [0,1]` spanning the full range. All template and
arc math uses `energy_n`, never raw `energy`. Without this the ~0.47–0.72 raw
band makes every arc nearly flat.

### 3.2 Harmonic gate (Camelot)

Edge allowed iff same code, ±1 same letter (wrap 12→1), or relative major/minor
(same number, letter swap). `key_agree==False` or `key_confidence<0.55`: don't
hard-block — allow with `w_unreliable_key` penalty. Optional `allow_energy_boost`
permits +2 same-letter with a penalty. (Extends existing `camelot.compatible`.)

### 3.3 BPM window + half/double fold

```
bpm_distance(a,b) = min(|a - x| for x in (b, 2b, b/2))   # 150 trap over 75 groove ≠ 75 apart
edge iff camelot_ok AND bpm_distance <= max(0.06*min(a,b), 4)
low bpm_confidence (<0.6): widen window 25% + add w_unreliable_bpm
```

### 3.4 Penalty-completed graph (the correctness fix)

The compatible-transition graph is **sparse**, so a Hamiltonian path frequently
does not exist and 2-opt/Held-Karp are undefined on it. Fix: order over the
**complete** graph where a non-compatible pair carries a large fixed
`HARD_CUT` penalty. Objective is **lexicographic**: minimize number of HARD_CUT
edges first, then total soft cost. `HARD_CUT >> Σ(soft costs)` enforces the
ordering. Residual HARD_CUT edges are surfaced as `## Hard transition` in output;
truly edgeless tracks go to `## No clean transition` (doubles as a BPM-prior QA
surface).

Soft edge cost (lower = smoother), all terms O(1):

```
cost(a→b) = bpm_distance/window
          + w_energy_jump * |energy_n_a - energy_n_b|
          + template_term(a,b)          # §3.5
          + w_unreliable_key/bpm (if flagged)
```

### 3.5 Energy templates (replaces monotonic arc)

A template is a target curve `T: position∈[0,1] → energy_n∈[0,1]`:

- `single_peak` — warm-up → peak → cool (classic)
- `wave` — multi-peak "peaks and valleys" (the baile ask)
- `plateau_drop` — sustained high, late drop
- `monotonic` — the old climb, kept as one option

Placement penalty: `template_term = w_template * |energy_n(track) − T(position)|`.
Ordering minimizes total deviation from the chosen curve. This directly encodes
build-up **and** build-down. Template is a per-playlist config choice.

### 3.6 Ordering algorithm (evolve existing `sequence.py`)

1. **Seed**: track nearest to `T(0)`.
2. **Greedy nearest-neighbor** on the penalty-completed graph (existing loop,
   now cost-minimizing with HARD_CUT).
3. **2-opt**: reverse segments while total lexicographic cost drops; cap 200
   passes / 2s per family.
4. **Exact**: `n ≤ 12` → Held-Karp DP (penalty-completed → always defined).
5. **Mode B (anchor routing)**: plain Dijkstra "opener → peak" bridge, for the
   sparse graph (non-edges excluded, not penalized) — the one place shortest-path
   is the right tool.

### 3.7 Stability, half #2

Re-ordering an existing playlist after N new tracks arrive: keep the frozen order
of prior members, insert new tracks at their lowest-cost gap (local, not global
re-solve), unless `--resequence` forces a full re-order. Acceptance test in §5.

---

## 4. Module layout (extend, don't cathedral)

```
src/walkman_dj/
  camelot.py       # EXISTS — add full rule table + reliability
  genre.py         # EXISTS — add CANON map + family map + Jaccard + audio-override
  sequence.py      # EXISTS — add penalty-completion, 2-opt, held_karp, templates
  distance.py      # NEW — bpm_distance, edge cost (pure)
  templates.py     # NEW — T curves + template_term (pure)
  family.py        # NEW — envelope learning + assignment + persistence
  graph.py         # NEW — build penalty-completed graph (networkx)
  seqcli.py        # NEW — CLI: --family / --full-set / --from A --to B / --template
config/sequencer.toml   # all weights, windows, family map, CANON
```

`numpy, networkx` only. No hdbscan, no sentence-transformers. Pure domain
(`camelot, distance, genre, templates`) is I/O-free and fully unit-tested.

---

## 5. Testing & acceptance

Pure-domain unit tests (no audio):

- `camelot.py`: full 24-key / 576-pair compatibility table.
- `distance.py`: half/double fold (174 vs 87 → 0), window floor, confidence widening.
- `genre.py`: canonicalization table ("Drum & Bass"/"dnb"/"drum n bass" → one canon),
  idempotence, Jaccard ("deep house"/"tech house" = 1/3, disjoint = 0).
- `templates.py`: each curve's endpoints/peaks; `wave` has ≥2 local maxima.
- `sequence.py`: Held-Karp vs greedy+2-opt on random penalty-completed graphs
  n≤12, assert `exact ≤ heuristic ≤ 1.3×exact`; HARD_CUT count is minimized first.
- **Stability test**: order a fixture family, add 10 tracks, re-run in
  freeze-and-insert mode, assert prior-member relative order unchanged and total
  reorder churn < 10%.
- Snapshot test on a sanitized library fixture.

Ear acceptance (one evening):

1. 3 known families order mixable start-to-finish in Rekordbox, pitch-fader only.
2. `wave` template on a baile family visibly rises **and falls** in the waveform overview.
3. < 10% of tracks land in `## No clean transition`.

---

## 6. Build phases

- **Phase 0 — Finish feature extraction** on the 42 pending + 28 failed + any new
  downloads; gate on `status='ok'`. (Nearly done: 4,679/4,834.)
- **Phase 1 — Pure domain + tests**: `camelot` (rule table), `distance`,
  `genre` (canon+Jaccard), `templates`. One sitting, TDD.
- **Phase 2 — Family assignment**: envelope learning + audio override +
  `track_genre` persistence (frozen). Eyeball families against crate intuition.
- **Phase 3 — Graph + template-guided ordering**: penalty-completed graph,
  evolve `sequence.py` (2-opt, Held-Karp, templates), `seqcli.py` → `.m3u8` +
  annotated `.md`. Wire into the overnight rebuild path.
- **Phase 4 — Stitching + Mode B + stability**: cross-family macro-arc assembly
  (ordered by family median `energy_n`), Dijkstra anchor routing, freeze-and-insert.
- **Phase 5 — Later**: `mix_in`/`mix_out` + `loudness_curve` tails for real
  overlap compatibility and segment-aware transition suggestions.

Prereq for all: Phase 0 complete.
```
```
