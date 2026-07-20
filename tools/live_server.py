"""Local live monitor server for the Walkman analysis run.

Serves a self-refreshing page (polls every ~1.5s) that streams what each
worker is doing right now, whether work is progressing or stalled, and the
curated 30-minute insights. Run:  PYTHONPATH=src python3 tools/live_server.py
Then open http://localhost:8765
"""
import json
import os
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from walkman_dj import live  # noqa: E402

DB = os.path.join(ROOT, "data", "walkman_analysis.db")
INSIGHTS = os.path.join(ROOT, "data", "insights.json")
LOG = os.path.join(ROOT, "data", "run.log")
TOTAL = 3265
PORT = 8765


_last_counts = {"ok": 0, "failed": 0}


def db_counts():
    out = {"ok": 0, "failed": 0}
    try:
        c = sqlite3.connect(DB, timeout=3.0)
        c.execute("PRAGMA busy_timeout=3000")
        out["ok"] = c.execute("SELECT count(*) FROM features WHERE status='ok'").fetchone()[0]
        out["failed"] = c.execute("SELECT count(*) FROM features WHERE status='failed'").fetchone()[0]
        c.close()
        _last_counts.update(out)          # cache last good read
    except sqlite3.Error:
        return dict(_last_counts)          # serve cached counts if DB is busy
    return out


def logfile_rate():
    """Honest end-to-end rate from completed batch durations (includes pull)."""
    try:
        import re
        tracks = secs = 0
        lines = open(LOG, encoding="utf-8", errors="replace").read().splitlines()
        batchlines = [l for l in lines if re.search(r"\[batch \d+/\d+\]", l)][-5:]
        for l in batchlines:
            m = re.search(r"pulled (\d+),.*?(\d+)s\s+\(", l)
            if m:
                tracks += int(m.group(1))
                secs += int(m.group(2))
        if tracks and secs:
            return round(tracks / secs * 60, 1)
    except Exception:  # noqa: BLE001
        pass
    return None


def build_status():
    st = live.read_state()
    counts = db_counts()
    analyzed = counts["ok"] + counts["failed"]
    main = st.get("main", {})
    total = main.get("total", TOTAL)
    workers = st.get("workers", [])
    phase = main.get("phase", "starting")
    now = st["now"]
    main_age = round(now - main.get("ts", now), 1) if main else 999
    newest_worker_age = min((w["age"] for w in workers), default=999)

    # liveness verdict
    if phase == "complete" or analyzed >= total:
        hb = ("done", "Analysis complete")
    elif phase == "analyzing":
        if workers and newest_worker_age <= 15:
            hb = ("live", f"Working — {len(workers)} workers active")
        elif newest_worker_age <= 30:
            hb = ("live", "Working")
        else:
            hb = ("stall", f"No worker update for {int(newest_worker_age)}s — possible stall")
    elif phase == "pulling":
        if main_age <= 40:
            hb = ("pulling", "Streaming files off the device")
        else:
            hb = ("stall", f"Pull stalled {int(main_age)}s — device asleep?")
    else:
        hb = ("starting", "Starting up")

    # prefer honest log-based rate (includes device-pull time); fall back to live
    rate = logfile_rate() or st.get("rate")
    remaining = max(0, total - analyzed)
    eta = None
    if rate:
        m = remaining / rate
        eta = f"{int(m//60)}h {int(m%60)}m" if m >= 60 else f"{int(m)}m"

    insights = {}
    try:
        insights = json.load(open(INSIGHTS, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass

    return {
        "analyzed": analyzed, "ok": counts["ok"], "failed": counts["failed"],
        "total": total, "remaining": remaining,
        "pct": round(100 * analyzed / total, 1) if total else 0,
        "phase": phase, "heartbeat": {"state": hb[0], "detail": hb[1]},
        "main": main, "main_age": main_age, "workers": workers,
        "completed": st.get("completed", []), "rate": rate, "eta": eta,
        "stages": live.STAGES, "stage_labels": live.STAGE_LABEL,
        "insights": insights, "server_time": time.strftime("%H:%M:%S"),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        if self.path.startswith("/status.json"):
            self._send(json.dumps(build_status()), "application/json")
        else:
            self._send(PAGE, "text/html; charset=utf-8")


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Walkman Crate — Live Task Monitor</title>
<style>
:root{
  --bg:#0b0d13;--panel:#12151f;--panel2:#171b28;--line:#242a3a;
  --ink:#e7e9f0;--mut:#868ca0;--dim:#5a6076;
  --amber:#ff7a1a;--cyan:#37d3dc;--good:#3ddc84;--warn:#ffb340;--crit:#ff5470;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  --sans:system-ui,-apple-system,"Segoe UI",sans-serif;
}
*{box-sizing:border-box}
body{margin:0;background:
  radial-gradient(1100px 560px at 82% -12%,rgba(255,122,26,.06),transparent 60%),
  radial-gradient(820px 460px at -5% 112%,rgba(55,211,220,.05),transparent 60%),var(--bg);
  color:var(--ink);font-family:var(--sans);-webkit-font-smoothing:antialiased;
  font-feature-settings:"tnum";}
.wrap{max-width:1180px;margin:0 auto;padding:24px 22px 64px;}
.eyebrow{font:600 11px/1 var(--sans);letter-spacing:.22em;color:var(--dim);text-transform:uppercase;}
h1{font:800 23px/1.1 var(--sans);letter-spacing:-.01em;margin:8px 0 16px;}
h1 .s{color:var(--amber);}
.mono{font-family:var(--mono);}

/* heartbeat banner */
.hb{display:flex;align-items:center;gap:12px;padding:12px 16px;border-radius:12px;
  border:1px solid var(--line);background:var(--panel);margin-bottom:14px;}
.hb .beat{width:11px;height:11px;border-radius:50%;flex:0 0 auto;}
.hb .txt{font-family:var(--mono);font-size:13.5px;}
.hb .meta{margin-left:auto;font-family:var(--mono);font-size:12px;color:var(--dim);}
.hb.live .beat{background:var(--good);animation:pulse 1.3s infinite;}
.hb.live .txt{color:var(--good);}
.hb.pulling .beat{background:var(--cyan);animation:pulse 1.3s infinite;}
.hb.pulling .txt{color:var(--cyan);}
.hb.stall{border-color:rgba(255,84,112,.4);background:rgba(255,84,112,.07);}
.hb.stall .beat{background:var(--crit);animation:blink .7s steps(2) infinite;}
.hb.stall .txt{color:var(--crit);}
.hb.done .beat{background:var(--amber);} .hb.done .txt{color:var(--amber);}
.hb.starting .beat{background:var(--dim);}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(61,220,132,.5)}70%{box-shadow:0 0 0 8px rgba(61,220,132,0)}100%{box-shadow:0 0 0 0 rgba(61,220,132,0)}}
@keyframes blink{50%{opacity:.25}}
@media(prefers-reduced-motion:reduce){.hb .beat{animation:none!important}}

/* rail */
.rail{display:grid;grid-template-columns:1.5fr repeat(4,1fr);gap:12px;margin-bottom:16px;}
.cell{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:13px 15px;}
.cell .k{font:600 10px/1 var(--sans);letter-spacing:.15em;color:var(--dim);text-transform:uppercase;}
.cell .v{font-family:var(--mono);font-size:24px;font-weight:600;margin-top:8px;}
.cell .v small{font-size:12px;color:var(--mut);font-weight:400;}
.pbar{height:9px;border-radius:6px;background:#0a0c12;border:1px solid var(--line);overflow:hidden;margin-top:9px;}
.pbar i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--amber),#ffb060);transition:width .5s;}

.sec{font:700 11px/1 var(--sans);letter-spacing:.15em;text-transform:uppercase;color:var(--mut);margin:22px 0 12px;}

/* worker lanes */
.lanes{display:grid;grid-template-columns:repeat(3,1fr);gap:11px;}
.lane{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:13px 14px;min-height:92px;transition:border-color .3s;}
.lane.act{border-color:rgba(255,122,26,.35);}
.lane.idle{opacity:.4;}
.lane .top{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;}
.lane .wid{font-family:var(--mono);font-size:10px;color:var(--dim);letter-spacing:.05em;}
.lane .age{font-family:var(--mono);font-size:10px;color:var(--dim);}
.lane .age.warn{color:var(--warn);} .lane .age.bad{color:var(--crit);}
.lane .track{font:600 13px/1.35 var(--sans);color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.lane .folder{font-family:var(--mono);font-size:10.5px;color:var(--mut);margin-bottom:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.lane .stagelbl{font-family:var(--mono);font-size:11px;color:var(--amber);margin-bottom:6px;}
.stagedots{display:flex;gap:4px;}
.sd{flex:1;height:4px;border-radius:2px;background:#0a0c12;}
.sd.done{background:var(--cyan);} .sd.act{background:var(--amber);box-shadow:0 0 7px rgba(255,122,26,.5);}

/* pull panel */
.pull{background:var(--panel);border:1px solid rgba(55,211,220,.3);border-radius:12px;padding:14px 16px;}
.pull .lbl{font-family:var(--mono);font-size:11px;color:var(--cyan);letter-spacing:.1em;margin-bottom:8px;}
.pull .file{font:600 15px/1.3 var(--sans);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pull .sub{font-family:var(--mono);font-size:11px;color:var(--mut);margin-top:6px;}

/* completed feed */
.feed{display:flex;flex-direction:column;gap:6px;max-height:270px;overflow:auto;}
.done-row{display:grid;grid-template-columns:1fr auto auto auto;gap:8px;align-items:center;
  background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:7px 11px;}
.done-row .t{font:500 12.5px/1.2 var(--sans);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.chip{font-family:var(--mono);font-size:10.5px;padding:2px 7px;border-radius:20px;border:1px solid var(--line);}
.chip.bpm{color:var(--amber);border-color:rgba(255,122,26,.3);}
.chip.key{color:var(--cyan);border-color:rgba(55,211,220,.3);}
.chip.en{color:var(--mut);}

/* insights */
.grid2{display:grid;grid-template-columns:1.15fr .85fr;gap:14px;align-items:start;}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:17px 18px;}
.card h2{font:700 12px/1 var(--sans);letter-spacing:.13em;text-transform:uppercase;color:var(--mut);margin:0 0 14px;display:flex;justify-content:space-between;align-items:center;}
.tag{font-family:var(--mono);font-size:11px;padding:3px 8px;border-radius:20px;border:1px solid var(--line);color:var(--dim);}
.hl{font:500 13.5px/1.55 var(--sans);border-left:2px solid var(--amber);padding:2px 0 2px 13px;margin:0 0 14px;color:var(--ink);}
.ins{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:10px;}
.ins li{position:relative;padding-left:19px;font:400 13px/1.5 var(--sans);}
.ins li::before{content:"";position:absolute;left:2px;top:7px;width:7px;height:7px;border-radius:2px;background:var(--amber);transform:rotate(45deg);}
.ins li b{color:var(--cyan);font-weight:600;}
.note{font-family:var(--mono);font-size:11.5px;color:var(--mut);margin-top:12px;padding-top:12px;border-top:1px solid var(--line);line-height:1.6;}
.foot{margin-top:22px;color:var(--dim);font:400 12px/1.6 var(--sans);display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;}
@media(max-width:820px){.rail{grid-template-columns:1fr 1fr}.lanes{grid-template-columns:1fr 1fr}.grid2{grid-template-columns:1fr}}
</style></head><body>
<div class="wrap">
  <div class="eyebrow">Sony NW-A300 · crate analysis engine · localhost:8765</div>
  <h1>Live Task Monitor <span class="s">/</span> what's happening right now</h1>

  <div id="hb" class="hb starting"><span class="beat"></span><span class="txt">connecting…</span><span class="meta"></span></div>

  <div class="rail">
    <div class="cell"><div class="k">Progress</div><div class="v"><span id="pct">0</span><small>%</small></div><div class="pbar"><i id="pbar"></i></div></div>
    <div class="cell"><div class="k">Analyzed</div><div class="v"><span id="analyzed">0</span><small>/<span id="total">—</span></small></div></div>
    <div class="cell"><div class="k">Remaining</div><div class="v" id="remaining">—</div></div>
    <div class="cell"><div class="k">Throughput</div><div class="v mono" style="font-size:19px" id="rate">—</div></div>
    <div class="cell"><div class="k">ETA</div><div class="v mono" style="font-size:19px" id="eta">—</div></div>
  </div>

  <div id="pullwrap"></div>

  <div class="sec">Workers — live analysis stage</div>
  <div class="lanes" id="lanes"></div>

  <div class="grid2">
    <div>
      <div class="sec">Curated findings <span class="tag" id="cyc" style="margin-left:8px"></span></div>
      <div class="card">
        <div class="hl" id="headline">Gathering data…</div>
        <ul class="ins" id="insights"></ul>
        <div class="note" id="rbnote"></div>
      </div>
    </div>
    <div>
      <div class="sec">Just finished</div>
      <div class="feed" id="feed"></div>
    </div>
  </div>

  <div class="foot">
    <span id="ftime">—</span>
    <span>Polls every 1.5s · curated findings refresh ~every 30 min · committed dark booth view</span>
  </div>
</div>

<script>
const STAGE_ORDER = ["fingerprint","load","tempo","key","energy","structure"];
const $ = id => document.getElementById(id);

function laneHTML(w, stages, labels){
  const cur = STAGE_ORDER.indexOf(w.stage);
  const dots = STAGE_ORDER.map((s,i)=>{
    let c = i<cur?"done":(i===cur?"act":"");
    return `<span class="sd ${c}"></span>`;
  }).join("");
  const ageCls = w.age>20?"bad":(w.age>10?"warn":"");
  const lbl = labels[w.stage] || w.stage;
  return `<div class="lane act">
    <div class="top"><span class="wid">pid ${w.wid}</span><span class="age ${ageCls}">${w.age}s</span></div>
    <div class="track">${esc(w.track||"—")}</div>
    <div class="folder">${esc(w.folder||"")}</div>
    <div class="stagelbl">▸ ${esc(lbl)}</div>
    <div class="stagedots">${dots}</div></div>`;
}
function idleLane(){return `<div class="lane idle"><div class="top"><span class="wid">worker</span></div><div class="track" style="color:var(--dim)">idle</div><div class="folder">waiting for work</div></div>`;}
function esc(s){return (s||"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));}

async function poll(){
  let d;
  try{ d = await (await fetch("/status.json",{cache:"no-store"})).json(); }
  catch(e){ $("hb").className="hb stall"; $("hb").querySelector(".txt").textContent="server unreachable"; return; }

  // heartbeat
  const hb=$("hb"); hb.className="hb "+d.heartbeat.state;
  hb.querySelector(".txt").textContent=d.heartbeat.detail;
  hb.querySelector(".meta").textContent="phase: "+d.phase;

  $("pct").textContent=d.pct; $("pbar").style.width=d.pct+"%";
  $("analyzed").textContent=d.analyzed; $("total").textContent=d.total;
  $("remaining").textContent=d.remaining;
  $("rate").textContent=d.rate?d.rate+"/min":"—";
  $("eta").textContent=d.eta||"—";
  $("ftime").textContent="updated "+d.server_time+(d.failed?(" · "+d.failed+" failed"):"");

  // pull panel
  const pw=$("pullwrap");
  if(d.phase==="pulling" && d.main){
    const m=d.main;
    pw.innerHTML=`<div class="pull"><div class="lbl">◀ STREAMING FROM DEVICE — batch ${m.batch}/${m.nbatches}</div>
      <div class="file">${esc(m.pulling||"")}</div>
      <div class="sub">${esc(m.folder||"")} · file ${m.pull_i}/${m.pull_n} in this batch</div></div>`;
  } else pw.innerHTML="";

  // lanes
  const nslots = (d.main && d.main.workers) ? d.main.workers : 9;
  let lanes = d.workers.map(w=>laneHTML(w,d.stages,d.stage_labels));
  while(lanes.length<nslots && d.phase!=="pulling") lanes.push(idleLane());
  $("lanes").innerHTML = lanes.join("") || idleLane();

  // completed feed
  $("feed").innerHTML = (d.completed||[]).map(c=>`<div class="done-row">
    <span class="t">${esc(c.track)}</span>
    <span class="chip bpm">${c.bpm?Math.round(c.bpm)+"":"—"}</span>
    <span class="chip key">${esc(c.camelot||"—")}</span>
    <span class="chip en">e${c.energy!=null?c.energy.toFixed(2):"—"}</span></div>`).join("")
    || '<div class="note">no tracks finished yet…</div>';

  // insights
  const ins=d.insights||{};
  $("cyc").textContent = ins.cycle?("cycle "+ins.cycle):"";
  $("headline").innerHTML = ins.headline||"Gathering data…";
  $("insights").innerHTML = (ins.insights||[]).map(x=>"<li>"+x+"</li>").join("");
  $("rbnote").innerHTML = ins.roadblocks_note ? ("⚑ "+ins.roadblocks_note) : "";
}
poll(); setInterval(poll,1500);
</script></body></html>"""


def main():
    live.ensure()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"live monitor on http://localhost:{PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
