#!/usr/bin/env python3
"""Live localhost monitor for the overnight Walkman curation run.
Serves a human-readable page at http://localhost:8765 that auto-polls /data
(JSON) every few seconds. Reads the same state files the loop writes."""
import json, os, re, datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
HERE=os.path.dirname(os.path.abspath(__file__))
PORT=8765

def jload(p, d):
    try: return json.load(open(p))
    except Exception: return d

def events(limit=120):
    out=[]
    p=os.path.join(HERE,"logs","progress.jsonl")
    if os.path.exists(p):
        for l in open(p):
            try: out.append(json.loads(l))
            except: pass
    return out[-limit:]

def build_fix_data(fx):
    """Fix-run mode: the Walkman library tag/art cleanup, wired into this monitor."""
    astate=jload(os.path.join(HERE,"logs","albums_state.json"),{})
    evs=events(400)
    tl=[]
    for e in reversed(evs):
        ev=e.get("ev"); t=e.get("t","")[11:19]
        if ev=="album_ok":
            tl.append((t,"fixed",e.get("album",""),
                       f"{e.get('tracks',0)} changed · {e.get('genre','')}","ok"))
        elif ev=="album_fail":
            tl.append((t,"fail",e.get("album",""),e.get("err") or e.get("note",""),"fail"))
        elif ev=="driver_start":
            tl.append((t,"start","Library fix started",e.get("note",""),"start"))
        elif ev=="loop_pass":
            tl.append((t,"done","Run complete",e.get("note",""),"rebuild"))
        if len(tl)>=40: break
    fails=[{"album":(v.get('folder') or k),"why":v.get('err') or v.get('note','')}
           for k,v in astate.items() if v.get("status")=="fail"][-15:]
    return {
        "mode":"fix",
        "now": datetime.datetime.now().strftime("%H:%M:%S"),
        "workers": fx.get("workers"),
        "worker_cards": sorted(jload(os.path.join(HERE,"workers.json"),[]),
                               key=lambda w:w.get("slot",0)),
        "done": fx.get("done"), "total": fx.get("total"), "remaining": fx.get("remaining"),
        "files_pushed": fx.get("files_pushed"), "singles_stripped": fx.get("singles_stripped"),
        "art_fetched": fx.get("art_fetched"), "interlopers": fx.get("interlopers"),
        "normalized": fx.get("normalized"), "renames": fx.get("renames"),
        "folders_per_min": fx.get("folders_per_min"), "eta_min": fx.get("eta_min"),
        "failed": fx.get("failed",0),
        "timeline": tl, "fails": fails,
        "active": [w.get("album") for w in jload(os.path.join(HERE,"workers.json"),[])
                   if w.get("phase") not in ("idle","done","")],
    }

def build_data():
    fx=jload(os.path.join(HERE,"fix_stats.json"),None)
    if fx:
        return build_fix_data(fx)
    astate=jload(os.path.join(HERE,"logs","albums_state.json"),{})
    manifest=jload(os.path.join(HERE,"playlists_manifest.json"),[])
    insights=jload(os.path.join(HERE,"insights.json"),{})
    queue=jload(os.path.join(HERE,"queues","albums.json"),[])
    evs=events()
    ok=[v for v in astate.values() if v.get("status")=="ok"]
    fail=[(k,v) for k,v in astate.items() if v.get("status")=="fail"]
    skip=[v for v in astate.values() if v.get("status")=="skipped_dup"]
    # pending = anything the driver will still attempt (fails get retried on restart)
    pending=[a for a in queue if astate.get(a["q"],{}).get("status") not in ("ok","skipped_dup")]
    from collections import Counter
    genre_ct=Counter()
    for v in ok: genre_ct[v.get("genre","?")]+=v.get("tracks",0)
    # timeline: last downloads/skips/rebuilds/plans
    tl=[]
    for e in reversed(evs):
        ev=e.get("ev"); t=e.get("t","")[11:19]
        if ev=="album_ok": tl.append((t,"download",f"{e.get('artist','')} — {e.get('album','')}",f"{e.get('tracks',0)} tracks · {e.get('genre','')}","ok"))
        elif ev=="album_skip_dup": tl.append((t,"skip",f"{e.get('artist','')} — {e.get('album','')}",f"already owned ({int(e.get('overlap',0)*100)}% match)","skip"))
        elif ev=="album_fail": tl.append((t,"fail",e.get('album',''),e.get('err') or e.get('note',''),"fail"))
        elif ev=="album_start": tl.append((t,"start",f"{e.get('artist','')} — {e.get('album','')}","downloading…","start"))
        elif ev=="rebuild": tl.append((t,"rebuild",f"Rebuilt {e.get('playlists',0)} playlists",f"{e.get('songs',0)} songs on device","rebuild"))
        elif ev in("queue_expand","queue_expand2","queue_expand3","queue_expand4","queue_expand5","queue_expand_deep"): tl.append((t,"queue",f"Queued +{e.get('added',0)} albums",f"{e.get('total',0)} total in queue","queue"))
        elif ev=="loop_pass": tl.append((t,"loop","Loop wake-up",e.get("note",""),"loop"))
        elif ev=="dedup_complete": tl.append((t,"dedup",f"Deduped library",f"removed {e.get('removed_files',0)} files","dedup"))
        elif ev=="cleanup" and e.get("freed_mb"): tl.append((t,"cleanup",f"Freed {e.get('freed_mb',0)/1000:.1f} GB disk",f"{e.get('removed',0)} staged folders"+(f" · {e.get('zee_offloaded',0)} to ZeeCloud" if e.get('zee_offloaded') else ""),"dedup"))
        if len(tl)>=40: break
    tracks_added=sum(v.get("tracks",0) for v in ok)
    # session-scoped: songs added since this session started (today)
    session=jload(os.path.join(HERE,"session.json"),{})
    ss=session.get("session_start")
    session_tracks=session_albums=0
    if ss:
        try:
            ssdt=datetime.datetime.fromisoformat(ss)
            for e in events(100000):        # scan full log so early-session downloads count
                if e.get("ev")=="album_ok":
                    try:
                        if datetime.datetime.fromisoformat(e.get("t",""))>=ssdt:
                            session_tracks+=e.get("tracks",0); session_albums+=1
                    except: pass
        except: pass
    # download rate: parse album_ok timestamps -> songs/min overall + last 15 min
    def ptime(s):
        try: return datetime.datetime.fromisoformat(s)
        except: return None
    dl_events=[(ptime(e.get("t","")),e.get("tracks",0)) for e in evs if e.get("ev")=="album_ok"]
    dl_events=[(t,n) for t,n in dl_events if t]
    now_dt=datetime.datetime.now()
    # overall rate from first download to now
    starts=[ptime(e.get("t","")) for e in events(400) if e.get("ev")=="driver_start"]
    starts=[s for s in starts if s]
    rate_overall=0.0
    if dl_events:
        span=(now_dt-min(t for t,_ in dl_events)).total_seconds()/60
        rate_overall=round(tracks_added/span,1) if span>0.5 else 0.0
    # recent rate: songs in the last 15 min
    recent=sum(n for t,n in dl_events if (now_dt-t).total_seconds()<=900)
    rate_recent=round(recent/15,1)
    # latest rebuild stats (classification + routing)
    last_rebuild=next((e for e in reversed(evs) if e.get("ev")=="rebuild"),{})
    classified=last_rebuild.get("classified")
    placements=last_rebuild.get("placements")
    unclassified=last_rebuild.get("unclassified")
    # how much to go: pending albums + estimated songs remaining (~14 tracks/album avg)
    avg_tr = round(tracks_added/max(1,len(ok))) or 14
    songs_remaining_est=len(pending)*avg_tr
    # ETA at recent rate
    eta_min = round(songs_remaining_est/rate_recent) if rate_recent>0 else None
    return {
        "now": datetime.datetime.now().strftime("%H:%M:%S"),
        "songs": insights.get("songs_on_device"),
        "albums_ok": len(ok), "albums_fail": len(fail), "albums_skip": len(skip),
        "pending": len(pending), "queue_total": len(queue),
        "playlists": len(manifest),
        "tracks_added": tracks_added,
        "session_tracks": session_tracks, "session_albums": session_albums,
        "rate_overall": rate_overall, "rate_recent": rate_recent,
        "workers": len(pending) and 5,
        "worker_cards": sorted(jload(os.path.join(HERE,"workers.json"),[]), key=lambda w:w.get("slot",0)),
        "classified": classified, "placements": placements, "unclassified": unclassified,
        "songs_remaining_est": songs_remaining_est, "eta_min": eta_min, "avg_tr": avg_tr,
        "genres": genre_ct.most_common(),
        "manifest": sorted(manifest,key=lambda m:(m.get("category",""),-m.get("count",0))),
        "timeline": tl,
        "fails": [{"album":(v.get('folder') or k[:40]),"why":v.get('err') or v.get('note','')} for k,v in fail][-15:],
        "plan": insights.get("plan",""),
        "insights": insights.get("insights",[]),
        "next_wake": insights.get("next_wake",""),
        "last_pass": insights.get("last_pass",""),
        "downloading": next((f"{e.get('artist','')} — {e.get('album','')}" for e in reversed(evs) if e.get("ev")=="album_start"),""),
    }

PAGE=r"""<!doctype html><html><head><meta charset=utf-8><title>Walkman Curation — Live</title>
<style>
:root{--bg:#0c0d11;--card:#16181f;--ink:#e9ecf1;--mut:#8b93a3;--acc:#7cc4ff;--acc2:#b892ff;--ok:#54d18c;--warn:#f0b849;--err:#ff6b6b;--line:#232633}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Inter,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:28px 22px 80px}
.top{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
h1{font-size:22px;margin:0;letter-spacing:-.01em}
.live{display:inline-flex;align-items:center;gap:6px;color:var(--ok);font-weight:700;font-size:12px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--ok);animation:p 1.4s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.25}}
.clock{color:var(--mut);margin-left:auto;font-variant-numeric:tabular-nums}
.kpis{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:20px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.kpi .v{font-size:24px;font-weight:800;letter-spacing:-.02em}.kpi .l{color:var(--mut);font-size:11px;margin-top:2px}.kpi .sub{color:var(--acc2);font-size:10px;font-weight:600;margin-top:4px}
.now{background:linear-gradient(90deg,color-mix(in srgb,var(--acc) 14%,transparent),transparent);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin:6px 0 20px}
.now b{color:var(--acc)}
.grid{display:grid;grid-template-columns:1.3fr 1fr;gap:20px}
@media(max-width:820px){.grid{grid-template-columns:1fr}.kpis{grid-template-columns:repeat(3,1fr)}}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.1em;color:var(--mut);margin:24px 0 10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:6px 0}
.row{display:flex;gap:10px;padding:8px 14px;border-bottom:1px solid var(--line);align-items:baseline}
.row:last-child{border-bottom:none}
.t{color:var(--mut);font-variant-numeric:tabular-nums;font-size:12px;min-width:58px}
.tag{font-size:10px;font-weight:700;text-transform:uppercase;border-radius:5px;padding:1px 7px;min-width:60px;text-align:center}
.tag.ok,.tag.download{background:color-mix(in srgb,var(--ok) 20%,transparent);color:var(--ok)}
.tag.skip{background:color-mix(in srgb,var(--mut) 22%,transparent);color:var(--mut)}
.tag.fail{background:color-mix(in srgb,var(--err) 20%,transparent);color:var(--err)}
.tag.start{background:color-mix(in srgb,var(--acc) 20%,transparent);color:var(--acc)}
.tag.rebuild,.tag.dedup{background:color-mix(in srgb,var(--acc2) 20%,transparent);color:var(--acc2)}
.tag.queue,.tag.loop{background:color-mix(in srgb,var(--warn) 20%,transparent);color:var(--warn)}
.m{flex:1}.m .s{color:var(--mut);font-size:12px}
.bar{display:flex;align-items:center;gap:9px;padding:4px 14px}.bn{width:120px;color:var(--mut);font-size:12px}.bt{height:9px;background:linear-gradient(90deg,var(--acc),var(--acc2));border-radius:5px}.bv{font-weight:700;font-size:12px}
.chip{display:inline-flex;gap:6px;align-items:center;background:var(--bg);border:1px solid var(--line);border-radius:999px;padding:3px 10px;margin:0 5px 6px 0;font-size:12px}
.chip b{color:var(--acc2)}
.plan{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--acc);border-radius:0 12px 12px 0;padding:14px 16px;white-space:pre-wrap}
.ins{padding:9px 14px;border-bottom:1px solid var(--line);font-size:13px}.ins:last-child{border:none}
.err .why{color:var(--err)}
.muted{color:var(--mut);padding:12px 14px}
.workers{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:6px 0 4px}
@media(max-width:820px){.workers{grid-template-columns:repeat(2,1fr)}}
.wk{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;min-height:96px}
.wk .wh{display:flex;justify-content:space-between;align-items:center;font-size:11px;color:var(--mut);margin-bottom:6px}
.wk .ph{font-weight:700;text-transform:uppercase;font-size:10px;letter-spacing:.06em}
.wk .al{font-weight:700;font-size:13px;line-height:1.25;margin-bottom:2px;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.wk .ar{color:var(--mut);font-size:12px;margin-bottom:8px}
.wk .pbar{height:8px;background:var(--bg);border:1px solid var(--line);border-radius:5px;overflow:hidden}
.wk .pf{height:100%;background:linear-gradient(90deg,var(--acc),var(--acc2));transition:width .5s}
.wk .pn{font-size:11px;color:var(--mut);margin-top:5px;min-height:14px}
.wk.searching .ph{color:var(--warn)}.wk.downloading .ph{color:var(--acc)}.wk.pushing .ph{color:var(--acc2)}
.wk.done .ph{color:var(--ok)}.wk.done .pf{background:var(--ok)}
.wk[data-ph="no seeders"] .ph,.wk[data-ph="push failed"] .ph{color:var(--err)}.wk[data-ph="idle"]{opacity:.5}
.wk.searching .pf,.wk.downloading .pf,.wk.pull .pf,.wk.fix .pf{animation:pulse 1.6s infinite}
.wk.pull .ph{color:var(--warn)}.wk.fix .ph{color:var(--acc)}.wk.push .ph{color:var(--acc2)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.55}}
</style></head><body><div class=wrap>
<div class=top><h1 id=title>🎧 Walkman Curation</h1><span class=live><span class=dot></span>LIVE</span><span class=clock id=clock>—</span></div>
<div class=now id=now></div>
<h2 id=wkhdr>Download workers</h2><div class=workers id=workers></div>
<div class=kpis id=kpis></div>
<div id=sec_plan><h2>Current plan</h2><div class=plan id=plan>—</div></div>
<div id=sec_ins><h2>Insights</h2><div class=card id=insights></div></div>
<div class=grid>
<div><h2>Activity feed</h2><div class=card id=timeline></div></div>
<div>
  <h2 id=hdr_genres>Genre expansion</h2><div class=card id=genres></div>
  <h2>Errors &amp; skips</h2><div class=card id=fails></div>
</div>
</div>
<div id=sec_pl><h2>Playlists on device (<span id=plcount>0</span>)</h2><div id=playlists></div></div>
</div>
<script>
const E=id=>document.getElementById(id);
async function tick(){
 try{const d=await (await fetch('/data?_='+Date.now())).json(); render(d);}catch(e){E('clock').textContent='reconnecting…';}
}
function renderWorkers(wc,n){
 E('workers').innerHTML=(wc.length?wc:Array.from({length:n||5},(_,i)=>({slot:i,phase:'idle'}))).map(w=>{
   const ph=(w.phase||'idle'); const cls=ph.replace(/ /g,'-');
   return `<div class="wk ${cls}" data-ph="${esc(ph)}"><div class=wh><span>Worker ${(w.slot??0)+1}</span><span class=ph>${esc(ph)}</span></div>`+
    `<div class=al>${esc(w.album||'—')}</div><div class=ar>${esc(w.artist||'')}</div>`+
    `<div class=pbar><div class=pf style="width:${w.pct||0}%"></div></div><div class=pn>${esc(w.note||'')}${w.pct?(' · '+w.pct+'%'):''}</div></div>`;
 }).join('');
}
function renderFix(d){
 E('clock').textContent='updated '+d.now;
 E('title').textContent='🎧 Walkman Library Fix';
 E('wkhdr').textContent=`Workers (${d.workers||0}× parallel)`;
 for(const id of ['sec_plan','sec_ins','sec_pl','hdr_genres','genres']) if(E(id)) E(id).style.display='none';
 const act=(d.active||[]).filter(Boolean);
 E('now').innerHTML = act.length? ('🔧 Fixing: <b>'+act.slice(0,4).map(esc).join('</b>, <b>')+'</b>'+(act.length>4?` +${act.length-4} more`:'')) : (d.remaining===0?'✅ Run complete':'Idle');
 const eta = d.eta_min!=null ? (d.eta_min>90?`~${Math.round(d.eta_min/60)}h left`:`~${d.eta_min}m left`) : '';
 const pct = d.total? Math.round(100*(d.done||0)/d.total):0;
 const kpi=(v,l,sub)=>`<div class=kpi><div class=v>${v??'—'}</div><div class=l>${l}</div>${sub?`<div class=sub>${sub}</div>`:''}</div>`;
 E('kpis').innerHTML=[
   kpi(`${d.done??'—'}/${d.total??'—'}`,'Folders fixed',`${pct}% · ${d.remaining??'—'} to go${eta?` · ${eta}`:''}`),
   kpi(d.files_pushed??'—','Files changed', d.folders_per_min?`${d.folders_per_min} folders/min`:''),
   kpi(d.singles_stripped??'—','Single-song albums stripped'),
   kpi(d.art_fetched??'—','Covers embedded'),
   kpi(d.interlopers??'—','Interlopers removed', d.normalized?`${d.normalized} tags normalized`:''),
   kpi(d.renames??'—','Tracks renumbered', d.failed?`${d.failed} failed`:'0 failed'),
 ].join('');
 renderWorkers(d.worker_cards||[], d.workers);
 E('timeline').innerHTML=d.timeline.map(([t,tag,m,s,cls])=>`<div class="row ${cls}"><span class=t>${t}</span><span class="tag ${cls}">${tag}</span><span class=m>${esc(m)}<div class=s>${esc(s)}</div></span></div>`).join('')||'<div class=muted>waiting…</div>';
 E('fails').innerHTML=d.fails.length?d.fails.map(f=>`<div class="row err"><span class=m>${esc(f.album)}<div class="s why">${esc(f.why||'')}</div></span></div>`).join(''):'<div class=muted>none 🎉</div>';
}
function render(d){
 if(d.mode==='fix') return renderFix(d);
 E('clock').textContent='updated '+d.now;
 E('now').innerHTML = d.downloading? ('⬇︎ Now downloading: <b>'+esc(d.downloading)+'</b>') : (d.last_pass? ('Last loop pass: <b>'+esc(d.last_pass)+'</b>'+(d.next_wake?(' · next wake ~'+esc(d.next_wake)):'')) : 'Idle');
 const kpi=(v,l,sub)=>`<div class=kpi><div class=v>${v??'—'}</div><div class=l>${l}</div>${sub?`<div class=sub>${sub}</div>`:''}</div>`;
 const eta = d.eta_min!=null ? (d.eta_min>90?`~${Math.round(d.eta_min/60)}h left`:`~${d.eta_min}m left`) : '';
 E('kpis').innerHTML=[
   kpi(d.session_tracks??d.tracks_added,'Songs added today', `${d.session_albums??d.albums_ok} albums this session`),
   kpi(d.rate_recent!=null?d.rate_recent:'—','Songs / min (last 15m)', (d.rate_overall?`${d.rate_overall}/min overall`:'')+(d.workers?` · ${d.workers}× parallel`:'')),
   kpi(d.classified??'—','Songs classified', d.unclassified!=null?`${d.unclassified} unsorted`:''),
   kpi(d.playlists,'Playlists identified', d.albums_skip?`${d.albums_skip} dups skipped`:''),
   kpi(d.placements??'—','Songs routed to playlists', d.songs?`of ${d.songs} on device`:''),
   kpi(d.songs_remaining_est??'—','Est. songs to go', (d.pending?`${d.pending} albums`:'')+(eta?` · ${eta}`:'')),
 ].join('');
 const wc=d.worker_cards||[];
 E('workers').innerHTML=(wc.length?wc:Array.from({length:5},(_,i)=>({slot:i,phase:'idle'}))).map(w=>{
   const ph=(w.phase||'idle'); const cls=ph.replace(/ /g,'-');
   return `<div class="wk ${cls}" data-ph="${esc(ph)}"><div class=wh><span>Worker ${(w.slot??0)+1}</span><span class=ph>${esc(ph)}</span></div>`+
    `<div class=al>${esc(w.album||'—')}</div><div class=ar>${esc(w.artist||'')}</div>`+
    `<div class=pbar><div class=pf style="width:${w.pct||0}%"></div></div><div class=pn>${esc(w.note||'')}${w.pct?(' · '+w.pct+'%'):''}</div></div>`;
 }).join('');
 E('plan').textContent=d.plan||'—';
 E('insights').innerHTML=(d.insights&&d.insights.length)?d.insights.map(i=>`<div class=ins>${esc(i)}</div>`).join(''):'<div class=muted>—</div>';
 E('timeline').innerHTML=d.timeline.map(([t,tag,m,s,cls])=>`<div class="row ${cls}"><span class=t>${t}</span><span class="tag ${cls}">${tag}</span><span class=m>${esc(m)}<div class=s>${esc(s)}</div></span></div>`).join('')||'<div class=muted>waiting…</div>';
 const gmax=d.genres.length?d.genres[0][1]:1;
 E('genres').innerHTML=d.genres.map(([g,c])=>`<div class=bar><span class=bn>${esc(g)}</span><span class=bt style="width:${Math.round(80*c/gmax)}%"></span><span class=bv>${c}</span></div>`).join('')||'<div class=muted>—</div>';
 E('fails').innerHTML=d.fails.length?d.fails.map(f=>`<div class="row err"><span class=m>${esc(f.album)}<div class="s why">${esc(f.why||'no download')}</div></span></div>`).join(''):'<div class=muted>none 🎉</div>';
 E('plcount').textContent=d.playlists;
 const cats={};d.manifest.forEach(m=>{(cats[m.category]=cats[m.category]||[]).push(m)});
 const order=['genre','cultural','vibe','discovery'];
 E('playlists').innerHTML=order.filter(c=>cats[c]).map(c=>`<div style="margin:8px 0"><div style="color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.1em;margin-bottom:4px">${c}</div>${cats[c].map(m=>`<span class=chip>${esc(m.name)}<b>${m.count}</b></span>`).join('')}</div>`).join('');
}
function esc(s){return String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
tick(); setInterval(tick, 4000);
</script></body></html>"""

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        if self.path.startswith("/data"):
            body=json.dumps(build_data()).encode()
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(body)))
            self.end_headers(); self.wfile.write(body)
        else:
            body=PAGE.encode()
            self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)

if __name__=="__main__":
    print(f"monitor: http://localhost:{PORT}",flush=True)
    HTTPServer(("127.0.0.1",PORT),H).serve_forever()
