#!/usr/bin/env python3
"""Render each ordered genre playlist as a node/edge transition graph.
Nodes = tracks (x = set position, y = energy arc), colored by Camelot key.
Edges: the chosen path (bold; green=harmonic, red-dashed=hard cut) + faint
local compatible edges (the mixable neighborhood). One self-contained HTML."""
import os, sys, re, subprocess, html, math
sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/src")
sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/overnight")
from walkman_dj import seqcli, family as fammod
from walkman_dj.graph import pair_matrix, HARD_CUT
import deploy_sets

DEV = "/storage/6439-6561/Music"
OUT = os.path.expanduser("~/Desktop/DJ Set Graphs.html")
W, H, PAD = 1120, 210, 30
_norm = deploy_sets._norm


def _hue(camelot):
    try:
        n = int(str(camelot)[:-1])
    except Exception:
        return "#666"
    return f"hsl({(n - 1) * 30},60%,60%)"


def _load_ordered(name, feat_map):
    """Read the deployed ordered m3u8 for a playlist, return matched tracks in order."""
    local = f"/tmp/wk_{_norm(name)}.m3u8"
    if not os.path.exists(local):
        return []
    txt = open(local, encoding="utf-8").read()
    tracks = []
    for _extinf, path in deploy_sets.parse_m3u8(txt):
        d = feat_map.get(_norm(os.path.splitext(os.path.basename(path))[0]))
        if d:
            t = dict(d)
            t["fingerprint"] = path
            tracks.append(t)
    return tracks


def _svg(name, tracks, cfg):
    n = len(tracks)
    if n < 2:
        return f"<div class=empty>{html.escape(name)}: too few analyzed tracks to graph</div>"
    raw = {t["fingerprint"]: (t.get("energy") or 0.0) for t in tracks}
    en = fammod.rescale_energy(raw)
    ev = [en[t["fingerprint"]] for t in tracks]
    pair = pair_matrix(tracks, None, ev, cfg)
    xs = [PAD + (W - 2 * PAD) * (i / (n - 1)) for i in range(n)]
    ys = [PAD + (H - 2 * PAD) * (1 - ev[i]) for i in range(n)]
    parts = [f'<svg viewBox="0 0 {W} {H}" class=g>']
    # faint local compatible edges (mixable neighborhood, ±8 positions)
    for i in range(n):
        for j in range(i + 1, min(n, i + 9)):
            if pair[i][j] < HARD_CUT:
                parts.append(f'<line x1={xs[i]:.0f} y1={ys[i]:.0f} x2={xs[j]:.0f} '
                             f'y2={ys[j]:.0f} stroke="#2f3a2f" stroke-width=.6/>')
    # chosen path
    hard = 0
    for i in range(n - 1):
        h = pair[i][i + 1] >= HARD_CUT
        hard += h
        col, dash = ("#c0554e", "4 3") if h else ("#7fae7f", "0")
        parts.append(f'<line x1={xs[i]:.0f} y1={ys[i]:.0f} x2={xs[i+1]:.0f} '
                     f'y2={ys[i+1]:.0f} stroke="{col}" stroke-width=1.7 stroke-dasharray="{dash}"/>')
    # nodes
    for i in range(n):
        parts.append(f'<circle cx={xs[i]:.0f} cy={ys[i]:.0f} r=3.2 fill="{_hue(tracks[i].get("camelot"))}" '
                     f'stroke="#111" stroke-width=.5><title>{html.escape(str(tracks[i].get("title") or "")[:60])} · '
                     f'{tracks[i].get("bpm") and round(tracks[i]["bpm"])}bpm · {tracks[i].get("camelot")}</title></circle>')
    parts.append("</svg>")
    sub = f"{n} tracks · {hard} hard cut{'s' if hard != 1 else ''} · energy arc top→bottom = high→low"
    return (f'<section><h2>{html.escape(name)} '
            f'<span class=meta>{sub}</span></h2>{"".join(parts)}</section>')


def main():
    feat_map = deploy_sets.features_by_filename()
    cfg = seqcli.load_cfg()
    order = ["Baile Funk", "Jersey Club", "UK Garage", "Tech House", "Organic & Afro House",
             "Nu-Disco & Funk", "Electro", "Atlanta Trap", "Opium & Rage", "Travis Scott",
             "Melodic Trap", "Toronto Nights", "Lyrical Hip-Hop", "Hip-Hop", "Alt R&B",
             "Afrobeats", "Downtempo", "Pop"]
    secs = [_svg(nm, _load_ordered(nm, feat_map), cfg) for nm in order]
    nav = " · ".join(f'<a href="#{_norm(nm)}">{html.escape(nm)}</a>' for nm in order)
    body = "".join(s.replace("<section>", f'<section id="{_norm(nm)}">')
                   for nm, s in zip(order, secs))
    doc = f"""<!doctype html><html><head><meta charset=utf-8><title>DJ Set Graphs</title><style>
 body{{background:#0d0d0d;color:#e8e4d8;font-family:ui-monospace,Menlo,monospace;margin:0;padding:24px}}
 h1{{font-size:16px;letter-spacing:2px;color:#c9b872}} .nav{{font-size:11px;line-height:2;margin-bottom:20px}}
 .nav a{{color:#8a8574;text-decoration:none}} .nav a:hover{{color:#c9b872}}
 section{{background:#141414;border:1px solid #262626;border-radius:8px;padding:12px 16px;margin-bottom:16px}}
 h2{{font-size:13px;color:#c9b872;margin:0 0 6px;letter-spacing:1px}}
 .meta{{color:#6a6656;font-size:11px;font-weight:normal;letter-spacing:0}}
 svg.g{{width:100%;height:auto;background:#0f0f0f;border-radius:4px}}
 .empty{{color:#6a6656;font-size:12px;padding:8px 0}}
 .legend{{font-size:11px;color:#8a8574;margin:8px 0 18px}} .legend b{{color:#7fae7f}} .legend i{{color:#c0554e;font-style:normal}}
</style></head><body>
<h1>WALKMAN · DJ SET TRANSITION GRAPHS</h1>
<div class=legend>node = track (color = Camelot key) · <b>green line</b> = harmonic transition ·
 <i>red dashed</i> = hard cut (mix manually) · faint lines = other mixable pairs nearby · y-axis = energy arc</div>
<div class=nav>{nav}</div>
{body}
</body></html>"""
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
