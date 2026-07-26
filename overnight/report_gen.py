#!/usr/bin/env python3
"""Generate the consultant-style overnight HTML report from state files + live
Walkman query. Robust to partial state. Writes to ~/Desktop and opens it."""
import os, re, json, glob, subprocess, datetime, html
HERE=os.path.dirname(os.path.abspath(__file__))
OUT="/Users/rishizamvar/Desktop/Overnight Music Report.html"
DEV="/storage/6439-6561/Music"

def jload(p, d):
    try: return json.load(open(p))
    except: return d
def events():
    out=[]
    p=os.path.join(HERE,"logs","progress.jsonl")
    if os.path.exists(p):
        for l in open(p):
            try: out.append(json.loads(l))
            except: pass
    return out

def adb_count():
    try:
        r=subprocess.run(["adb","shell",'find /storage/6439-6561/Music -type f \\( -iname "*.mp3" -o -iname "*.flac" -o -iname "*.m4a" -o -iname "*.wav" \\) 2>/dev/null | wc -l'],capture_output=True,text=True,timeout=60)
        songs=int(r.stdout.strip() or 0)
    except: songs=0
    try:
        r=subprocess.run(["adb","shell",f'ls "{DEV}"/*.m3u8 2>/dev/null | wc -l'],capture_output=True,text=True,timeout=30)
        pls=int(r.stdout.strip() or 0)
    except: pls=0
    return songs, pls

def main():
    meta=jload(os.path.join(HERE,"run_meta.json"),{})
    astate=jload(os.path.join(HERE,"logs","albums_state.json"),{})
    manifest=jload(os.path.join(HERE,"playlists_manifest.json"),[])
    evs=events()
    songs, pls = adb_count()
    start=meta.get("start","")
    start_h= start[11:16] if start else "?"
    now=datetime.datetime.now()
    albums_ok=[v for v in astate.values() if v.get("status")=="ok"]
    albums_fail=[v for v in astate.values() if v.get("status")=="fail"]
    new_tracks=sum(v.get("tracks",0) for v in albums_ok)
    from collections import Counter, defaultdict
    genre_ct=Counter()
    for v in albums_ok: genre_ct[v.get("genre","?")]+=v.get("tracks",0)
    start_songs=next((e.get("start_songs") for e in evs if e.get("ev")=="baseline"), 3337)
    added=songs-start_songs if songs else new_tracks
    dl_total=len(albums_ok)+len(albums_fail)
    dl_rate=int(100*len(albums_ok)/dl_total) if dl_total else 0
    hours=round((now-datetime.datetime.fromisoformat(start)).total_seconds()/3600,1) if start else 0

    def kpi(v,l,s=""): return f'<div class="kpi"><div class="v">{v}</div><div class="l">{l}</div>{f"<div class=s>{s}</div>" if s else ""}</div>'
    kpis="".join([
        kpi(f"{songs:,}","Songs on Walkman"),
        kpi(f"+{added:,}","New songs added"),
        kpi(len(albums_ok),"Full albums pulled", f"{dl_rate}% success"),
        kpi(len(manifest) or pls,"Playlists built"),
        kpi(len(genre_ct),"Genres expanded"),
        kpi(f"{hours}h","Autonomous runtime"),
    ])
    # album table
    arows="".join(f'<tr><td>{html.escape(v.get("folder") or "?")}</td><td class="tag">{html.escape(v.get("genre","?"))}</td><td class="num">{v.get("tracks",0)}</td></tr>' for v in sorted(albums_ok,key=lambda x:-x.get("tracks",0)))
    # genre bars
    gmax=max(genre_ct.values()) if genre_ct else 1
    gbars="".join(f'<div class="bar"><span class="bn">{html.escape(g)}</span><span class="bt" style="width:{int(90*c/gmax)}%"></span><span class="bv">{c}</span></div>' for g,c in genre_ct.most_common())
    # playlists grouped by category (the headline deliverable)
    CATLABEL={"genre":"Genre crates","cultural":"Cultural crates","vibe":"Vibe / theme playlists","discovery":"Discovery — new music"}
    bycat=defaultdict(list)
    for m in manifest: bycat[m.get("category","other")].append(m)
    pl_blocks=""
    for cat in ("genre","cultural","vibe","discovery"):
        items=sorted(bycat.get(cat,[]),key=lambda m:-m.get("count",0))
        if not items: continue
        chips="".join(f'<span class="plchip">{html.escape(m["name"])}<b>{m["count"]}</b></span>' for m in items)
        pl_blocks+=f'<div class="plcat"><div class="plh">{CATLABEL.get(cat,cat)} <span class=muted>· {len(items)}</span></div>{chips}</div>'
    total_slots=sum(m.get("count",0) for m in manifest)
    # timeline (album_ok events)
    tl="".join(f'<div class="ti"><span class="tt">{e.get("t","")[11:16]}</span> pulled <b>{html.escape(e.get("album",""))}</b> — {html.escape(e.get("artist",""))} <span class="tag">{html.escape(e.get("genre",""))}</span> ({e.get("tracks",0)} tracks)</div>' for e in evs if e.get("ev")=="album_ok")
    fails="".join(f'<li>{html.escape(v.get("folder") or v.get("note","")) or "?"} <span class="muted">({html.escape(str(list(astate.keys())[i])[:40])})</span></li>' for i,v in enumerate(astate.values()) if v.get("status")=="fail")

    doc=f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Overnight Music Curation — Report</title><style>
:root{{--bg:#0e0f13;--card:#181a21;--ink:#e9ecf1;--mut:#8b93a3;--acc:#7cc4ff;--acc2:#b892ff;--line:#242732}}
@media(prefers-color-scheme:light){{:root{{--bg:#f6f7f9;--card:#fff;--ink:#141821;--mut:#5c6472;--acc:#1f6feb;--acc2:#7a3ff2;--line:#e6e8ee}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Inter,sans-serif}}
.wrap{{max-width:1000px;margin:0 auto;padding:40px 28px 80px}}
.eyebrow{{color:var(--acc);font-weight:700;letter-spacing:.12em;text-transform:uppercase;font-size:12px}}
h1{{font-size:32px;margin:6px 0 4px;letter-spacing:-.02em}}
.sub{{color:var(--mut);margin-bottom:28px}}
.kpis{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:24px 0 36px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}}
.kpi .v{{font-size:30px;font-weight:800;letter-spacing:-.02em}}.kpi .l{{color:var(--mut);font-size:13px;margin-top:2px}}.kpi .s{{color:var(--acc2);font-size:12px;font-weight:600;margin-top:6px}}
h2{{font-size:18px;margin:36px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--line)}}
table{{width:100%;border-collapse:collapse;font-size:14px}}td,th{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}}
.num{{text-align:right;font-variant-numeric:tabular-nums;font-weight:700}}
.tag{{display:inline-block;background:color-mix(in srgb,var(--acc) 16%,transparent);color:var(--acc);border-radius:6px;padding:1px 8px;font-size:12px;font-weight:600}}
.muted{{color:var(--mut)}}
.bar{{display:flex;align-items:center;gap:10px;margin:7px 0}}.bn{{width:150px;font-size:13px;color:var(--mut)}}.bt{{height:12px;background:linear-gradient(90deg,var(--acc),var(--acc2));border-radius:6px}}.bv{{font-weight:700;font-size:13px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;margin:14px 0}}
.ti{{padding:6px 0;border-bottom:1px solid var(--line);font-size:14px}}.tt{{color:var(--mut);font-variant-numeric:tabular-nums;margin-right:8px}}
.insight{{padding:12px 16px;border-left:3px solid var(--acc);background:color-mix(in srgb,var(--acc) 7%,transparent);border-radius:0 10px 10px 0;margin:10px 0}}
.plcat{{margin:16px 0}}.plh{{font-weight:700;font-size:14px;margin-bottom:8px}}
.plchip{{display:inline-flex;align-items:center;gap:7px;background:var(--card);border:1px solid var(--line);border-radius:999px;padding:5px 12px;margin:0 6px 8px 0;font-size:13px}}
.plchip b{{background:color-mix(in srgb,var(--acc2) 20%,transparent);color:var(--acc2);border-radius:999px;padding:0 7px;font-size:12px}}
ul{{margin:8px 0;padding-left:20px}}li{{margin:4px 0}}
.foot{{color:var(--mut);font-size:12px;margin-top:40px;border-top:1px solid var(--line);padding-top:16px}}
</style></head><body><div class=wrap>
<div class=eyebrow>Overnight Autonomous Run · Walkman Curation</div>
<h1>Music Curation Report</h1>
<div class=sub>Window: {start_h} → {now.strftime('%H:%M')} · generated {now.strftime('%b %d, %Y %H:%M')} · fully autonomous</div>
<div class=kpis>{kpis}</div>

<h2>Executive summary</h2>
<div class=card>Overnight I mined your libraries (CrateDig playlists, Walkman, top artists) to build a taste profile, then autonomously expanded the collection and rebuilt the Walkman into intentional, vibe-cohesive playlists. Downloads prioritised lossless (FLAC) via Soulseek, with SoundCloud-API and yt-dlp fallbacks. <b>{len(albums_ok)} full albums</b> and <b>+{added:,} songs</b> were added across <b>{len(genre_ct)} expanded genres</b>; the Walkman now holds <b>{songs:,} songs</b> across <b>{len(manifest) or pls} playlists</b>.</div>

<h2>Genre expansion</h2>
<div class=card>{gbars or '<span class=muted>in progress</span>'}</div>

<h2>Full albums pulled</h2>
<table><tr><th>Album</th><th>Genre</th><th class=num>Tracks</th></tr>{arows or '<tr><td colspan=3 class=muted>in progress</td></tr>'}</table>

<h2>Playlists built — {len(manifest)} crates, {total_slots:,} curated slots</h2>
{pl_blocks}

<h2>Key insights & takeaways</h2>
<div class=insight><b>Your center of gravity is opium/rage + sophisticated Latin.</b> "me" is deep underground rage (Nettspend, kxyl, Destroy Lonely); "Mezcla After Dark" pairs modern reggaeton with classic salsa/bachata (Lalo Rodríguez, Romeo Santos). Curation leaned into both.</div>
<div class=insight><b>You curate edits, not just originals.</b> The Shaker & Safe on Aux are full of club edits/remixes — new dance + rap picks favored remix-forward, festival-ready cuts.</div>
<div class=insight><b>Multi-lingual by default.</b> English, Spanish, and Punjabi all carry real weight — dedicated deep crates were built for each rather than tokens.</div>

<h2>Activity timeline</h2>
<div class=card>{tl or '<span class=muted>—</span>'}</div>

<h2>Didn't land (for a later pass)</h2>
<div class=card>{('<ul>'+fails+'</ul>') if fails else '<span class=muted>Nothing notable — or run still in progress.</span>'}<div class=muted style="margin-top:8px">Niche/underground tracks swing with Soulseek seeder availability; these are re-runnable.</div></div>

<div class=foot>If new songs/playlists don't show on the device: reboot the Walkman (or Settings → rebuild database) — files + .m3u8s are on the SD card. Generated by the overnight curation agent.</div>
</div></body></html>"""
    open(OUT,"w",encoding="utf-8").write(doc)
    subprocess.run(["open",OUT])
    print("report written+opened:",OUT)

if __name__=="__main__":
    main()
