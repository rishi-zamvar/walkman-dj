# walkman-dj — music library workflow

**Library:** `~/Documents/MusicLake` (151GB, local, NOT in iCloud). Index: `data/library.db` (acoustic fingerprint = song identity). Analysis DB: `data/walkman_analysis.db` (BPM/Camelot/genre).

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
- Deletions go to Trash (never rm). `~/Documents` is NOT iCloud-synced for this repo (`.git` → `.git.nosync`).
