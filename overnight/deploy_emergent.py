"""Deploy emergent community sets to the Walkman. Loads analyzed on-device tracks,
builds emergent sets, writes one ordered .m3u8 per set (device-relative paths),
backs up + removes the superseded fixed-genre crates. Reuses deploy_sets helpers."""
import os, sys, re, subprocess, datetime, sqlite3
sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/src")
sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/overnight")
from walkman_dj import seqcli, emergent, genre_store
import deploy_sets

DEV = "/storage/6439-6561/Music"
DB = "/Users/rishizamvar/Documents/Projects/walkman-dj/data/walkman_analysis.db"
BACKUP = os.path.expanduser(f"~/Desktop/walkman-emergent-backup-{datetime.date.today()}")
_safe = lambda s: re.sub(r'[/:]', "-", s).strip()


def render_set_m3u8(ordered, music_root):
    lines = ["#EXTM3U"]
    for t in ordered:
        lines.append(f"#EXTINF:{int(t.get('duration') or 0)},{t.get('title') or ''}")
        lines.append(os.path.relpath(t["path"], music_root))
    return "\n".join(lines) + "\n"


def _device_rel_by_name():
    r = subprocess.run(["adb", "shell", f"cd {DEV} && find . -type f "
                        r"\( -iname '*.flac' -o -iname '*.mp3' -o -iname '*.m4a' -o -iname '*.wav' \)"],
                       capture_output=True, text=True, timeout=180)
    out = {}
    for l in r.stdout.replace("\r", "").splitlines():
        p = l.strip()
        p = p[2:] if p.startswith("./") else p
        if p:
            out[deploy_sets._norm(os.path.splitext(os.path.basename(p))[0])] = p
    return out


def load_ondevice_tracks():
    """Analyzed tracks currently on the device, with family + current device path,
    matched by filename (DB path may be stale after reorg)."""
    feat = deploy_sets.features_by_filename()
    fams = genre_store.get_all(DB)
    devrel = _device_rel_by_name()
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    meta = {r["fingerprint"]: dict(r) for r in con.execute("SELECT * FROM tracks")}
    buckets = {}
    try:
        buckets = {fp: b for fp, b in con.execute("SELECT fingerprint, bucket FROM track_bucket")}
    except sqlite3.OperationalError:
        pass
    con.close()
    out = []
    for key, d in feat.items():
        fp = d["fingerprint"]
        g = fams.get(fp)
        if not g:
            continue
        cur = devrel.get(key)
        if not cur:
            continue
        t = dict(d)
        t["family"] = g["family"]; t["canon_genre"] = g["canon_genre"]
        t["bucket"] = buckets.get(fp)          # 'A' vocal/rap | 'B' music-forward | None
        t["path"] = f"{DEV}/{cur}"
        t["title"] = (meta.get(fp) or {}).get("title") or os.path.splitext(os.path.basename(cur))[0]
        out.append(t)
    return out


def main(push=True):
    os.makedirs(BACKUP, exist_ok=True)
    for remote in deploy_sets.list_playlists():
        base = remote.rsplit("/", 1)[-1]
        subprocess.run(["adb", "pull", remote, os.path.join(BACKUP, base)], capture_output=True)
    print(f"backed up existing playlists -> {BACKUP}")
    cfg = seqcli.load_cfg()
    tracks = load_ondevice_tracks()
    print(f"{len(tracks)} on-device analyzed tracks")
    sets = emergent.build_sets(tracks, cfg)
    print(f"\n{'set':40} {'n':>4} {'hard':>5}  push")
    for s in sets:
        if s["n"] < 2:
            continue
        name = _safe(s["name"])
        m3u = render_set_m3u8(s["ordered"], DEV)
        local = f"/tmp/em_{deploy_sets._norm(name)}.m3u8"
        open(local, "w").write(m3u)
        pushed = ""
        if push:
            pr = subprocess.run(["adb", "push", local, f"{DEV}/{name}.m3u8"], capture_output=True, text=True)
            pushed = "pushed" if ("pushed" in pr.stdout + pr.stderr or "1 file" in pr.stdout + pr.stderr) else "?"
        print(f"{s['name'][:40]:40} {s['n']:>4} {len(s['hard']):>5}  {pushed}")
    if push:
        for nm in deploy_sets.GENRE_PLAYLISTS:
            subprocess.run(["adb", "shell", f'rm "{DEV}/{nm}.m3u8"'], capture_output=True)
    print("DEPLOY_EMERGENT DONE")


if __name__ == "__main__":
    main(push="--dry-run" not in sys.argv)
