#!/usr/bin/env python3
"""Reliable fallback: download missing curated-playlist tracks via yt-dlp (YouTube)
since Soulseek downloads are NAT-blocked here. Dedups vs the lake (incl. already-
downloaded), disk-guarded, resumable. Files -> MusicLake/_playlist_tracks/<genre>/<safe-query>.mp3
so they're deterministically findable for playlist assembly. Progress -> /tmp/pl_yt_progress.json."""
import os, re, json, time, glob, subprocess

LAKE = os.path.expanduser("~/Desktop/MusicLake")
PLDIR = "/Users/rishizamvar/Documents/Projects/walkman-dj/overnight/loop/playlists"
PROG = "/tmp/pl_yt_progress.json"
AUD = (".flac", ".mp3", ".m4a", ".wav", ".aac", ".opus", ".webm")
_norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
_safe = lambda s: re.sub(r"[^\w\s-]", "", s).strip()[:80]
_toks = lambda q: [t for t in re.findall(r"[a-z0-9]+", q.lower()) if len(t) > 2]


def lake_basenames():
    s = set()
    for dp, _, fns in os.walk(LAKE):
        for fn in fns:
            if os.path.splitext(fn)[1].lower() in AUD:
                s.add(_norm(os.path.splitext(fn)[0]))
    return s


def have(q, lake):
    toks = _toks(q); need = max(2, len(toks) - 1)
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
        if os.path.basename(pf).startswith("deployed_"):
            continue
        d = json.load(open(pf))
        if "candidates" not in d:
            continue
        genre = os.path.basename(pf)[:-5]
        for c in d["candidates"]:
            if not have(c["q"], lake):
                jobs.append((genre, c["q"]))
    total = len(jobs); got = 0; failed = []
    print(f"[pl-yt] {total} tracks to fetch via YouTube", flush=True)
    for i, (genre, q) in enumerate(jobs, 1):
        emit(stage="youtube", done=i - 1, total=total, got=got, current=f"{genre}: {q}")
        if free_gb() < 12:
            emit(stage="paused", note="disk<12", done=i - 1, total=total, got=got); break
        dest = os.path.join(LAKE, "_playlist_tracks", genre); os.makedirs(dest, exist_ok=True)
        out = os.path.join(dest, _safe(q))
        if glob.glob(out + ".*"):                    # already fetched
            got += 1; continue
        try:
            r = subprocess.run(
                ["yt-dlp", "-x", "--audio-format", "mp3", "--audio-quality", "0",
                 "--no-playlist", "--no-warnings", "-o", out + ".%(ext)s",
                 f"ytsearch1:{q} audio"],
                capture_output=True, text=True, timeout=300)
            if glob.glob(out + ".mp3"):
                got += 1; lake.add(_norm(q))
            else:
                failed.append(q)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{q}:{str(e)[:30]}")
    emit(stage="done", done=total, total=total, got=got, failed=failed)
    print(f"PL_YT DONE: got {got}/{total}, failed {len(failed)}", flush=True)
    for f in failed[:20]:
        print("  fail:", f)


if __name__ == "__main__":
    main()
