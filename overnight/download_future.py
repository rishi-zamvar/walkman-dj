#!/usr/bin/env python3
"""Download Future's full discography into MusicLake, one album at a time (Soulseek =
one connection). Resumable (skips albums already present), disk-guarded (pauses if
<12GB free). Progress -> /tmp/download_progress.json. Falls back soulseek only for now;
soundcloud/youtube fallback handled by later curation pass if an album can't be found."""
import os, re, sys, json, time, subprocess

SK = os.path.expanduser("~/.claude/skills/soulseek-download/soulseek_dl.py")
LAKE = os.path.expanduser("~/Desktop/MusicLake")
PROG = "/tmp/download_progress.json"
_norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())

# studio albums + major mixtapes/collabs
ALBUMS = [
    "Pluto", "Honest", "Monster", "Beast Mode", "56 Nights", "DS2", "Purple Reign",
    "EVOL", "Project ET", "Future", "HNDRXX", "Super Slimey", "Beast Mode 2",
    "WRLD on Drugs", "The WIZRD", "High Off Life", "Pluto x Baby Pluto",
    "I Never Liked You", "We Dont Trust You", "We Still Dont Trust You", "Mixtape Pluto",
]


def free_gb():
    return int(subprocess.run(["df", "-g", os.path.expanduser("~")],
                              capture_output=True, text=True).stdout.splitlines()[1].split()[3])


def lake_album_dirs():
    dirs = set()
    for d in os.listdir(LAKE):
        if os.path.isdir(os.path.join(LAKE, d)):
            dirs.add(_norm(d))
    return dirs


def emit(**kw):
    kw["last"] = time.time()
    json.dump(kw, open(PROG + ".tmp", "w")); os.replace(PROG + ".tmp", PROG)


def main():
    have = lake_album_dirs()
    dest = os.path.join(LAKE, "Future")
    os.makedirs(dest, exist_ok=True)
    got = 0; skipped = 0; failed = []
    for i, alb in enumerate(ALBUMS, 1):
        emit(stage="download", artist="Future", album=alb, done=i - 1, total=len(ALBUMS),
             got=got, current=f"Future — {alb}")
        # dedup: album folder already present anywhere in the lake?
        if any(_norm(alb) in h or h in _norm("Future " + alb) for h in have) and _norm(alb) in have:
            skipped += 1; continue
        if free_gb() < 12:
            emit(stage="paused", note="disk <12GB — pausing downloads", done=i - 1, total=len(ALBUMS), got=got)
            break
        try:
            r = subprocess.run(["python3", SK, "get", f"Future {alb}", "--dest", dest],
                               capture_output=True, text=True, timeout=1200)
            ok = "download" in (r.stdout + r.stderr).lower() and "failed" not in r.stdout.lower()[-200:]
            if ok:
                got += 1
            else:
                failed.append(alb)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{alb}:{str(e)[:40]}")
    emit(stage="done", got=got, skipped=skipped, failed=failed, total=len(ALBUMS), done=len(ALBUMS))
    print(f"FUTURE_DL DONE: got {got}, skipped {skipped}, failed {len(failed)}")
    for f in failed:
        print("  fail:", f)


if __name__ == "__main__":
    main()
