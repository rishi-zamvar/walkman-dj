"""Build a DJ-ready set for one genre, end to end:

  load -> prune vibe-outliers -> TSP-order by transitionability
       -> prune tracks that don't cleanly transition -> re-order (master list)
       -> LLM-rationalize each transition + song choice -> .m3u8 + annotated .md

Reuses the Set Sequencer's TSP solver (sequence.greedy_order/two_opt) and the
harmonic key helper (camelot.compatible). The rationalize step shells to the
already-authed `claude` CLI, so no API key or SDK is needed.
"""
import os
import sqlite3
import subprocess

import numpy as np
from sklearn.preprocessing import StandardScaler

from walkman_dj.sequence import greedy_order, two_opt
from walkman_dj.camelot import compatible

VIBE = ["valence", "acousticness", "danceability", "energy",
        "speechiness", "instrumentalness", "liveness"]


def load(db, genre):
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(
        """SELECT title, artist, bpm, camelot, energy, valence, acousticness,
                  essentia_danceability AS danceability, speechiness,
                  instrumentalness, liveness, genre_confidence, genre_source,
                  subgenre, bass_weight, brightness, canonical_path AS path
           FROM recordings WHERE genre=? AND valence IS NOT NULL""", (genre,))]


def matrix(rows):
    X = np.array([[r[k] if r[k] is not None else 0.0 for k in VIBE] for r in rows])
    return StandardScaler().fit_transform(X)


def prune_vibe(rows, Xs, k=1.3, min_conf=0.12):
    """Keep tracks that fit the genre's vibe. Drop centroid-outliers (>k std) and
    audio-classified tracks the model was unsure about (genre_confidence<min_conf)."""
    centroid = np.median(Xs, axis=0)
    dist = np.linalg.norm(Xs - centroid, axis=1)
    cut = dist.mean() + k * dist.std()
    keep = []
    for i, r in enumerate(rows):
        weak = r["genre_source"] == "audio" and (r["genre_confidence"] or 0.0) < min_conf
        if dist[i] <= cut and not weak:
            keep.append(i)
    return keep, dist


# --- Transition model: two AND-gates (bpm, key) + a drum/energy confidence score.
# A pair is mixable only if bpm-in-range AND key-harmonic AND drum/energy conf >= thresh.
# Otherwise there is no clean edge (INVALID cost). Vibe features are NOT used here —
# they only decide which tracks are SELECTED into the set (see curate()).
INVALID = 1000.0
BPM_PCT = 0.06        # ±6% mixable window (with pitch adjust)
BPM_FLOOR = 3.0
DRUM_MIN = 0.60       # min drum/energy similarity confidence to allow a mix
DRUM_FEATS = ["energy", "danceability", "bass_weight", "brightness"]


def bpm_in_range(a, b, pct=BPM_PCT, floor=BPM_FLOOR):
    ba, bb = a.get("bpm"), b.get("bpm")
    if not ba or not bb:
        return False
    win = max(ba * pct, floor)
    return any(abs(ba - m) <= win for m in (bb, bb * 2, bb / 2))   # incl. half/double-time


def key_harmonic(a, b):
    return bool(a.get("camelot") and b.get("camelot")
                and b["camelot"] in compatible(a["camelot"]))


def drum_energy_conf(a, b):
    """1 - mean absolute difference over drum/energy features (all ~0..1)."""
    diffs = [abs(a[k] - b[k]) for k in DRUM_FEATS
             if a.get(k) is not None and b.get(k) is not None]
    return 1.0 - sum(diffs) / len(diffs) if diffs else 0.0


def transition(a, b, pct=BPM_PCT, drum_min=DRUM_MIN):
    """Cost of mixing a->b: 1-confidence if all gates pass, else INVALID."""
    if not bpm_in_range(a, b, pct) or not key_harmonic(a, b):
        return INVALID
    conf = drum_energy_conf(a, b)
    if conf < drum_min:
        return INVALID
    return 1.0 - conf                    # lower cost = higher-confidence transition


def edge(i, j, Xs, rows):               # kept for callers/tests; Xs unused now
    return transition(rows[i], rows[j])


def _pair(idx, Xs, rows, pct=BPM_PCT, drum_min=DRUM_MIN):
    n = len(idx)
    P = [[0.0] * n for _ in range(n)]
    for x in range(n):
        for y in range(x + 1, n):
            w = transition(rows[idx[x]], rows[idx[y]], pct, drum_min)
            P[x][y] = P[y][x] = w
    return P


def order(idx, Xs, rows, budget=8.0):
    """TSP open-path over the given global indices. Returns (global_order, P, local_order)."""
    if len(idx) < 2:
        return list(idx), [[0.0]], list(range(len(idx)))
    P = _pair(idx, Xs, rows)
    en = [rows[i]["energy"] or 0.0 for i in idx]
    o = greedy_order(P, en, None, 0.0, start=int(np.argmin(en)))
    o = two_opt(o, P, en, None, 0.0, time_budget=budget)
    return [idx[x] for x in o], P, o


def prune_transitions(P, idx, pct=90):
    """Drop tracks that can't cleanly mix with ANYTHING (no valid edge to any other)."""
    n = len(P)
    if n < 3:
        return list(idx), [], 0.0
    drop = {i for i in range(n)
            if min((P[i][j] for j in range(n) if j != i), default=INVALID) >= INVALID}
    kept = [idx[i] for i in range(n) if i not in drop]
    dropped = [idx[i] for i in drop]
    return kept, dropped, INVALID


def clean_chain(global_order, rows):
    """Keep the run where every consecutive mix is valid; thread past any track whose
    incoming mix would be a hard cut. Returns (kept, dropped) global indices."""
    if not global_order:
        return [], []
    kept = [global_order[0]]
    dropped = []
    for i in global_order[1:]:
        if transition(rows[kept[-1]], rows[i]) < INVALID:
            kept.append(i)
        else:
            dropped.append(i)
    return kept, dropped


def curate(rows, prof, count=None):
    """Pick the tracks that fit the profile's vibe: hard tempo gate, then the best
    `count` by weighted distance to the target vibe. Returns global indices, best-fit first."""
    lo, hi = prof["bpm"]
    tgt, w = prof["target"], prof["weights"]
    scored = []
    for i, r in enumerate(rows):
        b = r["bpm"]
        if not b or b < lo or b > hi:                 # off-tempo / half-time misread -> out
            continue
        d = sum(w.get(k, 1.0) * ((float(r[k] if r[k] is not None else 0.0) - t) ** 2)
                for k, t in tgt.items())
        scored.append((d ** 0.5, i))
    scored.sort()
    return [i for _, i in scored[:count or prof["size"]]]


def order_arc(idx, Xs, rows, template, w_template=0.6, budget=8.0):
    """Order the selected tracks to follow an energy template (build/peak/cool),
    balancing transition smoothness against the arc. Starts on the lowest-energy track."""
    if len(idx) < 2:
        return list(idx)
    P = _pair(idx, Xs, rows)
    raw = [rows[i]["energy"] or 0.0 for i in idx]
    lo, hi = min(raw), max(raw)
    en = [(e - lo) / (hi - lo) if hi > lo else 0.5 for e in raw]   # spread for the template
    start = int(min(range(len(idx)), key=lambda x: en[x]))
    o = greedy_order(P, en, template, w_template, start=start)
    o = two_opt(o, P, en, template, w_template, time_budget=budget)
    return [idx[x] for x in o]


def _line(pos, r):
    return (f"{pos}. {r['artist']} - {r['title']} | "
            f"{int(r['bpm']) if r['bpm'] else '?'}bpm {r['camelot'] or '?'} "
            f"E={float(r['energy'] or 0):.2f} val={r['valence']:.2f} "
            f"dance={float(r['danceability'] or 0):.2f} acou={r['acousticness']:.2f} "
            f"[{r['subgenre'] or r['genre_source']}]")


def rationalize(global_order, rows, genre, timeout=600):
    """Shell to `claude -p`: rationalize the arc, flag the rough transitions + misfit songs."""
    body = "\n".join(_line(p, rows[i]) for p, i in enumerate(global_order, 1))
    prompt = (
        f"You are a veteran {genre} DJ reviewing a machine-ordered set ({len(global_order)} "
        "tracks, ordered by a transitionability algorithm: vibe distance + BPM + harmonic key). "
        "Don't review every transition. Instead give me:\n"
        "1. ARC: 2-3 sentences on how the energy/tempo journey reads overall.\n"
        "2. ROUGH SPOTS: the ~8 worst transitions by track number (e.g. '#47->48'), each with "
        "one line on why it clangs (tempo jump, key clash, energy drop) and a quick fix.\n"
        "3. MISFITS: any songs that don't belong in a "
        f"{genre} set at all, by number.\n"
        "4. VERDICT: one paragraph — is this a playable set, and the single biggest improvement.\n\n"
        "SET:\n" + body)
    try:
        out = subprocess.run(["claude", "-p"], input=prompt,
                             capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() or "(no rationale returned)"
    except Exception as e:
        return f"(rationalize skipped: {e})"


def run(db, genre, vibe=None, k=1.3, min_conf=0.12, pct=90, do_rationalize=False,
        out_dir="~/Desktop/DJ Sets"):
    from walkman_dj import profiles
    rows = load(db, genre)
    if len(rows) < 3:
        return {"error": f"only {len(rows)} '{genre}' tracks with vibe data"}
    Xs = matrix(rows)

    if vibe:                                  # curate toward a set vibe (Boiler Room, etc.)
        prof = profiles.PROFILES[vibe]
        size = prof["size"]
        pool = curate(rows, prof, count=int(size * 1.8))     # over-select; chain-prune trims
        ordered = order_arc(pool, Xs, rows, prof["template"])
        chain, dropped_trans = clean_chain(ordered, rows)    # keep only clean mixes
        final = chain[:size]
        keep = pool
        label = f"{genre} — {vibe.replace('_', ' ').title()}"
    else:                                     # order the whole crate, pruning outliers/isolates
        keep, _ = prune_vibe(rows, Xs, k, min_conf)
        go1, P1, o1 = order(keep, Xs, rows)
        kept, dropped_trans, thr = prune_transitions(P1, keep, pct)
        final, _, _ = order(kept, Xs, rows)
        label = f"{genre} — Vibe Path"

    out_dir = os.path.expanduser(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.join(out_dir, label.replace(" — ", " ").replace("—", "-"))
    with open(stem + ".m3u8", "w") as f:
        f.write("#EXTM3U\n")
        for i in final:
            f.write(f"#EXTINF:0,{rows[i]['artist']} - {rows[i]['title']}\n{rows[i]['path']}\n")

    if vibe:
        head = f"- {len(rows)} {genre} candidates → curated to **{len(final)}** that fit the {vibe} vibe"
    else:
        head = (f"- candidates: {len(rows)} → vibe-kept: {len(keep)} → master: **{len(final)}** "
                f"(pruned {len(rows) - len(keep)} outliers, {len(dropped_trans)} hard-transition)")
    md = [f"# {label}", "", head, ""]
    for p, i in enumerate(final, 1):
        r = rows[i]
        nxt = final[p] if p < len(final) else None
        keyok = (nxt is not None and r["camelot"] and rows[nxt]["camelot"]
                 and rows[nxt]["camelot"] in compatible(r["camelot"]))
        md.append(_line(p, r) + ("  ~key" if keyok else ""))
    if do_rationalize:
        md += ["", "## DJ rationale (LLM)", "", rationalize(final, rows, label)]
    open(stem + ".md", "w").write("\n".join(md) + "\n")

    return {"candidates": len(rows), "selected": len(final), "vibe": vibe or "full",
            "m3u8": stem + ".m3u8", "md": stem + ".md"}
