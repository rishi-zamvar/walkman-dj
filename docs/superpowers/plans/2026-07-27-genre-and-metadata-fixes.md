# Genre Labeling + Metadata Bug Fixes — Implementation Plan

> **For agentic workers:** TDD, task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Give every song a correct, remix-aware genre; fix the Spotify popularity/label bugs and the analyzer's hang-under-load; recalibrate acousticness and upgrade valence to a trained model.

**Architecture:** Genre = normalized embedded tag (primary, 75% coverage) + Discogs-400 audio classifier (fallback + remix override, since it hears the actual audio). Both mapped into one controlled vocabulary. Vibe features move from crude spectral proxies toward trained Essentia mood models where a model exists on disk.

**Tech Stack:** Python, Essentia (bundled TF backend), Discogs-EffNet + genre_discogs400 + mood_happy models (already at `~/Library/Mobile Documents/_internal/models/`), mutagen, SQLite.

**Decisions (locked with user):**
- Remixes: **audio classifier wins** over embedded tag on disagreement.
- Audio scope: **genre + valence** (mood_happy) upgrade; acousticness recalibrated by formula.
- **Include label** (fetched from `/albums/{id}`, cached per album).

**Model paths:** `MODELS = ~/Library/Mobile Documents/_internal/models`
- `discogs-effnet-bs64-1.pb` (embedding, output `PartitionedCall:1`)
- `genre_discogs400-discogs-effnet-1.pb` (input `serving_default_model_Placeholder`, output `PartitionedCall:0`) + `.json` (`classes`, 400 labels `Parent---Style`)
- `mood_happy-discogs-effnet-1.pb` (2D head on the embedding; index 0 = happy prob)

---

## Task 0: Schema — genre columns

**Files:** Modify `src/walkman_dj/schema.py` (NEW_COLUMNS), Test `tests/test_schema.py`

- [ ] Add to `NEW_COLUMNS`: `("genre","TEXT")`, `("subgenre","TEXT")`, `("genre_source","TEXT")`, `("genre_confidence","REAL")`.
- [ ] Extend `test_ensure_schema_adds_columns_idempotently` expected set with `genre`, `genre_source`.
- [ ] Run `pytest tests/test_schema.py -q` → PASS.

## Task 1: `genre_map.py` — controlled vocabulary + normalizers

**Files:** Create `src/walkman_dj/genre_map.py`, Test `tests/test_genre_map.py`

- `VOCAB`: ordered canonical set (Hip-Hop, Trap, R&B, Pop, Soul/Funk, Afrobeats, Amapiano, Reggaeton, Latin, Dancehall, Reggae, House, Tech House, Deep House, Bass House, UK Garage, Jersey Club, Baile Funk, Drum & Bass, Jungle, Dubstep, Techno, Trance, Electro, Breakbeat, Disco, Electronic, Rock, Indie, Jazz, Other).
- `GENERIC = {"Electronic","Dance","World","Other"}` — refine-via-audio buckets.
- `normalize(raw: str) -> str|None`: lowercase, strip, collapse separators; map via `_RAW` table (the 5 rap spellings → Hip-Hop, "contemporary r&b"/"r & b" → R&B, "drum & bass"/"dnb" → Drum & Bass, "uk garage"/"garage"/"ukg" → UK Garage, etc.). Unknown → None.
- `map_discogs(label: str) -> tuple[str, str]`: `"Electronic---Jersey Club"` → `("Jersey Club","Jersey Club")`; fold via a `_DISCOGS` table on the style, falling back to parent (`Electronic`→Electronic, `Hip Hop`→Hip-Hop). Returns `(canonical, subgenre)`.

- [ ] **Step 1: failing test** `tests/test_genre_map.py`:
```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import genre_map as gm

def test_normalize_collapses_rap_spellings():
    for s in ["hip hop","rap","rap/hip hop","hip-hop/rap","Hip-Hop"]:
        assert gm.normalize(s) == "Hip-Hop"

def test_normalize_known_genres():
    assert gm.normalize("contemporary r&b") == "R&B"
    assert gm.normalize("drum & bass") == "Drum & Bass"
    assert gm.normalize("uk garage") == "UK Garage"
    assert gm.normalize("tech house") == "Tech House"

def test_normalize_unknown_returns_none():
    assert gm.normalize("zzznotagenre") is None

def test_map_discogs_style_and_parent():
    assert gm.map_discogs("Electronic---Jersey Club") == ("Jersey Club", "Jersey Club")
    g, sub = gm.map_discogs("Hip Hop---Trap")
    assert g == "Trap" and sub == "Trap"
    g, _ = gm.map_discogs("Electronic---Some Unmapped Style")
    assert g == "Electronic"

def test_generic_set():
    assert "Electronic" in gm.GENERIC and "Hip-Hop" not in gm.GENERIC
```
- [ ] **Step 2:** run → FAIL (module missing).
- [ ] **Step 3:** implement `genre_map.py` to pass.
- [ ] **Step 4:** `pytest tests/test_genre_map.py -q` → PASS.
- [ ] **Step 5:** commit.

## Task 2: `genre_audio.py` — Discogs-400 audio classifier

**Files:** Create `src/walkman_dj/genre_audio.py`, Test `tests/test_genre_audio.py`

- `available() -> bool`: models dir + essentia present.
- `predict(path) -> dict`: `{"subgenre","genre","genre_confidence"}` via EffNet embedding → genre_discogs400 head → argmax → `genre_map.map_discogs(label)`. Averages the per-frame predictions. Models cached at module level (loaded once). Returns `{}` on any failure or if unavailable.
- Also expose `embedding(path)` so `intelligence`/valence can reuse it.

- [ ] **Step 1: failing test**:
```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import genre_audio as ga

def test_predict_bad_file_empty():
    assert ga.predict("/nonexistent.flac") == {}

def test_predict_real_track():
    if not ga.available():
        import pytest; pytest.skip("models/essentia missing")
    # reuse the flac finder pattern
    for dp,_,fns in os.walk(os.path.expanduser("~/Documents/MusicLake")):
        if "_dedup" in dp: continue
        for f in fns:
            if f.endswith(".flac"):
                out = ga.predict(os.path.join(dp,f))
                if out:
                    assert out["genre"] and 0.0 <= out["genre_confidence"] <= 1.0
                return
```
- [ ] **Step 2:** run → the bad-file test drives creation.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** `pytest tests/test_genre_audio.py -q` → PASS (real-track may skip if models absent in CI; runs locally).
- [ ] **Step 5:** commit.

## Task 3: `genre.py` — decision logic (remix override)

**Files:** Create `src/walkman_dj/genre.py`, Test `tests/test_genre.py`

- `is_remix(name: str) -> bool`: regex `remix|edit|bootleg|flip|vip|mashup|refix|rework|dub`.
- `decide(tag_genre, audio, remix) -> dict`: implements the ladder (audio wins on remix ≥0.15; else specific tag; else audio ≥0.10; else generic tag; else "Other"). Returns `{genre, subgenre, genre_source, genre_confidence}`.
- `label_track(path, tags, audio) -> dict`: glue — `normalize(tags.genre)`, `is_remix(title/basename)`, call `decide`.

- [ ] **Step 1: failing test**:
```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import genre as G

AUDIO = {"genre":"Jersey Club","subgenre":"Jersey Club","genre_confidence":0.4}

def test_remix_prefers_audio_over_tag():
    r = G.decide("Pop", AUDIO, remix=True)
    assert r["genre"] == "Jersey Club" and r["genre_source"] == "audio"

def test_specific_tag_wins_when_not_remix():
    r = G.decide("Hip-Hop", AUDIO, remix=False)
    assert r["genre"] == "Hip-Hop" and r["genre_source"] == "tag"

def test_generic_tag_refined_by_audio():
    r = G.decide("Electronic", AUDIO, remix=False)
    assert r["genre"] == "Jersey Club" and r["genre_source"] == "audio"

def test_no_tag_low_audio_falls_back():
    r = G.decide(None, {"genre":"House","genre_confidence":0.02}, remix=False)
    assert r["genre"] in ("House","Other")

def test_is_remix_detection():
    assert G.is_remix("Song (Salute Remix)") and not G.is_remix("Just A Song")
```
- [ ] **Step 2–4:** implement, tests PASS.
- [ ] **Step 5:** commit.

## Task 4: Wire genre into the analyzer

**Files:** Modify `src/walkman_dj/crate_cli.py` (`cmd_analyzer`), Modify `src/walkman_dj/intelligence.py` (call genre in full path), Test `tests/test_analyzer.py`

- In `cmd_analyzer`, after features computed and before write: read tags via mutagen, `audio = genre_audio.predict(path)`, `g = genre.label_track(path, tags, audio)`, `r.update(g)`. Guard in try/except (never fail the row on genre).
- Ensure `genre, subgenre, genre_source, genre_confidence` are in `_FEAT` (auto, since NEW_COLUMNS).
- [ ] Extend `test_analyzer_drains_queue_and_writes_row` to monkeypatch `genre_audio.predict` and assert the written row has a `genre`.
- [ ] `pytest tests/test_analyzer.py -q` → PASS.
- [ ] Commit.

## Task 5: Fix Spotify — timeouts, popularity, label

**Files:** Modify `src/walkman_dj/spotify_meta.py`, Test `tests/test_spotify_meta.py`

- Add `_TIMEOUT = 8` and pass `timeout=_TIMEOUT` to every `urlopen`; wrap in try/except → `{}`/`[]` so a hang or 429 never stalls the analyzer.
- Verify/store `spotify_popularity` from the track object.
- Add `_album_label(album_id)` → GET `/albums/{id}`, return `.label`, cached in a module dict; call it in `match` when `spotify_id` resolved.
- [ ] Extend tests: mock `_album` to return `{"label":"Opium"}`, assert `match(...)["label"] == "Opium"`; assert a `_search` raising is swallowed → `match` returns `{}`.
- [ ] `pytest tests/test_spotify_meta.py -q` → PASS.
- [ ] Commit.

## Task 6: Recalibrate acousticness + trained valence

**Files:** Modify `src/walkman_dj/essentia_features.py`, Test `tests/test_intelligence.py`

- Replace acousticness with a spread mapping: combine spectral flatness (low=tonal), centroid, and rolloff so bright/electronic → low, sparse/acoustic → high; validate distribution spreads (not pinned >0.6).
- Add optional `valence_from_embedding(emb)` using `mood_happy` head (index 0 prob); `intelligence.analyze_song` / analyzer prefers it when embedding available, else keeps proxy.
- [ ] Add test: on a bright electronic flac, `extract(p)["acousticness"] < 0.5` (was pinned high).
- [ ] `pytest tests/test_intelligence.py -q` → PASS.
- [ ] Commit.

## Task 7: Backfill

**Files:** Modify `src/walkman_dj/crate_cli.py` (add `backfill-genre` re-enqueue that includes already-analyzed rows)

- `backfill-genre`: enqueue every recording whose `genre IS NULL` (covers the 699 already done). Reuse the analyzer daemon.
- [ ] Run: `crate migrate-analysis` is untouched; `crate backfill-genre` then `crate analyzer --daemon`.
- [ ] Sanity after ~200 rows: `SELECT genre, COUNT(*) ... GROUP BY genre` shows a real spread; `genre_source` mix of tag/audio; remixes show `audio`.

---

## Definition of Done
- `SELECT COUNT(*) FROM recordings WHERE genre IS NOT NULL` climbing toward 7,149.
- Genre distribution is DJ-sensible (Hip-Hop/House/UKG/etc.), not all "Electronic".
- A sampled remix shows `genre_source='audio'` with the remix's genre, not the original's.
- `spotify_popularity`, `label` populated on matched rows.
- acousticness distribution spread (median well below 0.6); valence from mood_happy on rows with embeddings.
- All test suites green.
