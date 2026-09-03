"""Deep audio feature vector per track — the genre-classification standard set (MFCC +
chroma + spectral contrast + spectral stats), from the local FLACs. Stored as a JSON dict
in table `ml_features(id, vec)`. Resumable + parallel. Foundation for the genre model.
Run: python3 src/walkman_dj/ml_features.py
"""
import os, json, sqlite3, warnings, multiprocessing as mp
warnings.filterwarnings("ignore")
DB = os.path.expanduser("~/Documents/Projects/walkman-dj/data/library.db")


def compute(path):
    import numpy as np, librosa
    y, sr = librosa.load(path, sr=22050, mono=True, offset=20, duration=60)
    if len(y) < sr * 6:
        y, sr = librosa.load(path, sr=22050, mono=True, duration=60)
    if len(y) < sr * 3:
        return None
    f = {}
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20)
    for i in range(20):
        f[f"mfcc{i}_m"] = float(mfcc[i].mean()); f[f"mfcc{i}_s"] = float(mfcc[i].std())
    chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    for i in range(12):
        f[f"chroma{i}"] = float(chroma[i].mean())
    contrast = librosa.feature.spectral_contrast(y=y, sr=sr)
    for i in range(contrast.shape[0]):
        f[f"contrast{i}"] = float(contrast[i].mean())
    for name, fn in (("centroid", librosa.feature.spectral_centroid),
                     ("rolloff", librosa.feature.spectral_rolloff),
                     ("bandwidth", librosa.feature.spectral_bandwidth),
                     ("flatness", librosa.feature.spectral_flatness)):
        v = fn(y=y) if name == "flatness" else fn(y=y, sr=sr)
        f[f"{name}_m"] = float(v.mean()); f[f"{name}_s"] = float(v.std())
    zcr = librosa.feature.zero_crossing_rate(y)
    f["zcr_m"] = float(zcr.mean()); f["zcr_s"] = float(zcr.std())
    return f


def worker(job):
    rid, path = job
    try:
        return (rid, compute(path))
    except Exception:
        return (rid, None)


def main():
    con = sqlite3.connect(DB, timeout=120)
    con.execute("create table if not exists ml_features (id integer primary key, vec text)")
    con.commit()
    have = {r[0] for r in con.execute("select id from ml_features")}
    todo = [(rid, p) for rid, p in con.execute(
        "select id,canonical_path from recordings where canonical_path is not null")
        if p and rid not in have and os.path.exists(p)]
    print(f"to extract: {len(todo)}", flush=True)
    done = ok = 0
    with mp.Pool(8) as pool:
        for rid, f in pool.imap_unordered(worker, todo, chunksize=4):
            if f:
                con.execute("insert or replace into ml_features(id,vec) values(?,?)",
                            (rid, json.dumps(f))); ok += 1
            done += 1
            if done % 50 == 0:
                con.commit(); print(f"{done}/{len(todo)} ok={ok}", flush=True)
    con.commit(); con.close()
    print(f"ML_FEATURES DONE: {done} processed, {ok} written", flush=True)


if __name__ == "__main__":
    main()
