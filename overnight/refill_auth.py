#!/usr/bin/env python3
"""Authoritative corrupt-track refill for the 3 problem albums.
For each album: detect corrupt device tracks (flac -t for FLAC, ffmpeg otherwise),
then per corrupt track run the user's fallback order —
  1) Soulseek song   2) Soulseek alt query   3) SoundCloud API   4) YouTube (last)
Each candidate is integrity-checked BEFORE it is pushed; the device copy is
re-pulled and re-verified AFTER push. Only a device-verified-clean file counts.
Uses ONE Soulseek connection at a time (loop is stopped while this runs)."""
import os, re, subprocess, sys, time, glob
sys.path.insert(0,"/Users/rishizamvar/Documents/Projects/walkman-dj/overnight")
import sc_fallback as SC

DEV="/storage/6439-6561/Music"
SK="/Users/rishizamvar/.claude/skills/soulseek-download/soulseek_dl.py"
DL="/tmp/refill_dl"
AUD=(".flac",".mp3",".m4a",".wav",".aac",".aiff")

# (device_folder, album_artist, album_name)
ALBUMS=[
    ("A Great Chaos","Ken Carson","A Great Chaos"),
    ("GEMINI! [2024]","LUCKI","GEMINI!"),
    ("JackBoys 2","Travis Scott","JACKBOYS 2"),
    ("JackBoys","Travis Scott","JACKBOYS"),
]

def nt(t):
    t=re.sub(r"\(feat[^)]*\)|\bfeat\b.*|\bft\.?\b.*","",str(t).lower())
    return " ".join(re.sub(r"[^a-z0-9]+"," ",t).split())
def title_of(fn):
    stem=re.sub(r"^\d+\s*[-.]?\s*","",fn.rsplit(".",1)[0])
    return stem.split(" - ")[-1].strip()
def integ(p):
    if not p or not os.path.exists(p) or os.path.getsize(p)<200000: return False
    if p.lower().endswith(".flac"):
        r=subprocess.run(["flac","-t","-s",p],capture_output=True,text=True)
        return r.returncode==0 and "error" not in r.stderr.lower()
    r=subprocess.run(["ffmpeg","-v","error","-i",p,"-f","null","-"],capture_output=True,text=True)
    return not r.stderr.strip()
def dev_list(folder):
    r=subprocess.run(["adb","shell",f'ls "{DEV}/{folder}"'],capture_output=True,text=True)
    return [x for x in r.stdout.replace("\r","").splitlines() if x.strip().lower().endswith(AUD)]
def corrupt_in(folder):
    bad=[]
    for f in dev_list(folder):
        ext=f.rsplit(".",1)[-1]; loc=f"/tmp/ck.{ext}"
        subprocess.run(["adb","pull",f'{DEV}/{folder}/{f}',loc],capture_output=True)
        ok=integ(loc)
        if os.path.exists(loc): os.remove(loc)
        if not ok: bad.append(f)
    return bad

def push_verify(local, folder, remote, old_name):
    """Delete old corrupt file, push new, re-pull + re-verify device copy."""
    if old_name and old_name!=remote:
        subprocess.run(["adb","shell",f'rm "{DEV}/{folder}/{old_name}"'],capture_output=True)
    subprocess.run(["adb","push",local,f'{DEV}/{folder}/{remote}'],capture_output=True)
    ext=remote.rsplit(".",1)[-1]; chk=f"/tmp/pv.{ext}"
    subprocess.run(["adb","pull",f'{DEV}/{folder}/{remote}',chk],capture_output=True)
    ok=integ(chk)
    if os.path.exists(chk): os.remove(chk)
    if not ok:
        subprocess.run(["adb","shell",f'rm "{DEV}/{folder}/{remote}"'],capture_output=True)
    return ok

def _clear_dl():
    if os.path.isdir(DL):
        for f in glob.glob(DL+"/**",recursive=True):
            try:
                if os.path.isfile(f): os.remove(f)
            except Exception: pass
    os.makedirs(DL,exist_ok=True)

def soulseek_song(query, title):
    """Run soulseek get --song, return path to a clean, title-matching download."""
    _clear_dl()
    try:
        subprocess.run(["python3","-u",SK,"get","--song",query,"--dest",DL],
                       capture_output=True,text=True,timeout=240)
    except Exception:
        return None
    tt=[w for w in nt(title).split() if len(w)>2]
    best=None
    for f in glob.glob(DL+"/**/*",recursive=True):
        if not f.lower().endswith(AUD) or not os.path.isfile(f): continue
        fn=nt(os.path.basename(f))
        if tt and sum(w in fn for w in tt) < max(1,len(tt)-1): continue  # wrong song guard
        if integ(f):
            # prefer flac
            if best is None or (f.lower().endswith(".flac") and not best.lower().endswith(".flac")):
                best=f
    return best

def sc_song(artist,title):
    if SC._in_cooldown(): return None
    try: tok=SC.get_token()
    except Exception: tok=None
    if not tok: SC._set_cooldown(900); return None
    try: res=SC._api(tok,"/tracks",{"q":f"{artist} {title}","limit":15})
    except Exception: return None
    at=SC._toks(artist); tt=SC._toks(title)
    for t in res:
        words=set(re.sub(r"[^a-z0-9]+"," ",(t.get("title","")+" "+t.get("user",{}).get("username","")).lower()).split())
        if not (sum(w in words for w in tt)>=max(1,len(tt)-1) and (not at or any(a in words for a in at))): continue
        if (t.get("duration") or 0) < SC.MIN_DUR_MS: continue
        if not t.get("downloadable"): continue
        try:
            p=SC._download(tok,t["id"],"/tmp/sc_one")
            if integ(p): return p
            if os.path.exists(p): os.remove(p)
        except Exception: continue
    return None

def yt_song(query):
    for f in glob.glob("/tmp/yt_one.*"):
        try: os.remove(f)
        except Exception: pass
    try:
        subprocess.run(["yt-dlp","-f","bestaudio","-x","--audio-format","m4a","--audio-quality","0",
            "--no-playlist","--no-warnings","--no-update",f"ytsearch1:{query}","-o","/tmp/yt_one.%(ext)s"],
            capture_output=True,text=True,timeout=300)
    except Exception:
        return None
    for f in glob.glob("/tmp/yt_one.m4a"):
        if integ(f): return f
    return None

def refill_track(folder, artist, album, old_name):
    title=title_of(old_name)
    num_m=re.match(r"^\s*(\d+)",old_name)
    num=num_m.group(1) if num_m else ""
    print(f"  >>> {folder}/{old_name}  (title='{title}')",flush=True)
    chain=[
        ("soulseek",     lambda: soulseek_song(f"{artist} {title}", title)),
        ("soulseek-alt", lambda: soulseek_song(f"{title} {album}", title)),
        ("soundcloud",   lambda: sc_song(artist,title)),
        ("youtube",      lambda: yt_song(f"{artist} {title} {album}")),
    ]
    for src,fn in chain:
        try: cand=fn()
        except Exception: cand=None
        if not cand:
            print(f"      - {src}: no clean candidate",flush=True); continue
        ext=cand.rsplit(".",1)[-1]
        remote=f"{num} - {title}.{ext}" if num else f"{title}.{ext}"
        if push_verify(cand, folder, remote, old_name):
            print(f"      ✓ FIXED via {src} -> {remote}",flush=True)
            try: os.remove(cand)
            except Exception: pass
            return (src, remote)
        print(f"      - {src}: candidate clean but device copy failed re-verify",flush=True)
        try: os.remove(cand)
        except Exception: pass
    print(f"      ✗ UNRESOLVED: {folder}/{old_name}",flush=True)
    return None

def main():
    summary=[]
    for folder,artist,album in ALBUMS:
        print(f"\n### {folder} ({artist}) — detecting corrupt tracks...",flush=True)
        bad=corrupt_in(folder)
        print(f"    {len(bad)} corrupt: {bad}",flush=True)
        for old in bad:
            res=refill_track(folder,artist,album,old)
            summary.append((folder,old,res[0] if res else "UNRESOLVED",res[1] if res else ""))
    print("\n===== REFILL SUMMARY =====",flush=True)
    fixed=[s for s in summary if s[2]!="UNRESOLVED"]
    print(f"Fixed {len(fixed)}/{len(summary)}",flush=True)
    for folder,old,src,new in summary:
        mark="✓" if src!="UNRESOLVED" else "✗"
        print(f"  {mark} {folder}/{old}  [{src}] {new}",flush=True)
    print("REFILL_AUTH DONE",flush=True)

if __name__=="__main__":
    main()
