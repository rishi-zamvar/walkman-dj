"""Profile-driven DJ set builder + quality evaluation + iteration harness.

Each of the 8 blueprint sets is built by fitting tracks to that set's centroid in the
*discriminative* feature space (the spectral/groove/recognizability axes that actually
separate genres), pruning profile outliers, gating recognizability for party sets, then
ordering harmonically (Camelot + BPM + energy arc). `evaluate()` scores each set so the
algorithm can be tuned iteratively — coherence, distinctness (silhouette), mixability,
recognizability. Run `python3 src/walkman_dj/set_builder.py` for the iteration report.
"""
import os, sys, json, math, re, statistics as st
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from walkman_dj.camelot import camelot_edge_ok

DB = os.path.expanduser("~/Documents/Projects/walkman-dj/data/library.db")
BLUE = json.load(open("/tmp/playlist_blueprint.json"))

# discriminative distance features + default weights (seeded from CV separation power)
FEATS = ["sub_bass", "low_mid", "air", "perc_frac", "onset_rate",
         "speechiness", "bpm", "energy", "valence", "acousticness"]
WDEF = {"sub_bass": 1.3, "low_mid": 1.5, "air": 1.6, "perc_frac": 1.4, "onset_rate": 0.7,
        "speechiness": 1.5, "bpm": 1.2, "energy": 0.6, "valence": 0.3, "acousticness": 1.0}
EXTRA = ["year", "is_vocal"]             # soft context axes, off (weight 0) unless a config asks
ALLFEATS = FEATS + EXTRA
PARTY = {"function", "y2k", "rage"}     # sets where recognizability matters
MAINSTREAM = {"function", "rnb"}        # sing-along sets: HARD popularity gate — only well-known songs pass


def load():
    """All analysable tracks with a z-normalized discriminative vector. Returns
    (tracks, zstats). deezer_rank z-scored separately (median-imputed where absent)."""
    c = sqlite3.connect(DB)
    xtra = ["deezer_rank", "genre", "subgenre", "year", "is_vocal", "loudness_db",
            "key_confidence", "mix_in", "mix_out", "essentia_danceability", "tempo_cv"]
    cols = "id,artist,title,canonical_path,duration,camelot," + ",".join(FEATS) + "," + ",".join(xtra)
    cond = " and ".join(f"{f} is not null" for f in FEATS) + " and camelot is not null and canonical_path is not null"
    rows = list(c.execute(f"select {cols} from recordings where {cond}"))
    keys = cols.split(",")
    tracks = [dict(zip(keys, r)) for r in rows]
    zst = {}
    for f in FEATS:
        vals = [t[f] for t in tracks]
        zst[f] = (st.mean(vals), st.pstdev(vals) or 1.0)
    dz = [t["deezer_rank"] for t in tracks if t["deezer_rank"] is not None]
    dz_med = st.median(dz); zst["deezer_rank"] = (st.mean(dz), st.pstdev(dz) or 1.0)
    # soft context axes (median-imputed so they never shrink the pool): era + vocal presence
    yv = [t["year"] for t in tracks if t.get("year")]
    y_med = st.median(yv); zst["year"] = (st.mean(yv), st.pstdev(yv) or 1.0)
    vv = [t["is_vocal"] for t in tracks if t.get("is_vocal") is not None]
    v_med = st.median(vv) if vv else 0.5; zst["is_vocal"] = (st.mean(vv) if vv else 0.5, st.pstdev(vv) or 1.0)
    for t in tracks:
        t["_z"] = {f: (t[f] - zst[f][0]) / zst[f][1] for f in FEATS}
        t["_z"]["year"] = ((t["year"] if t.get("year") else y_med) - zst["year"][0]) / zst["year"][1]
        t["_z"]["is_vocal"] = ((t["is_vocal"] if t.get("is_vocal") is not None else v_med) - zst["is_vocal"][0]) / zst["is_vocal"][1]
        d = t["deezer_rank"] if t["deezer_rank"] is not None else dz_med
        t["_recog"] = d
        t["_recogz"] = (d - zst["deezer_rank"][0]) / zst["deezer_rank"][1]
    return tracks, zst


def _w(w, f):
    return w.get(f, 1.0 if f in FEATS else 0.0)   # EXTRA axes off unless a config weights them


def centroid(tracks):
    return {f: st.median([t["_z"][f] for t in tracks]) for f in ALLFEATS}


def wdist(z, cen, w):
    return math.sqrt(sum(_w(w, f) * (z[f] - cen[f]) ** 2 for f in ALLFEATS))


def pool_for(set_def, tracks):
    """genre-gated candidate pool for a set (its blueprint WHERE, re-checked in python
    against the loaded rows via a marker query)."""
    c = sqlite3.connect(DB)
    ids = {r[0] for r in c.execute(f"select id from recordings where {set_def['where']}")}
    return [t for t in tracks if t["id"] in ids]


def order_harmonic(members, cfg, energy_template="rise"):
    """Greedy path that walks the strongest transitions (edge_quality: key+BPM+loudness+
    spectral+groove), follows an energy arc, and spaces out vocal-heavy tracks so lyrics
    don't clash in the blend. Returns (ordered, good_transition_fraction)."""
    if not members:
        return [], 0.0
    remaining = list(members)
    n = len(remaining)
    bar = cfg.get("edge_bar", 0.45)

    def etarget(i):  # 0..1 position -> target energy
        p = i / max(1, n - 1)
        if energy_template == "peak":
            return 1 - abs(p - 0.62) / 0.62      # rise to ~62% then fall
        if energy_template == "wave":
            return 0.5 + 0.4 * math.sin(p * math.pi * 2)
        return 0.35 + 0.55 * p                    # steady rise

    # seed: lowest-energy track (or for peak, a mid banger — keep simple: lowest energy)
    remaining.sort(key=lambda t: t.get("energy") or 0.5)
    seq = [remaining.pop(0)]
    good = 0

    def is_vox(t):
        return (t.get("is_vocal") or 0) > 0.5

    while remaining:
        last = seq[-1]
        tpos = etarget(len(seq))
        quals = [(edge_quality(last, t, cfg), t) for t in remaining]
        strong = [(q, t) for q, t in quals if q >= bar]
        pool = strong if strong else quals            # fall back if nothing clears the bar

        def cscore(qt):
            q, t = qt
            en_pen = abs((t.get("energy") or 0.5) - tpos)          # follow the energy arc
            vox_pen = 0.25 if (is_vox(last) and is_vox(t)) else 0  # avoid vocal-on-vocal
            return q - 0.6 * en_pen - vox_pen                       # maximise: quality first

        q, nxt = max(pool, key=cscore)
        if q >= bar:
            good += 1
        seq.append(nxt); remaining.remove(nxt)
    return seq, good / max(1, len(seq) - 1)


JUNK_TITLE = re.compile(r"\b(skit|interlude|intro|outro|snippet|prelude|reprise|freestyle intro)\b", re.I)


def rejected_static(t, set_def, cfg):
    """Deterministic reject rules — a track is OUT of a set if any fires. No cap: everything
    else that fits the profile is admitted."""
    title = t.get("title") or ""
    if JUNK_TITLE.search(title):                       # skits / interludes / outros
        return "junk"
    dur = t.get("duration") or 0
    if dur and (dur < cfg.get("min_dur", 95) or dur > cfg.get("max_dur", 480)):
        return "duration"                              # too-short snippets / too-long edits
    if set_def["id"] in PARTY and cfg.get("recog_floor"):
        r = t.get("deezer_rank")                       # only reject on KNOWN low recognizability
        if r is not None and r < cfg["recog_floor"]:
            return "obscure"
    if set_def["id"] in MAINSTREAM and cfg.get("mainstream_floor"):
        # HARD gate: a sing-along set only carries mainstream/known songs. No popularity
        # score -> can't prove it's known -> OUT (per "amongst songs with a popularity score").
        r = t.get("deezer_rank")
        if r is None or r < cfg["mainstream_floor"]:
            return "not_mainstream"
    kc = t.get("key_confidence")                        # unreliable key -> harmonic match is a guess
    if kc is not None and kc < cfg.get("key_conf_min", 0.35):
        return "low_key_conf"
    tcv = t.get("tempo_cv")                             # unstable tempo -> won't hold a beatmatch
    if tcv is not None and tcv > cfg.get("tempo_cv_max", 0.14):
        return "tempo_drift"
    dance = t.get("essentia_danceability")             # ballad/skit in a floor set
    if cfg.get("dance_floor") and dance is not None and dance < cfg["dance_floor"]:
        return "undanceable"
    if set_def["id"] in PARTY and cfg.get("rnb_energy_floor"):
        # slow R&B/soul slow-jams kill a party floor — but hype trap/hip-hop can read
        # low-energy too, so only floor the *sung R&B-type* tracks (genre/subgenre + not-rapped).
        gs = ((t.get("genre") or "") + " " + (t.get("subgenre") or "")).lower()
        rnbish = any(k in gs for k in ("r&b", "rnb", "soul"))
        e = t.get("energy"); sp = t.get("speechiness")
        if rnbish and e is not None and e < cfg["rnb_energy_floor"] and (sp is None or sp < 0.4):
            return "slow_rnb"
    return None


def edge_quality(a, b, cfg):
    """Transition quality in [0,1]. Hard gates: harmonically compatible + tempo-bridgeable.
    Soft score blends BPM closeness, loudness continuity, spectral balance, and groove feel —
    a same-key/same-BPM pair still scores low if the mix would clash sonically. Returns 0 if
    the hard gates fail."""
    allow_boost = cfg.get("allow_boost", False)
    if not camelot_edge_ok(a.get("camelot"), b.get("camelot"), allow_boost):
        return 0.0
    ba, bb = a.get("bpm"), b.get("bpm")
    if not ba or not bb:
        return 0.0
    lo, hi = min(ba, bb), max(ba, bb)
    bpm_tol = cfg.get("bpm_tol", 0.08)
    within = (hi - lo) / lo <= bpm_tol
    octave = abs(hi / lo - 2) <= 0.06
    if not (within or octave):
        return 0.0
    bpm_s = 1.0 if octave else 1 - ((hi - lo) / lo) / bpm_tol
    # loudness continuity (dB gap), tolerance ~5 dB
    la, lb = a.get("loudness_db"), b.get("loudness_db")
    loud_s = 1 - min(1.0, abs(la - lb) / cfg.get("loud_tol", 5.0)) if la is not None and lb is not None else 0.7
    # spectral balance clash (z-distance over sub_bass/low_mid/air)
    spec = math.sqrt(sum((a["_z"][f] - b["_z"][f]) ** 2 for f in ("sub_bass", "low_mid", "air")))
    spec_s = 1 - min(1.0, spec / cfg.get("spec_tol", 2.2))
    # groove feel clash (z-distance over perc_frac/onset_rate)
    grv = math.sqrt(sum((a["_z"][f] - b["_z"][f]) ** 2 for f in ("perc_frac", "onset_rate")))
    grv_s = 1 - min(1.0, grv / cfg.get("grv_tol", 2.0))
    return 0.30 * bpm_s + 0.25 * loud_s + 0.25 * spec_s + 0.20 * grv_s


def transition_prune(members, cfg, min_degree=1):
    """Prune orphans + weak links: an edge only counts if its quality clears `edge_bar`, so a
    track whose BEST possible transition is still poor becomes an orphan. Keeps the largest
    connected component of good edges. Returns (survivors, orphans)."""
    bar = cfg.get("edge_bar", 0.45)
    n = len(members)
    adj = {i: set() for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            if edge_quality(members[i], members[j], cfg) >= bar:
                adj[i].add(j); adj[j].add(i)
    # k-core: iteratively drop nodes under min_degree
    alive = set(range(n))
    changed = True
    while changed:
        changed = False
        for i in list(alive):
            if len(adj[i] & alive) < min_degree:
                alive.discard(i); changed = True
    # keep the largest connected component (one continuous mixable network)
    seen, comps = set(), []
    for s in alive:
        if s in seen:
            continue
        stack, c = [s], set()
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x); c.add(x)
            stack += [y for y in adj[x] & alive if y not in seen]
        comps.append(c)
    keep = max(comps, key=len) if comps else set()
    return [members[i] for i in keep], [members[i] for i in range(n) if i not in keep]


def build_set(set_def, tracks, cfg):
    pool = pool_for(set_def, tracks)
    if len(pool) < 8:
        return None
    w = cfg.get("weights", WDEF)

    # dedupe the genre pool (never the same song twice), keep the higher-recognizability copy
    seen, dpool = set(), []
    for t in sorted(pool, key=lambda x: -(x["_recog"] or 0)):
        k = (re.sub(r"[^a-z0-9]", "", (t["artist"] or "").lower()),
             re.sub(r"[^a-z0-9]", "", re.sub(r"\(.*?\)", "", t["title"] or "").lower()))
        if k in seen:
            continue
        seen.add(k); dpool.append(t)

    # mainstream sets: derive the upper-echelon popularity floor from THIS pool's scored
    # tracks (pool-relative percentile), so "upper echelon" adapts to the genre.
    cfg2 = cfg
    if set_def["id"] in MAINSTREAM and cfg.get("mainstream_pct"):
        scored = sorted(t["deezer_rank"] for t in dpool if t.get("deezer_rank"))
        if scored:
            floor = scored[min(len(scored) - 1, int(cfg["mainstream_pct"] / 100 * len(scored)))]
            cfg2 = {**cfg, "mainstream_floor": floor}

    # deterministic reject rules -> clean pool the profile is measured on
    clean = [t for t in dpool if not rejected_static(t, set_def, cfg2)]
    if len(clean) < 12:
        clean = dpool

    # profile centroid — anchored on the recognizable core for party sets
    base = clean
    if cfg.get("centroid_recog") and set_def["id"] in PARTY:
        base = sorted(clean, key=lambda t: -(t["_recog"] or 0))[: max(15, len(clean) // 2)]
    cen = centroid(base)

    # ACCEPTANCE REGION: admit every clean track within k·MAD of the profile centre.
    # This is the membership rule — a deterministic radius, NOT a top-N cap.
    dists = [(wdist(t["_z"], cen, w), t) for t in clean]
    dvals = [d for d, _ in dists]
    med = st.median(dvals)
    mad = st.median([abs(d - med) for d in dvals]) or 1e-9
    K = cfg.get("radius_k", 2.5)

    def admit(k):
        return [t for d, t in dists if d <= med + k * mad]

    pool_members = admit(K)
    # only a LOWER bound: if the profile is too tight to be a viable pool, widen the radius
    min_set = cfg.get("min_set", 24)
    while len(pool_members) < min_set and K < 8:
        K += 0.5
        pool_members = admit(K)

    # sticky anchors: on a party floor the famous anthems MUST be in even if they read as
    # profile outliers (they're distinctive precisely because they're iconic). High
    # recognizability bypasses the radius — deep cuts still have to fit the profile.
    if set_def["id"] in PARTY and cfg.get("sticky_recog"):
        have = {t["id"] for t in pool_members}
        for _, t in dists:
            if t["id"] not in have and (t.get("deezer_rank") or 0) >= cfg["sticky_recog"]:
                pool_members.append(t); have.add(t["id"])

    # STAGE 2 — transition prune: keep only tracks that mix WELL into the set's network
    members, orphans = transition_prune(pool_members, cfg, min_degree=cfg.get("min_degree", 1))

    tmpl = {"sunset": "wave", "function": "peak", "rage": "peak"}.get(set_def["id"], "rise")
    ordered, mix_ok = order_harmonic(members, cfg, tmpl)
    return {"id": set_def["id"], "name": set_def["name"], "members": members,
            "ordered": ordered, "centroid": cen, "mix_ok": mix_ok, "w": w,
            "radius_k": round(K, 2), "pool": len(pool), "rejected": len(dpool) - len(clean),
            "admitted": len(pool_members), "orphans": len(orphans), "final": len(members),
            "thin": len(members) < cfg.get("recommend_below", 40)}


def evaluate(built, all_centroids):
    """Per-set quality. coherence↓ = tightness; distinct↑ = silhouette vs nearest other
    set; mix_ok↑; recog = median deezer of members."""
    m = built["members"]; cen = built["centroid"]; w = built["w"]
    coh = st.median([wdist(t["_z"], cen, w) for t in m])
    others = [wdist_c(cen, oc, w) for k, oc in all_centroids.items() if k != built["id"]]
    nearest = min(others) if others else 1.0
    distinct = round(nearest / (coh + 1e-9), 3)      # silhouette-ish: gap to nearest set / own spread
    recog = st.median([t["_recog"] for t in m])
    return {"n": len(m), "coherence": round(coh, 3), "distinct": distinct,
            "mix_ok": round(built["mix_ok"], 3), "recog_k": round(recog / 1000)}


def wdist_c(a, b, w):
    return math.sqrt(sum(_w(w, f) * (a[f] - b[f]) ** 2 for f in ALLFEATS))


def run(cfg, tracks):
    def _pass(c):
        out = {}
        for sd in BLUE:
            b = build_set(sd, tracks, c)
            if b:
                out[sd["id"]] = b
        return out
    built = _pass(cfg)
    if cfg.get("contrast"):
        cents0 = {k: b["centroid"] for k, b in built.items()}
        built = _pass({**cfg, "_other_centroids": cents0})
    cents = {k: b["centroid"] for k, b in built.items()}
    ev = {k: evaluate(b, cents) for k, b in built.items()}
    return built, ev


def aggregate(ev):
    coh = st.mean([e["coherence"] for e in ev.values()])
    dist = st.mean([e["distinct"] for e in ev.values()])
    mix = st.mean([e["mix_ok"] for e in ev.values()])
    party_recog = st.mean([ev[k]["recog_k"] for k in PARTY if k in ev])
    size = st.mean([e["n"] for e in ev.values()])
    Q = dist * 1.0 + mix * 1.2 - coh * 0.15 + (party_recog / 1000) * 0.4
    return {"coherence": round(coh, 3), "distinct": round(dist, 3),
            "mix_ok": round(mix, 3), "party_recog_k": round(party_recog),
            "mean_size": round(size, 1), "Q": round(Q, 3)}


# Profile-acceptance model: no upper cap. Deterministic reject rules (junk titles,
# duration guard, recognizability floor for party sets) + admit everything within
# radius_k·MAD of the set's profile centre. `radius_k` is the one tightness knob;
# `min_set` is the only size constraint (a floor, auto-widens the radius if unmet).
BASE = dict(weights={**WDEF, "year": 1.2, "is_vocal": 1.0},
            min_set=24, centroid_recog=True, recog_floor=200000,
            min_dur=95, max_dur=480,
            # transition-quality gates (Tier-1/2 metadata pruning)
            bpm_tol=0.08, loud_tol=5.0, spec_tol=2.2, grv_tol=2.0, edge_bar=0.45,
            key_conf_min=0.35, tempo_cv_max=0.09, dance_floor=0.28, rnb_energy_floor=0.42,
            sticky_recog=500000,
            mainstream_pct=50)   # function/rnb: only the top-50%-popular half of the pool (upper echelon)
CONFIGS = [
    ("k=2.0 strict profile", {**BASE, "radius_k": 2.0}),
    ("k=2.5 balanced",       {**BASE, "radius_k": 2.5}),
    ("k=3.0 inclusive",      {**BASE, "radius_k": 3.0}),
    ("k=3.5 loose",          {**BASE, "radius_k": 3.5}),
]
DEFAULT_CFG = "k=2.5 balanced"


def main():
    import sys as _sys
    tracks, _ = load()
    print(f"loaded {len(tracks)} fully-vectored tracks")
    print("acceptance model: no cap · reject rules + radius·MAD around each profile · floor only\n")
    hdr = f"{'radius (tightness)':<24}{'mean n':>8}{'coher↓':>8}{'distinct↑':>10}{'mix_ok↑':>9}{'recog(k)':>9}"
    print(hdr); print("-" * len(hdr))
    chosen = None
    want = _sys.argv[1] if len(_sys.argv) > 1 else DEFAULT_CFG
    for name, cfg in CONFIGS:
        built, ev = run(cfg, tracks)
        agg = aggregate(ev)
        print(f"{name:<24}{agg['mean_size']:>8}{agg['coherence']:>8}{agg['distinct']:>10}{agg['mix_ok']:>9}{agg['party_recog_k']:>9}")
        if want in name:
            chosen = (name, cfg, built, ev)
    if chosen is None:
        chosen = (CONFIGS[1][0], CONFIGS[1][1], *run(CONFIGS[1][1], tracks))
    name, cfg, built, ev = chosen
    print(f"\nEXPORTING: {name}   pool → transition-prune → set (no cap, floor only)\n")
    print(f"  {'set':<12}{'pool':>6}{'rej':>5}{'admit':>7}{'orphan':>8}{'FINAL':>7}{'mix_ok':>8}{'recog(k)':>9}")
    thin = []
    for k, b in built.items():
        e = ev[k]
        flag = "  ⚠ thin" if b["thin"] else ""
        print(f"  {k:<12}{b['pool']:>6}{b['rejected']:>5}{b['admitted']:>7}{b['orphans']:>8}{b['final']:>7}{e['mix_ok']:>8}{e['recog_k']:>9}{flag}")
        if b["thin"]:
            thin.append((k, b["final"]))
    if thin:
        print("\n  RECOMMEND MORE DOWNLOADS (set < 40 after transition prune):")
        for k, n in thin:
            print(f"    • {k}: only {n} mixable tracks — dig for more to reach a full set")
    best = (name, None, cfg, built, ev)
    # persist best build for export
    dump = {k: {"name": b["name"],
                "tracks": [{"id": t["id"], "artist": t["artist"], "title": t["title"],
                            "bpm": t["bpm"], "camelot": t["camelot"], "path": t["canonical_path"]}
                           for t in b["ordered"]]}
            for k, b in best[3].items()}
    json.dump(dump, open("/tmp/built_sets.json", "w"), indent=1, default=float)
    print(f"\nwrote /tmp/built_sets.json  (best config: {best[0]})")


if __name__ == "__main__":
    main()
