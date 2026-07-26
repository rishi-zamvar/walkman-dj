#!/usr/bin/env python3
"""Transfer computer-only ALBUM folders to the Walkman (the whole albums the user
wants to enjoy). Identifies real multi-track album folders (>=4 audio, track-numbered),
dedups vs what's already on the Walkman by basename, and adb-pushes missing tracks into
Music/<album-folder>/. Never touches existing device albums. Disk-cheap (adb push, no
local writes). Progress -> /tmp/album_transfer_progress.json."""
import os, re, json, time, subprocess

LAKE = os.path.expanduser("~/Desktop/MusicLake")
DEV = "/storage/6439-6561/Music"
PROG = "/tmp/album_transfer_progress.json"
AUD = (".flac", ".mp3", ".m4a", ".wav", ".aac")
_norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
_tracknum = re.compile(r'^\s*\d{1,2}\s*[-\.\s]')


def device_basenames():
    r = subprocess.run(["adb", "shell", f"find {DEV} -type f"],
                       capture_output=True, text=True, timeout=300)
    return set(_norm(os.path.splitext(os.path.basename(l.strip()))[0])
              for l in r.stdout.replace("\r", "").splitlines()
              if os.path.splitext(l.strip())[1].lower() in AUD)


def album_folders():
    """real album folders in the lake: >=4 audio, majority track-numbered."""
    out = []
    for dp, _, fns in os.walk(LAKE):
        if "_playlist_tracks" in dp or "_dedup" in dp:
            continue
        aud = [f for f in fns if os.path.splitext(f)[1].lower() in AUD]
        if len(aud) >= 4:
            numbered = sum(1 for f in aud if _tracknum.match(f))
            if numbered >= max(3, len(aud) * 0.5):
                out.append((dp, aud))
    return out


def emit(**kw):
    kw["last"] = time.time(); json.dump(kw, open(PROG + ".tmp", "w")); os.replace(PROG + ".tmp", PROG)


def main():
    ondev = device_basenames()
    albums = album_folders()
    total = len(albums)
    print(f"[album-transfer] {total} album folders in lake", flush=True)
    pushed = 0; alb_done = 0; skipped_alb = 0
    for i, (dp, aud) in enumerate(albums, 1):
        folder = os.path.basename(dp)
        missing = [f for f in aud if _norm(os.path.splitext(f)[0]) not in ondev]
        emit(stage="transfer", done=i - 1, total=total, pushed=pushed,
             current=folder[:40], missing_here=len(missing))
        if not missing:
            skipped_alb += 1; continue
        subprocess.run(["adb", "shell", f'mkdir -p "{DEV}/{folder}"'], capture_output=True)
        for f in missing:
            pr = subprocess.run(["adb", "push", os.path.join(dp, f), f"{DEV}/{folder}/{f}"],
                               capture_output=True, text=True)
            if "1 file" in pr.stdout + pr.stderr or "pushed" in pr.stdout + pr.stderr:
                pushed += 1; ondev.add(_norm(os.path.splitext(f)[0]))
        alb_done += 1
    emit(stage="done", done=total, total=total, pushed=pushed, albums=alb_done, skipped=skipped_alb)
    print(f"ALBUM_TRANSFER DONE: pushed {pushed} tracks across {alb_done} albums "
          f"({skipped_alb} already on device)", flush=True)


if __name__ == "__main__":
    main()
