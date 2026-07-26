#!/usr/bin/env python3
"""Metadata-driven scan of computer-only tracks (in MusicLake, not on Walkman).
Reads real album tags via ffprobe (fast) and groups into ALBUMS vs true SINGLES.
For albums, computes completeness (present vs tracktotal) so partial albums can be
flagged 'to fill'. READ-ONLY — writes only a report. Never edits tags or moves files."""
import os, re, json, time, subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

HOME = os.path.expanduser("~")
LAKE = f"{HOME}/Desktop/MusicLake"
MON = f"{HOME}/Desktop/consolidation-monitor/progress.json"
OUT = "/tmp/album_scan.json"
AUD = (".flac", ".mp3", ".m4a", ".wav", ".aac", ".aiff", ".aif")
_norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
t0 = time.time()


def emit(**kw):
    kw["elapsed_s"] = round(time.time() - t0, 1)
    p, t = kw.get("processed", 0), kw.get("total", 0)
    if p and kw["elapsed_s"] > 1:
        rate = p / kw["elapsed_s"]; kw["rate_per_s"] = round(rate, 1)
        kw["eta_s"] = round((t - p) / rate) if rate and t else None
    json.dump(kw, open(MON + ".tmp", "w")); os.replace(MON + ".tmp", MON)


def probe(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries",
             "format_tags=album,album_artist,ALBUM,ARTIST,artist,track,TRACK,tracktotal,TRACKTOTAL,disc",
             "-of", "json", path], capture_output=True, text=True, timeout=15).stdout
        tags = (json.loads(out).get("format", {}) or {}).get("tags", {}) if out else {}
    except Exception:
        tags = {}
    low = {k.lower(): v for k, v in tags.items()}
    return {
        "album": low.get("album"),
        "artist": low.get("album_artist") or low.get("artist"),
        "track": low.get("track"),
        "tot": low.get("tracktotal") or (low.get("track", "").split("/")[1]
                                         if low.get("track") and "/" in str(low.get("track")) else None),
    }


def main():
    # walkman basenames for dedup
    r = subprocess.run(["adb", "shell", "find /storage/6439-6561/Music -type f"],
                       capture_output=True, text=True, timeout=120)
    wk = set(_norm(os.path.splitext(os.path.basename(l.strip()))[0])
             for l in r.stdout.replace("\r", "").splitlines()
             if os.path.splitext(l.strip())[1].lower() in AUD)
    files = []
    for dp, _, fns in os.walk(LAKE):
        if "_dedup-losers" in dp:
            continue
        for fn in fns:
            if os.path.splitext(fn)[1].lower() in AUD and _norm(os.path.splitext(fn)[0]) not in wk:
                files.append(os.path.join(dp, fn))
    total = len(files)
    emit(stage="albumscan", phase_label="Reading album tags", processed=0, total=total)

    meta = {}
    done = [0]

    def work(p):
        return p, probe(p)
    with ThreadPoolExecutor(max_workers=16) as ex:
        for p, m in ex.map(work, files):
            meta[p] = m
            done[0] += 1
            if done[0] % 100 == 0:
                emit(stage="albumscan", phase_label="Reading album tags",
                     processed=done[0], total=total)

    # group by (artist, album)
    albums = defaultdict(list)
    singles = []
    for p, m in meta.items():
        alb = (m.get("album") or "").strip()
        if alb and _norm(alb) not in ("", "single", "singles", "unknownalbum", "variousartists"):
            albums[(_norm(m.get("artist")), _norm(alb))].append((p, m))
        else:
            singles.append(p)

    album_report = []
    for (na, nalb), items in albums.items():
        present = len(items)
        tot = 0
        for _, m in items:
            try:
                tot = max(tot, int(str(m.get("tot")).split("/")[0])) if m.get("tot") else tot
            except Exception:
                pass
        disp_alb = items[0][1].get("album"); disp_art = items[0][1].get("artist")
        album_report.append({"artist": disp_art, "album": disp_alb, "present": present,
                             "total": tot or None, "partial": bool(tot and present < tot),
                             "paths": [p for p, _ in items]})
    album_report.sort(key=lambda a: -a["present"])
    complete = [a for a in album_report if not a["partial"]]
    partial = [a for a in album_report if a["partial"]]
    json.dump({"albums": album_report, "singles": singles,
               "n_albums": len(album_report), "n_album_tracks": sum(a["present"] for a in album_report),
               "n_partial": len(partial), "n_singles": len(singles)}, open(OUT, "w"), indent=1)
    emit(stage="done", phase_label=f"{len(album_report)} albums ({len(partial)} partial), {len(singles)} singles",
         processed=total, total=total)
    print(f"ALBUMS: {len(album_report)} ({sum(a['present'] for a in album_report)} tracks) — "
          f"complete {len(complete)}, PARTIAL {len(partial)}")
    print(f"TRUE SINGLES (no album tag): {len(singles)}")
    print("\nPARTIAL albums (present/total → to fill):")
    for a in sorted(partial, key=lambda x: -x["present"])[:20]:
        print(f"  {a['present']}/{a['total']}  {a['artist']} — {a['album']}")
    print("\nlargest complete albums:")
    for a in complete[:12]:
        print(f"  {a['present']}{'/'+str(a['total']) if a['total'] else ''}  {a['artist']} — {a['album']}")
    print(f"\nreport -> {OUT}")
    print("ALBUM_SCAN DONE")


if __name__ == "__main__":
    main()
