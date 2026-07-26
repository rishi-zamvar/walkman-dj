#!/usr/bin/env python3
"""Move all real music from ~/Desktop/music and ~/Music into MusicLake, preserving
album/artist subfolders. Skips app-junk (Serato/GarageBand/rekordbox/PioneerDJ) and
Apple DRM (.m4p). On a filename collision with something already in the lake, keeps
the higher-quality copy (flac/wav > m4a/mp3 > ogg, then larger); the loser goes to
MusicLake/_dedup-losers/ (recoverable, not deleted). Same-disk moves = fast."""
import os, re, sys, json, time, shutil

HOME = os.path.expanduser("~")
LAKE = f"{HOME}/Desktop/MusicLake"
MON = f"{HOME}/Desktop/consolidation-monitor/progress.json"
LOSERS = f"{LAKE}/_dedup-losers"
AUD = (".flac", ".mp3", ".m4a", ".wav", ".aac", ".aiff", ".aif", ".ogg", ".opus")
QRANK = {".flac": 3, ".wav": 3, ".aiff": 3, ".aif": 3, ".m4a": 2, ".aac": 2, ".mp3": 2,
         ".ogg": 1, ".opus": 1}
JUNK = {"_serato_", "garageband", "audio music apps", "pioneerdj", "rekordbox",
        "wbru_playlists", "music"}   # ~/Music/Music = Apple Music DRM library
_norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
t0 = time.time()


def emit(**kw):
    kw["elapsed_s"] = round(time.time() - t0, 1)
    json.dump(kw, open(MON + ".tmp", "w")); os.replace(MON + ".tmp", MON)


def lake_index():
    idx = {}
    for dp, _, fns in os.walk(LAKE):
        if "_dedup-losers" in dp:
            continue
        for fn in fns:
            e = os.path.splitext(fn)[1].lower()
            if e in AUD:
                p = os.path.join(dp, fn)
                try:
                    idx.setdefault(_norm(os.path.splitext(fn)[0]),
                                   []).append((p, QRANK.get(e, 0), os.path.getsize(p)))
                except OSError:
                    pass
    return idx


def sources():
    out = []
    md = f"{HOME}/Desktop/music"
    if os.path.isdir(md):
        out.append((md, ""))
    mu = f"{HOME}/Music"
    if os.path.isdir(mu):
        for sub in os.listdir(mu):
            if _norm(sub) in JUNK or sub.startswith("."):
                continue
            p = os.path.join(mu, sub)
            if os.path.isdir(p):
                out.append((mu, sub))
    return out


def main():
    idx = lake_index()
    os.makedirs(LOSERS, exist_ok=True)
    files = []
    for root, sub in sources():
        base = os.path.join(root, sub)
        for dp, _, fns in os.walk(base):
            for fn in fns:
                if os.path.splitext(fn)[1].lower() in AUD and not os.path.islink(os.path.join(dp, fn)):
                    files.append((root, os.path.join(dp, fn)))
    total = len(files)
    emit(stage="fold", phase_label="Folding music into MusicLake", processed=0, total=total)
    moved = replaced = losers = 0
    for i, (root, src) in enumerate(files):
        e = os.path.splitext(src)[1].lower(); rank = QRANK.get(e, 0)
        sz = os.path.getsize(src) if os.path.exists(src) else 0
        key = _norm(os.path.splitext(os.path.basename(src))[0])
        rel = os.path.relpath(src, root)
        dest = os.path.join(LAKE, rel)
        existing = idx.get(key)
        if existing:
            best = max(existing, key=lambda x: (x[1], x[2]))
            if (rank, sz) > (best[1], best[2]):
                # incoming is better -> loser is the old best
                shutil.move(best[0], os.path.join(LOSERS, os.path.basename(best[0])))
                existing.remove(best)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(src, dest); existing.append((dest, rank, sz)); replaced += 1
            else:
                shutil.move(src, os.path.join(LOSERS, os.path.basename(src))); losers += 1
        else:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.exists(dest):
                dest = os.path.join(os.path.dirname(dest),
                                    f"{os.path.splitext(os.path.basename(dest))[0]}_dup{e}")
            shutil.move(src, dest); idx[key] = [(dest, rank, sz)]; moved += 1
        if (i + 1) % 25 == 0 or i + 1 == total:
            emit(stage="fold", phase_label="Folding music into MusicLake",
                 processed=i + 1, total=total, exact=moved, current=os.path.basename(src))
    # clean empty source dirs
    for root, sub in sources():
        for dp, dns, fns in os.walk(os.path.join(root, sub), topdown=False):
            try:
                if not os.listdir(dp):
                    os.rmdir(dp)
            except OSError:
                pass
    emit(stage="done", phase_label=f"Folded {moved} new + {replaced} upgrades; {losers} dupes to review",
         processed=total, total=total, exact=moved)
    print(f"moved-new={moved}  quality-upgrades={replaced}  dupe-losers(to _dedup-losers)={losers}")
    print(f"MusicLake audio now: {sum(len(v) for v in idx.values())}")
    print("MOVE_TO_LAKE DONE")


if __name__ == "__main__":
    main()
