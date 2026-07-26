#!/usr/bin/env python3
"""Full Walkman playlist rebuild: reclassify every on-device song, then build
genre crates + vibe playlists + cultural crates + discovery playlists (50-70,
cohesion-first, variety-selected). Idempotent — safe to re-run each pass."""
import json, re, os, sys, subprocess, datetime
from collections import defaultdict
HERE=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,"/Users/rishizamvar/Documents/Projects/walkman-dj/src")
from walkman_dj import rules as rulesmod
DEV="/storage/6439-6561/Music"
rules=rulesmod.load_rules("/Users/rishizamvar/Documents/Projects/walkman-dj/config/genre_rules.toml")

def n(s): return " ".join(re.sub(r"[^a-z0-9]+"," ",str(s).lower()).split())
def ntitle(t): return n(re.sub(r"\(feat[^)]*\)|\bfeat\b.*","",str(t).lower()))
def parse(rel):
    alb,fn=(rel.rsplit("/",1) if "/" in rel else (".",rel))
    stem=re.sub(r"^\d+\s*[-.]?\s*","",fn.rsplit(".",1)[0])
    fa,ti=(stem.split(" - ",1) if " - " in stem else (None,stem))
    return alb,fn,(fa.strip() if fa else ""),ti.strip()

def inventory():
    r=subprocess.run(["adb","shell",'cd /storage/6439-6561/Music && find . -type f \\( -iname "*.mp3" -o -iname "*.flac" -o -iname "*.m4a" -o -iname "*.wav" \\) 2>/dev/null'],capture_output=True,text=True,timeout=180)
    return [l.strip().replace("\r","").lstrip("./") for l in r.stdout.splitlines() if l.strip()]

def classify(dev):
    astate=json.load(open(f"{HERE}/logs/albums_state.json")) if os.path.exists(f"{HERE}/logs/albums_state.json") else {}
    newf={v["folder"].lower():v.get("genre") for v in astate.values() if v.get("status")=="ok" and v.get("folder")}
    def load(p):
        try: return json.load(open(p))
        except: return {}
    devfam_old=load("/tmp/devfam.json")
    gm=load("/tmp/genremap.json"); af=gm.get("af",{})
    albtitle={}
    for k,rec in af.items():
        a,fn=k.split("\t"); albtitle[(n(a),ntitle(rec[2]))]=rec[0]
    fam={}; art={}
    for rel in dev:
        alb,fn,fa,ti=parse(rel); art[rel]=fa or alb
        f=newf.get(alb.lower()) or devfam_old.get(rel)
        if not f or f=="??": f=albtitle.get((n(alb),ntitle(ti)))
        if not f or f=="??":
            for a2 in (alb,fa):
                r=rulesmod.classify_track(rules,folder=alb,filename=fn,artist=a2,features={})
                if r: f=r[0]; break
        fam[rel]=f or "??"
    return fam, art

def has(rel,kws):
    s=rel.lower(); return any(k in s for k in kws)

def pick(pool, cap, boosts=None, exclude=None):
    """Variety-select up to cap: boosted artists first, round-robin by album, dedupe titles."""
    seen=set(exclude or set()); out=[]
    groups=[[r for r in pool if has(r,boosts)], [r for r in pool if not has(r,boosts)]] if boosts else [pool]
    for g in groups:
        byalb=defaultdict(list)
        for r in g: byalb[r.split("/")[0]].append(r)
        for a in byalb: byalb[a].sort()
        order=sorted(byalb,key=lambda a:-len(byalb[a]))
        while len(out)<cap and any(byalb.values()):
            prog=False
            for a in order:
                while byalb[a]:                       # keep pulling past dupes
                    r=byalb[a].pop(0); t=ntitle(parse(r)[3])
                    if t in seen: continue            # dupe -> try next in this album
                    seen.add(t); out.append(r); prog=True
                    break
                if len(out)>=cap: break
            if not prog: break
        if len(out)>=cap: break
    return out

GENRE=[("Opium & Rage","opium_rage",70),("Atlanta Trap","atlanta_trap",70),("Travis Scott","travis_rage",65),
 ("Melodic Trap","melodic_trap",55),("Toronto Nights","toronto",70),("Lyrical Hip-Hop","lyrical_soul",70),
 ("Alt R&B","alt_rnb",70),("Afrobeats","afrobeats",70),("Tech House","tech_house",70),
 ("Organic & Afro House","organic_house",65),("Nu-Disco & Funk","nu_disco",70),("Baile Funk","baile_edits",70),
 ("UK Garage","ukg_bass",60),("Jersey Club","jersey_juke",45),("Electro","electro_classics",45),
 ("Downtempo","global_downtempo",50),("Hip-Hop","rap",65),("Pop","pop",50)]
CULTURAL=[("Reggaeton",["reggaeton"],["bad bunny","rauw","feid","karol","miko","myke","ozuna","balvin","eladio"],70),
 ("Latin Classics",["salsa","bachata","latin"],["romeo","aventura","marc anthony","lavoe","prince royce","frankie","eddie santiago"],70),
 ("Dembow",["dembow"],["tokischa","don miguelo","el alfa"],45),
 ("Punjabi",["punjabi"],["karan aujla","ap dhillon","shubh","diljit","sidhu","prabh","parmish"],65),
 ("Bollywood",["bollywood"],["arijit","pritam","shreya","atif"],55)]
NEWGENRE=[("Jungle & DnB",["jungle_dnb"],["nia archives","chase","overmono","goldie","sub focus"],60),
 ("Amapiano",["amapiano"],["uncle waffles","tyla","kabza","major league"],55),
 ("Progressive House",["progressive_house"],["anyma","argy","colyn","tale of us"],55),
 ("Trance",["trance"],["above","beyond","tiesto","armin","ferry corsten"],50),
 ("Bachata",["bachata"],["romeo","aventura","prince royce","monchy"],55),
 ("Salsa",["salsa"],["marc anthony","lavoe","frankie ruiz","eddie santiago","gilberto","willie colon"],55),
 ("Jersey Club",["jersey_juke"],["sliink","jersey club","cuffing"],45)]
VIBES=[("After Hours",["opium_rage","alt_rnb","toronto"],["lucki","destroy","carti","brent","party","jhene","drake"],65),
 ("Golden Hour",["nu_disco","organic_house","afrobeats"],["kaytra","barry","wizkid","tems","tyla","folamour"],65),
 ("Peak-Time Rager",["tech_house","opium_rage","electro_classics"],["fisher","john summit","carti","yeat","prydz","justice"],65),
 ("Safe on Aux",["atlanta_trap","lyrical_soul","toronto","travis_rage"],["migos","tyga","21 savage","kodak","gunna","kendrick","travis","drake"],70),
 ("Sunday Reset",["alt_rnb","global_downtempo"],["sza","frank","daniel caesar","snoh","kali","jhene","giveon"],65),
 ("Mezcla / Latin Night",["reggaeton","latin","bachata","salsa","dembow"],["bad bunny","rauw","karol","feid","romeo","aventura","ozuna"],70),
 ("Desi Function",["punjabi","bollywood"],["karan aujla","ap dhillon","shubh","diljit","sidhu","arijit"],60),
 ("Late Night Drive",["toronto","alt_rnb","melodic_trap"],["drake","party","don toliver","brent","juice","trippie"],65),
 ("Pregame",["atlanta_trap","opium_rage","jersey_juke","baile_edits"],["future","thug","carti","ken carson","sexyy"],65),
 ("Dance Floor Finds",["tech_house","organic_house","ukg_bass","jungle_dnb","amapiano","progressive_house"],["fred again","peggy","overmono","nia archives","anyma","sammy virji","uncle waffles","dom dolla"],70),
 ("Throwbacks",["throwback_rap","throwback_rnb","lyrical_soul","atlanta_trap"],["usher","ne-yo","lil wayne","kid cudi","50 cent","migos","tyga","travis porter","akon","t-pain","chris brown"],70),
 ("Function Heat",["baile_edits","jersey_juke","reggaeton","dembow","afrobeats"],["sexyy","glorilla","el alfa","tokischa","bad bunny","baile","jersey"],65),
 ("Deep Cuts",["opium_rage","alt_rnb","global_downtempo"],["lucki","nettspend","kxyl","homixide","destroy","summers","yeat"],60),
 ("Sunset Terrace",["organic_house","nu_disco","global_downtempo","afrobeats"],["keinemusik","rampa","adam port","me","barry","stavroz","bohmer","ben b","satori"],65),
 ("Warehouse",["tech_house","progressive_house","electro_classics","jungle_dnb"],["anyma","tale of us","argy","colyn","overmono","justice","prydz","sassari","ikin"],65),
 ("Heartbreak Hotel",["alt_rnb","toronto","melodic_trap"],["sza","brent","party","summer walker","jhene","6lack","giveon","juice"],60),
 ("Verano",["reggaeton","bachata","salsa","latin","afrobeats"],["bad bunny","feid","karol","romeo","aventura","marc anthony","ozuna","rauw"],70)]

def main():
    dev=inventory()
    fam,art=classify(dev)
    byfam=defaultdict(list)
    for r,f in fam.items(): byfam[f].append(r)
    # discovery = tracks from albums downloaded tonight
    astate=json.load(open(f"{HERE}/logs/albums_state.json")) if os.path.exists(f"{HERE}/logs/albums_state.json") else {}
    newfolders={v["folder"] for v in astate.values() if v.get("status")=="ok" and v.get("folder")}
    newtracks=[r for r in dev if r.split("/")[0] in newfolders]
    DISCOVERY=[("Rage Frontier",["opium_rage"],75),("New Latin Heat",["reggaeton","latin","bachata","salsa","dembow"],70),
               ("New R&B",["alt_rnb","toronto"],60),("New Dance",["tech_house","organic_house","nu_disco","jungle_dnb","amapiano","progressive_house","electro_classics"],70)]
    manifest=[]; os.makedirs("/tmp/wm_pl",exist_ok=True)
    def emit(name,cat,rels,minsize=20):
        if len(rels)<minsize: return False
        safe=re.sub(r'[/:]',"-",name); p=f"/tmp/wm_pl/{safe}.m3u8"
        with open(p,"w",encoding="utf-8") as fh:
            fh.write(f"#EXTM3U\n#PLAYLIST:{name}\n")
            for r in rels: fh.write(f"#EXTINF:0,{art[r]} - {parse(r)[3]}\n{r}\n")
        subprocess.run(["adb","push",p,f"{DEV}/{safe}.m3u8"],capture_output=True)
        manifest.append({"name":name,"category":cat,"count":len(rels)}); print(f"  [{cat}] {name}: {len(rels)}",flush=True)
        return True
    for name,f,cap in GENRE: emit(name,"genre",pick(byfam.get(f,[]),cap))
    for name,fams,boosts,cap in CULTURAL+NEWGENRE:
        pool=[r for f in fams for r in byfam.get(f,[])]
        pool+= [r for r in dev if has(r,boosts) and r not in pool]
        emit(name,"cultural" if (name,fams,boosts,cap) in CULTURAL else "genre",pick(pool,cap,boosts),12)
    for name,fams,boosts,cap in VIBES:
        pool=[r for f in fams for r in byfam.get(f,[])]
        emit(name,"vibe",pick(pool,cap,boosts),25)
    for name,fams,cap in DISCOVERY:
        pool=[r for r in newtracks if fam.get(r) in fams]
        emit(name,"discovery",pick(pool,cap),15)
    # delete stale playlists
    keep={re.sub(r'[/:]',"-",m["name"])+".m3u8" for m in manifest}
    ex=subprocess.run(["adb","shell",f'ls "{DEV}"/*.m3u8'],capture_output=True,text=True).stdout.replace("\r","").splitlines()
    for e in ex:
        e=e.strip()
        if e.endswith(".m3u8") and e.split("/")[-1] not in keep:
            subprocess.run(["adb","shell","rm",e.replace(" ","\\ ")],capture_output=True)
    json.dump(manifest,open(f"{HERE}/playlists_manifest.json","w"))
    subprocess.run(["adb","shell","am","broadcast","-a","android.intent.action.MEDIA_MOUNTED","-d",f"file://{DEV}"],capture_output=True)
    # richer stats for the live monitor
    classified=sum(1 for f in fam.values() if f and f!="??")       # songs given a genre
    placements=sum(m["count"] for m in manifest)                    # total playlist slots
    routed=set()                                                    # unique songs in >=1 playlist
    for name,f,cap in GENRE: routed|=set(pick(byfam.get(f,[]),cap))
    routed_unique=placements  # placements is the routed count incl. cross-playlist; keep simple
    open(f"{HERE}/logs/progress.jsonl","a").write(json.dumps({"ev":"rebuild",
        "playlists":len(manifest),"songs":len(dev),"classified":classified,
        "placements":placements,"unclassified":len(dev)-classified,
        "t":datetime.datetime.now().isoformat()})+"\n")
    print(f"REBUILD DONE: {len(manifest)} playlists over {len(dev)} songs "
          f"({classified} classified, {placements} playlist placements)")

if __name__=="__main__": main()
