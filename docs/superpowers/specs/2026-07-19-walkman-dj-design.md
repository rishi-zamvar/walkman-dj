# Walkman DJ Crate — Design Spec

**Date:** 2026-07-19
**Status:** Approved (design), pending implementation plan
**Device:** Sony NW-A300 Walkman, SD card `/storage/6439-6561/Music` (~71 GB, ~3,265 audio files) reached over **adb** (USB debugging enabled — `adb devices` → `1022482`).

## 1. Goal

Turn a flat, unorganized 3,265-track library into a set of **micro-genre vibe playlists**, each **sequenced like a DJ set** (energy arc + harmonic mixing), and produce a **rich, reusable MIR dataset** stored on the Mac so playlists can be regenerated without re-analyzing. The full build runs **autonomously overnight**.

Non-goals: on-device analysis, streaming services, transcoding, editing/moving the user's audio files (only `.m3u8` playlists are written to the device).

## 2. Architecture

Standalone Python project (`~/Documents/Projects/walkman-dj`, git, TDD). A **checkpointed, resumable pipeline**:

```
device (adb)  →  batch pull  →  MIR analysis  →  SQLite crate-DB (Mac)
                                                      │
                          classify (rules+audio) ─────┤
                                                      ▼
                              micro-genre vibe sets + BPM-band crates
                                                      │
                              harmonic + arc sequencing
                                                      ▼
                              .m3u8  →  push to Walkman  +  REPORT.md  +  ZeeChat ping
```

Modules (`src/walkman_dj/`), each single-purpose, independently testable:
- `device.py` — adb wrapper: list, batch-pull, push, retry-with-backoff, watchdog.
- `fingerprint.py` — content fingerprint (audio hash) for stable identity across renames/re-encodes.
- `analyze.py` — MIR feature extraction (BPM, key, energy, structure, timbre).
- `db.py` — SQLite schema + incremental upsert; the crate-DB is the single source of truth.
- `classify.py` — rules (artist/folder/keyword) + audio-assist + micro-genre carving.
- `sequence.py` — arc assignment + harmonic/energy transition scoring + ordering.
- `playlist.py` — `.m3u8` generation with device-relative paths.
- `report.py` — REPORT.md stats.
- `notify.py` — ZeeChat completion/blocker ping.
- `pipeline.py` — orchestrator: checkpoint, resume, batch loop, error isolation.

Config (`config/genre_rules.toml`): artist→bucket map, micro-genre rosters, per-genre BPM ranges, arc-type weights.

## 3. Analysis (per track)

Decode full track to **22.05 kHz mono** (standard MIR; fast, near-full accuracy for BPM/key).

| Feature | Method | Stored |
|---|---|---|
| **BPM** | librosa beat-track → **genre-anchored** half/double correction (fold into the track's genre-typical range) | `bpm`, `bpm_raw`, `bpm_confidence` |
| **Key → Camelot** | **dual profile** (Krumhansl–Schmuckler + Temperley) on chroma; map to Camelot | `key`, `camelot`, `key_confidence`, `key_agree` (bool) |
| **Energy + loudness curve** | RMS envelope over time (windowed) → overall energy + normalized curve | `energy`, `loudness_curve` (JSON array) |
| **Structure / mix points** | novelty-based segmentation → intro/breakdown/drop/outro; first & last stable downbeat | `segments` (rows), `mix_in`, `mix_out` |
| **Timbre + vocal** | spectral centroid (brightness), low-band ratio (bass), HPSS/vocal heuristic, danceability proxy | `brightness`, `bass_weight`, `is_vocal`, `danceability` |

Undecodable/too-short files are recorded with `status='failed'` and skipped from playlists (counted in report).

## 4. Storage — SQLite (Mac only)

`~/Documents/Projects/walkman-dj/data/walkman_analysis.db`

- **Identity:** `fingerprint` (content hash) is primary; `path`, `filename`, tags stored alongside and updated when a fingerprint reappears at a new path.
- **Tables:** `tracks` (identity + path + tags + status), `features` (1:1 with tracks), `segments` (N:1), `playlists`, `playlist_tracks` (ordered, with per-transition score), `runs` (run metadata/versioning).
- **Incremental:** a track already present (by fingerprint) with `status='ok'` is skipped on re-run. Playlists regenerate purely from the DB — **no audio needed** to re-sequence.

## 5. Classification — rules + audio-assist, deep micro-genre splits

1. **Album folders → artist buckets** deterministically (e.g. all Future albums → Atlanta/Trap family).
2. **Singles** classified by artist/keyword rules; ambiguous ones resolved by audio features (BPM/energy/timbre).
3. **Micro-genre carving** of large families using roster + audio features — e.g. the rap family splits into:
   - **Opium / Rage** — Carti (WLR/Die Lit/MUSIC), Ken Carson, Destroy Lonely, Homixide, rage-era Uzi (distorted 808s, high energy)
   - **Melodic / Emo trap** — Juice WRLD, melodic Uzi, Trippie, melodic Future
   - **Plugg / Cloud / Underground** — softer, hazy
   - **Atlanta / Hard trap** — Future, Thug/YSL, Gunna, Metro, 21
   - splits happen **within** an artist by audio features (a rage cut ≠ a melodic cut).

Electronic "Singles" split into the earlier families (Tech House, Organic/Afro/Melodic House, Nu-Disco/Funky, UKG/Bass, Global/Downtempo, Jersey/Juke, Electro/Classics, Baile Edits, Brazilian Funk). R&B/Afrobeats families likewise.

**Output:** ~25–35 vibe sets (natural set length ~30–70) **+ auto BPM-band crates** per genre family. Any set >~80 auto-chapters (Vol.1/2…). **Duplicates across sets allowed** (a track lives in every set it fits).

The full track→playlist assignment is written to `data/assignment.csv` for inspection.

## 6. Sequencing — arc + harmonic mixing

- **Arc auto-assigned** per playlist from its tracks' measured median energy/BPM:
  - **Hype-sustain** — front-loaded, high energy held, **peak finish**.
  - **Soul** — **seduction → late climax → afterglow** outro.
  - **Journey** — build → peak → hold (neutral middle).
- **Transition score** between adjacent tracks = weighted sum of `arc_fit + bpm_gap + key_compat (+ vocal_clash penalty)`; **weights differ per arc type** (hype leans BPM/energy, soul leans key/mood).
- **Harmonic rule:** Camelot **±1 + relative** major/minor are "compatible."
- **Ordering:** greedy nearest-neighbor seeded by the arc's target start, with a local-search improvement pass; ties broken by the arc shape. Endings: hype → peak track last; soul → softest/afterglow track last.

## 7. Execution (overnight)

- **Batched streaming:** pull ~150 tracks → analyze on ~9 workers (11 cores − 2) → delete temp → next batch. Peak disk ~5 GB (vs 35 GB free).
- **Resilience:** per-track DB commit on success; **auto-resume** (skip fingerprints already `ok`); adb calls retried with exponential backoff; a watchdog re-establishes the device if it drops.
- **TDD:** known-answer fixtures (a handful of tracks with hand-verified BPM/key) gate the analyzers before the full run; unit tests for db upsert, camelot mapping, arc scoring, m3u8 formatting.
- **Deliverables at completion:**
  - `.m3u8` sets pushed to the Walkman `/Music/` (device-relative paths the NW-A300 resolves).
  - `REPORT.md` — tracks analyzed/failed, BPM/key/energy distributions, per-playlist counts + assigned arc.
  - **ZeeChat ping** to the phone on completion or on a hard blocker.

## 8. Success criteria

- ≥ 95% of decodable tracks analyzed with BPM + Camelot + energy.
- Every vibe set is non-empty, arc-assigned, and harmonically ordered (no key jump outside the compatible set for > ~15% of transitions, given library constraints).
- Re-running the sequencer from the DB regenerates all playlists in seconds with no audio access.
- Playlists appear natively on the Walkman under Playlists.

## 9. Risks / mitigations

- **librosa key accuracy is imperfect** → dual-profile + confidence + `key_agree` flag; sequencing treats low-confidence keys as wildcards.
- **Half/double BPM errors** → genre-anchored correction.
- **adb flakiness overnight** → retry/backoff/watchdog + resume.
- **Micro-genre miscategorization** → `assignment.csv` is inspectable; rules are data-driven and re-runnable from the DB.
