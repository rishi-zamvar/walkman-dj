#!/usr/bin/env python3
"""Analyze MusicLake tracks that aren't in the DB yet (the newly-consolidated
computer-only songs). Reuses pipeline._analyze_worker on LOCAL paths (no device
pull). Skips already-analyzed tracks by normalized filename (fast, no decode).
Writes features into walkman_analysis.db. Progress -> /tmp/analyze_progress.json."""
import os, sys, re, json, time, sqlite3, argparse
from multiprocessing import Pool

ROOT = "/Users/rishizamvar/Documents/Projects/walkman-dj"
sys.path.insert(0, ROOT + "/src")
from walkman_dj import rules as rulesmod
from walkman_dj.db import CrateDB
from walkman_dj.pipeline import _analyze_worker, _read_tags, _guess_from_filename, prior_for

LAKE = os.path.expanduser("~/Desktop/MusicLake")
DB = ROOT + "/data/walkman_analysis.db"
CFG = ROOT + "/config/genre_rules.toml"
PROG = "/tmp/analyze_progress.json"
AUD = (".flac", ".mp3", ".m4a", ".wav", ".aac", ".aiff", ".aif")
_norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
t0 = time.time()


def emit(**kw):
    kw["elapsed_s"] = round(time.time() - t0, 1)
    p, t = kw.get("processed", 0), kw.get("total", 0)
    if p and kw["elapsed_s"] > 1:
        rate = p / kw["elapsed_s"]; kw["rate_per_s"] = round(rate, 2)
        kw["eta_s"] = round((t - p) / rate) if rate and t else None
    json.dump(kw, open(PROG + ".tmp", "w")); os.replace(PROG + ".tmp", PROG)


def analyzed_basenames():
    con = sqlite3.connect(DB)
    s = set()
    for (fn,) in con.execute("SELECT t.filename FROM features f JOIN tracks t "
                             "USING(fingerprint) WHERE f.status='ok'"):
        s.add(_norm(os.path.splitext(fn or "")[0]))
    con.close()
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    done_set = analyzed_basenames()
    files = []
    for dp, _, fns in os.walk(LAKE):
        if "_dedup-losers" in dp:
            continue
        for fn in fns:
            if os.path.splitext(fn)[1].lower() in AUD and not os.path.islink(os.path.join(dp, fn)):
                if _norm(os.path.splitext(fn)[0]) not in done_set:
                    files.append(os.path.join(dp, fn))
    if args.limit:
        files = files[:args.limit]
    total = len(files)
    print(f"[analyze_lake] {total} unanalyzed MusicLake tracks", flush=True)
    emit(stage="analyze", phase_label="Analyzing new MusicLake tracks", processed=0, total=total)

    db = CrateDB(DB); db.init()
    rules = rulesmod.load_rules(CFG)
    tasks = []
    for idx, path in enumerate(files):
        folder = os.path.basename(os.path.dirname(path))
        fn = os.path.basename(path)
        tasks.append((idx, path, prior_for(rules, folder, fn), os.path.splitext(fn)[0], folder))

    ok = failed = 0
    pool = Pool(args.workers, maxtasksperchild=40)
    try:
        for i, (idx, fp, feat) in enumerate(pool.imap_unordered(_analyze_worker, tasks), 1):
            path = files[idx]; fn = os.path.basename(path)
            folder = os.path.basename(os.path.dirname(path))
            if fp is None:
                fp = "bad:" + path
            artist, title = _read_tags(path)
            if not artist:
                ga, gt = _guess_from_filename(fn); artist = artist or ga; title = title or gt
            db.upsert_track(fingerprint=fp, path=path, filename=fn,
                            artist=artist, title=title, folder=folder)
            db.save_features(fp, feat)
            if feat.get("segments"):
                db.save_segments(fp, feat["segments"])
            if feat.get("status") == "ok":
                ok += 1
            else:
                failed += 1
            if i % 20 == 0 or i == total:
                emit(stage="analyze", phase_label="Analyzing new MusicLake tracks",
                     processed=i, total=total, exact=ok, current=fn[:50])
    finally:
        pool.close(); pool.join()
    emit(stage="done", phase_label=f"Analyzed {ok} tracks ({failed} failed)",
         processed=total, total=total, exact=ok)
    print(f"ANALYZE_LAKE DONE: ok={ok} failed={failed}", flush=True)


if __name__ == "__main__":
    main()
