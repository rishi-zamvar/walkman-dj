"""Tempo stability (tempo_cv) — how steady a track's tempo is, for beatmatchability.

Programmed/quantized tracks hold a near-constant beat spacing (low CV); live drummers,
rubato, and tape-warble drift (high CV) and won't hold a beatmatch. Computed from the
inter-beat intervals with octave-error trimming. Resumable + parallel; writes tempo_cv.
Run: python3 src/walkman_dj/tempo_stability.py
"""
import os, sqlite3, warnings, multiprocessing as mp
warnings.filterwarnings("ignore")
DB = os.path.expanduser("~/Documents/Projects/walkman-dj/data/library.db")


def compute(path):
    import numpy as np, librosa
    y, sr = librosa.load(path, sr=22050, mono=True, offset=20, duration=60)
    if len(y) < sr * 8:
        y, sr = librosa.load(path, sr=22050, mono=True, duration=60)
    if len(y) < sr * 6:
        return None
    _, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    if len(beats) < 10:
        return None
    ibi = np.diff(beats)
    med = np.median(ibi)
    if med <= 0:
        return None
    keep = ibi[(ibi > 0.6 * med) & (ibi < 1.6 * med)]   # drop half/double-beat errors
    if len(keep) < 6:
        return None
    m = float(np.mean(keep))
    return float(np.std(keep) / m) if m else None


def worker(job):
    rid, path = job
    try:
        return (rid, compute(path))
    except Exception:
        return (rid, None)


def main():
    con = sqlite3.connect(DB, timeout=120)
    cols = {r[1] for r in con.execute("pragma table_info(recordings)")}
    if "tempo_cv" not in cols:
        con.execute("alter table recordings add column tempo_cv real")
    con.commit()
    todo = [(rid, p) for rid, p in con.execute(
        "select id,canonical_path from recordings where tempo_cv is null and canonical_path is not null")
        if p and os.path.exists(p)]
    print(f"to analyze: {len(todo)}", flush=True)
    done = ok = 0
    with mp.Pool(8) as pool:
        for rid, v in pool.imap_unordered(worker, todo, chunksize=4):
            if v is not None:
                con.execute("update recordings set tempo_cv=? where id=?", (v, rid)); ok += 1
            done += 1
            if done % 50 == 0:
                con.commit(); print(f"{done}/{len(todo)} ok={ok}", flush=True)
    con.commit(); con.close()
    print(f"TEMPO_CV DONE: {done} processed, {ok} written", flush=True)


if __name__ == "__main__":
    main()
