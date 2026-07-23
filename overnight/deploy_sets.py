#!/usr/bin/env python3
"""Reorder the Walkman's genre playlists harmonically with the Set Sequencer.

Design: the DEVICE playlist is the source of truth for membership + current paths
(tracks.path in the DB is stale after the overnight reorg). We only apply ORDERING:
parse each existing .m3u8, match every entry to its MIR features by filename, run
order_family, then rewrite the SAME entries (same device paths) in harmonic order.
Entries with no features (unanalyzed) are appended unordered — never dropped.
Backs up all playlists first. Vibe/cultural/discovery playlists are left untouched."""
import os, sys, re, subprocess, datetime, sqlite3
sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/src")
from walkman_dj import seqcli, sequence

DEV = "/storage/6439-6561/Music"
DB = "/Users/rishizamvar/Documents/Projects/walkman-dj/data/walkman_analysis.db"
BACKUP = os.path.expanduser(f"~/Desktop/walkman-playlists-backup-{datetime.date.today()}")

# device playlist name -> energy template (wave for club-breaks that want peaks&valleys)
TEMPLATES = {
    "Baile Funk": "wave", "Jersey Club": "wave", "Alt R&B": "plateau_drop",
}
DEFAULT_TEMPLATE = "single_peak"
# only reorder these single-genre crates; leave vibe/cultural/discovery multi-family lists alone
GENRE_PLAYLISTS = {
    "Opium & Rage", "Atlanta Trap", "Travis Scott", "Melodic Trap", "Toronto Nights",
    "Lyrical Hip-Hop", "Alt R&B", "Afrobeats", "Tech House", "Organic & Afro House",
    "Nu-Disco & Funk", "Baile Funk", "UK Garage", "Jersey Club", "Electro",
    "Downtempo", "Hip-Hop", "Pop",
}
_norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def features_by_filename():
    """filename-basename (normalized) -> feature dict (fingerprint + MIR fields)."""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    m = {}
    for r in con.execute(
            "SELECT f.*, t.filename AS filename FROM features f JOIN tracks t USING(fingerprint) "
            "WHERE f.status='ok'"):
        d = dict(r)
        key = _norm(os.path.splitext(d["filename"] or "")[0])
        if key and key not in m:            # first wins on filename collision
            m[key] = d
    con.close()
    return m


def list_playlists():
    r = subprocess.run(["adb", "shell", f"find {DEV} -maxdepth 1 -iname '*.m3u8' 2>/dev/null"],
                       capture_output=True, text=True)
    return [l.strip() for l in r.stdout.replace("\r", "").splitlines() if l.strip()]


def backup_existing(remotes):
    os.makedirs(BACKUP, exist_ok=True)
    for remote in remotes:
        base = remote.rsplit("/", 1)[-1]
        subprocess.run(["adb", "pull", remote, os.path.join(BACKUP, base)], capture_output=True)
    print(f"backed up {len(remotes)} playlists -> {BACKUP}")


def parse_m3u8(text):
    """Return list of (extinf_or_None, path) entries in order."""
    entries, pending = [], None
    for line in text.replace("\r", "").splitlines():
        if line.startswith("#EXTINF"):
            pending = line
        elif line.startswith("#") or not line.strip():
            continue
        else:
            entries.append((pending, line.strip()))
            pending = None
    return entries


def reorder_playlist(name, remote, feat_map, push):
    r = subprocess.run(["adb", "shell", f'cat "{remote}"'], capture_output=True, text=True)
    entries = parse_m3u8(r.stdout)
    if not entries:
        return (name, 0, 0, 0, 0, "empty")
    # attach features by filename
    tracks, unmatched = [], []
    for extinf, path in entries:
        key = _norm(os.path.splitext(os.path.basename(path))[0])
        d = feat_map.get(key)
        if d:
            t = dict(d)
            t["_path"] = path
            t["_extinf"] = extinf
            tracks.append(t)
        else:
            unmatched.append((extinf, path))
    if len(tracks) <= 2:
        return (name, len(entries), 0, 0, len(unmatched), "too few analyzed to reorder")
    # order_family needs a stable id; use _path as fingerprint surrogate to dedup within set
    for t in tracks:
        t["fingerprint"] = t["_path"]
    tmpl = TEMPLATES.get(name, DEFAULT_TEMPLATE)
    cfg = seqcli.load_cfg()
    ordered, hard, iso = sequence.order_family(tracks, cfg, template=tmpl)
    # rebuild m3u8: ordered entries, then any isolated matched tracks (no harmonic
    # neighbor), then unmatched/unanalyzed — NOTHING is ever dropped.
    lines = ["#EXTM3U"]
    emitted = set()

    def _emit(extinf, path):
        if extinf:
            lines.append(extinf)
        lines.append(path)

    for t in ordered:
        _emit(t.get("_extinf"), t["_path"])
        emitted.add(t["_path"])
    for t in tracks:                       # isolated matched tracks order_family set aside
        if t["_path"] not in emitted:
            _emit(t.get("_extinf"), t["_path"])
            emitted.add(t["_path"])
    for extinf, path in unmatched:
        _emit(extinf, path)
    out = "\n".join(lines) + "\n"
    n_out = sum(1 for ln in lines if ln and not ln.startswith("#"))
    if n_out != len(entries):              # membership must be preserved exactly
        return (name, len(entries), 0, 0, len(unmatched), f"SKIP: {n_out}!={len(entries)} count mismatch")
    local = f"/tmp/wk_{_norm(name)}.m3u8"
    with open(local, "w") as f:
        f.write(out)
    pushed = ""
    if push:
        pr = subprocess.run(["adb", "push", local, remote], capture_output=True, text=True)
        pushed = "pushed" if "1 file" in (pr.stdout + pr.stderr) or "pushed" in (pr.stdout + pr.stderr) else "PUSH?"
    return (name, len(entries), len(hard), len(iso), len(unmatched), pushed)


def main(push=True):
    remotes = list_playlists()
    backup_existing(remotes)
    feat_map = features_by_filename()
    print(f"feature index: {len(feat_map)} filenames\n")
    print(f"{'playlist':22} {'tracks':>6} {'hard':>5} {'iso':>4} {'unmatched':>9}  push")
    done = 0
    for remote in remotes:
        name = os.path.splitext(remote.rsplit("/", 1)[-1])[0]
        if name not in GENRE_PLAYLISTS:
            continue
        res = reorder_playlist(name, remote, feat_map, push)
        print(f"{res[0]:22} {res[1]:>6} {res[2]:>5} {res[3]:>4} {res[4]:>9}  {res[5]}")
        done += 1
    print(f"\nREORDERED {done} genre playlists (of {len(remotes)} total). DEPLOY DONE")


if __name__ == "__main__":
    main(push="--dry-run" not in sys.argv)
