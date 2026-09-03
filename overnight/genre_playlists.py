"""Massive per-genre playlists — every track in a genre, ordered as one continuous journey.

Different job from dj_sets. A DJ set is ~50 tracks with a HARD in-key + in-BPM gate, and
anything that can't chain gets parked. A whole-genre playlist has to KEEP every track, so
this walks the same `dj_sets.edge_weight` scoring greedily but falls back to the least-bad
step when no gated edge exists: the harmonic ordering is a strong preference here, not a
guarantee. Rough steps are counted and reported so you know what you're getting.

Tracks with no bpm/camelot can't be placed in the walk at all; they're appended at the end
rather than dropped, so the playlist really is the whole genre.

Writes ~/Desktop/DJ Sets/genres.json ([{name, ids}]) for rekordbox_xml, plus a .m3u8 each.
The m3u8s carry LOCAL absolute paths (what rekordbox and `crate export` want), not the
device-relative paths order_walkman_playlists.render_m3u8 produces for the Walkman.
"""
import os, sys, re, json, sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from walkman_dj import dj_sets   # noqa: E402

DB = os.path.join(HERE, "..", "data", "library.db")
OUT_DIR = os.path.expanduser("~/Desktop/DJ Sets")
GENRES_JSON = os.path.join(OUT_DIR, "genres.json")
MIN_TRACKS = 60


def walk(tracks, tun):
    """Greedy best-transition path. Never drops a track: when nothing clears the key/BPM
    gate it takes the smallest effective BPM jump instead and counts it as a rough step."""
    remaining = sorted(tracks, key=lambda t: t.get("energy") or 0.5)
    seq = [remaining.pop(0)]
    rough = 0
    while remaining:
        last = seq[-1]
        best = best_w = None
        for t in remaining:
            w = dj_sets.edge_weight(last, t, tun)
            if w is not None and (best_w is None or w > best_w):
                best, best_w = t, w
        if best is None:                       # no mixable edge left — take the least-bad
            rough += 1
            lb = last.get("bpm") or 0
            best = min(remaining, key=lambda t: dj_sets.eff_bpm_delta(lb, t.get("bpm") or 0))
        seq.append(best)
        remaining.remove(best)
    return seq, rough


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT id,canonical_path,artist,title,duration,bpm,camelot,energy,valence,genre,"
        "subgenre,key_confidence,bpm_confidence FROM recordings "
        "WHERE TRIM(COALESCE(genre,''))<>'' AND genre NOT IN ('Other','Alternative') "
        "AND canonical_path IS NOT NULL AND COALESCE(blacklisted,0)=0")]
    for r in rows:
        r["fingerprint"] = str(r["id"])

    by_genre = {}
    for r in rows:
        by_genre.setdefault(r["genre"], []).append(r)

    out = []
    for genre, group in sorted(by_genre.items(), key=lambda kv: -len(kv[1])):
        if len(group) < MIN_TRACKS:
            continue
        mixable = [t for t in group if t.get("bpm") and t.get("camelot")]
        tail = sorted((t for t in group if t not in mixable),
                      key=lambda t: (t.get("artist") or "", t.get("title") or ""))
        ordered, rough = walk(mixable, dj_sets.tuning_for(genre)) if mixable else ([], 0)
        ordered += tail
        base = os.path.join(OUT_DIR, re.sub(r'[<>:"|?*\\/]', "_", genre))
        with open(base + ".m3u8", "w") as fh:
            fh.write("#EXTM3U\n")
            for t in ordered:
                fh.write(f"#EXTINF:{int(t.get('duration') or 0)},"
                         f"{t.get('artist') or ''} - {t.get('title') or ''}\n")
                fh.write(t["canonical_path"] + "\n")
        out.append({"name": genre, "ids": [t["id"] for t in ordered]})
        print(f"  {genre:<14} {len(ordered):>5} tracks · {rough} rough steps "
              f"({100 * rough / max(1, len(ordered) - 1):.1f}%) · {len(tail)} unmixable appended")
    json.dump(out, open(GENRES_JSON, "w"))
    print(f"\n{len(out)} genre playlists · {sum(len(g['ids']) for g in out)} entries -> {GENRES_JSON}")


if __name__ == "__main__":
    main()
