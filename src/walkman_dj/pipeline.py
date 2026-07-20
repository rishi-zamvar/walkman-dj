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

from walkman_dj import device, analyze, rules as rulesmod, live
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
    # split sub-families (the 23 that actually ship)
    "opium_rage_rage": "Opium Den", "opium_rage_melodic": "Vamp Anthems",
    "atlanta_trap_rage": "Trap Furnace", "atlanta_trap_melodic": "Codeine Dreams",
    "melodic_trap_rage": "Rockstar Riot", "melodic_trap_melodic": "Cloud Nine",
    "travis_rage": "Astroworld Rage",
    "toronto_rage": "6ix Energy", "toronto_melodic": "Midnight 6ix",
    "lyrical_soul_rage": "Pen Game", "lyrical_soul_melodic": "Sunday Soul",
    "alt_rnb_rage": "Golden Hour", "alt_rnb_melodic": "Velvet Hours",
    "tech_house_rage": "Sweat Box", "tech_house_melodic": "Basement Warmup",
    "afrobeats": "Lagos Nights", "organic_house": "Sunrise Ritual",
    "nu_disco": "Groove Theory", "ukg_bass": "London Garage",
    "global_downtempo": "Dusty Passport", "jersey_juke": "Jersey Bounce",
    "electro_classics": "Robot Disco", "baile_edits": "Favela Bounce",
    # base families (rarely produced; kept for safety)
    "atlanta_trap": "Trap Furnace", "opium_rage": "Opium Den",
    "melodic_trap": "Cloud Nine", "toronto": "Midnight 6ix",
    "lyrical_soul": "Pen Game", "alt_rnb": "Velvet Hours",
    "tech_house": "Sweat Box", "brazilian_funk": "Favela Bounce",
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


# BPM-band sub-crates OFF by default — the user wants ~20-25 total playlists,
# not 100+. The vibe sets (with micro-splits) are the deliverable.
MAKE_BAND_CRATES = False
BAND_FAMILIES = {"tech_house", "organic_house", "nu_disco", "ukg_bass",
                 "baile_edits", "jersey_juke", "electro_classics",
                 "global_downtempo", "atlanta_trap", "opium_rage", "afrobeats"}


def fallback_family(t):
    """Route an otherwise-unclassified track into an electronic family by tempo/energy.
    Keeps the eclectic Singles out of one giant 'Misc' blob."""
    bpm = t.get("bpm") or 0
    e = t.get("energy") or 0.5
    if bpm >= 158:
        return "jersey_juke"          # juke / footwork / jungle range
    if bpm >= 143:
        return "ukg_bass"             # UK garage / bass
    if bpm >= 132:
        return "baile_edits"          # baile / hard edits
    if bpm >= 126:
        return "tech_house"
    if bpm >= 120:
        return "organic_house" if e < 0.52 else "tech_house"
    if bpm >= 110:
        return "nu_disco"
    return "global_downtempo"         # downtempo / world / slow


def safe_name(name):
    return "".join(c if c.isalnum() or c in " -&" else "_" for c in name).strip()


# ---------- analysis worker (top-level for spawn) ----------

def _analyze_worker(task):
    idx, path, prior, track, folder = task
    import signal
    from walkman_dj import live
    wid = os.getpid()

    def _timeout(signum, frame):
        raise TimeoutError("analysis exceeded 120s")

    try:
        live.set_worker(wid, track, "fingerprint", folder)
        try:
            signal.signal(signal.SIGALRM, _timeout)
            signal.alarm(120)                       # hard per-track timeout
        except (ValueError, AttributeError):
            pass                                    # not main thread / unsupported
        fp = fingerprint_file(path)
        feat = analyze.analyze_file(
            path, prior=prior,
            on_stage=lambda s: live.set_worker(wid, track, s, folder))
        signal.alarm(0)
        live.worker_idle(wid)
        return idx, fp, feat
    except Exception as e:  # noqa: BLE001
        try:
            signal.alarm(0)
        except Exception:  # noqa: BLE001
            pass
        live.worker_idle(wid)
        return idx, None, {"status": "failed", "segments": [], "error": str(e)[:200]}


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

def _wait_online(log, every=10, max_wait=3600):
    """Block until the device is reachable (overnight resilience)."""
    waited = 0
    while not device.device_online():
        if waited == 0:
            log("[device] offline — waiting for it to come back...")
        time.sleep(every)
        waited += every
        if waited >= max_wait:
            raise RuntimeError(f"device offline > {max_wait}s")
    if waited:
        log(f"[device] back online after {waited}s")


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
    live.reset()
    done_base = len(files) - len(todo)
    pool = Pool(workers, maxtasksperchild=40)
    try:
        for bi, group in enumerate(batches, 1):
            t0 = time.time()
            pulled = []  # (DeviceFile, localpath)
            retry_queue = list(group)
            failed_twice = []
            for attempt in (1, 2):
                still = []
                for pi, f in enumerate(retry_queue, 1):
                    live.set_main(phase="pulling", pulling=f.filename,
                                  folder=f.folder, pull_i=pi, pull_n=len(retry_queue),
                                  batch=bi, nbatches=len(batches), workers=workers,
                                  done=done_base, total=len(files))
                    _wait_online(log)
                    try:
                        local = device.pull(f.path, tmp_dir)
                        pulled.append((f, local))
                    except Exception as e:  # noqa: BLE001
                        if attempt == 2:
                            log(f"[pull-fail] {f.path}: {e}")
                            failed_twice.append(f)
                        else:
                            still.append(f)
                retry_queue = still
                if not retry_queue:
                    break
            live.set_main(phase="analyzing", batch=bi, nbatches=len(batches),
                          workers=workers, done=done_base, total=len(files),
                          batch_size=len(pulled))
            meta = {}  # idx -> (DeviceFile, localpath)
            tasks = []
            for idx, (f, local) in enumerate(pulled):
                meta[idx] = (f, local)
                tasks.append((idx, local, prior_for(rules, f.folder, f.filename),
                              os.path.splitext(f.filename)[0], f.folder))
            ok = 0
            # stream results as each track finishes (true per-track progress)
            for idx, fp, feat in pool.imap_unordered(_analyze_worker, tasks):
                f, local = meta[idx]
                if fp is None:
                    fp = "bad:" + f.path  # record the failure under a stable id
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
                    done_base += 1
                    live.push_done(os.path.splitext(f.filename)[0], feat.get("bpm"),
                                   feat.get("camelot"), feat.get("energy"), f.folder)
                    live.set_main(phase="analyzing", batch=bi, nbatches=len(batches),
                                  workers=workers, done=done_base, total=len(files),
                                  batch_size=len(pulled))
                try:
                    os.remove(local)                # free disk as each finishes
                except OSError:
                    pass
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
            fams = [fallback_family(t)]
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

    # 3b. BPM-band crates — disabled by default (keeps total ~20-25 playlists)
    if MAKE_BAND_CRATES:
        for fam, members in list(fam_members.items()):
            if fam not in BAND_FAMILIES:
                continue
            bands = defaultdict(list)
            for t in members:
                if t.get("bpm"):
                    bands[bpm_band(t["bpm"])].append(t)
            for band, bt in bands.items():
                if len(bt) >= 15:
                    all_playlists[f"{fam}::band::{band}"] = bt

    for key, members in sorted(all_playlists.items(), key=lambda kv: -len(kv[1])):
        if len(members) < 10:          # no playlist smaller than 10 songs
            continue
        energies = [m["energy"] or 0.0 for m in members]
        arc = assign_arc(energies)
        seq = order_playlist(members, arc)
        if "::band::" in key:
            fam, band = key.split("::band::")
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
