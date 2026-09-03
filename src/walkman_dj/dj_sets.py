"""DJ Set Builder — main-genre buckets -> metadata-rich transition graph (subgenre-aware)
-> Louvain vibe clusters (~50 tracks) -> 0-hard-jump paths. See
docs/superpowers/specs/2026-08-08-dj-set-builder.md.

CLI:
  python3 -m walkman_dj.dj_sets --target 50 [--dry] [--min-genre 60]
"""
import os, sys, re, sqlite3, subprocess, argparse, tomllib, statistics as st
import numpy as np
from sklearn.cluster import KMeans

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "overnight"))
import json
from walkman_dj.sequence import order_family
from walkman_dj.camelot import camelot_edge_ok
from walkman_dj.distance import bpm_distance, bpm_window
import order_walkman_playlists as dev          # device_index, resolve, render_m3u8, safe_name, norm

SETS_JSON = os.path.expanduser("~/Desktop/DJ Sets/sets.json")   # name -> ordered track ids (for xml)

DB = os.path.join(ROOT, "data", "library.db")
CFG = os.path.join(ROOT, "config", "sequencer.toml")
TUNING = os.path.join(ROOT, "config", "dj_genre_tuning.toml")
OUT_DIR = os.path.expanduser("~/Desktop/DJ Sets")
MUSIC_ROOT = "/storage/6439-6561/Music"

# subgenre families: same subgenre=1.0, same family=0.6, else 0.3
FAMILIES = {
    "trap": ["rage", "melodic trap", "atlanta trap", "dark trap", "psychedelic trap",
             "trap soul", "pluggnb", "plugg", "southern hip hop", "trap"],
    "hiphop": ["conscious", "west coast", "chicago", "experimental hip hop", "alternative hip hop",
               "boom bap", "pop rap", "progressive rap", "hip hop", "hip-hop", "gangsta"],
    "rnb": ["alternative r&b", "contemporary r&b", "neo soul", "trap soul", "r&b", "rnb"],
    "house": ["deep house", "tech house", "soulful house", "pop house", "bass house",
              "afro house", "house", "disco house"],
    "ukg": ["2-step", "2 step", "uk garage", "speed garage", "bassline", "garage"],
    "dnb": ["jungle", "ragga", "liquid", "neurofunk", "drum & bass", "dnb", "breakbeat"],
    "techno": ["techno", "peak time", "melodic techno", "hard techno"],
    "latin": ["reggaeton", "perreo", "bachata", "salsa", "corridos", "dembow", "latin"],
    "afro": ["afrobeats", "afropop", "amapiano", "afro house", "afroswing"],
    "electronic": ["electronic", "edm", "electro", "trance", "dubstep"],
}
_SUB2FAM = {sub: fam for fam, subs in FAMILIES.items() for sub in subs}


def fam_of(subgenre):
    s = (subgenre or "").lower()
    for sub, fam in _SUB2FAM.items():
        if sub in s:
            return fam
    return None


def subgenre_affinity(a, b):
    sa, sb = (a or "").lower().strip(), (b or "").lower().strip()
    if sa and sa == sb:
        return 1.0
    fa, fb = fam_of(sa), fam_of(sb)
    if fa and fa == fb:
        return 0.6
    return 0.3


# per-genre tuning (bpm window, soft-factor weights, energy template)
def load_tuning():
    if os.path.exists(TUNING):
        with open(TUNING, "rb") as f:
            return tomllib.load(f)
    return {}

DEFAULT_TUN = dict(bpm_window_pct=0.06, bpm_floor=6.0, wk=1.0, wb=1.0, we=0.8, wv=0.6, ws=0.8,
                   template="wave")
GENRE_TUN = {
    "House": dict(bpm_window_pct=0.05, wk=1.4, we=0.7, ws=0.7, template="single_peak"),
    "Deep House": dict(bpm_window_pct=0.05, wk=1.4, template="single_peak"),
    "Tech House": dict(bpm_window_pct=0.05, wk=1.4, template="single_peak"),
    "Techno": dict(bpm_window_pct=0.04, wk=1.5, we=0.6, template="single_peak"),
    "Trance": dict(bpm_window_pct=0.05, wk=1.4, template="single_peak"),
    "UK Garage": dict(bpm_window_pct=0.06, wk=1.3, template="wave"),
    "Drum & Bass": dict(bpm_window_pct=0.05, wk=1.3, we=1.0, template="single_peak"),
    "Jungle": dict(bpm_window_pct=0.05, wk=1.2, we=1.0, template="single_peak"),
    "Trap": dict(bpm_window_pct=0.10, wk=0.6, we=1.0, ws=1.0, template="wave"),
    "Hip Hop": dict(bpm_window_pct=0.10, wk=0.6, we=1.0, ws=1.0, template="wave"),
    "Drill": dict(bpm_window_pct=0.09, wk=0.7, template="wave"),
    "R&B": dict(bpm_window_pct=0.10, wk=0.5, we=0.7, wv=0.9, template="single_peak"),
    "Afrobeats": dict(bpm_window_pct=0.06, wk=1.0, template="wave"),
    "Amapiano": dict(bpm_window_pct=0.05, wk=1.2, template="single_peak"),
    "Baile Funk": dict(bpm_window_pct=0.07, wk=0.9, template="wave"),
    "Latin": dict(bpm_window_pct=0.07, wk=0.9, template="wave"),
    "Reggaeton": dict(bpm_window_pct=0.07, wk=0.9, template="wave"),
}


def tuning_for(genre):
    t = dict(DEFAULT_TUN); t.update(GENRE_TUN.get(genre, {})); return t


# cross-genre fusion buckets — genres that share a tempo/energy zone and mix together.
# Pooled and set-built like any bucket; every transition still gated in-key + in-BPM.
FUSION = [
    ("Club Bass", ["Jersey Club", "UK Garage", "Baile Funk"]),   # ~130-145 bass-club
]


def eff_bpm_delta(a, b):
    return min(abs(a - b), abs(a - 2 * b), abs(2 * a - b))


def edge_weight(a, b, tun):
    """None if not mixable (fails key or BPM gate); else transitionability weight > 0."""
    if not camelot_edge_ok(a.get("camelot"), b.get("camelot")):
        return None
    ba, bb = a.get("bpm"), b.get("bpm")
    if not ba or not bb:
        return None
    win = max(max(ba, bb) * tun["bpm_window_pct"], tun["bpm_floor"])
    d = eff_bpm_delta(ba, bb)
    if d > win:
        return None
    conf = min(a.get("key_confidence") or 0.5, b.get("key_confidence") or 0.5)
    key = 0.5 + 0.5 * conf
    bpm = 1 - d / win
    en = 1 - abs((a.get("energy") or 0.5) - (b.get("energy") or 0.5))
    val = 1 - abs((a.get("valence") or 0.5) - (b.get("valence") or 0.5))
    sub = subgenre_affinity(a.get("subgenre"), b.get("subgenre"))
    w = tun["wk"] * key + tun["wb"] * bpm + tun["we"] * en + tun["wv"] * val + tun["ws"] * sub
    return max(0.01, w)


# ---- Stage 3: vibe clustering (k-means on the metadata feature space) ----
FEATCOLS = ["energy", "valence", "brightness", "bass_weight", "mir_danceability"]
_FAMS = sorted(FAMILIES.keys())


def feat_matrix(tracks):
    """Standardized vibe features + halftime-folded BPM + subgenre-family one-hot."""
    rows = []
    for t in tracks:
        v = [t.get(c) for c in FEATCOLS]
        bpm = t.get("bpm") or 0
        v.append(bpm / 2 if bpm >= 105 else bpm)          # fold halftime so 140≈70 cluster together
        fam = fam_of(t.get("subgenre"))
        v += [1.0 if fam == f else 0.0 for f in _FAMS]     # subgenre family drives vibe grouping
        rows.append(v)
    X = np.array(rows, dtype=float)
    with np.errstate(invalid="ignore"):
        col_mean = np.nanmean(X, axis=0)
    col_mean = np.where(np.isnan(col_mean), 0.0, col_mean)
    idx = np.where(np.isnan(X)); X[idx] = np.take(col_mean, idx[1])
    nnum = len(FEATCOLS) + 1
    mu, sd = X[:, :nnum].mean(0), X[:, :nnum].std(0); sd[sd == 0] = 1
    X[:, :nnum] = (X[:, :nnum] - mu) / sd
    X[:, nnum:] *= 1.6                                     # weight subgenre-family a touch higher
    return X


def cluster_vibe(tracks, target):
    """Carve a genre bucket into ~target-sized vibe clusters (every track assigned)."""
    n = len(tracks)
    if n <= int(target * 1.3):
        return [tracks]
    k = max(2, round(n / target))
    labels = KMeans(n_clusters=k, n_init=4, random_state=1).fit_predict(feat_matrix(tracks))
    groups = {}
    for t, l in zip(tracks, labels):
        groups.setdefault(int(l), []).append(t)
    out = []
    for c in groups.values():
        out.extend(cluster_vibe(c, target) if len(c) > int(target * 1.4) else [c])
    # merge tiny clusters (< 0.4·target) into the nearest surviving cluster by centroid
    keep = [c for c in out if len(c) >= int(target * 0.4)]
    tiny = [c for c in out if len(c) < int(target * 0.4)]
    if keep and tiny:
        cent = [feat_matrix(c).mean(0) for c in keep]
        for c in tiny:
            cc = feat_matrix(c).mean(0)
            j = min(range(len(keep)), key=lambda k_: float(np.linalg.norm(cent[k_] - cc)))
            keep[j].extend(c)
        out = keep
    elif keep:
        out = keep
    return out


def order_clean(tracks, cfg, template):
    """Order into a set with GUARANTEED 0 hard jumps: loop until the sequence has no
    residual hard cut and no isolated track (each pass strictly shrinks the set, so it
    always terminates). Parked = tracks that can't chain in-key + in-BPM to the core."""
    cur = list(tracks); parked = []
    while len(cur) > 1:
        ordered, hard, isolated = order_family(cur, cfg, template=template)
        remove = set(isolated) | {h["to"] for h in hard}
        if not remove:
            return ordered, parked
        parked += [t for t in cur if t["fingerprint"] in remove]
        cur = [t for t in cur if t["fingerprint"] not in remove]
    return cur, parked


def _vibe_name(ctracks):
    e = st.mean([t.get("energy") or 0.5 for t in ctracks])
    v = st.mean([t.get("valence") or 0.5 for t in ctracks])
    return ("Peak-Time" if e >= .55 and v >= .55 else "Dark" if e >= .55 else
            "Sunset" if v >= .55 else "Late-Night")


# subgenre labels too broad to distinguish one cluster from another within a genre
GENERIC = {"southern hip hop", "hip hop", "hip-hop", "trap", "rap", "pop rap", "r&b", "rnb",
           "contemporary r&b", "house", "uk garage", "garage", "electronic", "pop", "latin",
           "drum & bass", "dnb", "reggaeton", "afrobeats", "experimental"}


def _bpm_band(ctracks):
    b = st.median([t.get("bpm") for t in ctracks if t.get("bpm")])
    return f"{int(round(b/10)*10)}s"          # 138 -> "140s"


def dominant_sub(ctracks, genre):
    """Prefer a DISTINCTIVE subgenre (not the genre name, not a broad catch-all); else a
    vibe + BPM-band tag, so clusters get differentiated, meaningful names."""
    from collections import Counter
    subs = Counter(s.title() for t in ctracks
                   if (s := (t.get("subgenre") or "").strip().lower())
                   and s != genre.lower() and s not in GENERIC)
    if subs and subs.most_common(1)[0][1] >= 3:
        return subs.most_common(1)[0][0]
    return f"{_vibe_name(ctracks)} {_bpm_band(ctracks)}"


def compat(a, b, cfg):
    """EXACTLY the sequencer's non-hard-cut condition: in-key (Camelot) AND in strict BPM
    window (widen=False, halftime-aware). Guarantees redistribution inserts never create an
    out-of-key or out-of-BPM transition."""
    if not camelot_edge_ok(a.get("camelot"), b.get("camelot")):
        return False
    if not a.get("bpm") or not b.get("bpm"):
        return False
    win = bpm_window(a["bpm"], b["bpm"], cfg["bpm_window_pct"], cfg["bpm_window_floor"], widen=False)
    return bpm_distance(a["bpm"], b["bpm"]) <= win


def try_insert(track, seq, cfg):
    """Insert only where BOTH neighbor transitions stay valid → set keeps 0 bad transitions."""
    for i in range(len(seq) - 1):
        if compat(seq[i], track, cfg) and compat(track, seq[i + 1], cfg):
            seq.insert(i + 1, track); return True
    if seq and compat(seq[-1], track, cfg):
        seq.append(track); return True
    if seq and compat(track, seq[0], cfg):
        seq.insert(0, track); return True
    return False


def build(target=50, min_genre=60, dry=False, only_genre=None, min_set=40, max_set=90):
    with open(CFG, "rb") as f:
        cfg = tomllib.load(f)
    # STRICT per-transition: every step must be in-key (Camelot same / ±1 / relative) AND
    # in-BPM (within a beatmatchable ~8% window). We DON'T loosen the gate. Instead we get
    # wide coverage the creative way — long MONOTONIC energy/BPM journeys (below): a set
    # ramps up across a big tempo range via many small in-key/in-BPM steps, so almost
    # nothing is a true outlier while no transition is ever out of key or off-tempo.
    cfg = dict(cfg); cfg["allow_energy_boost"] = False
    cfg["bpm_window_pct"] = 0.08; cfg["bpm_window_floor"] = 6.0
    cfg["key_conf_min"] = 0.0        # NO "unreliable-key" bypass -> key mismatch is always hard
    cfg["bpm_conf_min"] = 0.0        # NO "unreliable-bpm" window-widen -> strict BPM window
    os.makedirs(OUT_DIR, exist_ok=True)
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT id,canonical_path,artist,title,duration,bpm,camelot,energy,valence,genre,"
        "subgenre,key_confidence,bpm_confidence,brightness,bass_weight,mir_danceability "
        "FROM recordings WHERE TRIM(COALESCE(genre,''))<>'' AND genre NOT IN ('Other','Alternative') "
        "AND bpm IS NOT NULL AND camelot IS NOT NULL AND canonical_path IS NOT NULL "
        "AND COALESCE(blacklisted,0)=0")]
    for r in rows:
        r["fingerprint"] = str(r["id"])
    by_genre = {}
    for r in rows:
        by_genre.setdefault(r["genre"], []).append(r)

    print(f"{len(rows)} tracks · {len(by_genre)} genres · target {target}/set\n")

    # ---- STAGE 1-4: build every set as a monotonic in-key + in-BPM journey. Collect, don't emit. ----
    all_sets = []                    # [genre, [ordered track rows]]

    def process(genre, group):
        pool = []
        for ct in cluster_vibe(group, target):
            if len(ct) < 8:
                pool += ct; continue
            ordered, parked = order_clean(ct, cfg, template="monotonic")
            pool += parked
            if len(ordered) >= 10:
                all_sets.append([genre, ordered])
            else:
                pool += ordered
        return pool

    def build_bucket(label, tracks):
        pool = process(label, tracks)
        rounds = 0
        while len(pool) >= max(15, target // 2) and rounds < 3:
            rounds += 1
            new_pool = process(label, pool)
            if len(new_pool) >= len(pool):
                break
            pool = new_pool

    genres = [only_genre] if only_genre else [g for g, ts in
              sorted(by_genre.items(), key=lambda x: -len(x[1])) if len(ts) >= min_genre]
    for genre in genres:
        print(f"■ {genre}: {len(by_genre[genre])} tracks")
        build_bucket(genre, by_genre[genre])
    if not only_genre:                                          # cross-genre fusion buckets
        for fname, members in FUSION:
            pool = [t for g in members for t in by_genre.get(g, [])]
            if len(pool) >= 20:
                print(f"■ {fname}: {len(pool)} tracks (fusion)")
                build_bucket(fname, pool)

    # ---- STAGE 5: prune to DJ-usable sizes on the real track objects (no filename remap). ----
    keep = [s for s in all_sets if len(s[1]) >= min_set]
    small = [s for s in all_sets if len(s[1]) < min_set]
    queue = [(sg, tr) for sg, rows in small for tr in rows]     # dissolved-set tracks
    for s in keep:                                              # + excess from oversized sets
        if len(s[1]) > max_set:
            queue += [(s[0], tr) for tr in s[1][max_set:]]      # a prefix of a valid chain is valid
            s[1] = s[1][:max_set]
    placed = dropped = 0
    for sg, tr in queue:                                        # redistribute: smallest same-genre first
        cand = sorted((s for s in keep if len(s[1]) < max_set), key=lambda s: (s[0] != sg, len(s[1])))
        for s in cand:
            if try_insert(tr, s[1], cfg):
                placed += 1; break
        else:
            dropped += 1
    print(f"\nprune: {len(all_sets)} built -> {len(keep)} kept · dissolved {len(small)} · "
          f"redistributed {placed} · dropped {dropped}")

    # ---- STAGE 6: name, emit (.m3u8 + .md + push), and record ids for the Rekordbox XML. ----
    devidx = {} if dry else dev.device_index()
    used_names, made, sets_json = {}, [], []
    for genre, ordered in keep:
        sub = dominant_sub(ordered, genre)
        name = f"{genre} - {sub}"
        if name in used_names:
            name = f"{genre} - {sub} · {_vibe_name(ordered)}"
        while name in used_names:
            used_names[name] += 1
            name = f"{genre} - {sub} {used_names[name] + 1}"
        used_names[name] = 1
        misses, ambig = [], []
        m3u8 = dev.render_m3u8(ordered, devidx if not dry else {}, misses, ambig) if not dry else ""
        base = os.path.join(OUT_DIR, dev.safe_name(name))
        if not dry:
            open(base + ".m3u8", "w").write(m3u8)
            open(base + ".md", "w").write(dev.render_md(name, ordered, "monotonic", []))
            subprocess.run(["adb", "push", base + ".m3u8", f"{MUSIC_ROOT}/{dev.safe_name(name)}.m3u8"],
                           capture_output=True, text=True)
        made.append((name, len(ordered)))
        sets_json.append({"name": name, "ids": [r["id"] for r in ordered]})
    if not dry:
        json.dump(sets_json, open(SETS_JSON, "w"))
    sizes = sorted(len(s[1]) for s in keep)
    print(f"{len(made)} DJ sets · sizes {sizes[0]}-{sizes[-1]} (median {sizes[len(sizes)//2]}) · "
          f"0 out-of-key + 0 out-of-BPM · sheets in {OUT_DIR}")
    return made


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=50)
    ap.add_argument("--min-genre", type=int, default=60)
    ap.add_argument("--only-genre")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    build(target=a.target, min_genre=a.min_genre, dry=a.dry, only_genre=a.only_genre)


if __name__ == "__main__":
    main()
