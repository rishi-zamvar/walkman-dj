#!/usr/bin/env python3
"""Slim the Walkman to a curated set: the 10 most-DIFFERENT genre playlists (largest
set per distinct genre) + keep all vibe/cultural playlists. Remove the rest of the
redundant emergent genre sets. The full library lives on the USB source-of-truth."""
import os, re, sqlite3, subprocess, sys
sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/src")
sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/overnight")
from walkman_dj import emergent, seqcli, genre_store
import deploy_sets

DEV = "/storage/6439-6561/Music"
DB = "/Users/rishizamvar/Documents/Projects/walkman-dj/data/walkman_analysis.db"
# 10 maximally-distinct genres to keep one playlist each (largest)
KEEP_GENRES = ["Tech House", "Atlanta Trap", "Baile Funk", "UK Garage", "Jersey Club",
               "Alt R&B", "Afrobeats", "Lyrical Hip-Hop", "Electro", "Downtempo"]
EM = re.compile(r' · \d+ · (Peak|Warm|Deep)$')


def build_sets():
    fams = genre_store.get_all(DB)
    buck = {fp: b for fp, b in sqlite3.connect(DB).execute("select fingerprint,bucket from track_bucket")}
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    tr = []
    for r in con.execute("select * from features where status='ok'"):
        d = dict(r); g = fams.get(d["fingerprint"])
        if not g:
            continue
        d["family"] = g["family"]; d["canon_genre"] = g["canon_genre"]; d["bucket"] = buck.get(d["fingerprint"])
        tr.append(d)
    con.close()
    return emergent.build_sets(tr, seqcli.load_cfg())


def main():
    sets = build_sets()
    # largest set per keep-genre
    keepers = {}
    for s in sets:
        for g in KEEP_GENRES:
            if s["name"].startswith(g + " ·"):
                if g not in keepers or s["n"] > keepers[g][1]:
                    keepers[g] = (s["name"], s["n"])
    keep_names = {deploy_sets._norm(v[0]) for v in keepers.values()}
    print("KEEPING 10 genre playlists:")
    for g, (nm, n) in keepers.items():
        print(f"  {nm}  ({n} tracks)")
    # prune device: delete emergent-pattern playlists not in keep_names; keep vibe (non-emergent)
    r = subprocess.run(["adb", "shell", f"find {DEV} -maxdepth 1 -iname '*.m3u8'"], capture_output=True, text=True)
    dev = [l.strip() for l in r.stdout.replace("\r", "").splitlines() if l.strip()]
    deleted = kept_vibe = 0
    for remote in dev:
        name = os.path.basename(remote)[:-5]
        if not EM.search(name):
            kept_vibe += 1; continue                 # vibe/cultural -> keep
        if deploy_sets._norm(name) in keep_names:
            continue                                 # a keeper genre set
        subprocess.run(["adb", "shell", f'rm "{remote}"'], capture_output=True); deleted += 1
    r = subprocess.run(["adb", "shell", f"find {DEV} -maxdepth 1 -iname '*.m3u8' | wc -l"], capture_output=True, text=True)
    print(f"\nremoved {deleted} redundant genre sets; kept {kept_vibe} vibe/cultural + 10 genre")
    print(f"playlists on Walkman now: {r.stdout.strip()}")
    print("WALKMAN_LEAN DONE")


if __name__ == "__main__":
    main()
