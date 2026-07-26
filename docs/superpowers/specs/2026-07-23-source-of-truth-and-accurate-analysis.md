# One Source of Truth + Accurate Analysis Engine

**Goal:** (Phase 0) Consolidate all music from Walkman, USB, and computer into a single deduped source of truth at `~/Desktop/MusicLake`. (Phase 1) Replace the librosa analysis engine with a SOTA downbeat-anchored, octave-correct stack so BPM and beat grids are accurate enough for CDJ/Rekordbox export.

**Order:** Phase 0 runs first and completes before Phase 1. The new analyzer only ever runs against the consolidated lake.

---

## Current-state inventory (2026-07-23)

| Location | Audio | Notes |
|---|---|---|
| Walkman `/storage/6439-6561/Music` | 5,606 | most complete set |
| USB `/Volumes/Lexar/Contents` | 2,995 | known-good Rekordbox export |
| `~/Desktop/MusicLake` | 2,413 | current lake, 1,438 artist dirs, `_lake-manifest.json`, all real files (0 symlinks) |
| `~/Desktop/music` | 663 | soulseek downloads, folder-per-album |
| `~/Music` | 37 | Music.app strays |
| **total instances** | **11,714** | dedupes to est. ~5,500–6,000 unique |

Analysis DB `data/walkman_analysis.db` already holds 6,578 content-fingerprints (`features` table keyed on `fingerprint`).

Excluded from consolidation: `~/nas_zeecloud` (53 GB — DJ mix videos + old Desktop archive, not library tracks).

## Locked decisions

1. **Dedup policy:** keep **highest quality only** per song (flac > 320k mp3 > lower bitrate > other). Losers go to macOS Trash (recoverable), never `rm`.
2. **Device-only tracks:** Walkman-only and USB-only songs are **copied into MusicLake now**, so the lake is the complete superset immediately.
3. **Dedup key:** content fingerprint (`fingerprint.py`, SHA1 of first 30 s @22050) **AND** normalized `(artist, title)` tag match — collapses cross-format dupes the raw fingerprint misses.
4. **Analysis engine:** Beat This! (downbeat phase) + DeepRhythm (octave-correct BPM) + linear-regression constant-tempo snap. No madmom.

---

# Phase 0 — Consolidate to MusicLake

## 0.1 Fingerprint + tag index of every source
For each of the 5 locations, walk audio files and build a row: `(location, path, fingerprint, artist, title, format, bitrate, quality_rank)`.
- Reuse `src/walkman_dj/fingerprint.py::fingerprint_file`.
- Reuse existing DB fingerprints where the file is already analyzed (skip re-decode) — join on path where possible, else compute.
- `quality_rank`: flac/wav/aiff = 3; m4a/aac/mp3 ≥ 320k = 2; mp3 < 320k / ogg = 1.
- Normalized `(artist, title)`: lowercase, strip feat./remix parens tags consistently, collapse whitespace/punctuation (reuse the normalization used by `deploy_sets._norm` / genre canon where sensible).
- Write the index to `~/Desktop/MusicLake/_consolidation-index.json`.

## 0.2 Dedup resolution
Group rows by dedup key (fingerprint OR normalized artist+title). Within each group pick the **winner** = max `quality_rank`, tie-broken by (a) already in MusicLake, (b) largest file size. Everything else is a **loser**.
- Emit `_consolidation-plan.json`: winners (keep/copy target path in lake) + losers (trash) + device-only-to-pull.
- **Dry-run print first**: N unique songs, N dupes to trash, N device-only to pull, total bytes moving. Human-review gate before any file operation.

## 0.3 Fold winners into MusicLake
- Winners already in the lake: leave in place.
- Winners in `~/Desktop/music` / `~/Music`: move into `MusicLake/<AlbumArtist|Artist>/` (tag-based foldering, mirror existing lake convention). Cross-device safe copy (`shutil.copyfile` + `os.remove`; SMB/removable reject `copystat`).
- Winners that are **device-only** (Walkman/USB): pull the file (`adb pull` for Walkman, `cp` for USB) into `MusicLake/<Artist>/`.
- Losers: `trashItem` semantics via macOS Trash (use `osascript`/`send2trash`), never `rm -f`. Refuse to trash anything outside the 5 known roots.

## 0.4 Regenerate manifest
- Rewrite `_lake-manifest.json`: one row per unique song with `path, fingerprint, artist, title, album, format, source_origin (which locations it came from), quality_rank`.
- This manifest is the master catalog. USB + Walkman are rebuilt *from* the lake in a later milestone (out of scope here).

## 0.5 Verify
- Assert: every unique dedup key present in exactly one lake file.
- Assert: no audio file lost (winners_kept + losers_trashed == total_instances after fingerprint).
- Print final unique count + reclaimed bytes.

---

# Phase 1 — Accurate analysis engine

## 1.1 Environment
Fresh venv `~/.venvs/grids`: `torch` (MPS wheel), `beat-this`, `deeprhythm`, `soxr`, `einops`, `rotary-embedding-torch`; `ffmpeg` present. Isolated from the main librosa env — the analyzer shells out to it, or the pipeline imports it when this venv is active. No madmom, no numpy pin.

## 1.2 New beat/BPM module `src/walkman_dj/beatgrid.py`
- `detect_beats(path) -> {beats: [t...], downbeats: [t...]}` via `beat_this.inference.File2Beats(device="mps")`.
- `robust_bpm(path) -> float` via DeepRhythm.
- `constant_grid(beats, downbeats, bpm_hint) -> {bpm, anchor, residual}`:
  - Least-squares fit `beat_time ≈ a·index + b` → `grid_bpm = 60/a`.
  - If `grid_bpm` ≈ 2× or 0.5× `bpm_hint` (DeepRhythm), snap to DeepRhythm's octave.
  - `anchor` = first downbeat time (the grid's beat-1 phase).
  - `residual` = RMS of (beat_time − regressed grid) → real confidence, replaces the fake `1.0`.

## 1.3 Wire into `analyze.py`
- Replace `bpm_of` with the beatgrid stack; keep key/energy/structure as-is.
- Output adds `beat_grid_bpm`, `beat_anchor` (seconds), `first_downbeat`, `grid_residual`; keep `bpm` = `beat_grid_bpm`.

## 1.4 DB migration
- `ALTER TABLE features ADD COLUMN beat_anchor REAL, first_downbeat REAL, grid_residual REAL, beat_grid_bpm REAL`.
- Backfill by re-running the new analyzer over the consolidated lake.

## 1.5 Fix the CDJ/Rekordbox export
- Beat grid anchor (`Inizio`) = `beat_anchor` (the real downbeat), **not** `mix_in`.
- Regenerate `~/Desktop/walkman-cdj-test.xml` from the new fields; re-verify in Rekordbox that grid + downbeat land on the actual kick.

## 1.6 Validate accuracy
- Sample 15 tracks spanning genres; open in Rekordbox; confirm beat-1 sits on a downbeat and BPM is octave-correct on four-on-the-floor.
- Report before/after BPM deltas (flag every track whose octave changed).

---

## Risks
- **Fingerprint isn't robust across lossy re-encodes** → that's why dedup also uses `(artist, title)`; fingerprint catches exact/near dupes, tags catch cross-format.
- **Trashing the wrong file** → dry-run gate + Trash-not-rm + root allowlist.
- **beat-this MPS quirks** → fall back to `device="cpu"` (model is tiny; still overnight-fast).
- **Pulling 3k+ device files is slow** → acceptable per locked decision; incremental + resumable, skip fingerprints already in lake.
