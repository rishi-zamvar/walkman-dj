# Overnight curation loop — per-wakeup playbook (FULL AUTONOMY, no questions)

Goal by 7:45am: Walkman rebuilt into intentional, vibe-cohesive HYBRID playlists (tight genre crates + cross-genre vibe/theme playlists + deep cultural crates + discovery), expanded with new full albums + tracks (highest quality). Cut songs that don't fit — cohesion over coverage. Report auto-opens at 7:45 (guard job bd55iri8g).

## Each wakeup, do ONE meaningful chunk then re-schedule:
1. **Check time.** If ≥ 07:35, do a final playlist rebuild + write playlists_manifest.json, then `ScheduleWakeup stop:true` (the 7:44 guard opens the report).
2. **Read state:** taste_profile.md, queues/albums.json, logs/albums_state.json (what downloaded), current Walkman songs (`adb shell find ... Music`).
3. **Pick the next unit of work** (track status in overnight/progress.jsonl):
   - **First priority (do early, reliable):** rebuild ALL Walkman playlists into the hybrid blueprint below from the CURRENT on-device songs (classify via genre rules + taste). Push .m3u8, delete stale ones. This is high-value and needs no downloads. Write playlists_manifest.json = [{name,category,count}].
   - **Then:** as albums land (albums_state ok), fold their tracks into the right genre + vibe playlists; build the new-genre crates (trance, jungle, amapiano, bollywood, punjabi, reggaeton, salsa/bachata, dembow, prog house).
   - **Then:** expand queues — append discovery albums/tracks to queues/albums.json (new artists extrapolated per pillar) so the driver keeps pulling. Be creative, branch out.
4. **Log** each action to progress.jsonl (ev, detail). Update playlists_manifest.json.
5. **ScheduleWakeup** ~1500-1800s out (reason = what you did), same prompt, unless stopping.

## Playlist blueprint (hybrid — build/refine these; size 50-70, cut for vibe)
GENRE CRATES: Opium & Rage · Underground Rage · Atlanta Trap · Toronto/Melodic · Lyrical Hip-Hop · Tech House · Festival House · Organic/Afro House · Nu-Disco & Funk · Progressive House · Trance · Jungle & DnB · UK Garage/Bassline · Baile Funk · Jersey Club · Alt R&B · Afrobeats · Amapiano
CULTURAL (deep): Reggaeton · Latin Classics (Salsa & Bachata) · Dembow · Latin Pop/Crossover · Punjabi · Bollywood
VIBE/THEME (cross-genre): After Hours (2-6am deep) · Golden Hour (warm groove) · Peak-Time Rager · Safe on Aux (crossover/throwback rap) · Sunday Reset (mellow R&B/soul) · Pregame/Function · Late Night Drive · Mezcla / Latin Night · Desi Function · Throwbacks (2000s-2010s)
DISCOVERY (new music extrapolated): Rage Frontier · New Latin Heat · Dance Floor Finds · New R&B

## Tools
- Downloads: soulseek skill (albums, FLAC-first) → SC API (creds in /tmp/sc_creds.json, /tracks/{id}/download) → yt-dlp scsearch. Always highest quality. Verify not 30s previews (dur ≥75s).
- Walkman: adb. Music root `/storage/6439-6561/Music`. Playlists = .m3u8 with relative paths + #EXTINF. Push via `adb push`, delete via `adb shell rm`. Genre classify: rules.classify_track(folder=album,artist=album,filename) + /tmp/genremap + online cache.
- One soulseek session + one adb at a time. Driver (drive.py, task blia6ra5g) is already downloading albums — don't run soulseek concurrently with it; if you need to download, either append to queues/albums.json (let driver handle) or check the driver isn't mid-download.
- ZeeChat only if truly blocked. Report guard at 7:44 is automatic.

## Night 2 mandate (re-armed 22:39) — KEEP EXPANDING
- Driver now has a DEDUP GUARD (skips albums already on device by ≥60% title overlap) — safe to queue aggressively.
- Each pass: (1) if drive.py stopped, restart it (queue skips done/dups); (2) run rebuild.py to fold new albums into crates; (3) AGGRESSIVELY append fresh ALBUMS to queues/albums.json — user wants a shit-ton more UKG, jungle/DnB, baile funk + proper Brazilian funk, trance, jazz house, ghetto house/juke, plus keep extrapolating discovery across ALL pillars. Prefer albums over singles. Add ~15-25 albums per pass, always NEW artists/albums not already in queue.
- Add new genre crates + creative vibe playlists to rebuild.py as genres deepen (e.g. Jazz House, Ghetto House, Jungle classics, 90s Garage, Brazilian Funk).
- No fixed stop — keep looping through the night. A 7:45 report guard is armed separately.

## Disk hygiene (every pass — user mandate)
- Run `python3 cleanup.py` each loop pass: deletes ~/Desktop/music staging folders already pushed to Walkman (status 'ok'), so the driver's downloads can't fill the disk. NEVER deletes playlists (.m3u8) or the DJ Sets. Reclaimed 22.5GB on first run.
- ZeeCloud (/Volumes/ZeeCloud) is NOT mounted right now — cleanup.py auto-offloads failed/partial staging leftovers there IF it mounts. When idle and ZeeCloud is up, also offload other non-essential large/old files (keep song playlists on the drive). Log freed_mb to progress.jsonl (shows on monitor).
- Monitor: http://localhost:8765 (monitor_server.py, port 8765) — auto-refreshes every 4s. Keep insights.json updated each pass with plan/insights/last_pass/next_wake/songs_on_device so the page stays live.

## Parallel + SC-fallback update
- Driver is now drive_parallel.py (5 workers, ports 61000-65000, live status in workers.json). Restart via run_in_background python3 drive_parallel.py (NOT drive.py). It has: dedup guard + SoundCloud API fallback (sc_fallback.py, /tracks/{id}/download, ≥75s preview guard, our token matching) for TARGET genres when Soulseek is empty.
- Monitor: monitor_server.py on :8765 shows 5 worker cards + KPIs (songs added TONIGHT since 20:00, songs/min, classified, playlists, routed, to-go). Keep it alive (nohup or run_in_background).
- To EVEN OUT genres: keep queuing NEW target-genre artists/albums (UKG, jungle, baile, trance, jazz house, ghetto house, salsa, bollywood, dembow, amapiano) — different artists each pass since Soulseek/SC coverage is thin. Prioritize target genres to front of queue.
