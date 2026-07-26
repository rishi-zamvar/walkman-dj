#!/usr/bin/env python3
"""SOTA analysis of every not-yet-analyzed MusicLake track. Beat This!+DeepRhythm for
BPM/downbeat grid, librosa for key/energy/timbre/structure. Models load once. Serial
(single MPS GPU) but resumable. Run with ~/.venvs/analysis/bin/python.
Progress -> /tmp/analyze_progress.json."""
import os, sys, re, json, time, sqlite3
ROOT = "/Users/rishizamvar/Documents/Projects/walkman-dj"
sys.path.insert(0, ROOT + "/src")
import librosa
from walkman_dj import analyze, beatgrid
from walkman_dj.db import CrateDB
from walkman_dj.fingerprint import fingerprint_file

LAKE = os.path.expanduser("~/Desktop/MusicLake")
DB = ROOT + "/data/walkman_analysis.db"
PROG = f"/tmp/analyze_progress_{os.environ.get('SHARD_I', '0')}.json"
AUD = (".flac", ".mp3", ".m4a", ".wav", ".aac", ".aiff", ".aif")
_norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
t0 = time.time()


def add_columns():
    con = sqlite3.connect(DB)
    for col in ("beat_anchor REAL", "first_downbeat REAL", "grid_residual REAL",
                "beat_engine TEXT"):
        try:
            con.execute(f"ALTER TABLE features ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    con.commit(); con.close()


def emit(done, total, ok):
    el = time.time() - t0
    rate = done / el if el > 1 else 0
    json.dump({"stage": "analyze", "processed": done, "total": total, "ok": ok,
               "rate_per_s": round(rate, 3), "eta_min": round((total - done) / rate / 60, 1) if rate else None,
               "elapsed_s": round(el, 1), "last": time.time()}, open(PROG + ".tmp", "w"))
    os.replace(PROG + ".tmp", PROG)


def analyzed_basenames():
    con = sqlite3.connect(DB); s = set()
    for (fn,) in con.execute("SELECT t.filename FROM features f JOIN tracks t "
                             "USING(fingerprint) WHERE f.status='ok' AND f.beat_engine IS NOT NULL"):
        s.add(_norm(os.path.splitext(fn or "")[0]))
    con.close(); return s


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    add_columns()
    done_set = analyzed_basenames()
    files = []
    for dp, _, fns in os.walk(LAKE):
        if "_dedup-losers" in dp:
            continue
        for fn in fns:
            p = os.path.join(dp, fn)
            if os.path.splitext(fn)[1].lower() in AUD and not os.path.islink(p):
                if _norm(os.path.splitext(fn)[0]) not in done_set:
                    files.append(p)
    # optional path filter (prioritize a subset, e.g. _playlist_tracks)
    pf = os.environ.get("PATH_FILTER")
    if pf:
        files = [f for f in files if pf in f]
    # shard across parallel workers (SHARD_N workers, this is SHARD_I) — no races
    N = int(os.environ.get("SHARD_N", "1")); I = int(os.environ.get("SHARD_I", "0"))
    if N > 1:
        files = [f for k, f in enumerate(sorted(files)) if k % N == I]
    if limit:
        files = files[:limit]
    total = len(files)
    print(f"[sota] {total} tracks to analyze (SOTA engine)", flush=True)
    emit(0, total, 0)

    db = CrateDB(DB); db.init()
    ok = 0
    for i, path in enumerate(files, 1):
        fn = os.path.basename(path); folder = os.path.basename(os.path.dirname(path))
        try:
            fp = fingerprint_file(path)
            feat = {"status": "ok"}
            feat.update(beatgrid.beatgrid_of(path))                 # bpm/anchor/conf
            y, sr = librosa.load(path, sr=22050, mono=True, duration=360)
            feat.update(analyze.key_of(y, sr))
            feat.update(analyze.energy_timbre_of(y, sr))
            feat.update(analyze.structure_of(y, sr))
            artist, title = None, None
            try:
                import mutagen
                m = mutagen.File(path, easy=True)
                if m:
                    artist = (m.get("artist") or [None])[0]
                    title = (m.get("title") or [None])[0]
            except Exception:
                pass
            if not artist and " - " in os.path.splitext(fn)[0]:
                artist, title = os.path.splitext(fn)[0].split(" - ", 1)
            db.upsert_track(fingerprint=fp, path=path, filename=fn,
                            artist=artist, title=title, folder=folder)
            db.save_features(fp, feat)
            if feat.get("segments"):
                db.save_segments(fp, feat["segments"])
            ok += 1
        except Exception as e:  # noqa: BLE001
            try:
                fp = "bad:" + path
                db.upsert_track(fingerprint=fp, path=path, filename=fn, artist=None, title=None, folder=folder)
                db.save_features(fp, {"status": "failed"})
            except Exception:
                pass
            print(f"[fail] {fn}: {str(e)[:80]}", flush=True)
        if i % 10 == 0 or i == total:
            emit(i, total, ok)
    emit(total, total, ok)
    print(f"SOTA_ANALYZE DONE: ok={ok}/{total}", flush=True)


if __name__ == "__main__":
    main()
