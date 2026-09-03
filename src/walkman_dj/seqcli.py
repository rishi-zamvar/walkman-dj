"""CLI: order a family / full set / anchor bridge -> .m3u8 + annotated .md."""
import argparse
import os
import tomllib
from walkman_dj.sequence import order_family

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CFG = os.path.join(ROOT, "config", "sequencer.toml")
DEFAULT_DB = os.path.join(ROOT, "data", "archive_walkman_analysis.db")


def load_cfg(path=DEFAULT_CFG):
    with open(path, "rb") as f:
        return tomllib.load(f)


def render_m3u8(ordered, music_root):
    lines = ["#EXTM3U"]
    for t in ordered:
        dur = int(t.get("duration") or 0)
        title = t.get("title") or t["fingerprint"]
        rel = os.path.relpath(t["path"], music_root)
        lines.append(f"#EXTINF:{dur},{title}")
        lines.append(rel)
    return "\n".join(lines) + "\n"


def render_md(ordered, hard, isolated, template):
    hardset = {(h["from"], h["to"]) for h in hard}
    out = [f"# Set — template: {template}", ""]
    for i, t in enumerate(ordered):
        out.append(
            f"{i+1:>2}. **{t.get('title') or t['fingerprint']}** — "
            f"{t.get('bpm')} BPM · {t.get('camelot')} · E={float(t.get('energy') or 0):.2f}")
        if i + 1 < len(ordered):
            nxt = ordered[i + 1]
            if (t["fingerprint"], nxt["fingerprint"]) in hardset:
                out.append("    - `## Hard transition` — no harmonic/BPM bridge, mix manually")
    out += ["", "## No clean transition", ""]
    out += [f"- {fp}" for fp in isolated] if isolated else ["_(none)_"]
    return "\n".join(out) + "\n"


def _load_family_tracks(db_path, family):
    """Join features(audio) + tracks(path/title) + track_genre(family), status='ok'."""
    import sqlite3
    from walkman_dj import genre_store
    fams = genre_store.get_all(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = []
    for r in con.execute(
            "SELECT f.*, t.path AS path, t.title AS title, t.filename AS filename "
            "FROM features f JOIN tracks t USING(fingerprint) WHERE f.status='ok'"):
        d = dict(r)
        fp = d["fingerprint"]
        if fams.get(fp, {}).get("family") != family:
            continue
        d["path"] = d.get("path") or fp
        d["title"] = d.get("title") or d.get("filename") or fp
        rows.append(d)
    con.close()
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--config", default=DEFAULT_CFG)
    ap.add_argument("--family", required=True)
    ap.add_argument("--template", default=None)
    ap.add_argument("--music-root", default="/storage/6439-6561/Music")
    ap.add_argument("--out", required=True, help="output basename (writes .m3u8 + .md)")
    a = ap.parse_args(argv)
    cfg = load_cfg(a.config)
    template = a.template or cfg["default_template"]
    tracks = _load_family_tracks(a.db, a.family)
    ordered, hard, isolated = order_family(tracks, cfg, template=template)
    with open(a.out + ".m3u8", "w") as f:
        f.write(render_m3u8(ordered, a.music_root))
    with open(a.out + ".md", "w") as f:
        f.write(render_md(ordered, hard, isolated, template))
    print(f"{a.family}: {len(ordered)} ordered, {len(hard)} hard, {len(isolated)} isolated")


if __name__ == "__main__":
    main()
