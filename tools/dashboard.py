"""Generate a live HTML monitor for the Walkman analysis run.

Reads the crate-DB + run.log, plus a curated insights JSON (written by Claude
each 30-min cycle), and emits a self-contained dashboard.html artifact.
"""
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "walkman_analysis.db")
LOG = os.path.join(ROOT, "data", "run.log")
INSIGHTS = os.path.join(ROOT, "data", "insights.json")
OUT = os.path.join(ROOT, "data", "dashboard.html")
TOTAL = 3265

CAMELOT_ORDER = [f"{n}{l}" for n in range(1, 13) for l in ("A", "B")]


def q(conn, sql):
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.Error:
        return []


def gather():
    d = {"ok": 0, "failed": 0, "bpm": [], "camelot": {}, "energy": 0.0,
         "vocal": 0.0, "dance": 0.0, "bpm_bins": [], "fam": [], "extremes": {}}
    if os.path.exists(DB):
        c = sqlite3.connect(DB)
        d["ok"] = (q(c, "SELECT count(*) FROM features WHERE status='ok'") or [[0]])[0][0]
        d["failed"] = (q(c, "SELECT count(*) FROM features WHERE status='failed'") or [[0]])[0][0]
        row = q(c, "SELECT avg(energy),avg(is_vocal),avg(danceability) FROM features WHERE status='ok'")
        if row and row[0][0] is not None:
            d["energy"], d["vocal"], d["dance"] = row[0]
        # bpm histogram in 8-bpm bins from 60..200
        bins = defaultdict(int)
        for (b,) in q(c, "SELECT bpm FROM features WHERE status='ok' AND bpm IS NOT NULL"):
            bins[int(b // 8) * 8] += 1
            d["bpm"].append(b)
        d["bpm_bins"] = [(lo, bins.get(lo, 0)) for lo in range(56, 200, 8)]
        for cam, n in q(c, "SELECT camelot,count(*) FROM features WHERE status='ok' AND camelot IS NOT NULL GROUP BY camelot"):
            d["camelot"][cam] = n
        # family tally (light classification by folder/filename/artist)
        try:
            sys.path.insert(0, os.path.join(ROOT, "src"))
            from walkman_dj import rules as rulesmod
            rules = rulesmod.load_rules(os.path.join(ROOT, "config", "genre_rules.toml"))
            tally = defaultdict(int)
            for folder, filename, artist in q(c, "SELECT folder,filename,artist FROM tracks t JOIN features f USING(fingerprint) WHERE f.status='ok'"):
                fams = rulesmod.classify_track(rules, folder=folder, filename=filename, artist=artist, features={})
                for fam in (fams or ["misc"]):
                    tally[fam] += 1
            d["fam"] = sorted(tally.items(), key=lambda kv: -kv[1])
        except Exception:  # noqa: BLE001
            d["fam"] = []
        # extremes
        fast = q(c, "SELECT filename,bpm FROM features JOIN tracks USING(fingerprint) WHERE status='ok' ORDER BY bpm DESC LIMIT 1")
        slow = q(c, "SELECT filename,bpm FROM features JOIN tracks USING(fingerprint) WHERE status='ok' AND bpm>0 ORDER BY bpm ASC LIMIT 1")
        hot = q(c, "SELECT filename,energy FROM features JOIN tracks USING(fingerprint) WHERE status='ok' ORDER BY energy DESC LIMIT 1")
        if fast: d["extremes"]["fastest"] = tuple(fast[0])
        if slow: d["extremes"]["slowest"] = tuple(slow[0])
        if hot: d["extremes"]["hottest"] = tuple(hot[0])
        c.close()
    return d


def parse_log():
    info = {"phase": "starting", "batch": None, "nbatches": None, "last": None,
            "rate": None, "roadblocks": [], "recent": []}
    if not os.path.exists(LOG):
        return info
    lines = open(LOG, encoding="utf-8", errors="replace").read().splitlines()
    stamps = []
    for ln in lines:
        m = re.search(r"\[batch (\d+)/(\d+)\].*?ok (\d+).*?(\d+)s\s+\(", ln)
        if m:
            info["batch"], info["nbatches"] = int(m.group(1)), int(m.group(2))
            ts = ln[:8]
            stamps.append(ts)
            info["recent"].append(ln.strip())
        if "listing device" in ln: info["phase"] = "listing device"
        if "[analysis]" in ln: info["phase"] = "analyzing"
        if "[playlists]" in ln: info["phase"] = "building playlists"
        if "run done" in ln: info["phase"] = "complete"
        if any(k in ln for k in ("pull-fail", "push-fail", "[FATAL]", "offline")):
            info["roadblocks"].append(ln.strip()[:160])
    info["recent"] = info["recent"][-6:]
    # rate from batch timestamps (150 tracks each)
    if len(stamps) >= 2:
        def secs(t):
            h, m, s = map(int, t.split(":"))
            return h * 3600 + m * 60 + s
        dt = secs(stamps[-1]) - secs(stamps[0])
        if dt < 0: dt += 86400
        nb = len(stamps) - 1
        if dt > 0:
            info["rate"] = round(nb * 150 / (dt / 60), 1)  # tracks/min
    if lines:
        info["last"] = lines[-1][:8] if re.match(r"\d\d:\d\d:\d\d", lines[-1]) else None
    return info


def load_insights():
    if os.path.exists(INSIGHTS):
        try:
            return json.load(open(INSIGHTS, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"headline": "Analysis in progress.",
            "insights": [], "roadblocks_note": "", "cycle": 0}


def bar_row(label, count, mx, color):
    w = 0 if mx == 0 else round(100 * count / mx)
    return (f'<div class="row"><span class="rl">{label}</span>'
            f'<span class="rb"><i style="width:{w}%;background:{color}"></i></span>'
            f'<span class="rn">{count}</span></div>')


def render(d, log, ins):
    analyzed = d["ok"] + d["failed"]
    pct = round(100 * analyzed / TOTAL, 1)
    remaining = TOTAL - analyzed
    eta = "—"
    if log["rate"]:
        mins = remaining / log["rate"]
        eta = f"{int(mins//60)}h {int(mins%60)}m" if mins >= 60 else f"{int(mins)}m"
    now = time.strftime("%H:%M")
    cyc = ins.get("cycle", 0)

    # BPM histogram (vertical bars)
    mxb = max((n for _, n in d["bpm_bins"]), default=1) or 1
    bars = ""
    for lo, n in d["bpm_bins"]:
        h = round(100 * n / mxb)
        emph = " emph" if lo in (128, 136, 120) else ""
        bars += f'<div class="vbar{emph}" title="{lo}-{lo+7} bpm: {n}"><i style="height:{h}%"></i><b>{lo}</b></div>'

    # Camelot distribution (top keys)
    cam_sorted = sorted(d["camelot"].items(), key=lambda kv: -kv[1])[:12]
    mxc = max((n for _, n in cam_sorted), default=1) or 1
    cam_rows = "".join(bar_row(k, n, mxc, "var(--cyan)") for k, n in cam_sorted)

    # family
    mxf = max((n for _, n in d["fam"][:12]), default=1) or 1
    fam_rows = "".join(bar_row(pretty(f), n, mxf, "var(--amber)") for f, n in d["fam"][:12])

    # gauges
    def gauge(label, val, color, unit=""):
        pctv = round(val * 100)
        return (f'<div class="gauge"><div class="gv" style="--p:{pctv};--c:{color}">'
                f'<span>{val:.2f}{unit}</span></div><div class="gl">{label}</div></div>')

    insights_html = "".join(f"<li>{i}</li>" for i in ins.get("insights", [])) or \
        "<li>Gathering data…</li>"
    rb = log["roadblocks"]
    rb_note = ins.get("roadblocks_note", "")
    if rb:
        rb_html = "".join(f'<div class="rbk warn">{r}</div>' for r in rb[-6:])
        rb_status, rb_cls = f"{len(rb)} event(s)", "warn"
    else:
        rb_html = '<div class="rbk ok">No pull failures, no decode errors, device steady.</div>'
        rb_status, rb_cls = "all clear", "ok"
    if rb_note:
        rb_html = f'<div class="rbk note">{rb_note}</div>' + rb_html

    ext = d["extremes"]
    def ex(k, unit):
        if k in ext:
            fn = ext[k][0].rsplit(".", 1)[0][:34]
            return f'{fn} · <b>{ext[k][1]:.0f}{unit}</b>'
        return "—"

    recent = "".join(f'<div class="logln">{l}</div>' for l in log["recent"][-6:]) or \
        '<div class="logln">warming up…</div>'

    phase = log["phase"]
    live = phase == "analyzing"
    batch_txt = (f'BATCH {log["batch"]}/{log["nbatches"]}' if log["batch"] else phase.upper())

    return TEMPLATE.format(
        now=now, cyc=cyc, pct=pct, analyzed=analyzed, total=TOTAL, remaining=remaining,
        ok=d["ok"], failed=d["failed"], eta=eta,
        rate=(f'{log["rate"]}/min' if log["rate"] else "—"),
        batch_txt=batch_txt, live_cls=("live" if live else ""),
        headline=ins.get("headline", ""),
        bars=bars, cam_rows=cam_rows, fam_rows=fam_rows,
        g_energy=gauge("ENERGY", d["energy"], "var(--amber)"),
        g_vocal=gauge("VOCAL", d["vocal"], "var(--cyan)"),
        g_dance=gauge("DANCE", d["dance"], "var(--amber)"),
        insights=insights_html, rb_html=rb_html, rb_status=rb_status, rb_cls=rb_cls,
        fastest=ex("fastest", " bpm"), slowest=ex("slowest", " bpm"),
        hottest=(ext.get("hottest", ("—", 0))[0].rsplit(".", 1)[0][:34] if "hottest" in ext else "—"),
        recent=recent, phase=phase)


PRETTY = {
    "atlanta_trap": "Atlanta Trap", "opium_rage": "Opium / Rage", "melodic_trap": "Melodic Trap",
    "travis_rage": "Travis / Rage", "toronto": "Toronto", "lyrical_soul": "Lyrical / Soul",
    "alt_rnb": "Alt-R&B", "afrobeats": "Afrobeats", "tech_house": "Tech House",
    "organic_house": "Organic House", "nu_disco": "Nu-Disco", "ukg_bass": "UK Garage / Bass",
    "global_downtempo": "Global / Downtempo", "jersey_juke": "Jersey / Juke",
    "electro_classics": "Electro / Classics", "baile_edits": "Baile Edits",
    "brazilian_funk": "Brazilian Funk", "misc": "Unclassified",
}
def pretty(f): return PRETTY.get(f, f.replace("_", " ").title())


TEMPLATE = r"""<title>Walkman Crate — Live Analysis Monitor</title>
<style>
:root{{
  --bg:#0b0d13; --panel:#12151f; --panel2:#171b28; --line:#242a3a;
  --ink:#e7e9f0; --mut:#868ca0; --dim:#5a6076;
  --amber:#ff7a1a; --cyan:#37d3dc; --good:#3ddc84; --warn:#ffb340; --crit:#ff5470;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  --sans:system-ui,-apple-system,"Segoe UI",sans-serif;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:
  radial-gradient(1200px 600px at 80% -10%,rgba(255,122,26,.06),transparent 60%),
  radial-gradient(900px 500px at 0% 110%,rgba(55,211,220,.05),transparent 60%),
  var(--bg);color:var(--ink);font-family:var(--sans);
  font-feature-settings:"tnum";-webkit-font-smoothing:antialiased;}}
.wrap{{max-width:1120px;margin:0 auto;padding:26px 22px 60px;}}
.eyebrow{{font:600 11px/1 var(--sans);letter-spacing:.22em;color:var(--dim);text-transform:uppercase;}}
h1{{font:800 24px/1.1 var(--sans);letter-spacing:-.01em;margin:8px 0 2px;text-wrap:balance;}}
h1 .amp{{color:var(--amber);}}
.sub{{color:var(--mut);font:400 13.5px/1.5 var(--sans);max-width:64ch;margin:0 0 20px;}}
.mono{{font-family:var(--mono);}}

/* status rail */
.rail{{display:grid;grid-template-columns:1.4fr repeat(4,1fr);gap:12px;margin-bottom:14px;}}
.cell{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;}}
.cell .k{{font:600 10px/1 var(--sans);letter-spacing:.16em;color:var(--dim);text-transform:uppercase;}}
.cell .v{{font-family:var(--mono);font-size:26px;font-weight:600;margin-top:8px;letter-spacing:-.02em;}}
.cell .v small{{font-size:13px;color:var(--mut);font-weight:400;}}
.cell.prog{{grid-row:span 1;}}
.pbar{{height:10px;border-radius:6px;background:#0a0c12;border:1px solid var(--line);overflow:hidden;margin-top:10px;}}
.pbar i{{display:block;height:100%;width:{pct}%;
  background:linear-gradient(90deg,var(--amber),#ffb060);transition:width .6s;}}
.batch{{display:flex;align-items:center;gap:8px;font-family:var(--mono);font-size:13px;color:var(--cyan);}}
.dot{{width:8px;height:8px;border-radius:50%;background:var(--dim);}}
.batch.live .dot{{background:var(--good);box-shadow:0 0 0 0 rgba(61,220,132,.6);animation:pulse 1.6s infinite;}}
@keyframes pulse{{70%{{box-shadow:0 0 0 7px rgba(61,220,132,0)}}100%{{box-shadow:0 0 0 0 rgba(61,220,132,0)}}}}
@media(prefers-reduced-motion:reduce){{.batch.live .dot{{animation:none}}}}

/* grid */
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 18px 16px;}}
.card.wide{{grid-column:1/-1;}}
.card h2{{font:700 12px/1 var(--sans);letter-spacing:.14em;text-transform:uppercase;color:var(--mut);margin:0 0 16px;display:flex;justify-content:space-between;align-items:center;}}
.tag{{font-family:var(--mono);font-size:11px;padding:3px 8px;border-radius:20px;border:1px solid var(--line);color:var(--dim);}}
.tag.amber{{color:var(--amber);border-color:rgba(255,122,26,.35);}}
.tag.cyan{{color:var(--cyan);border-color:rgba(55,211,220,.35);}}

/* bpm histogram */
.hist{{display:flex;align-items:flex-end;gap:3px;height:150px;}}
.vbar{{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:100%;gap:5px;}}
.vbar i{{width:100%;background:linear-gradient(180deg,var(--amber),rgba(255,122,26,.25));border-radius:3px 3px 0 0;min-height:2px;}}
.vbar.emph i{{background:linear-gradient(180deg,#ffd08a,var(--amber));box-shadow:0 0 10px rgba(255,122,26,.4);}}
.vbar b{{font-family:var(--mono);font-size:8.5px;color:var(--dim);font-weight:500;}}

/* bar rows */
.row{{display:grid;grid-template-columns:44px 1fr 34px;align-items:center;gap:10px;margin-bottom:7px;}}
.rl{{font-family:var(--mono);font-size:12px;color:var(--ink);}}
.rb{{height:8px;background:#0a0c12;border-radius:5px;overflow:hidden;}}
.rb i{{display:block;height:100%;border-radius:5px;}}
.rn{{font-family:var(--mono);font-size:11px;color:var(--mut);text-align:right;}}
.fam .rl{{font-size:11px;color:var(--mut);width:auto;}}
.fam .row{{grid-template-columns:120px 1fr 34px;}}

/* gauges */
.gauges{{display:flex;gap:22px;justify-content:space-around;}}
.gauge{{text-align:center;}}
.gv{{width:96px;height:96px;border-radius:50%;display:grid;place-items:center;
  background:conic-gradient(var(--c) calc(var(--p)*1%),#0a0c12 0);position:relative;}}
.gv::before{{content:"";position:absolute;inset:9px;border-radius:50%;background:var(--panel);}}
.gv span{{position:relative;font-family:var(--mono);font-size:18px;font-weight:600;}}
.gl{{margin-top:9px;font:600 10px/1 var(--sans);letter-spacing:.14em;color:var(--dim);}}

/* insights */
.insights{{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:11px;}}
.insights li{{position:relative;padding-left:20px;font:400 13.5px/1.5 var(--sans);color:var(--ink);}}
.insights li::before{{content:"";position:absolute;left:2px;top:8px;width:7px;height:7px;border-radius:2px;background:var(--amber);transform:rotate(45deg);}}
.insights li b{{color:var(--cyan);font-weight:600;}}
.hl{{font:500 14px/1.55 var(--sans);color:var(--ink);border-left:2px solid var(--amber);padding:2px 0 2px 14px;margin:0 0 16px;}}

/* roadblocks + log */
.rbk{{font-family:var(--mono);font-size:12px;padding:9px 12px;border-radius:8px;margin-bottom:7px;border:1px solid var(--line);}}
.rbk.ok{{color:var(--good);border-color:rgba(61,220,132,.25);background:rgba(61,220,132,.05);}}
.rbk.warn{{color:var(--warn);border-color:rgba(255,179,64,.3);background:rgba(255,179,64,.05);}}
.rbk.note{{color:var(--mut);}}
.logfeed{{background:#080a0f;border:1px solid var(--line);border-radius:10px;padding:12px 14px;max-height:150px;overflow:auto;}}
.logln{{font-family:var(--mono);font-size:11.5px;color:var(--mut);line-height:1.7;white-space:nowrap;}}
.ext{{display:flex;gap:18px;flex-wrap:wrap;margin-top:4px;}}
.ext div{{font-family:var(--mono);font-size:12px;color:var(--mut);}}
.ext b{{color:var(--amber);}}
.foot{{margin-top:22px;color:var(--dim);font:400 12px/1.6 var(--sans);display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;}}
.pill{{font-family:var(--mono);font-size:11px;color:var(--good);}}
@media(max-width:760px){{.rail{{grid-template-columns:1fr 1fr}}.grid{{grid-template-columns:1fr}}.cell.prog{{grid-column:1/-1}}}}
</style>

<div class="wrap">
  <div class="eyebrow">Sony NW-A300 · crate analysis engine</div>
  <h1>Walkman Crate <span class="amp">/</span> Live Analysis Monitor</h1>
  <p class="sub">Streaming the library off the device in batches, extracting BPM, musical key, energy and structure per track, and sequencing micro-genre DJ sets. This board refreshes every ~30 minutes with fresh readings and curated findings.</p>

  <div class="rail">
    <div class="cell prog">
      <div class="k">Progress</div>
      <div class="v">{pct}<small>%</small></div>
      <div class="pbar"><i></i></div>
      <div class="batch {live_cls}" style="margin-top:10px"><span class="dot"></span>{batch_txt}</div>
    </div>
    <div class="cell"><div class="k">Analyzed</div><div class="v">{analyzed}<small>/{total}</small></div></div>
    <div class="cell"><div class="k">Remaining</div><div class="v">{remaining}</div></div>
    <div class="cell"><div class="k">Throughput</div><div class="v mono" style="font-size:20px">{rate}</div></div>
    <div class="cell"><div class="k">ETA</div><div class="v mono" style="font-size:20px">{eta}</div></div>
  </div>

  <div class="hl">{headline}</div>

  <div class="grid">
    <div class="card wide">
      <h2>Tempo distribution <span class="tag amber">BPM · 8-bpm bins</span></h2>
      <div class="hist">{bars}</div>
    </div>

    <div class="card">
      <h2>Harmonic keys <span class="tag cyan">Camelot · top 12</span></h2>
      {cam_rows}
    </div>

    <div class="card">
      <h2>Character <span class="tag">mean · 0–1</span></h2>
      <div class="gauges">{g_energy}{g_vocal}{g_dance}</div>
      <div class="ext">
        <div>fastest · <b>{fastest}</b></div>
        <div>slowest · <b>{slowest}</b></div>
      </div>
    </div>

    <div class="card fam">
      <h2>Genre families so far <span class="tag amber">rule-classified</span></h2>
      {fam_rows}
    </div>

    <div class="card">
      <h2>Roadblocks <span class="tag">{rb_status}</span></h2>
      {rb_html}
      <h2 style="margin-top:16px">Recent batches</h2>
      <div class="logfeed">{recent}</div>
    </div>

    <div class="card wide">
      <h2>Curated findings <span class="tag cyan">cycle {cyc}</span></h2>
      <ul class="insights">{insights}</ul>
    </div>
  </div>

  <div class="foot">
    <span>Updated {now} · phase: {phase} · this is a committed dark "booth" view — glanceable in a dim room.</span>
    <span class="pill">● refreshes ~every 30 min</span>
  </div>
</div>
"""


def main():
    d = gather()
    log = parse_log()
    ins = load_insights()
    html = render(d, log, ins)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {OUT} — analyzed={d['ok']+d['failed']} rate={log['rate']} phase={log['phase']}")


if __name__ == "__main__":
    main()
