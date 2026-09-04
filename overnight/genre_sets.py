"""One high-energy, genre-PURE DJ set per genre. No size cap, no minimum.

Replaces the old dj_sets multi-set flow for this job. That one clustered each genre into
~50-track sets, pruned them to a 40..90 size band, then REDISTRIBUTED the leftovers into
any set with room — it preferred the same genre but fell back to any set once same-genre
ones filled, and since the 90 cap bound everywhere that fallback fired constantly. Result:
25% of all set entries sat in a set named for a different genre (a Trance set holding 24
Hip Hop tracks). Here a set is exactly one genre, and its size is decided only by what can
actually mix.

Construction: greedy walk that will only step along a transition passing dj_sets.compat —
in-key (Camelot) AND within the genre's beatmatchable BPM window, half/double-time aware.
Candidates are scored by dj_sets.edge_weight plus an energy-template pull, so among legal
steps it takes the best-sounding one that follows the arc. Whatever the walk can't reach is
then offered to try_insert, which only places a track where BOTH neighbours stay legal.

  NOTE: compat() and edge_weight() disagree about the BPM window — bpm_window() takes the
  pct of the SLOWER track, edge_weight takes it of the FASTER, so edge_weight's implicit
  gate is wider. compat is the conservative one, so it is the gate here and edge_weight is
  used purely as a score. Gating on edge_weight instead leaves ~15 out-of-window steps in
  a 1300-track set.

Small genres get every track tried as a seed, since one bad starting point dead-ends the
whole chain (Footwork went 21% -> 71% on seed choice alone).

Energy shape is `monotonic`: a long steady build. This matters for coverage as well as
feel — a rising arc lets one set cross a wide tempo range in many small in-key steps,
where a plateau shape fights the BPM ramp and strands most of the genre.
"""
import os, sys, json, sqlite3, tomllib, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from walkman_dj import dj_sets, templates   # noqa: E402

ROOT = os.path.dirname(HERE)
DB = os.path.join(ROOT, "data", "library.db")
CFG = os.path.join(ROOT, "config", "sequencer.toml")
OUT_DIR = os.path.expanduser("~/Desktop/DJ Sets")
SETS_JSON = os.path.join(OUT_DIR, "sets.json")
TEMPLATE = "monotonic"
MIN_GENRE = 20          # a genre this small can't make a set worth playing
MIN_SET = 10            # a chain shorter than this isn't a set; report it instead
MULTISEED_MAX = 200     # try every seed below this size (cost is O(n^3))


def genre_cfg(base, genre):
    """Sequencer config with THIS genre's tuned BPM window, so the gate we build with is
    the gate we verify against."""
    tun = dj_sets.tuning_for(genre)
    return {**base, "bpm_window_pct": tun["bpm_window_pct"],
            "bpm_window_floor": tun["bpm_floor"]}, tun


def chain(tracks, seed, tun, cfg, n):
    rem = [t for t in tracks if t is not seed]
    seq = [seed]
    while rem:
        last, pos = seq[-1], len(seq) / max(1, n - 1)
        tgt = templates.target(TEMPLATE, pos)
        best = best_s = None
        for t in rem:
            if not dj_sets.compat(last, t, cfg):        # hard gate: key + BPM
                continue
            w = dj_sets.edge_weight(last, t, tun) or 0.01
            s = w - 0.6 * abs((t.get("energy") or 0.5) - tgt)
            if best_s is None or s > best_s:
                best, best_s = t, s
        if best is None:
            break
        seq.append(best); rem.remove(best)
    changed = True
    while changed:                                      # place stragglers legally
        changed = False
        for t in list(rem):
            if dj_sets.try_insert(t, seq, cfg):
                rem.remove(t); changed = True
    return seq, rem


def build(tracks, genre, base):
    cfg, tun = genre_cfg(base, genre)
    n = len(tracks)
    seeds = tracks if n <= MULTISEED_MAX else [min(tracks, key=lambda t: t.get("energy") or 0.5)]
    best = ([], tracks)
    for s in seeds:
        seq, rem = chain(tracks, s, tun, cfg, n)
        if len(seq) > len(best[0]):
            best = (seq, rem)
        if len(seq) == n:
            break
    return best[0], best[1], cfg


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(CFG, "rb") as f:
        base = dict(tomllib.load(f))
    base["allow_energy_boost"] = False
    base["key_conf_min"] = 0.0      # no "unreliable key" bypass — key mismatch is always hard
    base["bpm_conf_min"] = 0.0      # no "unreliable bpm" window-widen

    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT id,canonical_path,artist,title,duration,bpm,camelot,energy,valence,genre,"
        "subgenre,key_confidence,bpm_confidence FROM recordings "
        "WHERE TRIM(COALESCE(genre,''))<>'' AND genre NOT IN ('Other','Alternative') "
        "AND bpm IS NOT NULL AND camelot IS NOT NULL AND canonical_path IS NOT NULL "
        "AND COALESCE(blacklisted,0)=0")]
    for r in rows:
        r["fingerprint"] = str(r["id"])
    by_genre = {}
    for r in rows:
        by_genre.setdefault(r["genre"], []).append(r)

    out, skipped, total_bad = [], [], 0
    for genre, group in sorted(by_genre.items(), key=lambda kv: -len(kv[1])):
        if len(group) < MIN_GENRE:
            continue
        t0 = time.time()
        seq, parked, cfg = build(group, genre, base)
        bad = sum(1 for i in range(len(seq) - 1) if not dj_sets.compat(seq[i], seq[i + 1], cfg))
        total_bad += bad
        if len(seq) < MIN_SET:
            skipped.append((genre, len(seq), len(group)))
            continue
        out.append({"name": genre, "ids": [t["id"] for t in seq]})
        print(f"  {genre:<13} {len(seq):>4}/{len(group):<4} ({100*len(seq)/len(group):>3.0f}%) · "
              f"{len(parked):>3} parked · {bad} bad · {time.time()-t0:.1f}s")
        with open(os.path.join(OUT_DIR, f"{genre.replace('/', '_')} (set).m3u8"), "w") as fh:
            fh.write("#EXTM3U\n")
            for t in seq:
                fh.write(f"#EXTINF:{int(t.get('duration') or 0)},"
                         f"{t.get('artist') or ''} - {t.get('title') or ''}\n{t['canonical_path']}\n")
    json.dump(out, open(SETS_JSON, "w"))
    if skipped:
        print("\ntoo little that mixes to form a set:")
        for g, n, tot in skipped:
            print(f"  {g:<13} only {n}/{tot} chain")
    print(f"\n{len(out)} genre-pure sets · {sum(len(s['ids']) for s in out)} tracks · "
          f"{total_bad} out-of-key/out-of-BPM transitions -> {SETS_JSON}")


if __name__ == "__main__":
    main()
