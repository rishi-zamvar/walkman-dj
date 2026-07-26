#!/usr/bin/env python3
"""Phase 0 consolidation — MusicLake single source of truth.

Modes:
  (default) DRY RUN  — read-only. Indexes every source, computes the highest-quality-only
                       dedup plan, reports N unique / N to trash / N to pull / GB moving.
                       Fast: reuses the 6,749 DB fingerprints; unmatched files dedup by
                       (artist,title) tag / normalized filename. NOTHING is moved/trashed.
  --execute          — apply the approved plan: fingerprint-VERIFY each trash candidate,
                       Trash losers (never rm), pull device-only tracks into the lake,
                       regenerate the manifest. (built after dry-run approval)

Emits live KPIs/ETA to ~/Desktop/consolidation-monitor/progress.json throughout, so the
web monitor can render real-time progress."""
import os, sys, re, json, time, sqlite3, subprocess
sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/src")

HOME = os.path.expanduser("~")
LAKE = f"{HOME}/Desktop/MusicLake"
DB = "/Users/rishizamvar/Documents/Projects/walkman-dj/data/walkman_analysis.db"
DEV = "/storage/6439-6561/Music"
USB = "/Volumes/Lexar/Contents"
LOCAL_SOURCES = [("lake", LAKE), ("music", f"{HOME}/Desktop/music"), ("appmusic", f"{HOME}/Music")]
AUDIO_EXT = {".flac", ".wav", ".aiff", ".aif", ".m4a", ".aac", ".mp3", ".ogg", ".opus", ".wma"}
QRANK = {".flac": 3, ".wav": 3, ".aiff": 3, ".aif": 3, ".m4a": 2, ".aac": 2, ".mp3": 2,
         ".ogg": 1, ".opus": 1, ".wma": 1}
MON = f"{HOME}/Desktop/consolidation-monitor"
PROG = f"{MON}/progress.json"

_norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())

try:
    from mutagen import File as MutagenFile
except Exception:  # noqa: BLE001
    MutagenFile = None

_STATE = {"started": time.time()}


def emit(**kw):
    os.makedirs(MON, exist_ok=True)
    _STATE.update(kw)
    _STATE["ts"] = time.time()
    _STATE["elapsed_s"] = round(_STATE["ts"] - _STATE["started"], 1)
    proc, tot = _STATE.get("processed", 0), _STATE.get("total", 0)
    if proc and _STATE["elapsed_s"] > 0.5:
        rate = proc / _STATE["elapsed_s"]
        _STATE["rate_per_s"] = round(rate, 1)
        _STATE["eta_s"] = round((tot - proc) / rate) if rate > 0 and tot else None
    tmp = PROG + ".tmp"
    json.dump(_STATE, open(tmp, "w"))
    os.replace(tmp, PROG)


def logline(msg):
    _STATE.setdefault("log", [])
    _STATE["log"] = (_STATE["log"] + [f"{time.strftime('%H:%M:%S')}  {msg}"])[-12:]
    emit()


def load_db():
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    fp_by_base, meta = {}, {}
    for r in con.execute("SELECT fingerprint,filename,artist,title FROM tracks"):
        base = _norm(os.path.splitext(r["filename"] or "")[0])
        if base and base not in fp_by_base:
            fp_by_base[base] = r["fingerprint"]
        meta[r["fingerprint"]] = {"artist": r["artist"], "title": r["title"]}
    con.close()
    return fp_by_base, meta


def tags_of(path):
    if not MutagenFile:
        return None, None
    try:
        m = MutagenFile(path, easy=True)
        if not m:
            return None, None
        return (m.get("artist") or [None])[0], (m.get("title") or [None])[0]
    except Exception:  # noqa: BLE001
        return None, None


def guess_from_name(base):
    if " - " in base:
        a, t = base.split(" - ", 1)
        return a.strip(), t.strip()
    return None, base


def walk_local(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dp, _, fns in os.walk(root):
        for fn in fns:
            ext = os.path.splitext(fn)[1].lower()
            if ext in AUDIO_EXT:
                p = os.path.join(dp, fn)
                try:
                    out.append((p, fn, ext, os.path.getsize(p)))
                except OSError:
                    pass
    return out


def walk_walkman():
    exprs = " -o ".join(f"-iname '*{e}'" for e in (".flac", ".mp3", ".m4a", ".wav", ".aac"))
    cmd = f"find {DEV} -type f \\( {exprs} \\) -exec stat -c '%s|%n' {{}} +"
    r = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, timeout=600)
    out = []
    for line in r.stdout.replace("\r", "").splitlines():
        if "|" not in line:
            continue
        size, path = line.split("|", 1)
        fn = os.path.basename(path)
        try:
            out.append((path, fn, os.path.splitext(fn)[1].lower(), int(size)))
        except ValueError:
            pass
    return out


def build_index():
    emit(stage="index", phase_label="Loading DB fingerprints", processed=0, total=0)
    logline("loading 6,749 DB fingerprints…")
    fp_by_base, meta = load_db()

    # Gather LOCAL sources first, then collapse by physical realpath so symlinks
    # (e.g. ~/Desktop/music links that resolve into MusicLake) don't count twice.
    src_pri = {"lake": 0, "music": 1, "appmusic": 2, "usb": 3}
    by_real = {}
    n_links = 0
    for i, (src, root) in enumerate([("lake", LAKE), ("music", f"{HOME}/Desktop/music"),
                                     ("appmusic", f"{HOME}/Music"), ("usb", USB)]):
        emit(stage="index", phase_label=f"Scanning {src}", processed=i, total=5)
        logline(f"scanning {src}…")
        found = walk_local(root)
        links = 0
        for p, fn, ext, sz in found:
            rp = os.path.realpath(p)
            if os.path.islink(p):
                links += 1
            prev = by_real.get(rp)
            if prev is None or src_pri[src] < src_pri[prev[0]]:
                # canonical path = the real file; attribute to highest-priority source seen
                by_real[rp] = [src, rp, os.path.basename(rp), ext, sz, True]
        n_links += links
        logline(f"  {src}: {len(found)} entries ({links} symlinks)")
    rows = list(by_real.values())
    logline(f"collapsed to {len(rows)} unique physical local files ({n_links} symlinks folded)")

    emit(stage="index", phase_label="Scanning walkman", processed=4, total=5)
    logline("scanning walkman via adb…")
    for p, fn, ext, sz in walk_walkman():
        rows.append(["walkman", p, fn, ext, sz, False])
    emit(stage="index", phase_label="Scan complete", processed=5, total=5, instances=len(rows))

    # assign fingerprints from DB (free); unmatched -> None (dedup by tag/name)
    emit(stage="fingerprint", phase_label="Matching fingerprints", processed=0, total=len(rows))
    matched = 0
    for i, row in enumerate(rows):
        fp = fp_by_base.get(_norm(os.path.splitext(row[2])[0]))
        row.append(fp)
        if fp:
            matched += 1
        if i % 500 == 0:
            emit(stage="fingerprint", phase_label="Matching fingerprints",
                 processed=i, total=len(rows), matched=matched)
    emit(stage="fingerprint", phase_label="Fingerprint match complete",
         processed=len(rows), total=len(rows), matched=matched)
    logline(f"{matched}/{len(rows)} matched a DB fingerprint (no decode needed)")
    return rows, meta


def dedup(rows):
    emit(stage="dedup", phase_label="Grouping duplicates", processed=0, total=len(rows))
    groups = {}
    for i, row in enumerate(rows):
        src, p, fn, ext, sz, local, fp = row
        if fp:
            key = ("fp", fp)
        else:
            # filename-only dedup (no file I/O — the slow USB makes tag reads unusable here;
            # execution fingerprint-verifies each trash candidate before it's Trashed).
            base = os.path.splitext(fn)[0]
            a, t = guess_from_name(base)
            key = ("tag", _norm(a), _norm(t)) if (a and t) else ("base", _norm(base))
        groups.setdefault(key, []).append(row)
        if i % 500 == 0:
            emit(stage="dedup", phase_label="Grouping duplicates", processed=i, total=len(rows),
                 unique=len(groups))
    emit(stage="dedup", phase_label="Resolving winners", processed=len(rows), total=len(rows),
         unique=len(groups))

    rankkey = lambda r: (QRANK.get(r[3], 0), 1 if r[0] == "lake" else 0, r[4])
    to_trash, to_pull, keep = [], [], []
    for members in groups.values():
        members.sort(key=rankkey, reverse=True)
        w = members[0]
        (keep if w[0] in ("lake", "music", "appmusic") else to_pull).append(w)
        for m in members[1:]:
            if m[0] in ("lake", "music", "appmusic"):
                to_trash.append(m)
    return groups, keep, to_trash, to_pull


def main():
    rows, meta = build_index()
    groups, keep, to_trash, to_pull = dedup(rows)
    gb = lambda items: round(sum(m[4] for m in items) / 1e9, 2)
    by_source = {s: sum(1 for r in rows if r[0] == s)
                 for s in ("lake", "music", "appmusic", "usb", "walkman")}
    plan = {
        "total_instances": len(rows), "unique_songs": len(groups),
        "keep_in_lake": len(keep),
        "to_trash_count": len(to_trash), "to_trash_gb": gb(to_trash),
        "to_pull_count": len(to_pull), "to_pull_gb": gb(to_pull),
        "by_source": by_source,
    }
    json.dump({"plan": plan, "to_trash": [m[1] for m in to_trash],
               "to_pull": [m[1] for m in to_pull]},
              open(f"{LAKE}/_consolidation-plan.json", "w"), indent=1)
    emit(stage="done", phase_label="DRY RUN COMPLETE — awaiting approval",
         processed=len(rows), total=len(rows), plan=plan)
    logline("DRY RUN COMPLETE — review the plan, then approve execution")

    print("\n" + "=" * 60)
    print("PHASE 0 DRY-RUN — CONSOLIDATION PLAN")
    print("=" * 60)
    print(f"  file instances scanned : {plan['total_instances']}")
    print(f"  UNIQUE songs           : {plan['unique_songs']}")
    print(f"  already in lake (keep) : {plan['keep_in_lake']}")
    print(f"  -> TO TRASH (dupes)    : {plan['to_trash_count']}  ({plan['to_trash_gb']} GB reclaimed)")
    print(f"  -> TO PULL into lake   : {plan['to_pull_count']}  ({plan['to_pull_gb']} GB)")
    print(f"  source breakdown       : {by_source}")
    print("=" * 60)
    print("NO FILES MOVED OR TRASHED.  CONSOLIDATE_DRYRUN DONE")


if __name__ == "__main__":
    main()
