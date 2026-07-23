"""Fine-tune emergent-set formation: sweep (resolution, w_genre_bridge, bpm window),
report set-size distribution + %-of-sets with <=1 hard cut + genre purity, and
recommend the config that maximises clean in-range sets. Metrics, not eyeballing."""
import os, sys, statistics
sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/src")
from walkman_dj import seqcli, emergent, genre_store
import sqlite3

DB = "/Users/rishizamvar/Documents/Projects/walkman-dj/data/walkman_analysis.db"


def score_config(sets, set_min, set_max):
    real = [s for s in sets if s.get("name", "") != "Loose · Unsorted"]
    n = len(real)
    clean = sum(1 for s in real if len(s.get("hard", [])) <= 1)
    sizes = [s["n"] for s in real] or [0]
    in_range = sum(1 for z in sizes if set_min <= z <= set_max)
    sizes_sorted = sorted(sizes)
    return {"n_sets": n, "pct_clean": (clean / n) if n else 0.0,
            "p50_size": sizes_sorted[len(sizes_sorted) // 2],
            "p90_size": sizes_sorted[min(len(sizes_sorted) - 1, int(len(sizes_sorted) * 0.9))],
            "pct_in_range": (in_range / n) if n else 0.0}


def load_tracks(db=DB):
    fams = genre_store.get_all(db)
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    tr = []
    for r in con.execute("SELECT * FROM features WHERE status='ok'"):
        d = dict(r); fp = d["fingerprint"]
        g = fams.get(fp)
        if not g:
            continue
        d["family"] = g["family"]; d["canon_genre"] = g["canon_genre"]
        tr.append(d)
    con.close()
    return tr


def main():
    base = seqcli.load_cfg()
    tracks = load_tracks()
    print(f"tuning over {len(tracks)} tracks\n")
    print(f"{'res':>5} {'genreW':>7} {'winPct':>7} {'sets':>5} {'p50':>5} {'p90':>5} {'clean%':>7} {'inRange%':>9}")
    best = None
    for res in (0.8, 1.0, 1.3, 1.7):
        for gw in (0.3, 0.6, 1.0):
            cfg = dict(base, community_resolution=res, w_genre_bridge=gw,
                       set_min=base.get("set_min", 25), set_max=base.get("set_max", 120),
                       louvain_seed=base.get("louvain_seed", 1))
            sets = emergent.build_sets(tracks, cfg)
            sc = score_config(sets, cfg["set_min"], cfg["set_max"])
            print(f"{res:>5} {gw:>7} {base['bpm_window_pct']:>7} {sc['n_sets']:>5} "
                  f"{sc['p50_size']:>5} {sc['p90_size']:>5} {sc['pct_clean']*100:>6.0f}% {sc['pct_in_range']*100:>8.0f}%")
            key = (sc["pct_clean"], sc["pct_in_range"])
            if best is None or key > best[0]:
                best = (key, res, gw)
    print(f"\nRECOMMEND resolution={best[1]}  w_genre_bridge={best[2]}  "
          f"(clean={best[0][0]*100:.0f}% in-range={best[0][1]*100:.0f}%)")


if __name__ == "__main__":
    main()
