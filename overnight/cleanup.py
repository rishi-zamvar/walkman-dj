#!/usr/bin/env python3
"""Reclaim disk: delete local staging album folders that are already safely on the
Walkman (albums_state status 'ok'). Optionally offload to ZeeCloud when mounted.
NEVER touches playlists (.m3u8) or anything not confirmed pushed."""
import json, os, shutil, subprocess, datetime, re
HERE=os.path.dirname(os.path.abspath(__file__))
MUSIC="/Users/rishizamvar/Desktop/music"
ZEE="/Volumes/ZeeCloud"
STATE=os.path.join(HERE,"logs","albums_state.json")
LOG=os.path.join(HERE,"logs","progress.jsonl")

def log(ev,**kw):
    kw["ev"]=ev; kw["t"]=datetime.datetime.now().isoformat()
    open(LOG,"a").write(json.dumps(kw)+"\n")

def dirsize(p):
    tot=0
    for dp,_,fs in os.walk(p):
        for f in fs:
            try: tot+=os.path.getsize(os.path.join(dp,f))
            except OSError: pass
    return tot

def main():
    state=json.load(open(STATE)) if os.path.exists(STATE) else {}
    pushed_folders={v.get("folder") for v in state.values()
                    if v.get("status")=="ok" and v.get("folder")}
    if not os.path.isdir(MUSIC):
        log("cleanup", freed_mb=0, note="no staging dir"); return
    freed=0; removed=[]
    for d in os.listdir(MUSIC):
        p=os.path.join(MUSIC,d)
        if not os.path.isdir(p): continue
        # only delete folders confirmed pushed to the Walkman
        if d in pushed_folders:
            sz=dirsize(p)
            try:
                shutil.rmtree(p); freed+=sz; removed.append(d)
            except OSError: pass
    # loose files already pushed (single-track downloads) — leave; only whole staged albums
    freed_mb=round(freed/1e6,1)
    zee_up=0
    # ZeeCloud offload is DISABLED during active runs: non-pushed staging folders are
    # in-flight downloads the driver still needs — moving them would break the pipeline.
    # ZeeCloud is also ~94% full, a poor offload target. Deleting pushed folders above
    # already reclaims the space safely. (Re-enable a targeted offload only for folders
    # whose album status is explicitly 'fail' AND free space on ZeeCloud > 20GB.)
    zee_free_gb = 0
    if os.path.isdir(ZEE):
        try:
            st=os.statvfs(ZEE); zee_free_gb=st.f_bavail*st.f_frsize/1e9
        except OSError: pass
    log("cleanup", freed_mb=freed_mb, removed=len(removed), zee_offloaded=zee_up,
        zee_mounted=os.path.isdir(ZEE))
    print(f"cleanup: freed {freed_mb} MB ({len(removed)} pushed folders) | "
          f"ZeeCloud offload: {zee_up} | ZeeCloud mounted: {os.path.isdir(ZEE)}")

if __name__=="__main__":
    main()
