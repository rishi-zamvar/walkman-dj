"""Analyze the Music Lake locally, reusing existing analysis. Skips any song
already in the DB (matched by normalized artist|title, then by fingerprint),
analyzes only the rest. No device / adb — reads the Lake files directly."""
import os, re, sys, time
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import analyze, rules as rulesmod, live  # noqa: E402
from walkman_dj.db import CrateDB  # noqa: E402
from walkman_dj.fingerprint import fingerprint_file  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "walkman_analysis.db")
CFG = os.path.join(ROOT, "config", "genre_rules.toml")
LAKE = "/Users/rishizamvar/Desktop/MusicLake"
AUDIO = (".mp3", ".flac", ".m4a", ".wav", ".aac", ".aiff", ".aif", ".ogg")
WORKERS = 4               # 18 GB machine — keep concurrent audio loads bounded
MAX_DUR = 600            # skip anything > 10 min (DJ set recordings, mixes, GoPro)
MAX_MB = 130             # size guard (fast path before reading duration)


def norm(s):
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"\(feat[^)]*\)|\bfeat\b.*|\bft\.?\b.*", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def tags(path):
    try:
        import mutagen
        m = mutagen.File(path, easy=True)
        if not m:
            return None, None, 0.0
        return ((m.get("artist") or [None])[0], (m.get("title") or [None])[0],
                getattr(m.info, "length", 0.0) or 0.0)
    except Exception:
        return None, None, 0.0


def too_long(path, dur):
    """A DJ-set recording / mix / GoPro clip — skip so it can't OOM the machine."""
    try:
        if os.path.getsize(path) > MAX_MB * 1024 * 1024:
            return True
    except OSError:
        pass
    return dur > MAX_DUR


def from_name(name):
    stem = re.sub(r"^\d+\s*[-.]\s*", "", os.path.splitext(name)[0])
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        return a.strip(), t.strip()
    return None, stem.strip()


def worker(task):
    path, prior, track, folder = task
    wid = os.getpid()
    try:
        live.set_worker(wid, track, "fingerprint", folder)
        fp = fingerprint_file(path)
        feat = analyze.analyze_file(
            path, prior=prior,
            on_stage=lambda s: live.set_worker(wid, track, s, folder))
        live.worker_idle(wid)
        return path, fp, feat
    except Exception as e:  # noqa: BLE001
        live.worker_idle(wid)
        return path, None, {"status": "failed", "segments": [], "error": str(e)[:150]}


def main():
    db = CrateDB(DB)
    db.init()
    rules = rulesmod.load_rules(CFG)

    # build skip-set from already-analyzed DB rows (artist|title) + fingerprints
    import sqlite3
    c = sqlite3.connect(DB)
    seen_keys = set()
    for a, t in c.execute("SELECT artist,title FROM tracks JOIN features USING(fingerprint) WHERE status='ok'"):
        if t:
            seen_keys.add(f"{norm(a)}|{norm(t)}")
    c.close()
    print(f"already-analyzed keys: {len(seen_keys)}", flush=True)

    # list Lake files
    files = []
    for dp, _, fs in os.walk(LAKE):
        if "/_" in dp + "/":   # skip _folded/_djsets/etc meta
            pass
        for f in fs:
            if f.lower().endswith(AUDIO):
                files.append(os.path.join(dp, f))
    print(f"lake files: {len(files)}", flush=True)

    # decide which need analysis (tag-based skip)
    todo = []
    skipped = skipped_long = 0
    for p in files:
        a, t, dur = tags(p)
        if too_long(p, dur):                 # DJ set / mix / recording -> never load it
            skipped_long += 1
            continue
        if not a or not t:
            fa, ft = from_name(os.path.basename(p))
            a = a or fa
            t = t or ft
        key = f"{norm(a)}|{norm(t)}"
        if key in seen_keys:
            skipped += 1
            continue
        # bpm prior from classification
        folder = os.path.basename(os.path.dirname(p))
        fams = rulesmod.classify_track(rules, folder=folder, filename=os.path.basename(p),
                                       artist=a, features={})
        prior = rulesmod.bpm_prior(rules, fams[0]) if fams else (70, 180)
        todo.append((p, prior, a, t, folder))
    print(f"skipped (already analyzed): {skipped}  |  skipped (too long/big): "
          f"{skipped_long}  |  to analyze: {len(todo)}", flush=True)
    live.reset()
    live.set_main(phase="analyzing", total=len(todo), done=0, workers=WORKERS, nbatches=1, batch=1)

    pool = Pool(WORKERS, maxtasksperchild=15)   # recycle workers often to release RAM
    done = 0
    try:
        tasks = [(p, prior, t or os.path.basename(p), folder)
                 for (p, prior, a, t, folder) in todo]
        meta = {p: (a, t, folder) for (p, prior, a, t, folder) in todo}
        for path, fp, feat in pool.imap_unordered(worker, tasks):
            a, t, folder = meta[path]
            if fp is None:
                fp = "bad:" + path
            elif not db.needs_analysis(fp):    # fingerprint already in DB -> skip save
                done += 1
                continue
            db.upsert_track(fingerprint=fp, path=path, filename=os.path.basename(path),
                            artist=a, title=t, folder=folder)
            db.save_features(fp, feat)
            if feat.get("segments"):
                db.save_segments(fp, feat["segments"])
            if feat.get("status") == "ok":
                live.push_done(t or os.path.basename(path), feat.get("bpm"),
                               feat.get("camelot"), feat.get("energy"), folder)
            done += 1
            if done % 25 == 0:
                live.set_main(phase="analyzing", total=len(todo), done=done, workers=WORKERS,
                              nbatches=1, batch=1)
                print(f"  ...{done}/{len(todo)}", flush=True)
    finally:
        pool.close(); pool.join()
    print(f"DONE: analyzed {done} new lake songs (DB now has "
          f"{db.counts()['ok']} ok)", flush=True)


if __name__ == "__main__":
    main()
