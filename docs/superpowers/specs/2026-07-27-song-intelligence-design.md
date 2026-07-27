# Song Intelligence — Design Spec

**Date:** 2026-07-27
**Status:** Approved (design), pending implementation plan
**Repo:** walkman-dj

## Goal

Give every song in MusicLake one unified, normalized metadata record — combining our own MIR analysis, Spotify-parity audio features (computed via Essentia), and Spotify catalog metadata — so all songs are directly comparable ("apples-to-apples"), and make new songs get analyzed automatically on ingest.

Three user requirements this satisfies:
1. **Gather all metadata Spotify has** — catalog fields from Spotify's live API + the audio-feature "vibe numbers" computed ourselves (Spotify deprecated `/audio-features` and `/audio-analysis` on 2024-11-27 for apps without grandfathered access; ours gets 403).
2. **Normalize all metadata apples-to-apples** — one schema, consistent scales (bounded features as 0-1 floats), one source of truth.
3. **Auto-analyze on ingest** — `crate ingest` enqueues; a background worker analyzes.

## Non-goals

- Not building a UI. This is data + pipeline.
- Not re-deriving Spotify's exact model outputs bit-for-bit — we target close, internally-consistent equivalents.
- Not touching the crate dedup/organize/export behavior beyond adding the async enqueue hook.

## Key decisions (locked during brainstorming)

- **Scope:** full parity — compute audio-features ourselves AND pull Spotify catalog metadata.
- **Engine:** Essentia (Spotify-grade pretrained models) for the features we don't already compute.
- **Ingest mode:** async queue — ingest stays fast; a worker daemon does the heavy analysis.
- **Store:** unify into `library.db` (single source of truth); migrate + retire `walkman_analysis.db`.

## Architecture

Four independently-testable units + a one-time backfill.

```
crate ingest ──[fast: fingerprint + dedupe + file]──> enqueue fingerprint in pending_analysis
                                                              │
crate analyzer (daemon) ──drain queue──> analyze_song(path) + spotify_match(artist,title)
                                                              │
                                                     write unified recordings row
                                                     (delete from pending_analysis)
```

### Unit 1 — Unified normalized schema (`library.db`)

Extend the existing `recordings` table (keyed by `fingerprint`, already the identity). All bounded features are **0-1 floats**; tempo is real BPM (SOTA-corrected); key stored in **both** Camelot and Spotify pitch-class/mode. Add a `pending_analysis` queue table.

**`recordings` columns (existing + new):**

Identity / catalog:
- existing: `id, canonical_path, artist, title, album, fmt, duration, dur_bucket, fp, akey`
- new: `isrc TEXT, spotify_id TEXT, spotify_popularity INTEGER, release_date TEXT, label TEXT, artist_genres TEXT` (json array), `spotify_match_confidence REAL`

Our MIR (migrate from walkman_analysis.db features):
- `bpm REAL, bpm_confidence REAL, key TEXT, camelot TEXT, key_confidence REAL, energy REAL, brightness REAL, bass_weight REAL, mir_danceability REAL, is_vocal INTEGER, mix_in REAL, mix_out REAL, beat_anchor REAL, first_downbeat REAL, beat_engine TEXT`
- key/mode (Spotify style): `spotify_key INTEGER` (0-11 pitch class), `spotify_mode INTEGER` (0=minor,1=major)

Essentia Spotify-parity (new):
- `valence REAL, acousticness REAL, instrumentalness REAL, speechiness REAL, liveness REAL, loudness_db REAL, essentia_danceability REAL`

Provenance:
- `analysis_version TEXT, analyzed_at TEXT`

**`pending_analysis` table (the queue):**
- `fingerprint TEXT PRIMARY KEY, path TEXT, enqueued_at TEXT, attempts INTEGER DEFAULT 0`

**Structure segments** stay in a `segments` table (migrated from analysis DB), keyed by fingerprint.

**Migration:** a one-time `crate migrate-analysis` step copies existing analysis (bpm/key/energy/etc.) from `walkman_analysis.db` into `library.db.recordings` by matching on normalized `(artist,title)` at high confidence, so ~7,355 already-analyzed tracks don't need re-running for the MIR fields (only the new Essentia + Spotify fields). Device-phantom rows in the analysis DB are ignored (path not under MusicLake).

### Unit 2 — `analyze_song(path)` engine

Pure function, `src/walkman_dj/intelligence.py`: audio file → normalized feature dict. Composes:
- SOTA beatgrid (`beatgrid.py`, existing) → bpm, beat_anchor, first_downbeat, beat_engine
- librosa (`analyze.py`, existing) → key/camelot, energy, brightness, bass_weight, mir_danceability, is_vocal, mix points, structure segments
- **Essentia (new)** → valence, acousticness, instrumentalness, speechiness, liveness, loudness_db, essentia_danceability

All outputs normalized to the schema's scales. Carries `ANALYSIS_VERSION` constant; bump it when the engine changes so stale rows can be detected/re-run. Deterministic (same file → same values). Never raises on a bad file — returns `{"status":"failed"}`.

### Unit 3 — Spotify catalog matcher

`src/walkman_dj/spotify_meta.py`, reuses the `spotify-playlists` skill's OAuth token (independent app token). Per song:
- `GET /v1/search?type=track&q=artist:"X" track:"Y"` → candidates
- Score by fuzzy (artist,title) + duration proximity; require confidence ≥ threshold (else leave catalog fields null — never store a wrong match)
- On match: fetch `spotify_id, popularity, isrc (external_ids), release_date (album), label` and artist genres via `/artists`
- Rate-limited (Spotify ~180 req/min); cached by spotify_id; batch-friendly. Access-token only (never rotates the skill's refresh token — learned rule).

### Unit 4 — Async ingest pipeline + worker

- **Hook:** `crate ingest` (in `crate.py`), after a successful add/replace, inserts the fingerprint into `pending_analysis`. Ingest speed unchanged.
- **Worker:** `crate analyzer` (in `crate_cli.py`) — a loop that drains `pending_analysis`: for each, run `analyze_song` + `spotify_match`, write the unified row, delete from queue. `--once` (drain and exit) or `--daemon` (poll forever). Increments `attempts`; after N failures, marks failed and moves on. Idempotent: re-analyzing overwrites the row cleanly.
- **Backfill:** the same worker seeds `pending_analysis` with all 7,170 existing songs (minus those with current `analysis_version`) and drains — the one-time full analysis. Essentia ≈ 15s/track → several hours; runs detached, disk-cheap (writes are KB).

## Normalization guarantees (goal #2)

Documented in `docs/song-intelligence-SCHEMA.md`:
- Every bounded feature ∈ [0,1]; `loudness_db` in dBFS; `bpm` real BPM (SOTA-corrected, no octave errors); key in Camelot + Spotify pitch-class/mode; `analysis_version` stamped on every row.
- One row per unique recording (fingerprint). Two songs compare directly on any field.

## Error handling

- `analyze_song` never raises — returns `{"status":"failed"}`; worker records the failure, bumps attempts, continues.
- Spotify matcher: on 429, back off and retry; on no confident match, leave catalog fields null (don't guess).
- Worker survives crashes: queue is durable (a table), so restart resumes. Backfill is resumable.
- Missing Essentia install → engine degrades gracefully (fills what it can, marks essentia fields null) rather than blocking.

## Testing

- **Unit 1 (schema):** migration test — seed a fake analysis DB, run migrate, assert MIR fields land in recordings by (artist,title); pending_analysis enqueue/drain roundtrip.
- **Unit 2 (engine):** golden-value tests on 2-3 known lake tracks — bpm within ±1 of known, key matches, all features in [0,1], deterministic (two runs equal), corrupt-file returns failed.
- **Unit 3 (matcher):** mocked Spotify search JSON — correct track matches high-confidence; wrong-artist candidate is rejected (confidence gate); 429 backoff.
- **Unit 4 (worker):** enqueue 2 fingerprints → `analyzer --once` → both rows written + queue empty; idempotent second run; failed track increments attempts.
- **Integration:** `crate ingest <file>` → row appears in pending_analysis → `analyzer --once` → unified row populated.

## Implementation phasing

1. **Schema + migration** — extend recordings, add pending_analysis + segments, `crate migrate-analysis`, `crate enrich` retire. (Unblocks everything.)
2. **Engine** — `intelligence.py` with Essentia; `crate analyze <file>` prints the dict; golden tests.
3. **Spotify matcher** — `spotify_meta.py`; `crate spotify-match <query>`; mocked tests.
4. **Async worker + ingest hook** — enqueue on ingest; `crate analyzer --once/--daemon`; integration test.
5. **Backfill** — seed all 7,170; drain detached; verify coverage.

## Files

| File | Responsibility |
|---|---|
| `src/walkman_dj/db.py` (extend) or `crate.py` schema | new columns + pending_analysis + segments tables + migration |
| `src/walkman_dj/intelligence.py` (create) | `analyze_song(path)` engine + `ANALYSIS_VERSION` |
| `src/walkman_dj/essentia_features.py` (create) | Essentia model loading + the 7 new features (isolated so Essentia is one import site) |
| `src/walkman_dj/spotify_meta.py` (create) | Spotify catalog matcher |
| `src/walkman_dj/crate_cli.py` (extend) | `analyze`, `analyzer`, `spotify-match`, `migrate-analysis` commands |
| `src/walkman_dj/crate.py` (extend) | ingest → enqueue hook |
| `docs/song-intelligence-SCHEMA.md` (create) | the normalized field dictionary |
| `tests/test_intelligence.py`, `tests/test_spotify_meta.py`, `tests/test_analyzer.py` (create) | per-unit tests |

## Definition of Done

1. `library.db.recordings` holds the full normalized schema; `walkman_analysis.db` migrated + retired.
2. `analyze_song` produces all fields (MIR + Essentia) normalized + versioned; golden tests pass.
3. Spotify catalog fields populated for confidently-matched songs; wrong matches never stored.
4. `crate ingest` enqueues; `crate analyzer` drains and writes unified rows; all wired with passing integration test.
5. All 7,170 existing songs backfilled (or marked failed) — every song comparable apples-to-apples.
