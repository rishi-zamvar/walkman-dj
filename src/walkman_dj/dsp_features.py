"""Genre-discriminative DSP features computed from the local FLACs.
Adds sub_bass/low_mid/air/perc_frac/onset_rate — separate genres 2-3x better than
the genre-agnostic MIR features. Resumable + parallel. ponytail: single-file lib.
"""
import os, sys, json, sqlite3, warnings, multiprocessing as mp
warnings.filterwarnings("ignore")
DB=os.path.expanduser("~/Documents/Projects/walkman-dj/data/library.db")
COLS=["sub_bass","low_mid","air","perc_frac","onset_rate"]

def compute(path):
    import numpy as np, librosa
    y,sr=librosa.load(path,sr=22050,mono=True,offset=30,duration=45)
    if len(y)<sr*5:
        y,sr=librosa.load(path,sr=22050,mono=True,duration=45)
    if len(y)<sr*2: return None
    S=np.abs(librosa.stft(y,n_fft=4096))**2
    f=librosa.fft_frequencies(sr=sr,n_fft=4096)
    tot=S.sum()+1e-9
    sub=float(S[(f>=20)&(f<80)].sum()/tot)
    lowmid=float(S[(f>=80)&(f<250)].sum()/tot)
    air=float(S[f>=8000].sum()/tot)
    H,P=librosa.effects.hpss(y)
    perc=float(np.sum(P**2)/(np.sum(H**2)+np.sum(P**2)+1e-9))
    ons=librosa.onset.onset_detect(y=y,sr=sr,units='time')
    orate=float(len(ons)/(len(y)/sr))
    return (sub,lowmid,air,perc,orate)

def worker(job):
    rid,path=job
    try:
        r=compute(path)
        return (rid,r)
    except Exception:
        return (rid,None)

def main():
    con=sqlite3.connect(DB,timeout=120)
    cols={r[1] for r in con.execute("pragma table_info(recordings)")}
    for c in COLS:
        if c not in cols:
            con.execute(f"alter table recordings add column {c} real")
    con.commit()
    todo=[(rid,p) for rid,p in con.execute(
        "select id,canonical_path from recordings where sub_bass is null and canonical_path is not null")
        if p and os.path.exists(p)]
    print(f"to analyze: {len(todo)}",flush=True)
    done=ok=0
    with mp.Pool(8) as pool:
        for rid,r in pool.imap_unordered(worker,todo,chunksize=4):
            if r:
                con.execute(f"update recordings set {','.join(c+'=?' for c in COLS)} where id=?",(*r,rid))
                ok+=1
            done+=1
            if done%50==0:
                con.commit(); print(f"{done}/{len(todo)} ok={ok}",flush=True)
    con.commit(); con.close()
    print(f"DSP DONE: {done} processed, {ok} written",flush=True)

if __name__=="__main__":
    main()
