"""Recover Lake songs the tag-match missed: fingerprint each unmatched song,
link it to existing analysis (same recording) or analyze fresh, and write a
Lake-path DB row so the set-builder finds everything by path. RAM-safe."""
import os, re, sys, sqlite3
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import analyze, rules as rulesmod, live       # noqa: E402
from walkman_dj.db import CrateDB                              # noqa: E402
from walkman_dj.fingerprint import fingerprint_file           # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "walkman_analysis.db")
CFG = os.path.join(ROOT, "config", "genre_rules.toml")
LAKE = "/Users/rishizamvar/Desktop/MusicLake"
AUDIO = (".mp3", ".flac", ".m4a", ".wav", ".aac", ".aiff", ".aif", ".ogg")
WORKERS = 6
MAX_DUR, MAX_MB = 600, 130


def norm(s):
    if not s:
        return ""
    s = re.sub(r"\(feat[^)]*\)|\bfeat\b.*|\bft\.?\b.*", " ", s.lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def meta(path):
    try:
        import mutagen
        m = mutagen.File(path, easy=True)
        a = (m.get("artist") or [None])[0] if m else None
        t = (m.get("title") or [None])[0] if m else None
        dur = getattr(m.info, "length", 0.0) if m else 0.0
    except Exception:
        a = t = None; dur = 0.0
    if not a or not t:
        stem = re.sub(r"^\d+\s*[-.]\s*", "", os.path.splitext(os.path.basename(path))[0])
        if " - " in stem:
            fa, ft = stem.split(" - ", 1); a = a or fa.strip(); t = t or ft.strip()
        else:
            t = t or stem.strip()
    return a, t, dur


def fp_worker(task):
    path, prior, track, folder = task
    wid = os.getpid()
    try:
        live.set_worker(wid, track, "fingerprint", folder)
        fp = fingerprint_file(path)
        return path, fp, None
    except Exception as e:  # noqa: BLE001
        live.worker_idle(wid)
        return path, None, str(e)[:100]


def an_worker(task):
    path, prior, track, folder = task
    wid = os.getpid()
    try:
        feat = analyze.analyze_file(path, prior=prior,
                                    on_stage=lambda s: live.set_worker(wid, track, s, folder))
        live.worker_idle(wid)
        return path, feat
    except Exception as e:  # noqa: BLE001
        live.worker_idle(wid)
        return path, {"status": "failed", "segments": [], "error": str(e)[:100]}


def main():
    db = CrateDB(DB); db.init()
    rules = rulesmod.load_rules(CFG)
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    paths_in = set(); keys_in = set()
    for r in c.execute("SELECT path,artist,title FROM tracks JOIN features USING(fingerprint) WHERE status='ok'"):
        if r["path"]:
            paths_in.add(r["path"])
        if r["title"]:
            keys_in.add(f"{norm(r['artist'])}|{norm(r['title'])}")
    c.close()

    unmatched = []
    for dp, _, fs in os.walk(LAKE):
        if os.path.basename(dp).startswith("_"):
            continue
        for f in fs:
            if not f.lower().endswith(AUDIO):
                continue
            p = os.path.join(dp, f)
            a, t, dur = meta(p)
            if p in paths_in or f"{norm(a)}|{norm(t)}" in keys_in:
                continue                                   # already reachable
            try:
                if os.path.getsize(p) > MAX_MB * 1024 * 1024 or dur > MAX_DUR:
                    continue                               # too long -> skip
            except OSError:
                continue
            folder = os.path.basename(dp)
            fams = rulesmod.classify_track(rules, folder=folder, filename=f, artist=a, features={})
            prior = rulesmod.bpm_prior(rules, fams[0]) if fams else (70, 180)
            unmatched.append((p, prior, t or f, folder, a))
    print(f"unmatched Lake songs to recover: {len(unmatched)}", flush=True)
    live.reset()
    live.set_main(phase="analyzing", total=len(unmatched), done=0, workers=WORKERS,
                  nbatches=1, batch=1)

    tasks = [(p, prior, tr, fo) for (p, prior, tr, fo, a) in unmatched]
    metamap = {p: (a, tr, fo, prior) for (p, prior, tr, fo, a) in unmatched}

    # Phase 1: fingerprint -> link to existing analysis; collect the ones needing analysis
    to_analyze = []
    done = 0
    pool = Pool(WORKERS, maxtasksperchild=40)
    try:
        for path, fp, err in pool.imap_unordered(fp_worker, tasks):
            a, tr, fo, prior = metamap[path]
            if fp:
                db.upsert_track(fingerprint=fp, path=path, filename=os.path.basename(path),
                                artist=a, title=tr, folder=fo)      # link Lake path
                if db.needs_analysis(fp):
                    to_analyze.append((path, prior, tr, fo))
            done += 1
            if done % 25 == 0:
                live.set_main(phase="analyzing", total=len(unmatched), done=done,
                              workers=WORKERS, nbatches=1, batch=1)
    finally:
        pool.close(); pool.join()
    print(f"linked by fingerprint: {len(unmatched) - len(to_analyze)}  |  "
          f"need fresh analysis: {len(to_analyze)}", flush=True)

    # Phase 2: analyze the genuinely-new ones (RAM-safe, capped loads)
    pool = Pool(WORKERS, maxtasksperchild=15)
    try:
        d2 = 0
        for path, feat in pool.imap_unordered(an_worker, to_analyze):
            a, tr, fo, prior = metamap[path]
            fp = fingerprint_file(path) if feat.get("status") == "ok" else "bad:" + path
            db.upsert_track(fingerprint=fp, path=path, filename=os.path.basename(path),
                            artist=a, title=tr, folder=fo)
            db.save_features(fp, feat)
            if feat.get("segments"):
                db.save_segments(fp, feat["segments"])
            d2 += 1
            if d2 % 20 == 0:
                print(f"  ...analyzed {d2}/{len(to_analyze)}", flush=True)
    finally:
        pool.close(); pool.join()
    print(f"DONE: recovered {len(unmatched)} songs (DB now {db.counts()['ok']} ok)", flush=True)


if __name__ == "__main__":
    main()
