#!/usr/bin/env python3
"""Curate + harmonically order + deploy ONE playlist to the Walkman.
Matches target track queries -> lake files (with BPM/Camelot/energy features),
orders via the Set Sequencer (Camelot-adjacent + energy arc), writes ordered local
paths, and calls the walkman-playlist-push skill.

Usage:
  curate_playlist.py --name "Future" --queries future_tracks.txt [--size 25]
  curate_playlist.py --name "UK Garage" --genre uk_garage [--size 22]  (uses researched JSON, matches _playlist_tracks)
"""
import os, re, sys, json, argparse, sqlite3, subprocess
ROOT = "/Users/rishizamvar/Documents/Projects/walkman-dj"
sys.path.insert(0, ROOT + "/src")
from walkman_dj.sequence import order_playlist, assign_arc

LAKE = os.path.expanduser("~/Desktop/MusicLake")
DB = ROOT + "/data/walkman_analysis.db"
PUSH = os.path.expanduser("~/.claude/skills/walkman-playlist-push/push_playlist.py")
AUD = (".flac", ".mp3", ".m4a", ".wav", ".aac")
_norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
_toks = lambda q: [t for t in re.findall(r"[a-z0-9]+", q.lower()) if len(t) > 2]


def lake_files(subdir=None):
    root = os.path.join(LAKE, subdir) if subdir else LAKE
    out = {}
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if os.path.splitext(fn)[1].lower() in AUD and not os.path.islink(os.path.join(dp, fn)):
                out.setdefault(_norm(os.path.splitext(fn)[0]), os.path.join(dp, fn))
    return out


def feat_by_base():
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    m = {}
    for r in con.execute("SELECT t.filename, f.bpm, f.camelot, f.energy FROM tracks t "
                         "JOIN features f USING(fingerprint) WHERE f.status='ok' AND f.bpm>0"):
        k = _norm(os.path.splitext(r["filename"] or "")[0])
        if k and k not in m:
            m[k] = (r["bpm"], r["camelot"] or "1A", r["energy"] or 0.5)
    con.close(); return m


def best_match(query, files):
    toks = _toks(query); need = max(2, len(toks) - 1)
    best = None; bestscore = 0
    for base, path in files.items():
        score = sum(1 for t in toks if t in base)
        if score >= need and score > bestscore:
            best, bestscore = (base, path), score
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--queries"); ap.add_argument("--genre")
    ap.add_argument("--size", type=int, default=22)
    a = ap.parse_args()

    if a.genre:
        j = json.load(open(f"{ROOT}/overnight/loop/playlists/{a.genre}.json"))
        queries = [c["q"] for c in j["candidates"]]
        files = {**lake_files(f"_playlist_tracks/{a.genre}"), **lake_files()}
    else:
        queries = [l.strip() for l in open(a.queries) if l.strip()]
        files = lake_files()
    feats = feat_by_base()

    members = []; used = set(); missed = []
    for q in queries:
        m = best_match(q, files)
        if not m or m[1] in used:
            missed.append(q); continue
        base, path = m; used.add(path)
        bpm, cam, en = feats.get(base, (120.0, "1A", 0.5))
        members.append({"path": path, "bpm": bpm, "camelot": cam, "energy": en,
                        "title": os.path.splitext(os.path.basename(path))[0]})
        if len(members) >= a.size:
            break
    if len(members) < 5:
        print(f"only {len(members)} matched — aborting"); sys.exit(1)

    arc = assign_arc([m["energy"] for m in members])
    seq = order_playlist(members, arc)
    tf = f"/tmp/_curate_{_norm(a.name)}.txt"
    open(tf, "w").write("\n".join(m["path"] for m in seq))
    print(f"[{a.name}] matched {len(members)}/{len(queries)}, missed {len(missed)} -> ordered, deploying")
    r = subprocess.run(["python3", PUSH, "--name", a.name, "--tracks", tf],
                       capture_output=True, text=True)
    print(r.stdout.strip()); print(r.stderr.strip()[:200] if r.stderr else "")
    # record
    rec = {"name": a.name, "size": len(seq), "missed": missed[:20],
           "tracks": [m["title"] for m in seq]}
    json.dump(rec, open(f"{ROOT}/overnight/loop/playlists/deployed_{_norm(a.name)}.json", "w"), indent=1)


if __name__ == "__main__":
    main()
