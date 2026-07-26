#!/usr/bin/env python3
"""Overnight download driver. Grinds the album queue: Soulseek album download ->
push folder to Walkman -> log. Resumable (state file). Best-effort per album."""
import os, re, json, glob, time, subprocess, datetime
HERE=os.path.dirname(os.path.abspath(__file__))
SK="/Users/rishizamvar/.claude/skills/soulseek-download/soulseek_dl.py"
MUSIC="/Users/rishizamvar/Desktop/music"
DEV="/storage/6439-6561/Music"
STATE=os.path.join(HERE,"logs","albums_state.json")
LOG=os.path.join(HERE,"logs","progress.jsonl")
AUDIO=(".mp3",".flac",".m4a",".wav",".aac",".aiff")

def log(ev, **kw):
    kw["ev"]=ev; kw["t"]=datetime.datetime.now().isoformat()
    with open(LOG,"a") as f: f.write(json.dumps(kw)+"\n")

def snapshot(): return set(os.listdir(MUSIC)) if os.path.isdir(MUSIC) else set()

def _nt(t):
    t=re.sub(r"\(feat[^)]*\)|\bfeat\b.*|\bft\.?\b.*|\(with[^)]*\)|\(prod[^)]*\)"," ",str(t).lower())
    t=re.sub(r"\b(clean|dirty|explicit|remaster(ed)?|bonus|deluxe|web|flac|24bit)\b","",t)
    return " ".join(re.sub(r"[^a-z0-9]+"," ",t).split())

def device_index():
    """Set of normalized track-title tokens already on the device (for dedup)."""
    r=subprocess.run(["adb","shell",'cd /storage/6439-6561/Music && find . -type f \\( -iname "*.mp3" -o -iname "*.flac" -o -iname "*.m4a" -o -iname "*.wav" \\) 2>/dev/null'],
                     capture_output=True,text=True,timeout=180)
    titles=set()
    for l in r.stdout.splitlines():
        rel=l.strip().replace("\r","").lstrip("./")
        fn=rel.rsplit("/",1)[-1]
        stem=re.sub(r"^\d+\s*[-.]?\s*","",fn.rsplit(".",1)[0]).split(" - ")[-1]
        t=_nt(stem)
        if len(t)>2: titles.add(t)
    return titles

def album_titles(local_dir):
    out=set()
    for f in os.listdir(local_dir):
        if not f.lower().endswith((".mp3",".flac",".m4a",".wav",".aac")): continue
        stem=re.sub(r"^\d+\s*[-.]?\s*","",f.rsplit(".",1)[0]).split(" - ")[-1]
        t=_nt(stem)
        if len(t)>2: out.add(t)
    return out

def push_folder(local_dir, remote_name):
    n=0
    subprocess.run(["adb","shell","mkdir","-p",f"{DEV}/{remote_name}"],capture_output=True)
    for f in sorted(os.listdir(local_dir)):
        if f.startswith("._") or not f.lower().endswith(AUDIO): continue
        r=subprocess.run(["adb","push",os.path.join(local_dir,f),f"{DEV}/{remote_name}/{f}"],
                         capture_output=True,text=True)
        if "pushed" in (r.stdout+r.stderr): n+=1
    return n

def main():
    albums=json.load(open(os.path.join(HERE,"queues","albums.json")))
    state=json.load(open(STATE)) if os.path.exists(STATE) else {}
    os.makedirs(MUSIC,exist_ok=True)
    on_device=device_index()                        # dedup guard: titles already owned
    log("driver_start", total=len(albums), done=sum(1 for a in albums if state.get(a["q"],{}).get("status")=="ok"), on_device=len(on_device))
    for a in albums:
        q=a["q"]
        if state.get(q,{}).get("status")in("ok","skipped_dup"): continue
        log("album_start", album=a["album"], artist=a["artist"], genre=a["genre"])
        before=snapshot()
        try:
            r=subprocess.run(["python3",SK,"get",q],capture_output=True,text=True,timeout=900)
        except Exception as e:
            state[q]={"status":"fail","err":str(e)[:80]}; json.dump(state,open(STATE,"w"))
            log("album_fail", album=a["album"], err=str(e)[:80]); continue
        new=snapshot()-before
        newdirs=[d for d in new if os.path.isdir(os.path.join(MUSIC,d))]
        pushed=0; folder=None
        if newdirs:
            folder=max(newdirs, key=lambda d: len(os.listdir(os.path.join(MUSIC,d))))
            # DEDUP GUARD: if most of this album's tracks are already on the device, skip push
            atitles=album_titles(os.path.join(MUSIC,folder))
            if atitles:
                overlap=len(atitles & on_device)/len(atitles)
                if overlap>=0.6:
                    state[q]={"status":"skipped_dup","folder":folder,"overlap":round(overlap,2),"genre":a["genre"]}
                    json.dump(state,open(STATE,"w"))
                    log("album_skip_dup", album=a["album"], artist=a["artist"], overlap=round(overlap,2))
                    time.sleep(1); continue
            pushed=push_folder(os.path.join(MUSIC,folder), folder)
            on_device|=atitles                       # keep index current for later albums
        if pushed>0:
            subprocess.run(["adb","shell","am","broadcast","-a","android.intent.action.MEDIA_MOUNTED","-d",f"file://{DEV}"],capture_output=True)
            state[q]={"status":"ok","folder":folder,"tracks":pushed,"genre":a["genre"]}
            log("album_ok", album=a["album"], artist=a["artist"], genre=a["genre"], tracks=pushed, folder=folder)
        else:
            state[q]={"status":"fail","note":"no album folder / push"}
            log("album_fail", album=a["album"], note="no download")
        json.dump(state,open(STATE,"w"))
        time.sleep(2)
    log("driver_done", ok=sum(1 for v in state.values() if v.get("status")=="ok"))

if __name__=="__main__":
    main()
