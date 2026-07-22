#!/usr/bin/env python3
"""Light live monitor for the targeted analyzer. Reads data/live/ via
live.read_state() and serves an auto-refreshing dashboard on :8770.
Per-worker cards (track + stage), progress bar, wall-clock rate, recent completions."""
import os, sys, json
from http.server import HTTPServer, BaseHTTPRequestHandler
sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/src")
from walkman_dj import live

PORT = 8770
PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Walkman · Analyzing</title>
<style>
 body{background:#0d0d0d;color:#e8e4d8;font-family:ui-monospace,Menlo,monospace;margin:0;padding:24px}
 h1{font-size:15px;letter-spacing:2px;color:#c9b872;margin:0 0 4px}
 .sub{color:#8a8574;font-size:12px;margin-bottom:16px}
 .bar{height:16px;background:#1c1c1c;border:1px solid #333;border-radius:3px;overflow:hidden;margin:8px 0}
 .fill{height:100%;background:linear-gradient(90deg,#7a6a2a,#c9b872);transition:width .4s}
 .kpis{display:flex;gap:24px;margin:12px 0 20px;flex-wrap:wrap}
 .kpi b{display:block;font-size:22px;color:#e8e4d8}.kpi span{font-size:11px;color:#8a8574;text-transform:uppercase;letter-spacing:1px}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px}
 .card{background:#161616;border:1px solid #2a2a2a;border-radius:6px;padding:10px 12px;min-height:56px}
 .card .t{font-size:12px;color:#e8e4d8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .card .s{font-size:10px;color:#c9b872;text-transform:uppercase;letter-spacing:1px;margin-top:4px}
 .card .f{font-size:10px;color:#6a6656;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .idle{opacity:.35}
 .done{font-size:11px;color:#8a8574;margin-top:20px}.done div{padding:2px 0;border-bottom:1px solid #1a1a1a}
 .pill{color:#7fae7f}
</style></head><body>
<h1>WALKMAN · SET SEQUENCER — ANALYZING NEW DOWNLOADS</h1>
<div class=sub id=phase>connecting…</div>
<div class=bar><div class=fill id=fill style=width:0%></div></div>
<div class=kpis>
 <div class=kpi><b id=done>0</b><span>analyzed</span></div>
 <div class=kpi><b id=total>0</b><span>target</span></div>
 <div class=kpi><b id=rate>–</b><span>tracks/min</span></div>
 <div class=kpi><b id=nw>0</b><span>workers</span></div>
</div>
<div class=grid id=grid></div>
<div class=done id=doneList></div>
<script>
async function tick(){
 try{const s=await (await fetch('/data')).json();
  const m=s.main||{};
  const done=m.done||0,total=m.total||0;
  document.getElementById('phase').textContent=(m.phase||'—').toUpperCase()+
    (m.phase=='pulling'&&m.pulling?(' · '+m.pulling):'')+
    (m.batch?('  · batch '+m.batch+'/'+m.nbatches):'');
  document.getElementById('done').textContent=done;
  document.getElementById('total').textContent=total;
  document.getElementById('fill').style.width=(total?100*done/total:0)+'%';
  document.getElementById('rate').textContent=s.rate!=null?s.rate:'–';
  document.getElementById('nw').textContent=(s.workers||[]).filter(w=>w.stage!='idle').length;
  const g=document.getElementById('grid');g.innerHTML='';
  (s.workers||[]).forEach(w=>{const d=document.createElement('div');
    d.className='card'+(w.stage=='idle'?' idle':'');
    d.innerHTML='<div class=t>'+(w.track||'—')+'</div><div class=s>'+(w.stage||'')+
      '</div><div class=f>'+(w.folder||'')+'</div>';g.appendChild(d);});
  const dl=document.getElementById('doneList');dl.innerHTML='<div style=color:#c9b872>recent</div>'+
    (s.completed||[]).map(c=>'<div><span class=pill>✓</span> '+(c.track||'')+
      ' — '+(c.bpm?Math.round(c.bpm):'?')+'bpm · '+(c.camelot||'?')+'</div>').join('');
 }catch(e){document.getElementById('phase').textContent='waiting for analyzer…';}
}
setInterval(tick,1500);tick();
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/data"):
            body = json.dumps(live.read_state()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


if __name__ == "__main__":
    print(f"analyze monitor → http://localhost:{PORT}", flush=True)
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
