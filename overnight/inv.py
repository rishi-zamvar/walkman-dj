#!/usr/bin/env python3
"""Authoritative per-folder integrity inventory of the corrupt-suspect albums.
flac -t (reference decoder) for .flac; ffmpeg for mp3/m4a. Prints clean/corrupt
per folder so we know which duplicate to keep and exactly what to refill."""
import os, re, subprocess
DEV="/storage/6439-6561/Music"
FOLDERS=["A Great Chaos","GEMINI!","GEMINI! [2024]","The Gemini Principle (2008)",
         "JackBoys","JackBoys 2"]
AUD=(".flac",".mp3",".m4a",".wav")
def ls(folder):
    r=subprocess.run(["adb","shell",f'ls "{DEV}/{folder}"'],capture_output=True,text=True)
    return [x for x in r.stdout.replace("\r","").splitlines() if x.strip().lower().endswith(AUD)]
def test(local):
    if local.lower().endswith(".flac"):
        r=subprocess.run(["flac","-t","-s",local],capture_output=True,text=True)
        return r.returncode==0 and "error" not in r.stderr.lower()
    r=subprocess.run(["ffmpeg","-v","error","-i",local,"-f","null","-"],capture_output=True,text=True)
    return not r.stderr.strip()
os.makedirs("/tmp/inv",exist_ok=True)
for folder in FOLDERS:
    files=ls(folder)
    if not files:
        print(f"[MISSING/EMPTY] {folder}",flush=True); continue
    clean=0; bad=[]
    for f in files:
        ext=f.rsplit(".",1)[-1]
        loc=f"/tmp/inv/x.{ext}"
        subprocess.run(["adb","pull",f'{DEV}/{folder}/{f}',loc],capture_output=True)
        if os.path.exists(loc) and test(loc): clean+=1
        else: bad.append(f)
        if os.path.exists(loc): os.remove(loc)
    print(f"\n### {folder}: {clean}/{len(files)} CLEAN, {len(bad)} corrupt",flush=True)
    for b in bad: print(f"    ✗ {b}",flush=True)
print("\nINVENTORY DONE",flush=True)
