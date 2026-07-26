#!/usr/bin/env python3
"""Remove dangling entries for the deleted DJcity tracks from every Walkman .m3u8.
Backs up each playlist first, drops lines referencing a deleted track (and its
preceding #EXTINF), pushes back only if changed. Emits progress to the monitor."""
import os, sys, json, time, subprocess

DEV = "/storage/6439-6561/Music"
MATCHES = "/tmp/djcity_matches_walkman.json"
PBACKUP = os.path.expanduser("~/Desktop/djcity-removed-2026-07-23/_playlists_backup")
MON = os.path.expanduser("~/Desktop/consolidation-monitor/progress.json")
t0 = time.time()
_norm = lambda s: s.strip().lower()


def emit(**kw):
    kw["elapsed_s"] = round(time.time() - t0, 1)
    json.dump(kw, open(MON + ".tmp", "w")); os.replace(MON + ".tmp", MON)


def main():
    remotes = json.load(open(MATCHES))["exact"]
    dead = set()
    for r in remotes:
        rel = r[len(DEV) + 1:] if r.startswith(DEV) else os.path.basename(r)
        dead.add(_norm(rel))
        dead.add(_norm(os.path.basename(rel)))   # basename fallback
    os.makedirs(PBACKUP, exist_ok=True)

    r = subprocess.run(["adb", "shell", f"find {DEV} -maxdepth 1 -iname '*.m3u8'"],
                       capture_output=True, text=True)
    pls = [l.strip() for l in r.stdout.replace("\r", "").splitlines() if l.strip()]
    emit(stage="playlist", phase_label="Cleaning playlists", processed=0, total=len(pls))

    total_removed = 0
    report = []
    for i, remote in enumerate(pls):
        base = remote.rsplit("/", 1)[-1]
        local = os.path.join(PBACKUP, base)
        subprocess.run(["adb", "pull", remote, local], capture_output=True)
        try:
            lines = open(local, encoding="utf-8", errors="replace").read().splitlines()
        except Exception:
            continue
        out, removed, j = [], 0, 0
        while j < len(lines):
            ln = lines[j]
            if ln.startswith("#EXTINF"):
                nxt = lines[j + 1] if j + 1 < len(lines) else ""
                nb = _norm(nxt); nbn = _norm(os.path.basename(nxt))
                if nb in dead or nbn in dead:
                    removed += 1; j += 2; continue      # drop EXTINF + its path line
                out.append(ln); j += 1; continue
            nb = _norm(ln); nbn = _norm(os.path.basename(ln))
            if ln and not ln.startswith("#") and (nb in dead or nbn in dead):
                removed += 1; j += 1; continue
            out.append(ln); j += 1
        if removed:
            tmp = f"/tmp/_pl_{i}.m3u8"
            open(tmp, "w", encoding="utf-8").write("\n".join(out) + "\n")
            subprocess.run(["adb", "push", tmp, remote], capture_output=True)
            total_removed += removed
            report.append((base, removed))
        if (i + 1) % 3 == 0 or i + 1 == len(pls):
            emit(stage="playlist", phase_label="Cleaning playlists",
                 processed=i + 1, total=len(pls), exact=total_removed, current=base)

    json.dump({"total_entries_removed": total_removed, "playlists_touched": len(report),
               "detail": report}, open("/tmp/djcity_playlist_result.json", "w"), indent=1)
    emit(stage="done", phase_label=f"Removed {total_removed} dangling entries from {len(report)} playlists",
         processed=len(pls), total=len(pls), exact=total_removed)
    print(f"playlists scanned: {len(pls)}  touched: {len(report)}  entries removed: {total_removed}")
    for base, n in sorted(report, key=lambda x: -x[1])[:25]:
        print(f"  {n:>3}  {base}")
    print("DJCITY_PLAYLIST_CLEAN DONE")


if __name__ == "__main__":
    main()
