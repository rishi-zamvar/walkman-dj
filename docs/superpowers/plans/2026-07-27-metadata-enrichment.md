# Comprehensive Metadata Enrichment — Plan

**Goal:** For every track, get the richest + most accurate metadata by *identifying* it and
looking it up in the best databases (not guessing from audio), with per-field source priority
and provenance, then surface insights.

**Ponytail stance:** multi-source in *coverage*, lean in *code*. One module (`enrich.py`) holds
identify + per-source lookups + merge. Reuse what exists: Chromaprint fp (already stored),
`fpcalc` (installed), `genre_map` (normaliser), Rekordbox import (keys), Essentia (vibe + audio
fallback), the existing `isrc/label/release_date/genre*` columns.

**Core principle — accuracy ranking.** Look it up, don't guess. Audio classification is the LAST
resort (only for edits/bootlegs not in any DB).

---

## Sources (what each is best at)

| Source | Auth | Best for | Notes |
|---|---|---|---|
| **AcoustID** | free key | *identify* track → MusicBrainz ID from our fingerprint | we already have fps |
| **MusicBrainz** | none (User-Agent) | canonical artist/title, ISRC, release date, label, genre | 1 req/s |
| **Discogs** | free token | **electronic genre + granular STYLE**, label, year, catalog# | richest for this crate |
| **Last.fm** | free key | crowd genre/mood tags (tiebreaker + mood insight) | |
| **Spotify** | have creds | popularity (429-limited → low priority) | already wired |
| **Rekordbox** | — | key, BPM, beatgrid | already built |
| **Essentia** | — | vibe features + audio genre FALLBACK | already built |

## Field resolution priority (first hit wins)
- **genre / subgenre:** Discogs style → MusicBrainz genre → Last.fm top tag → embedded tag → Essentia audio (confidence-floored). All normalised through `genre_map`.
- **label / release_date / ISRC:** MusicBrainz → Discogs → Spotify.
- **year / catalog# / country / mood_tags:** Discogs / MusicBrainz / Last.fm (new columns).
- **key / bpm / first_downbeat:** **Rekordbox is the sole source of truth** — enrich NEVER writes these
  (Discogs/MB/Spotify BPM+key are ignored). Only `crate import-rekordbox` touches them; our MIR is the
  pre-import placeholder.
- **popularity:** Spotify.
- **valence/energy/etc.:** Essentia.

Every written field records its source (provenance) so "genre from Discogs" is distinguishable
from "genre from audio" — that's what makes the insights trustworthy.

---

## Task 0: keys + schema

**Files:** `src/walkman_dj/schema.py`, `src/walkman_dj/keys.py` (new, tiny), `tests/test_schema.py`

- [ ] Add columns to `NEW_COLUMNS`: `("year","INTEGER"), ("catalog_no","TEXT"), ("country","TEXT"),
  ("mood_tags","TEXT"), ("mbid","TEXT"), ("provenance","TEXT")`. `provenance` = JSON `{field: source}`.
- [ ] `keys.py`: `get(name)` reads env var first, else `~/.config/keys/projects.toml`. Returns None if absent.
  Names: `ACOUSTID_KEY`, `DISCOGS_TOKEN`, `LASTFM_KEY`.
- [ ] Test: ensure_schema idempotent adds `mbid`, `provenance`; `keys.get` returns None cleanly when unset.

**User action (one-time, free):** register for [AcoustID](https://acoustid.org/new-application),
[Discogs token](https://www.discogs.com/settings/developers), [Last.fm key](https://www.last.fm/api/account/create);
drop them in `~/.config/keys/projects.toml`. A missing key just skips that source.

## Task 1: identify — fingerprint → MusicBrainz ID

**Files:** `src/walkman_dj/enrich.py` (new), `tests/test_enrich.py`

- [ ] `_fpcalc(path) -> (fingerprint_str, duration)`: run `fpcalc -json path`, parse. (Re-run rather than
  re-encode the stored int array — `fpcalc` is installed and it's the exact string AcoustID wants.)
- [ ] `identify(path, artist, title) -> {mbid, artist, title, ...}`: AcoustID lookup
  (`api.acoustid.org/v2/lookup`, meta=`recordings+releasegroups+compress`) → best MBID by score.
  Fallback when no key/no match: MusicBrainz recording search by `artist:"" AND recording:""`, pick best by duration.
- [ ] Test: mock `_fpcalc` + urlopen, assert `identify` returns the top-scored MBID; fallback path returns a search hit.

## Task 2: per-source lookups

**Files:** `enrich.py`, `tests/test_enrich.py`

Each returns a flat dict of only the fields it knows (missing = absent), never raises:
- [ ] `mb(mbid) -> {genre, label, release_date, isrc, country, artist, title}` — MB recording+release
  (`inc=genres+tags+artist-credits+releases+isrcs`). genre = top-voted tag.
- [ ] `discogs(artist, title) -> {genre, style, label, year, catalog_no}` — Discogs `/database/search`
  type=release; style = first Discogs style, genre from parent. (Best signal for this crate.)
- [ ] `lastfm(artist, title) -> {tags: [...]}` — `track.getTopTags`; keep tags over a weight cutoff.
- [ ] Tests: mock each endpoint's JSON, assert the dict shape + that a failed request → `{}`.

## Task 3: merge (accuracy-ranked) + normalise

**Files:** `enrich.py`, `tests/test_enrich.py`

- [ ] `merge(sources: dict, existing_row) -> (updates, provenance)`: apply the field-priority table.
  genre/subgenre normalised via `genre_map.normalize` / `map_discogs`; record which source won each field.
  **`bpm`, `camelot`, `key`, `first_downbeat`, `beat_engine` are on a hard denylist — merge never emits them**
  (Rekordbox owns those). Never overwrite a tag-sourced genre with a weaker source.
- [ ] Test: given Discogs style="UK Garage" + MB genre="electronic" + audio="House", `merge` picks
  UK Garage with provenance genre=discogs; catalog fields come from MB.

## Task 4: `crate enrich`

**Files:** `src/walkman_dj/crate_cli.py`, `enrich.py`

- [ ] `enrich_one(row) -> updates`: identify → fan out to mb/discogs/lastfm (skip sources w/o key) → merge → return.
- [ ] On-disk cache table `enrich_cache(key TEXT PRIMARY KEY, json TEXT)` keyed by source+query so re-runs and
  retries cost nothing and we stay polite. `# ponytail: shared sqlite cache, fine until concurrent writers`.
- [ ] Rate-limit per source (MB 1/s, Discogs ~1/s, AcoustID 3/s) via a per-source last-call timestamp + sleep.
- [ ] `cmd_enrich(args)`: `crate enrich [--limit N] [--only-unlabeled] [--refresh]`. Default targets rows where
  `genre='Other'/NULL` or `label/isrc/release_date` missing. Progress line every 25.
- [ ] Test: monkeypatch identify + sources, run `enrich` on a temp DB, assert row gets genre + label + provenance.

## Task 5: `crate insights`

**Files:** `crate_cli.py`, `src/walkman_dj/insights.py` (new)

- [ ] `report(db) -> str`: genre distribution, subgenre top-N, decade/year histogram, top labels,
  BPM bands × Camelot heat, vibe cluster centroids (reuse `vibe_path.matrix`), and a coverage/provenance
  summary (how much of each field is DB-sourced vs audio-guessed). Plain text + write `data/insights.txt`.
- [ ] `crate insights` prints it. Test: on a tiny seeded DB, assert the report contains the genre counts.

---

## Definition of Done
- `crate enrich` fills genre/label/release_date/ISRC/year for the ~1,436 "Other" + untagged rows from real
  databases, with per-field provenance.
- Genre for released tracks comes from Discogs/MB (accurate), audio only as marked fallback.
- `crate insights` shows a rich, trustworthy breakdown (genre, era, label, key/BPM, vibe) with source coverage.
- All new logic behind mocked tests; live path degrades gracefully when a key is missing.

## Explicitly skipped (YAGNI unless asked)
- Beatport/Traxsource (no official free API — unofficial scraping is fragile). Add if Discogs coverage falls short.
- One Tagger integration — our AcoustID+MB+Discogs path covers the same sources natively; use One Tagger's GUI
  ad-hoc if you want manual review.
- Writing tags back into the audio files — we enrich `library.db` only. Add a `crate writetags` later if devices need it.
