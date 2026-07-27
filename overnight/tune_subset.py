#!/usr/bin/env python3
"""Supervised weight tuning on a CONSTRAINED ground-truth subset.

Pick ~N songs from wildly-different, confidently-labeled families (the known-good
grouping), then sweep the algorithm's weights and score each config by how well the
emergent communities RECOVER those known genres — Adjusted Rand Index (ARI) + genre
homogeneity. Only the grouping stage runs (graph + Louvain, no ordering), so each
config is ~0.08s at n~350 → hundreds of configs in seconds. Reports the peak config."""
import os, sys, time, random, sqlite3
sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/src")
from walkman_dj import libgraph, community, seqcli, genre_store
from sklearn.metrics import adjusted_rand_score, homogeneity_score, completeness_score

DB = "/Users/rishizamvar/Documents/Projects/walkman-dj/data/walkman_analysis.db"
# wildly-different, unambiguous families = the ground-truth playlists
TRUTH_FAMILIES = ["tech_house", "atlanta_trap", "alt_rnb", "baile_edits",
                  "afrobeats", "jersey_juke", "global_downtempo", "organic_house"]
PER_FAMILY = 45
SEED = 7


def load_subset():
    fams = genre_store.get_all(DB)
    # keep highest-confidence tracks per family
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    feats = {r["fingerprint"]: dict(r) for r in con.execute("SELECT * FROM features WHERE status='ok'")}
    con.close()
    by_fam = {f: [] for f in TRUTH_FAMILIES}
    for fp, g in fams.items():
        if g["family"] in by_fam and fp in feats:
            d = dict(feats[fp]); d["family"] = g["family"]; d["canon_genre"] = g["canon_genre"]
            d["_conf"] = g.get("confidence") or 0
            by_fam[g["family"]].append(d)
    rng = random.Random(SEED)
    subset = []
    for f, rows in by_fam.items():
        rows.sort(key=lambda r: -r["_conf"])                 # confident first
        pool = rows[:max(PER_FAMILY * 2, PER_FAMILY)]
        rng.shuffle(pool)
        subset.extend(pool[:PER_FAMILY])
    return subset


def group(tracks, cfg):
    """Grouping stage only (no ordering). Returns fingerprint -> community id."""
    G = libgraph.build_graph(tracks, cfg)
    comms, loose = community.detect(G, set_min=cfg["set_min"], set_max=cfg["set_max"],
                                    resolution=cfg["community_resolution"], seed=cfg["louvain_seed"])
    label = {}
    for i, c in enumerate(comms):
        for fp in c:
            label[fp] = i
    nxt = len(comms)
    for fp in loose:                                         # each loose track its own cluster
        label[fp] = nxt; nxt += 1
    return label


def score(tracks, label):
    y_true = [t["family"] for t in tracks]
    y_pred = [label.get(t["fingerprint"], -1) for t in tracks]
    return (adjusted_rand_score(y_true, y_pred),
            homogeneity_score(y_true, y_pred),
            completeness_score(y_true, y_pred),
            len(set(y_pred)))


def main():
    base = seqcli.load_cfg()
    tracks = load_subset()
    print(f"ground-truth subset: {len(tracks)} tracks over {len(TRUTH_FAMILIES)} families "
          f"({PER_FAMILY}/family target)\n")
    grid_gw = [1.0, 2.0]
    grid_res = [1.7, 2.2, 2.8, 3.5, 4.5]
    grid_ej = [0.3, 0.5]
    print(f"{'gW':>5} {'res':>5} {'eJump':>6} {'#comm':>6} {'ARI':>6} {'homog':>6} {'compl':>6}")
    best = None; t0 = time.time(); runs = 0
    for gw in grid_gw:
        for res in grid_res:
            for ej in grid_ej:
                cfg = dict(base, w_genre_bridge=gw, community_resolution=res, w_energy_jump=ej,
                           set_min=base.get("set_min", 10), set_max=base.get("set_max", 120),
                           louvain_seed=base.get("louvain_seed", 1))
                lab = group(tracks, cfg); runs += 1
                ari, hom, com, nc = score(tracks, lab)
                if best is None or ari > best[0]:
                    best = (ari, gw, res, ej, hom, com, nc)
    dt = time.time() - t0
    # reprint the best plus a compact view: for the winning eJump, show the gw×res grid
    ari, gw, res, ej, hom, com, nc = best
    for gwx in grid_gw:
        for resx in grid_res:
            cfg = dict(base, w_genre_bridge=gwx, community_resolution=resx, w_energy_jump=ej,
                       set_min=base.get("set_min", 10), set_max=base.get("set_max", 120),
                       louvain_seed=base.get("louvain_seed", 1))
            a, h, c, n = score(tracks, group(tracks, cfg))
            mark = "  <<<" if (gwx == gw and resx == res) else ""
            print(f"{gwx:>5} {resx:>5} {ej:>6} {n:>6} {a:>6.3f} {h:>6.3f} {c:>6.3f}{mark}")
    print(f"\n{runs} configs in {dt:.1f}s ({dt/runs*1000:.0f} ms/config)")
    print(f"PEAK: w_genre_bridge={gw}  community_resolution={res}  w_energy_jump={ej}")
    print(f"      ARI={ari:.3f}  homogeneity={hom:.3f}  completeness={com:.3f}  #communities={nc}")


if __name__ == "__main__":
    main()
