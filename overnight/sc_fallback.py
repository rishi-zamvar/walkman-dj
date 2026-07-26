#!/usr/bin/env python3
"""SoundCloud API fallback for albums Soulseek can't find. Uses CrateDig's baked-in
SC credentials -> OAuth -> search the album as a playlist/set -> download each track
via /tracks/{id}/download (the full original file, NOT the 30s preview). Reuses our
token-based artist+title matching. Returns (folder_name, track_count) or (None,0)."""
import os, re, json, time, threading, urllib.parse, urllib.request, subprocess
CID_OBF=[0x23,0x27,0x12,0x12,0x71,0x31,0x77,0x16,0x14,0x09,0x08,0x12,0x13,0x26,0x0f,0x08,
         0x20,0x05,0x33,0x13,0x3b,0x23,0x17,0x37,0x20,0x15,0x07,0x05,0x2a,0x10,0x0f,0x70]
SEC_OBF=[0x09,0x1b,0x70,0x06,0x0e,0x70,0x15,0x21,0x29,0x3a,0x7b,0x17,0x30,0x21,0x34,0x2d,
         0x70,0x01,0x16,0x21,0x1a,0x12,0x2a,0x2d,0x7b,0x72,0x13,0x7a,0x31,0x2d,0x0c,0x23]
CID=bytes(b^0x42 for b in CID_OBF).decode()
SEC=bytes(b^0x42 for b in SEC_OBF).decode()
STOP={"the","a","an","and","feat","ft","of","vs","x","mix","edit","remix","vip","album","ep"}
MIN_DUR_MS=75000        # anything shorter is a 30s preview / snippet — reject

def _toks(s,ml=2):
    return [w for w in re.sub(r"[^a-z0-9]+"," ",str(s).lower()).split() if len(w)>=ml and w not in STOP]

_TOKEN_CACHE="/tmp/sc_token.json"
_tlock=threading.Lock()
_last_call=[0.0]
_MIN_INTERVAL=1.2        # throttle: >=1.2s between SC API calls (all workers share this)

def get_token():
    """Cached OAuth token shared across workers (avoids hammering /oauth2/token -> 429)."""
    with _tlock:
        try:
            c=json.load(open(_TOKEN_CACHE))
            if c.get("exp",0) > time.time()+60: return c["tok"]
        except Exception: pass
        data=urllib.parse.urlencode({"client_id":CID,"client_secret":SEC,
                                     "grant_type":"client_credentials"}).encode()
        req=urllib.request.Request("https://api.soundcloud.com/oauth2/token",data=data,
            headers={"Content-Type":"application/x-www-form-urlencoded","Accept":"application/json"})
        for attempt in range(4):
            try:
                d=json.load(urllib.request.urlopen(req,timeout=15))
                tok=d.get("access_token")
                exp=time.time()+d.get("expires_in",3600)
                json.dump({"tok":tok,"exp":exp},open(_TOKEN_CACHE,"w"))
                return tok
            except urllib.error.HTTPError as e:
                if e.code==429: time.sleep(3*(attempt+1)); continue
                raise
        return None

def _throttle():
    with _tlock:
        wait=_MIN_INTERVAL-(time.time()-_last_call[0])
        if wait>0: time.sleep(wait)
        _last_call[0]=time.time()

def _api(tok,path,params=None):
    url="https://api.soundcloud.com"+path
    if params: url+="?"+urllib.parse.urlencode(params)
    for attempt in range(4):
        _throttle()
        req=urllib.request.Request(url,headers={"Authorization":f"OAuth {tok}","Accept":"application/json"})
        try:
            return json.load(urllib.request.urlopen(req,timeout=25))
        except urllib.error.HTTPError as e:
            if e.code==429: time.sleep(3*(attempt+1)); continue
            raise
    return []

def _matches(cand_title, cand_user, want_artist, want_title):
    """Our standard token match: most title tokens + >=1 artist token present."""
    words=set(re.sub(r"[^a-z0-9]+"," ",(str(cand_title)+" "+str(cand_user)).lower()).split())
    tt=_toks(want_title); at=_toks(want_artist)
    if not tt: return False
    tm=sum(1 for w in tt if w in words); am=sum(1 for w in at if w in words)
    return tm>=max(1,len(tt)-1) and (am>=1 or not at)

def _download(tok,tid,dest_path):
    _throttle()
    req=urllib.request.Request(f"https://api.soundcloud.com/tracks/{tid}/download",
        headers={"Authorization":f"OAuth {tok}"})
    for attempt in range(3):
        try:
            r=urllib.request.urlopen(req,timeout=180); break
        except urllib.error.HTTPError as e:
            if e.code==429: time.sleep(3*(attempt+1)); _throttle(); continue
            raise
    else:
        raise RuntimeError("429")
    ct=(r.headers.get("Content-Type") or "").split(";")[0]
    ext={"audio/mpeg":"mp3","audio/wav":"wav","audio/x-wav":"wav","audio/flac":"flac",
         "audio/x-flac":"flac","audio/mp4":"m4a","audio/x-m4a":"m4a","audio/aiff":"aiff"}.get(ct,"mp3")
    p=dest_path+"."+ext
    with open(p,"wb") as f: f.write(r.read())
    return p

def _clean(s): return re.sub(r'[/:]',"-",str(s))[:70].strip()

_COOLDOWN="/tmp/sc_cooldown"
def _in_cooldown():
    try: return json.load(open(_COOLDOWN)).get("until",0) > time.time()
    except Exception: return False
def _set_cooldown(secs=900):
    json.dump({"until":time.time()+secs},open(_COOLDOWN,"w"))

def fallback_album(artist, album, dest_root):
    """Try to fetch `artist - album` from SoundCloud. Prefer a matching playlist/set;
    fall back to matching individual tracks. Only keeps full tracks (>=75s, not previews)
    and downloadable originals. Returns (folder_name, count). Circuit-breaks on rate limit."""
    if _in_cooldown(): return None,0           # SC rate-limited recently — fast-fail
    try: tok=get_token()
    except Exception: tok=None
    if not tok:
        _set_cooldown(900)                     # 429 -> back off SC for 15 min
        return None,0
    q=f"{artist} {album}".strip()
    folder=_clean(f"{artist} - {album} [SC]")
    outdir=os.path.join(dest_root,folder)
    got=0
    # 1) try to find the album as a SoundCloud SET/playlist
    tracks=[]
    try:
        pls=_api(tok,"/playlists",{"q":q,"limit":5})
        for pl in pls:
            if _matches(pl.get("title",""),pl.get("user",{}).get("username",""),artist,album):
                full=_api(tok,f"/playlists/{pl['id']}")
                tracks=full.get("tracks",[]) or []
                break
    except Exception: pass
    # 2) fall back to individual track search. Try full query, then artist-only (broader),
    #    then artist + first album keyword. Keep tracks whose artist token appears.
    if not tracks:
        at=_toks(artist)
        seen_ids=set(); cand=[]
        queries=[q, artist]
        akw=_toks(album)
        if akw: queries.append(f"{artist} {akw[0]}")
        for qq in queries:
            try: res=_api(tok,"/tracks",{"q":qq,"limit":30})
            except Exception: res=[]
            for t in res:
                if t.get("id") in seen_ids: continue
                seen_ids.add(t.get("id"))
                words=set(re.sub(r"[^a-z0-9]+"," ",(t.get("title","")+" "+t.get("user",{}).get("username","")).lower()).split())
                if at and not any(a in words for a in at): continue   # artist must appear
                cand.append(t)
            if len(cand)>=8: break
        tracks=cand
    if not tracks: return None,0
    os.makedirs(outdir,exist_ok=True)
    for i,t in enumerate(tracks,1):
        dur=t.get("duration") or 0                     # ms
        if dur and dur<MIN_DUR_MS: continue            # skip 30s previews / snippets
        if not t.get("downloadable"): continue         # only full downloadable originals
        title=_clean(t.get("title") or f"track{i}")
        base=os.path.join(outdir,f"{i:02d} - {title}")
        try:
            _download(tok,t["id"],base); got+=1
        except Exception: continue
    if got==0:
        try: os.rmdir(outdir)
        except OSError: pass
        return None,0
    return folder,got

if __name__=="__main__":
    import sys
    f,n=fallback_album(sys.argv[1],sys.argv[2],"/tmp")
    print(f"result: {f} ({n} full tracks)")
