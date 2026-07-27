# Song Intelligence — Normalized Field Dictionary

Every song in `library.db.recordings` has one row keyed by acoustic fingerprint (`fp`).
All bounded features are **0-1 floats**, tempo is real BPM, loudness is dBFS — so any two
songs compare apples-to-apples. `analysis_version` stamps the engine/schema version.

## Identity & catalog

| Field | Type / range | Source | Meaning |
|---|---|---|---|
| `id` | int | — | row id |
| `fp` | json int[] | Chromaprint | acoustic fingerprint (song identity) |
| `canonical_path` | text | crate | file location in MusicLake |
| `artist`, `title`, `album` | text | tags | cleaned tag metadata |
| `akey` | text | derived | normalized `artist title` (dedup/match key) |
| `fmt` | text | file | flac / mp3 / m4a / wav |
| `duration` | seconds | file | track length |
| `isrc` | text | Spotify | international recording code |
| `spotify_id` | text | Spotify | matched track id |
| `spotify_popularity` | int 0-100 | Spotify | popularity score |
| `release_date` | text | Spotify | album release date |
| `label` | text | Spotify | record label |
| `artist_genres` | json str[] | Spotify | genres from the artist |
| `spotify_match_confidence` | 0-1 | matcher | match confidence (≥0.7 to store) |

## Our MIR (librosa + SOTA beatgrid)

| Field | Range/units | Meaning |
|---|---|---|
| `bpm` | real BPM | SOTA-corrected tempo (Beat This!+DeepRhythm; no octave errors) |
| `bpm_confidence` | 0-1 | tempo confidence |
| `key` | pitch name | musical key (C, C#, …) |
| `camelot` | e.g. `8A` | Camelot wheel key (for harmonic mixing) |
| `key_confidence` | 0-1 | key estimate confidence |
| `spotify_key` | 0-11 | pitch class (Spotify convention) |
| `spotify_mode` | 0/1 | 0=minor, 1=major |
| `energy` | 0-1 | perceived intensity |
| `brightness` | 0-1 | spectral centroid (tonal brightness) |
| `bass_weight` | 0-1 | low-frequency energy share |
| `mir_danceability` | 0-1 | librosa danceability (onset regularity) |
| `is_vocal` | 0/1 | vocal-forward vs instrumental (librosa hpss) |
| `mix_in`, `mix_out` | seconds | usable DJ mix points |
| `beat_anchor`, `first_downbeat` | seconds | beat-grid anchors |
| `beat_engine` | text | `beatthis+deeprhythm` / `deeprhythm-only` / `librosa-fallback` |

## Essentia Spotify-parity (approximations, normalized)

Essentia-derived equivalents of Spotify's proprietary audio features (Spotify never
published exact formulas; these are internally consistent + normalized).

| Field | Range | Meaning |
|---|---|---|
| `valence` | 0-1 | musical positivity/mood |
| `acousticness` | 0-1 | acoustic vs electronic |
| `instrumentalness` | 0-1 | likelihood of no vocals |
| `speechiness` | 0-1 | spoken-word presence |
| `liveness` | 0-1 | live-recording likelihood |
| `essentia_danceability` | 0-1 | Essentia danceability (cross-check vs `mir_danceability`) |
| `loudness_db` | dBFS | integrated loudness (real dB, negative) |

## Provenance

| Field | Meaning |
|---|---|
| `analysis_version` | engine/schema version (`si-1.0`); bump forces re-analysis |
| `analyzed_at` | ISO timestamp of last analysis |

## Structure segments (`segments` table)

Per song, keyed by `fingerprint`: `idx, label, start, end` where label ∈
`intro / build / drop / breakdown / outro`.

## Pipeline

`crate ingest` → enqueues fingerprint in `pending_analysis` →
`crate analyzer` drains → `analyze_song` (MIR+Essentia) + `spotify_meta.match` →
writes this row. `crate backfill` seeds all un-analyzed songs. `crate migrate-analysis`
reuses existing MIR from the legacy `walkman_analysis.db`.
