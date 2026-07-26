#!/usr/bin/env python3
"""Complete genuinely-partial REAL albums: for each real multi-track album missing
tracks, look up the canonical tracklist on MusicBrainz, find which titles are missing
from the album folder, and download them via yt-dlp into that folder. Skips tag-noise
'partials' (single-track folders with misleading tracktotal). Disk-guarded, resumable.
Progress -> /tmp/album_complete_progress.json."""
import os, re, json, time, glob, subprocess, urllib.parse, urllib.request

LAKE = os.path.expanduser("~/Desktop/MusicLake")
SCAN = "/tmp/album_scan.json"
PROG = "/tmp/album_complete_progress.json"
AUD = (".flac", ".mp3", ".m4a", ".wav", ".aac")
_norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
UA = {"User-Agent": "walkman-dj/1.0 (personal library tool)"}


def mb_tracklist(artist, album):
    """MusicBrainz: return list of recording titles for the best-matching release."""
    q = urllib.parse.quote(f'artist:"{artist}" AND release:"{album}"')
    url = f"https://musicbrainz.org/ws/2/release/?query={q}&fmt=json&limit=1"
    try:
        req = urllib.request.Request(url, headers=UA)
        data = json.load(urllib.request.urlopen(req, timeout=20))
        rels = data.get("releases", [])
        if not rels:
            return []
        rid = rels[0]["id"]
        time.sleep(1.1)                              # MB rate limit
        url2 = f"https://musicbrainz.org/ws/2/release/{rid}?inc=recordings&fmt=json"
        rd = json.load(urllib.request.urlopen(urllib.request.Request(url2, headers=UA), timeout=20))
        titles = []
        for m in rd.get("media", []):
            for t in m.get("tracks", []):
                titles.append(t["title"])
        return titles
    except Exception:
        return []


def free_gb():
    return int(subprocess.run(["df", "-g", os.path.expanduser("~")],
                              capture_output=True, text=True).stdout.splitlines()[1].split()[3])


def album_dir(artist, album):
    """find the lake folder holding this album's tracks."""
    for dp, _, fns in os.walk(LAKE):
        if "_playlist_tracks" in dp or "_dedup" in dp:
            continue
        if any(f.lower().endswith(AUD) for f in fns) and _norm(album) in _norm(os.path.basename(dp)):
            return dp
    return None


def emit(**kw):
    kw["last"] = time.time(); json.dump(kw, open(PROG + ".tmp", "w")); os.replace(PROG + ".tmp", PROG)


def main():
    d = json.load(open(SCAN))
    # REAL partial albums: multi-track, meaningfully incomplete, not tag noise
    reals = [a for a in d["albums"] if a.get("partial") and a["total"]
             and a["present"] >= 5 and a["present"] / a["total"] >= 0.55]
    total = len(reals); got = 0; done_alb = 0
    print(f"[album-complete] {total} real partial albums to complete", flush=True)
    for i, a in enumerate(reals, 1):
        art, alb = a.get("artist") or "", a.get("album") or ""
        emit(stage="complete", done=i - 1, total=total, got=got, current=f"{art} - {alb}")
        if free_gb() < 12:
            emit(stage="paused", note="disk<12", done=i - 1, total=total, got=got); break
        adir = album_dir(art, alb)
        if not adir:
            continue
        present = {_norm(os.path.splitext(f)[0]) for f in os.listdir(adir)
                   if f.lower().endswith(AUD)}
        titles = mb_tracklist(art, alb)
        if not titles:
            continue
        missing = [t for t in titles if not any(_norm(t) in p or p in _norm(t) for p in present)]
        for t in missing[:8]:                        # cap per album
            if free_gb() < 12:
                break
            out = os.path.join(adir, re.sub(r"[^\w\s-]", "", t)[:70])
            if glob.glob(out + ".*"):
                continue
            try:
                subprocess.run(["yt-dlp", "-x", "--audio-format", "mp3", "--audio-quality", "0",
                                "--no-playlist", "--no-warnings", "-o", out + ".%(ext)s",
                                f"ytsearch1:{art} {t} audio"],
                               capture_output=True, text=True, timeout=300)
                if glob.glob(out + ".mp3"):
                    got += 1
            except Exception:
                pass
        done_alb += 1
        time.sleep(1.1)
    emit(stage="done", done=total, total=total, got=got, albums=done_alb)
    print(f"ALBUM_COMPLETE DONE: {got} tracks added across {done_alb} albums", flush=True)


if __name__ == "__main__":
    main()
