#!/usr/bin/env python3
"""Prune DJ sets smaller than MIN tracks: dissolve them and try to redistribute each of
their tracks into a surviving set where it mixes (in-key + in-BPM) — inserted only where
BOTH neighbor transitions stay valid, so every set keeps 0 hard jumps. Tracks that fit no
set are dropped from playlists entirely.

Rewrites ~/Desktop/DJ Sets/*.m3u8 (+ .md), re-pushes to the Walkman (deleting removed
playlists), and leaves rekordbox.xml regeneration to rekordbox_xml.py.

  python3 prune_small_sets.py [--min 40] [--dry]
"""
import os, sys, re, glob, sqlite3, subprocess, argparse, unicodedata
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from walkman_dj.camelot import camelot_edge_ok

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "library.db")
SETS = os.path.expanduser("~/Desktop/DJ Sets")
MUSIC_ROOT = "/storage/6439-6561/Music"
AUDIO = (".flac", ".mp3", ".m4a", ".wav", ".aiff", ".aif", ".ogg", ".opus")
BPM_WIN = 0.08


def norm(name):
    n = unicodedata.normalize("NFC", os.path.basename(name)).lower().strip().lstrip("._")
    n = re.sub(r"\.[a-z0-9]{2,4}$", "", n)
    return re.sub(r'[<>:"|?*\\]', "_", n).strip()


def compat(a, b):
    """in-key AND in-BPM (halftime-aware) — the mix gate."""
    if not camelot_edge_ok(a["camelot"], b["camelot"]):
        return False
    x, y = a["bpm"], b["bpm"]
    if not x or not y:
        return False
    d = min(abs(x - y), abs(x - 2 * y), abs(2 * x - y))
    return d <= max(max(x, y) * BPM_WIN, 6)


def lib_index():
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    idx = {}
    for r in con.execute("SELECT id,canonical_path,artist,title,bpm,camelot,energy,genre,duration "
                         "FROM recordings WHERE canonical_path IS NOT NULL"):
        idx[norm(r["canonical_path"])] = dict(r)
    return idx


def device_index():
    r = subprocess.run(["adb", "shell", f"find {MUSIC_ROOT!r} -type f"], capture_output=True, text=True)
    idx = {}
    for p in r.stdout.splitlines():
        p = p.strip()
        if p.lower().endswith(AUDIO) and p.startswith(MUSIC_ROOT + "/"):
            idx.setdefault(norm(p), p[len(MUSIC_ROOT) + 1:])
    return idx


def try_insert(track, seq):
    """Insert track into an ordered set only if it keeps every transition valid. Returns True."""
    if not seq:
        return False
    for i in range(len(seq) - 1):                         # between neighbors: both sides must hold
        if compat(seq[i], track) and compat(track, seq[i + 1]):
            seq.insert(i + 1, track); return True
    if compat(seq[-1], track):                            # append to the tail
        seq.append(track); return True
    if compat(track, seq[0]):                             # prepend to the head
        seq.insert(0, track); return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=40)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    lib = lib_index()

    sets = []                                             # (name, genre, [rows])
    for f in sorted(glob.glob(os.path.join(SETS, "*.m3u8"))):
        name = os.path.basename(f)[:-5]
        genre = name.split(" - ")[0]
        rows = []
        for ln in open(f, encoding="utf-8"):
            ln = ln.strip()
            if ln.startswith("#") or not ln:
                continue
            r = lib.get(norm(ln))
            if r:
                rows.append(r)
        if rows:
            sets.append([name, genre, rows])

    MAXSIZE = 90                                          # don't let a set balloon
    keep = [s for s in sets if len(s[2]) >= a.min]
    small = [s for s in sets if len(s[2]) < a.min]
    pool = [(sg, tr) for _, sg, rows in small for tr in rows]     # tracks from dissolved sets
    for s in keep:                                                # + excess from oversized sets
        if len(s[2]) > MAXSIZE:
            pool += [(s[1], tr) for tr in s[2][MAXSIZE:]]         # prefix stays a valid chain
            s[2] = s[2][:MAXSIZE]
    print(f"{len(sets)} sets -> keep {len(keep)} (>={a.min}), dissolve {len(small)}, "
          f"{len(pool)} tracks to redistribute (cap {MAXSIZE})")

    placed = dropped = 0
    for sg, tr in pool:
        cand = [s for s in keep if len(s[2]) < MAXSIZE]           # not-full, same-genre + smallest first
        cand.sort(key=lambda s: (s[1] != sg, len(s[2])))
        for s in cand:
            if try_insert(tr, s[2]):
                placed += 1; break
        else:
            dropped += 1
    print(f"redistributed {placed} tracks · dropped {dropped} (fit no set)")

    if a.dry:
        print("DRY — no files/device changed"); return

    # rewrite local sheets: remove dissolved sets, rewrite keeps
    for name, _, _ in small:
        for ext in (".m3u8", ".md"):
            p = os.path.join(SETS, name + ext)
            if os.path.exists(p): os.remove(p)
    dev = device_index()
    pushed = 0
    # clear device set playlists (keep DJ Mixes), then push updated keeps
    subprocess.run(["adb", "shell",
                    f"find {MUSIC_ROOT!r} -maxdepth 1 -name '*.m3u8' ! -name 'DJ Mixes.m3u8' -delete"],
                   capture_output=True, text=True)
    for name, _, rows in keep:
        m3u = ["#EXTM3U"]; md = [f"# {name}", f"_{len(rows)} tracks_", ""]
        for i, r in enumerate(rows):
            rel = dev.get(norm(r["canonical_path"]))
            if not rel:
                continue
            m3u.append(f"#EXTINF:{int(r['duration'] or 0)},{(r['artist'] or '')} - {(r['title'] or '')}".rstrip(" -"))
            m3u.append(rel)
            md.append(f"{i+1:>2}. **{r['title'] or '?'}** — {r['artist'] or ''} · "
                      f"{round(r['bpm']) if r['bpm'] else '?'} BPM · {r['camelot'] or '?'} "
                      f"· E={float(r['energy'] or 0):.2f}")
        base = os.path.join(SETS, name)
        open(base + ".m3u8", "w").write("\n".join(m3u) + "\n")
        open(base + ".md", "w").write("\n".join(md) + "\n")
        pr = subprocess.run(["adb", "push", base + ".m3u8", f"{MUSIC_ROOT}/{name}.m3u8"],
                            capture_output=True, text=True)
        pushed += (pr.returncode == 0)
    print(f"pushed {pushed}/{len(keep)} sets to device")


if __name__ == "__main__":
    main()
