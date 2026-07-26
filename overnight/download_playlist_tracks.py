#!/usr/bin/env python3
"""Download the missing curated-playlist candidate tracks (from overnight/loop/playlists/*.json)
via Soulseek song-mode, deduped vs the lake, into MusicLake/_playlist_tracks/<genre>/.
One connection, serial, resumable, disk-guarded (pause <12GB). Progress -> /tmp/pl_download_progress.json."""
import os, re, sys, json, time, glob, subprocess

SK = os.path.expanduser("~/.claude/skills/soulseek-download/soulseek_dl.py")
LAKE = os.path.expanduser("~/Desktop/MusicLake")
PLDIR = "/Users/rishizamvar/Documents/Projects/walkman-dj/overnight/loop/playlists"
PROG = "/tmp/pl_download_progress.json"
AUD = (".flac", ".mp3", ".m4a", ".wav", ".aac")
_norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
_toks = lambda q: [t for t in re.findall(r"[a-z0-9]+", q.lower()) if len(t) > 2]


def lake_basenames():
    s = set()
    for dp, _, fns in os.walk(LAKE):
        for fn in fns:
            if os.path.splitext(fn)[1].lower() in AUD:
                s.add(_norm(os.path.splitext(fn)[0]))
    return s


def have(q, lake):
    toks = _toks(q)
    need = max(2, len(toks) - 1)
    return any(sum(1 for t in toks if t in b) >= need for b in lake)


def free_gb():
    return int(subprocess.run(["df", "-g", os.path.expanduser("~")],
                              capture_output=True, text=True).stdout.splitlines()[1].split()[3])


def emit(**kw):
    kw["last"] = time.time(); json.dump(kw, open(PROG + ".tmp", "w")); os.replace(PROG + ".tmp", PROG)


def main():
    lake = lake_basenames()
    jobs = []
    for pf in sorted(glob.glob(f"{PLDIR}/*.json")):
        d = json.load(open(pf))
        genre = os.path.basename(pf)[:-5]
        for c in d["candidates"]:
            if not have(c["q"], lake):
                jobs.append((genre, c["q"]))
    total = len(jobs)
    print(f"[pl-dl] {total} missing candidate tracks to download", flush=True)
    got = 0; failed = []
    for i, (genre, q) in enumerate(jobs, 1):
        emit(stage="download", done=i - 1, total=total, got=got, current=f"{genre}: {q}")
        if free_gb() < 12:
            emit(stage="paused", note="disk <12GB", done=i - 1, total=total, got=got); break
        # re-dedup live (a prior download may have covered it)
        if have(q, lake):
            continue
        dest = os.path.join(LAKE, "_playlist_tracks", genre)
        os.makedirs(dest, exist_ok=True)
        before = sum(len(fs) for _, _, fs in os.walk(dest))
        success = False
        for attempt in (1, 2):                       # retry once (Soulseek port errors are transient)
            try:
                subprocess.run(["python3", SK, "get", q, "--song", "--dest", dest],
                               capture_output=True, text=True, timeout=600)
            except Exception:  # noqa: BLE001
                pass
            after = sum(len(fs) for _, _, fs in os.walk(dest))
            if after > before:                       # a real file landed = success
                success = True; break
            time.sleep(3)
        if success:
            got += 1; lake.add(_norm(q))
        else:
            failed.append(q)
    emit(stage="done", done=total, total=total, got=got, failed=failed)
    print(f"PL_DL DONE: got {got}/{total}, failed {len(failed)}", flush=True)
    for f in failed[:30]:
        print("  fail:", f)


if __name__ == "__main__":
    main()
