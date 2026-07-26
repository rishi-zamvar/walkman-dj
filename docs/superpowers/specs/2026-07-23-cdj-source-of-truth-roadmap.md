# CDJ USB Source-of-Truth — Roadmap

## Vision
One FAT32 USB stick is the **complete, CDJ-readable library of all my music** — every track analyzed, organized into playlists, written **directly** (no Rekordbox app in the loop). Once it's the source of truth, the computer keeps only the handful of playlists I use most + whatever I need for a set, and the rest is offloaded.

## Hard constraints
- **Stay FAT32.** The Lexar (124 GB, FAT32) is already CDJ-correct. Never reformat. 4 GB/file limit is a non-issue for audio.
- **CDJ-readable = Pioneer device-export format**, not m3u8. On disk that is:
  - `Contents/` — the audio files (Rekordbox lays them out `Contents/<Artist>/...`).
  - `PIONEER/rekordbox/export.pdb` (+ `exportExt.pdb`) — the library database: tracks, playlists, artists, albums, keys, colors, histories.
  - `PIONEER/USBANLZ/<hash>/ANLZ0000.DAT` + `.EXT` — **per-track analysis**: beat grid, waveform (preview + detail), memory/hot cues, phrase data.
  - `PIONEER/Artwork/` — album-art thumbnails. `*.DAT` MySettings files.
- **No Rekordbox.** Everything generated programmatically.

## Assets we already have (this is why it's feasible)
- **librosa MIR for ~6,600 tracks** in `walkman_analysis.db`: BPM (genre-corrected), key→Camelot, beat/onset data, energy + 16-pt loudness curve, `mix_in`/`mix_out` cue points, MFCC structure segments. → these map directly onto Rekordbox's BPM, key, beat grid, memory cues, and phrase fields.
- **A known-good Rekordbox export already on the USB** (2,995 tracks, made by Rekordbox). We can *read* it to learn the exact byte-layout these CDJs expect and match it — far safer than writing blind.
- **Emergent genre playlists + vibe playlists + the A/B classifier** → become the USB's playlist tree and track colors.
- The whole **walkman-dj pipeline** (analysis, playlist building, adb/file I/O, dedup, prefix-pull).

## Tooling landscape (researched)
| Tool | Role | Notes |
|---|---|---|
| [pyrekordbox](https://github.com/dylanljones/pyrekordbox) (Py) | **read** pdb/ANLZ, **write** XML | knows the ANLZ struct (read); write-ANLZ "planned" |
| [kimtore/rex](https://github.com/kimtore/rex) (Go) | **write** `export.pdb` (tracks+playlists) | only proven pdb *writer*; experimental; no ANLZ |
| [rekordcrate](https://github.com/Holzhaus/rekordcrate) (Rust) | **read/parse** device exports | best format validator |
| [Deep Symmetry djl-analysis](https://djl-analysis.deepsymmetry.org/rekordbox-export-analysis/) | **the format spec** | authoritative reverse-engineering of pdb + ANLZ |

**Reality check:** no tool produces a *full* export (with beatgrids/waveforms/cues) without Rekordbox. `rex` gets us a browsable pdb; the ANLZ files we generate ourselves from our librosa analysis + the Deep Symmetry spec, validated against the existing known-good ANLZ on the USB.

## Fidelity ladder (ship incrementally, de-risk)
- **L0 — folders only.** Copy audio to FAT32; CDJ browses by folder, analyzes on load. Trivial fallback, no playlists.
- **L1 — pdb + playlists.** `export.pdb` with all tracks + our playlist tree. CDJ shows the full library and playlists; still analyzes audio on load. **First shippable target.**
- **L2 — + ANLZ.** Generate beat grids, waveforms, memory/hot cues from our analysis. Full CDJ UX (instant waveform, quantized cues). **The prize.**

Ship L1, then climb to L2 — each verified on the actual CDJ.

## Roadmap

**Phase 0 — Recon & format lock**
Read the existing `export.pdb` + one `ANLZ0000.DAT/.EXT` with rekordcrate + pyrekordbox. Document the exact tables/pages/rows and analysis sections these CDJs consume. Confirm the CDJ model and whether to target **legacy pdb** (max compatibility, what's already there) vs **Device Library Plus / OneLibrary** (SQLite-based, easier to write, newer players only). Lock the format. Deliverable: a byte-level template + field-mapping (our DB → Rekordbox fields).

**Phase 1 — Consolidation & 100% analysis coverage**
Define the canonical audio library (MusicLake as the lake + device-only tracks pulled back). Dedup, normalize tags (artist/title/album/genre). Ensure every track is analyzed (`status='ok'`) — extend librosa coverage to the stragglers. Compute the extra Rekordbox-needed fields we don't store yet (precise first-beat offset, per-track ReplayGain, track color from A/B/genre).

**Phase 2 — PDB writer → L1 (browsable + playlists)**
Generate `export.pdb`/`exportExt.pdb` for the full library + the playlist tree (emergent genre sets, vibe playlists, A/B as colors). Use `rex` as the engine (fork/feed it our data) or port its pdb page-writer to Python for repo cohesion. Copy audio into `Contents/`. **Verify on the CDJ: full browse + playlists.**

**Phase 3 — ANLZ writer → L2 (full fidelity)**
Per track, write `ANLZ0000.DAT` + `.EXT`: **beat grid** (from librosa beat positions + genre-corrected BPM), **waveform preview + color detail** (downsampled RMS/spectral from audio), **memory cue** at `mix_in` and a **hot cue** bank from structure segments/drops. Match the existing ANLZ byte layout exactly. **Verify on the CDJ: instant waveforms + quantized cues.**

**Phase 4 — Artwork & metadata polish**
Album-art thumbnails → `PIONEER/Artwork/`. Track color / rating / comment / genre / label fields populated. Playlist ordering = our harmonic set order.

**Phase 5 — The `cdj-upload` skill + incremental sync**
A CC skill: point it at new music → it analyzes, appends to the USB export (idempotent, resumable, never re-doing done tracks), verifies structure, and reports. Handles "add these 40 tracks + this playlist" without a full rebuild.

**Phase 6 — Offload**
Once the USB is verified as the source of truth, prune computer music to only most-used + set playlists. `walkman_analysis.db` stays as the master index/catalog of everything on the USB.

## Decisions to lock (before Phase 2)
1. **CDJ model / format target** — legacy `export.pdb` (widest compat, matches current USB) vs OneLibrary/DLP (easier write, newer only). *Recommend legacy pdb* to match the existing export + guarantee compatibility.
2. **Fidelity to ship first** — L1 (browsable+playlists) now, L2 later? *Recommend yes* — get a usable USB fast, then add analysis.
3. **Writer language** — fork `rex` (Go, proven) vs port to Python (repo cohesion, our analysis is Python). *Recommend: prototype with `rex` in Phase 2, port the pdb+ANLZ writer to Python by Phase 3 so the whole pipeline is one language.*
4. **Consolidation scope & dedup policy** — MusicLake is the canonical lake (don't re-dedupe per prior note); device-only tracks get pulled back in.

## Risks & mitigations
- **Reverse-engineered formats → device quirks.** Mitigate by matching the *existing known-good export byte-for-byte* and testing on the real CDJ at every phase (L1, then L2).
- **`rex` is experimental.** Treat its pdb output as a starting point; validate every field against the existing pdb with rekordcrate before trusting it on hardware.
- **Losing the current working export.** Back up the whole `PIONEER/` + `Contents/` before writing anything.
- **Scope creep.** The ladder (L0→L1→L2) means there's always a working USB; we never leave it broken.
