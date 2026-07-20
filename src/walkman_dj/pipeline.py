"""Orchestrator: batched pull -> analyze -> DB -> classify -> sequence -> playlists.

Checkpointed and resumable: every analyzed track is committed immediately; a
re-run skips tracks already `ok` (by device path, so it doesn't even re-pull).
"""
import argparse
import csv
import os
import shutil
import sys
import time
import traceback
from collections import defaultdict
from multiprocessing import Pool

from walkman_dj import device, analyze, rules as rulesmod
from walkman_dj.db import CrateDB
from walkman_dj.fingerprint import fingerprint_file
from walkman_dj.microsplit import split_family
from walkman_dj.sequence import assign_arc, order_playlist
from walkman_dj.playlist import write_playlist
from walkman_dj.report import build_report
from walkman_dj.notify import ping

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DB = os.path.join(ROOT, "data", "walkman_analysis.db")
DEFAULT_CFG = os.path.join(ROOT, "config", "genre_rules.toml")
DEFAULT_TMP = os.path.join(ROOT, "audio_tmp")
DEFAULT_PLDIR = os.path.join(ROOT, "data", "playlists")

DISPLAY = {
    "atlanta_trap": "Atlanta Trap", "opium_rage": "Opium Rage",
    "opium_rage_rage": "Opium Rage — Hard", "opium_rage_melodic": "Opium Rage — Melodic",
    "atlanta_trap_rage": "Atlanta Trap — Hard", "atlanta_trap_melodic": "Atlanta Trap — Melodic",
    "melodic_trap": "Melodic Trap", "melodic_trap_rage": "Melodic Trap — Hard",
    "melodic_trap_melodic": "Melodic Trap — Soft", "travis_rage": "Travis / Rage",
    "toronto": "Toronto", "toronto_rage": "Toronto — Uptempo", "toronto_melodic": "Toronto — Late Night",
    "lyrical_soul": "Lyrical / Soul", "alt_rnb": "Alt-R&B / Late Night",
    "afrobeats": "Afrobeats", "tech_house": "Tech House", "organic_house": "Organic / Afro House",
    "nu_disco": "Nu-Disco / Funky", "ukg_bass": "UK Garage / Bass",
    "global_downtempo": "Global / Downtempo", "jersey_juke": "Jersey / Juke",
    "electro_classics": "Electro / Classics", "baile_edits": "Baile Edits",
    "brazilian_funk": "Brazilian Funk",
}


# ---------- pure helpers (unit-tested) ----------

def plan_batches(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def pending_only(files, database, fp_of):
    return [f for f in files if database.needs_analysis(fp_of(f))]


def prior_for(rules, folder, filename):
    fams = rulesmod.classify_track(rules, folder=folder, filename=filename,
                                   artist=None, features={})
    if fams:
        return rulesmod.bpm_prior(rules, fams[0])
    return (70, 180)


def bpm_band(bpm, width=4):
    lo = int(bpm // width) * width
    return f"{lo}-{lo + width - 1}"


def pretty(family):
    return DISPLAY.get(family, family.replace("_", " ").title())


def safe_name(name):
    return "".join(c if c.isalnum() or c in " -&" else "_" for c in name).strip()


# ---------- analysis worker (top-level for spawn) ----------

def _analyze_worker(task):
    path, prior = task
    try:
        fp = fingerprint_file(path)
        feat = analyze.analyze_file(path, prior=prior)
        return fp, feat
    except Exception as e:  # noqa: BLE001
        return None, {"status": "failed", "segments": [], "error": str(e)[:200]}


def _read_tags(path):
    try:
        import mutagen
        m = mutagen.File(path, easy=True)
        if not m:
            return None, None
        a = (m.get("artist") or [None])[0]
        t = (m.get("title") or [None])[0]
        return a, t
    except Exception:  # noqa: BLE001
        return None, None


def _guess_from_filename(filename):
    stem = os.path.splitext(filename)[0]
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        return a.strip(), t.strip()
    return None, stem


# ---------- analysis phase ----------

def run_analysis(db, rules, files, tmp_dir, batch=150, workers=9, log=print):
    os.makedirs(tmp_dir, exist_ok=True)
    # resume: skip files whose path is already ok
    ok_paths = set()
    for r in db.all_ok():
        ok_paths.add(r["path"])
    todo = [f for f in files if f.path not in ok_paths]
    log(f"[analysis] {len(files)} total, {len(files) - len(todo)} already ok, "
        f"{len(todo)} to analyze")
    batches = plan_batches(todo, batch)
    pool = Pool(workers, maxtasksperchild=40)
    try:
        for bi, group in enumerate(batches, 1):
            t0 = time.time()
            pulled = []  # (DeviceFile, localpath)
            for f in group:
                try:
                    local = device.pull(f.path, tmp_dir)
                    pulled.append((f, local))
                except Exception as e:  # noqa: BLE001
                    log(f"[pull-fail] {f.path}: {e}")
            tasks = [(local, prior_for(rules, f.folder, f.filename))
                     for f, local in pulled]
            results = pool.map(_analyze_worker, tasks)
            ok = 0
            for (f, local), (fp, feat) in zip(pulled, results):
                if fp is None:
                    fp = "bad:" + f.path  # still record the failure under a stable id
                artist, title = _read_tags(local)
                if not artist:
                    ga, gt = _guess_from_filename(f.filename)
                    artist = artist or ga
                    title = title or gt
                db.upsert_track(fingerprint=fp, path=f.path, filename=f.filename,
                                artist=artist, title=title, folder=f.folder)
                db.save_features(fp, feat)
                if feat.get("segments"):
                    db.save_segments(fp, feat["segments"])
                if feat.get("status") == "ok":
                    ok += 1
            for _, local in pulled:
                try:
                    os.remove(local)
                except OSError:
                    pass
            dt = time.time() - t0
            log(f"[batch {bi}/{len(batches)}] pulled {len(pulled)}, ok {ok}, "
                f"{dt:.0f}s  ({db.counts()})")
    finally:
        pool.close()
        pool.join()
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------- playlist phase ----------

def build_playlists(db, rules, dry_run, playlists_dir, log=print):
    os.makedirs(playlists_dir, exist_ok=True)
    tracks = db.all_ok()
    log(f"[playlists] {len(tracks)} ok tracks")

    # 1. classify -> families (allow duplicates)
    fam_members = defaultdict(list)
    assignment_rows = []
    for t in tracks:
        fams = rulesmod.classify_track(
            rules, folder=t["folder"], filename=t["filename"],
            artist=t["artist"], features=t)
        if not fams:
            fams = ["misc"]
        assignment_rows.append((t["fingerprint"], t["path"], "|".join(fams)))
        for fam in fams:
            fam_members[fam].append(t)

    # 2. micro-split big families
    final_groups = defaultdict(list)
    for fam, members in fam_members.items():
        mapping = split_family(fam, members)
        for t in members:
            final_groups[mapping[t["fingerprint"]]].append(t)

    # 3. per group -> arc + order + write + push
    summary = []
    all_playlists = dict(final_groups)

    # 3b. BPM-band crates per top-level family (>=8 tracks in a band)
    for fam, members in list(fam_members.items()):
        bands = defaultdict(list)
        for t in members:
            if t.get("bpm"):
                bands[bpm_band(t["bpm"])].append(t)
        for band, bt in bands.items():
            if len(bt) >= 8:
                all_playlists[f"{fam}::band::{band}"] = bt

    for key, members in sorted(all_playlists.items(), key=lambda kv: -len(kv[1])):
        if len(members) < 4:
            continue
        energies = [m["energy"] or 0.0 for m in members]
        arc = assign_arc(energies)
        seq = order_playlist(members, arc)
        if "::band::" in key:
            fam, _, band = key.split("::band::")
            name = f"{pretty(fam)} {band}bpm"
        else:
            name = pretty(key)
        fname = safe_name(name) + ".m3u8"
        out = os.path.join(playlists_dir, fname)
        write_playlist(seq, out)
        if not dry_run:
            try:
                device.push(out)
            except Exception as e:  # noqa: BLE001
                log(f"[push-fail] {fname}: {e}")
        summary.append({"name": name, "arc": arc, "count": len(seq)})
        log(f"[playlist] {name}  arc={arc}  n={len(seq)}")

    # assignment.csv
    with open(os.path.join(ROOT, "data", "assignment.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["fingerprint", "path", "families"])
        w.writerows(assignment_rows)
    return summary


def _histograms(db):
    tracks = db.all_ok()
    bpm_hist = defaultdict(int)
    cam_hist = defaultdict(int)
    for t in tracks:
        if t.get("bpm"):
            lo = int(t["bpm"] // 10) * 10
            bpm_hist[f"{lo}-{lo + 9}"] += 1
        if t.get("camelot"):
            cam_hist[t["camelot"]] += 1
    return (dict(sorted(bpm_hist.items())),
            dict(sorted(cam_hist.items(), key=lambda kv: -kv[1])))


# ---------- main ----------

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, default=150)
    ap.add_argument("--workers", type=int, default=9)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--config", default=DEFAULT_CFG)
    ap.add_argument("--tmp", default=DEFAULT_TMP)
    ap.add_argument("--playlists-out", default=DEFAULT_PLDIR)
    ap.add_argument("--skip-analysis", action="store_true",
                    help="only rebuild playlists from existing DB")
    args = ap.parse_args(argv)

    os.makedirs(os.path.dirname(args.db), exist_ok=True)
    db = CrateDB(args.db)
    db.init()
    rules = rulesmod.load_rules(args.config)

    def log(msg):
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    try:
        if not args.skip_analysis:
            log("listing device audio...")
            files = device.list_audio()
            log(f"device has {len(files)} audio files")
            if args.limit:
                files = files[:args.limit]
                log(f"limited to {len(files)}")
            run_analysis(db, rules, files, args.tmp, args.batch, args.workers, log)

        summary = build_playlists(db, rules, args.dry_run, args.playlists_out, log)
        counts = db.counts()
        bpm_hist, cam_hist = _histograms(db)
        stats = {"analyzed": counts["ok"], "failed": counts["failed"],
                 "playlists": summary, "bpm_hist": bpm_hist, "camelot_hist": cam_hist}
        report_path = os.path.join(ROOT, "REPORT.md")
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(build_report(stats))
        log(f"report -> {report_path}")
        msg = (f"Walkman run done — {counts['ok']} analyzed, {counts['failed']} failed, "
               f"{len(summary)} playlists built"
               + (" (dry-run, not pushed)" if args.dry_run else ""))
        log(msg)
        if not args.dry_run:
            try:
                ping("ZeeChat: " + msg)
            except Exception as e:  # noqa: BLE001
                log(f"[notify-fail] {e}")
    except Exception:
        tb = traceback.format_exc()
        log("[FATAL] " + tb)
        try:
            ping("ZeeChat: Walkman run BLOCKED — " + tb.splitlines()[-1][:150])
        except Exception:  # noqa: BLE001
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
