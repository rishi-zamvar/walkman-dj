#!/usr/bin/env python3
"""Parallel overnight download driver. N fixed worker threads, each on its own
Soulseek listening port + display slot, pulling albums from a shared queue.
Each worker publishes live status (album, phase, %) to workers.json for the
monitor. Dedup guard + push-to-Walkman per album. Resumable, thread-safe."""
import os, re, json, time, subprocess, datetime, threading, queue
HERE=os.path.dirname(os.path.abspath(__file__))
SK="/Users/rishizamvar/.claude/skills/soulseek-download/soulseek_dl.py"
MUSIC="/Users/rishizamvar/Desktop/music"
DEV="/storage/6439-6561/Music"
STATE=os.path.join(HERE,"logs","albums_state.json")
LOG=os.path.join(HERE,"logs","progress.jsonl")
WORKERS_FILE=os.path.join(HERE,"workers.json")
AUDIO=(".mp3",".flac",".m4a",".wav",".aac",".aiff")
WORKERS=1  # Soulseek allows ONE connection per account; parallel logins get the account kicked
BASE_PORT=61000
TARGET_GENRES={"ukg_bass","jungle_dnb","baile_edits","trance","organic_house",
               "jersey_juke","bollywood","dembow","amapiano","salsa"}

_lock=threading.Lock()
_wlock=threading.Lock()
_odlock=threading.Lock()
on_device=set()
worker_status={}   # slot -> dict

def log(ev,**kw):
    kw["ev"]=ev; kw["t"]=datetime.datetime.now().isoformat()
    with _lock: open(LOG,"a").write(json.dumps(kw)+"\n")
def load_state():
    with _lock:
        try: return json.load(open(STATE))
        except: return {}
def save_state(s):
    with _lock: json.dump(s,open(STATE,"w"))
def set_status(slot, **kw):
    with _wlock:
        worker_status[slot]={**worker_status.get(slot,{}),**kw,"slot":slot,"updated":datetime.datetime.now().strftime("%H:%M:%S")}
        json.dump(list(worker_status.values()),open(WORKERS_FILE,"w"))

def _nt(t):
    t=re.sub(r"\(feat[^)]*\)|\bfeat\b.*|\bft\.?\b.*|\(with[^)]*\)|\(prod[^)]*\)"," ",str(t).lower())
    t=re.sub(r"\b(clean|dirty|explicit|remaster(ed)?|bonus|deluxe|web|flac|24bit)\b","",t)
    return " ".join(re.sub(r"[^a-z0-9]+"," ",t).split())
def device_index():
    r=subprocess.run(["adb","shell",'cd /storage/6439-6561/Music && find . -type f \\( -iname "*.mp3" -o -iname "*.flac" -o -iname "*.m4a" -o -iname "*.wav" \\) 2>/dev/null'],capture_output=True,text=True,timeout=180)
    titles=set()
    for l in r.stdout.splitlines():
        fn=l.strip().replace("\r","").lstrip("./").rsplit("/",1)[-1]
        stem=re.sub(r"^\d+\s*[-.]?\s*","",fn.rsplit(".",1)[0]).split(" - ")[-1]
        t=_nt(stem)
        if len(t)>2: titles.add(t)
    return titles
def album_titles(local_dir):
    out=set()
    for f in os.listdir(local_dir):
        if not f.lower().endswith(AUDIO): continue
        stem=re.sub(r"^\d+\s*[-.]?\s*","",f.rsplit(".",1)[0]).split(" - ")[-1]
        t=_nt(stem)
        if len(t)>2: out.add(t)
    return out
def push_folder(local_dir, remote_name, slot):
    files=[f for f in sorted(os.listdir(local_dir)) if not f.startswith("._") and f.lower().endswith(AUDIO)]
    subprocess.run(["adb","shell","mkdir","-p",f"{DEV}/{remote_name}"],capture_output=True)
    n=0
    for i,f in enumerate(files,1):
        r=subprocess.run(["adb","push",os.path.join(local_dir,f),f"{DEV}/{remote_name}/{f}"],capture_output=True,text=True)
        if "pushed" in (r.stdout+r.stderr): n+=1
        set_status(slot, phase="pushing", pct=int(100*i/max(1,len(files))), note=f"{i}/{len(files)} to Walkman")
    return n

def run_download(q, port, slot):
    """Run soulseek get, streaming stdout to estimate progress."""
    env=dict(os.environ, SOULSEEK_PORT=str(port))
    p=subprocess.Popen(["python3","-u",SK,"get",q],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=env)
    t0=time.time()
    pct=5
    try:
        for line in p.stdout:
            low=line.lower()
            if "searching" in low or "candidates" in low or "search" in low:
                set_status(slot, phase="searching", pct=8)
            elif "%" in line:
                m=re.search(r"(\d{1,3})\s*%",line)
                if m: set_status(slot, phase="downloading", pct=max(10,min(95,int(m.group(1)))))
            elif "download" in low or "transfer" in low or "kbps" in low or "mb" in low:
                pct=min(90,pct+7); set_status(slot, phase="downloading", pct=pct, note=line.strip()[:40])
            elif "no candidates" in low or "failed" in low or "error" in low:
                set_status(slot, phase="no seeders", pct=0)
    except Exception: pass
    try: p.wait(timeout=900)
    except Exception:
        p.kill()
    return

STOP=threading.Event()
def worker(slot, port, jobs):
    while not STOP.is_set():
        try: a=jobs.get(timeout=5)
        except queue.Empty:
            set_status(slot, phase="waiting", pct=0, album="", artist="", note="awaiting new albums"); continue
        q=a["q"]
        st=load_state()
        if st.get(q,{}).get("status")in("ok","skipped_dup"): jobs.task_done(); continue
        set_status(slot, phase="searching", pct=5, album=a["album"], artist=a["artist"], genre=a["genre"], note="")
        log("album_start", album=a["album"], artist=a["artist"], genre=a["genre"], port=port, slot=slot)
        before=set(os.listdir(MUSIC)) if os.path.isdir(MUSIC) else set()
        run_download(q, port, slot)
        new=(set(os.listdir(MUSIC)) if os.path.isdir(MUSIC) else set())-before
        newdirs=[d for d in new if os.path.isdir(os.path.join(MUSIC,d))]
        s=load_state()
        if not newdirs:
            # Soulseek found nothing. For TARGET genres, fall back to the SoundCloud API
            # (full downloadable originals, preview-guarded) with our matching logic.
            if a.get("genre") in TARGET_GENRES:
                set_status(slot, phase="soundcloud", pct=30, note="Soulseek empty → SC API")
                try:
                    import sc_fallback
                    folder,got=sc_fallback.fallback_album(a["artist"],a["album"],MUSIC)
                except Exception:
                    folder,got=None,0
                if folder and got>0:
                    pushed=push_folder(os.path.join(MUSIC,folder),folder,slot)
                    if pushed>0:
                        atl=album_titles(os.path.join(MUSIC,folder))
                        with _odlock: on_device.update(atl)
                        s[q]={"status":"ok","folder":folder,"tracks":pushed,"genre":a["genre"],"src":"soundcloud"}
                        save_state(s); set_status(slot, phase="done", pct=100, note=f"+{pushed} via SoundCloud")
                        log("album_ok", album=a["album"], artist=a["artist"], genre=a["genre"], tracks=pushed, folder=folder, src="soundcloud")
                        jobs.task_done(); continue
            s[q]={"status":"fail","note":"no download","fails":s.get(q,{}).get("fails",0)+1,"genre":a.get("genre")}; save_state(s)
            set_status(slot, phase="no seeders", pct=0, note="not on SL or SC")
            log("album_fail", album=a["album"], note="no download"); jobs.task_done(); continue
        folder=max(newdirs, key=lambda d: len(os.listdir(os.path.join(MUSIC,d))))
        atitles=album_titles(os.path.join(MUSIC,folder))
        with _odlock: owned=set(on_device)
        if atitles and len(atitles & owned)/len(atitles)>=0.6:
            s[q]={"status":"skipped_dup","folder":folder,"overlap":round(len(atitles&owned)/len(atitles),2),"genre":a["genre"]}
            save_state(s); set_status(slot, phase="dup skipped", pct=100, note="already owned")
            log("album_skip_dup", album=a["album"], artist=a["artist"], overlap=round(len(atitles&owned)/len(atitles),2)); jobs.task_done(); continue
        pushed=push_folder(os.path.join(MUSIC,folder), folder, slot)
        if pushed>0:
            with _odlock: on_device.update(atitles)
            s[q]={"status":"ok","folder":folder,"tracks":pushed,"genre":a["genre"]}; save_state(s)
            set_status(slot, phase="done", pct=100, note=f"+{pushed} tracks")
            log("album_ok", album=a["album"], artist=a["artist"], genre=a["genre"], tracks=pushed, folder=folder)
        else:
            s[q]={"status":"fail","note":"push failed","fails":s.get(q,{}).get("fails",0)+1,"genre":a.get("genre")}; save_state(s)
            set_status(slot, phase="push failed", pct=0)
            log("album_fail", album=a["album"], note="push failed")
        jobs.task_done()

MAX_RETRY=3           # per-album attempt cap across the continuous run
_attempts={}

def main():
    global on_device
    os.makedirs(MUSIC,exist_ok=True)
    on_device=device_index()
    jobs=queue.Queue()
    log("driver_start", workers=WORKERS, parallel=True, continuous=True, on_device=len(on_device))
    threads=[threading.Thread(target=worker,args=(i,BASE_PORT+i*1000,jobs),daemon=True) for i in range(WORKERS)]
    for t in threads: t.start()
    # continuous feeder: keep the job queue topped up from the (live-growing) album queue,
    # retrying pending/failed albums up to MAX_RETRY, polling for new ones the loop appends.
    while True:
        try:
            albums=json.load(open(os.path.join(HERE,"queues","albums.json")))
        except Exception:
            time.sleep(10); continue
        st=load_state()
        if jobs.qsize() < WORKERS*2:              # refill when workers are catching up
            added=0
            for a in albums:
                q=a["q"]
                info=st.get(q,{})
                stt=info.get("status")
                if stt in ("ok","skipped_dup"): continue
                # persistent fail-cap: albums that failed >=2 times across the night rest,
                # so workers aren't stuck re-trying niche albums that aren't on any source
                if stt=="fail" and info.get("fails",1) >= 2: continue
                if _attempts.get(q,0) >= MAX_RETRY: continue
                _attempts[q]=_attempts.get(q,0)+1
                jobs.put(a); added+=1
                if jobs.qsize() >= WORKERS*4: break
            if added==0 and jobs.qsize()==0:
                # nothing left to try right now — refresh device index, wait for the loop to add more
                on_device=device_index()
                time.sleep(45)
        time.sleep(8)

if __name__=="__main__":
    main()
