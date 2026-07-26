# Overnight Autonomous Loop — Spec & Definition of Done

**Mode:** Run 24/7 in a self-healing loop until EVERY task below is genuinely done. No time
constraint (could be all weekend). Silent — no ZeeChat pings; all status goes to the live
monitor + a progress log. Update the monitor continuously (~30-min cadence).

**Reference:** builds on the consolidation already done (MusicLake = 7,650-track single library),
the Set Sequencer, the emergent community graph, and the walkman-dj analysis pipeline.

---

## Locked decisions (from Q&A 2026-07-23)

| Area | Decision |
|---|---|
| Analysis engine | **SOTA: Beat This! (downbeat/beatgrid) + DeepRhythm (octave-correct BPM) + librosa (key/energy/timbre/cues)** + constant-tempo snap. Fix the decode (ffmpeg/dedicated venv). |
| Album completion | **Fill ALL partial albums** via download before transfer (Soulseek → soulseek-alt → SoundCloud → YouTube last). |
| Playlist count | **7 curated** (not 10). |
| Playlist size | **~15–25 tracks**, harmonically ordered + energy arc (Future discography = exception, larger). |
| Curation method | **Deep forum/community research** (Reddit, RA, Discogs, DJ pools) → best, most-cohesive tracklist. **Download whatever makes each list best** (not limited to library). |
| Future scope | **Full catalog** — download complete discography (studio + major mixtapes), build from it. |
| Genre strictness (curated) | **Genre-authentic; classic DJ edits/bootlegs OK where they're staples** (UKG/jungle). **Baile = proper album tracks, NO remixes/edits.** No cheesy pool-edits. |
| Emergent graph | **Soft, genre-PRIORITIZED vibe edges** (reverses the earlier hard gate). Use ALL metadata. Target **~85–90% genre homogeneity with natural bridges**. **Deploy the good communities as extra playlists.** |
| Walkman scope | **All completed albums + the 7 curated playlists (+ good emergent communities).** (Reverses earlier "keep lean".) |
| Album integrity | **Never modify existing complete albums.** Album membership from the **album TAG**, not folder. Never demote album tracks to singles. Only true singles (no real album tag) get album-tag stripped → stay in lake. |
| Reporting | **Silent.** Live monitor at :8799 + progress log only. |

## The 7 curated playlists
1. **UK Garage (UKG)** — 2-step / speed garage; classic bootlegs OK.
2. **Ghetto House** — Chicago booty/ghetto house (Dance Mania lineage: DJ Deeon, DJ Funk, Paul Johnson…).
3. **Future — full discography** — download complete catalog; build the definitive playlist.
4. **Jungle / DnB.**
5. **Afro / Organic House.**
6. **Drake / Toronto rap-trap.**
7. **Real Baile Funk** — proper Brazilian baile album tracks (Funk Brasil / MC Fioti / album sources), NOT edits/remixes.

*(Genre interpretations above — correct any before launch.)*

---

## Tasks

### T1 — Complete + transfer albums to Walkman
- Metadata-scan the computer-only tracks (album tag driven). Group into albums vs true singles.
- For each partial album: research its full tracklist, download missing tracks to complete it.
- UKG-Setlist (Gunna setlist) → becomes a playlist; its true singles → strip album tag → lake.
- Dedup vs what's already on the Walkman. Push completed albums to the Walkman SD card.
- **Never touch existing complete albums.**

### T2 — Analyze all songs on disk (SOTA engine)
- Stand up a working venv (torch + beat-this + deeprhythm + librosa w/ functioning decode).
- New `beatgrid.py`: downbeats+beats (Beat This!), octave-correct BPM (DeepRhythm), least-squares
  constant-tempo snap, anchor = first downbeat, real confidence = fit residual.
- Analyze EVERY MusicLake track not yet `ok` (~all 7,650 + new downloads). Store in `walkman_analysis.db`.

### T3 — Tune the emergent community graph
- Soft, genre-prioritized affinity edges using ALL features (BPM, Camelot, energy, brightness,
  bass, danceability, genre, beatgrid confidence…). Reverse the hard gate.
- Tune weights on a ground-truth subset until **≥85% genre homogeneity with natural bridges**.
- Deploy the genuinely-cohesive communities as extra Walkman playlists.

### T4 — Curate the 7 playlists
- Per genre: forum/community research → canonical + best tracks → download missing → dedup →
  harmonic + energy-arc order (Set Sequencer) → ~15–25 tracks → deploy `.m3u8` to Walkman.

### T5 — Build the walkman-playlist-push skill
- A CC skill that pushes `.m3u8` playlists to a connected Walkman (adb), idempotent + verified.

### T6 — Live monitor (continuous)
- Persistent dashboard at **http://127.0.0.1:8799**: current activity, **last-updated timestamp**,
  concise human-readable bullets of what's happened, and **KPIs** (tracks analyzed / albums completed
  / downloads done / playlists built / % of each task). Refresh ~every 30 min (or on state change).

---

## Definition of Done — the loop STOPS only when ALL are true
1. ✅ Every MusicLake track has `status='ok'` features from the SOTA engine (or logged-unanalyzable).
2. ✅ Every partial album is complete (or download options exhausted + logged).
3. ✅ All computer-only albums transferred to the Walkman (deduped, existing albums untouched).
4. ✅ Emergent graph tuned to ≥85% genre homogeneity; cohesive communities deployed as playlists.
5. ✅ All 7 curated playlists built (research-backed, harmonically ordered, ~15–25 tracks) + on Walkman.
6. ✅ `walkman-playlist-push` skill built + test-verified.
7. ✅ Monitor shows the final completed state + KPIs.

## Guardrails
- Never delete/modify existing complete albums or user files (Trash, never `rm`, for anything removed).
- Soulseek: ONE connection at a time. Download fallback order fixed. Lossless-first.
- Dedup everything against the Walkman + lake before adding.
- Resumable + self-healing: crashes/offline-device/failed-downloads retry; the loop continues.
- Disk watch: keep ≥10 GB free; if low, pause downloads + surface on monitor (don't fail hard).
