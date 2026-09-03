# walkman-dj — music library workflow

**Library:** `~/Music/MusicLake` (154GB, local, NOT in iCloud). Index + analysis: `data/library.db` (acoustic fingerprint = song identity; BPM/key/genre/features live on `recordings`, plus `segments`, `ml_features`, `track_tags`). `data/archive_walkman_analysis.db` is the retired pre-merge analysis DB, read only by `crate enrich`.

## Use these skills (auto-discovered)
- **`music-library`** — ALL library ops: add song (`crate ingest`), fetch canonical file (`crate get`), dedupe/organize/verify, enrich BPM/genre, export playlists. CLI: `bin/crate`.
- **`walkman-transfer` / `walkman-playlist-push`** — put music/playlists on the Sony NW-A300 (adb). Resolve files via `crate get` first.
- **`cdj-upload`** — write Rekordbox export.pdb to a FAT32 USB for CDJs.
- **`spotify-playlists` / `soulseek-download`** — acquire music.
- **`djcity-purge`** — remove DJ-pool watermarked tracks by cover-art hash.

## Rules
- Only add music via `crate ingest` (never drop files into MusicLake directly).
- Album membership = album TAG, not folder. True singles (no album tag) → `_Singles/`.
- One FLAC master per song; devices get transcodes via `crate get --format` / `crate export`.
- `crate prune` reconciles index->disk (relinks moved files by fingerprint, drops rows for gone ones); `crate rescan` indexes files the lake has but the index doesn't. Both dry-run by default; **prune first** — rescan would refile a moved file as new and strand its analysis.
- Never compare library paths as raw strings: APFS is case-insensitive and mixes NFC/NFD. Use `crate._pathkey`.
- Deletions go to Trash (never rm). `~/Documents` is NOT iCloud-synced for this repo (`.git` → `.git.nosync`).
