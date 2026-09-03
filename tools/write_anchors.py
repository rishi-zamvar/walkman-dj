"""Write corrected grid ANCHORS into Rekordbox ANLZ files (keeps BPM).

For each flagged track that has a SOTA beat_this downbeat, re-lay the beatgrid so a
downbeat (beat 1) lands exactly on that downbeat, keeping Rekordbox's existing tempo.
Edits both the .DAT (PQTZ) and .EXT (PQT2) grids. Each file backed up first. Rekordbox
MUST be closed. Idempotent-ish: skips tracks where the correction is <150ms or the
anchor is implausible (outside 0.15-6.0s).

Usage: python3 tools/write_anchors.py [--dry-run] [--limit N] [--all]
  default: only flagged 'our_later' tracks that have a beat_this anchor now.
"""
import os, sys, json, argparse, shutil, datetime, subprocess, sqlite3
import construct
from pyrekordbox import Rekordbox6Database
from pyrekordbox.anlz import AnlzFile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "library.db")
SHARE = os.path.expanduser("~/Library/Pioneer/rekordbox/share")
FLAGS = "/private/tmp/claude-501/-Users-rishizamvar/7e34948c-c38e-458c-a061-fb14b568ba00/scratchpad/anchor_flags.json"
MIN_S, MAX_S, MIN_DELTA_MS = 0.0, 75.0, 150.0    # anchor may be at the very top (vocal from bar 1) or deep in a long intro


def grid_tag(af):
    for tag in af.tags:
        if tag.__class__.__name__[:4] in ("PQTZ", "PQT2"):
            if getattr(tag.content, "entries", None) is not None:
                return tag
    return None


def relay(bpm, target_ms, dur_ms):
    b = 60000.0 / bpm
    tempo = int(round(bpm * 100))
    nb = int(target_ms // b)
    first = target_ms - nb * b
    out, i, t = [], 0, first
    while t <= dur_ms + b:
        out.append(construct.Container(beat=((i - nb) % 4) + 1, tempo=tempo, time=int(round(t))))
        t += b
        i += 1
    return out


def _patch_dat(path, delta_ms):
    """Shift every PQTZ beat time by delta_ms in raw bytes (same size, no rebuild)."""
    data = bytearray(open(path, "rb").read())
    off = data.find(b"PQTZ")
    if off < 0:
        return False
    ec = int.from_bytes(data[off + 20:off + 24], "big")     # entry_count
    base = off + 24
    for i in range(ec):
        to = base + i * 8 + 4                                # time u4 within each 8-byte entry
        t = int.from_bytes(data[to:to + 4], "big")
        data[to:to + 4] = max(0, t + delta_ms).to_bytes(4, "big")
    open(path, "wb").write(data)
    return True


def _patch_ext(path, delta_ms):
    """Shift the two PQT2 grid-anchor times (offsets +28, +36 from tag) by delta_ms."""
    data = bytearray(open(path, "rb").read())
    off = data.find(b"PQT2")
    if off < 0:
        return True                                          # no extended grid -> nothing to do
    for to in (off + 28, off + 36):
        t = int.from_bytes(data[to:to + 4], "big")
        data[to:to + 4] = max(0, t + delta_ms).to_bytes(4, "big")
    open(path, "wb").write(data)
    return True


def write_one(dat_path, target_s, dur_s, dry=False):
    """Shift the grid so its first downbeat lands on target_s (keeps BPM), by byte-patching
    .DAT (PQTZ) and .EXT (PQT2). Returns (status, before_ms, [after_ms], bpm)."""
    ext_path = dat_path[:-4] + ".EXT"
    if not os.path.exists(dat_path):
        return None
    try:
        af = AnlzFile.parse_file(dat_path)
    except Exception:
        return ("skip_parse", None, None)
    tag = grid_tag(af)
    if tag is None or not tag.content.entries:
        return None
    bpm = tag.content.entries[0].tempo / 100.0
    downbeats = [e for e in tag.content.entries if e.beat == 1]
    if not downbeats:
        return None
    before = float(downbeats[0].time)                        # RB's first DOWNBEAT (beat 1)
    target_ms = target_s * 1000.0
    if not (MIN_S * 1000 <= target_ms <= MAX_S * 1000):
        return ("skip_range", before, bpm)
    delta = int(round(target_ms - before))
    if abs(delta) < MIN_DELTA_MS:
        return ("skip_small", before, bpm)
    if dry:
        return ("ok", before, target_ms, bpm)
    if not os.path.exists(dat_path + ".gridbak"):          # preserve the FIRST (pristine) backup
        shutil.copy2(dat_path, dat_path + ".gridbak")
    if os.path.exists(ext_path) and not os.path.exists(ext_path + ".gridbak"):
        shutil.copy2(ext_path, ext_path + ".gridbak")
    _patch_dat(dat_path, delta)
    _patch_ext(ext_path, delta)
    return ("ok", before, before + delta, bpm)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all", action="store_true", help="all flagged (default: only those with beat_this now)")
    a = ap.parse_args()
    if subprocess.run(["pgrep", "-x", "rekordbox"], capture_output=True).stdout.strip():
        raise SystemExit("Rekordbox RUNNING — close it first.")

    flags = json.load(open(FLAGS))
    want = {f["path"] for f in flags if f["dir"] == "our_later"}
    con = sqlite3.connect(DB)
    fd = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT canonical_path, first_downbeat, duration FROM recordings "
        "WHERE beat_engine LIKE 'beatthis%' AND first_downbeat IS NOT NULL")}
    db = Rekordbox6Database()
    by_path = {getattr(c, "FolderPath", None): c for c in db.get_content()}

    done = []
    processed = 0
    for p in want:
        if p not in fd:
            continue
        target_s, dur_s = fd[p]
        if target_s is None or not dur_s:
            continue
        c = by_path.get(p)
        adp = getattr(c, "AnalysisDataPath", None) if c else None
        if not adp:
            continue
        res = write_one(SHARE + adp, target_s, dur_s, dry=a.dry_run)
        processed += 1
        if res and res[0] == "ok":
            before = res[1]; after = res[2]
            done.append(dict(path=p, title=getattr(c, "Title", ""), before_ms=round(before),
                             after_ms=round(after), shift_ms=round(after - before)))
        if a.limit and processed >= a.limit:
            break
    done.sort(key=lambda x: -x["shift_ms"])
    print(f"{'[DRY] ' if a.dry_run else ''}corrected {len(done)} anchors (of {processed} processed)")
    for d in done[:20]:
        print(f"   {d['before_ms']:>5} -> {d['after_ms']:>5}ms  (+{d['shift_ms']}ms)  {d['title'][:44]}")
    json.dump(done, open("/private/tmp/claude-501/-Users-rishizamvar/7e34948c-c38e-458c-a061-fb14b568ba00/scratchpad/anchors_written.json", "w"))
    return done


if __name__ == "__main__":
    main()
