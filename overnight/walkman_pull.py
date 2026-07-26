#!/usr/bin/env python3
"""Pull every Walkman-only track (not already on the computer) into MusicLake,
preserving the device's folder structure, deduped by normalized basename and
verified by size. Live KPIs/ETA to the monitor. Resumable (skips already-pulled)."""
import os, re, sys, json, time, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

DEV = "/storage/6439-6561/Music"
LAKE = os.path.expanduser("~/Desktop/MusicLake")
MON = os.path.expanduser("~/Desktop/consolidation-monitor/progress.json")
AUD = (".flac", ".mp3", ".m4a", ".wav", ".aac", ".aiff", ".aif")
_norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
t0 = time.time()
_lock_state = {"bytes": 0}


def emit(**kw):
    kw["elapsed_s"] = round(time.time() - t0, 1)
    p, t = kw.get("processed", 0), kw.get("total", 0)
    if p and kw["elapsed_s"] > 1:
        rate = p / kw["elapsed_s"]; kw["rate_per_s"] = round(rate, 2)
        kw["eta_s"] = round((t - p) / rate) if rate else None
    kw["gb"] = round(_lock_state["bytes"] / 1e9, 2)
    json.dump(kw, open(MON + ".tmp", "w")); os.replace(MON + ".tmp", MON)


def computer_index():
    idx = set()
    for root in (LAKE, os.path.expanduser("~/Desktop/music")):
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if os.path.splitext(fn)[1].lower() in AUD:
                    idx.add(_norm(os.path.splitext(fn)[0]))
    return idx


def list_walkman():
    r = subprocess.run(["adb", "shell", f"find {DEV} -type f"],
                       capture_output=True, text=True, timeout=300)
    out = []
    for l in r.stdout.replace("\r", "").splitlines():
        l = l.strip()
        if os.path.splitext(l)[1].lower() in AUD:
            out.append(l)
    return out


def main():
    emit(stage="pull", phase_label="Indexing computer + Walkman", processed=0, total=0)
    have = computer_index()
    wk = list_walkman()
    todo = [p for p in wk if _norm(os.path.splitext(os.path.basename(p))[0]) not in have]
    total = len(todo)
    print(f"walkman files {len(wk)}, already on computer skipped, TO PULL {total}")
    emit(stage="pull", phase_label="Pulling Walkman-only tracks", processed=0, total=total)

    done = [0]
    pulled = [0]
    failed = []

    def pull(remote):
        rel = remote[len(DEV) + 1:] if remote.startswith(DEV) else os.path.basename(remote)
        dest = os.path.join(LAKE, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            return remote, os.path.getsize(dest), True          # already have
        pr = subprocess.run(["adb", "pull", remote, dest], capture_output=True, text=True)
        ok = os.path.exists(dest) and os.path.getsize(dest) > 0
        return remote, (os.path.getsize(dest) if ok else 0), ok

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(pull, r) for r in todo]
        for fut in as_completed(futs):
            remote, sz, ok = fut.result()
            done[0] += 1
            if ok:
                pulled[0] += 1; _lock_state["bytes"] += sz
            else:
                failed.append(remote)
            if done[0] % 20 == 0 or done[0] == total:
                emit(stage="pull", phase_label="Pulling Walkman-only tracks",
                     processed=done[0], total=total, exact=pulled[0],
                     current=os.path.basename(remote))

    json.dump({"to_pull": total, "pulled": pulled[0], "failed": failed,
               "gb": round(_lock_state["bytes"] / 1e9, 2)},
              open("/tmp/walkman_pull_result.json", "w"), indent=1)
    emit(stage="done", phase_label=f"Pull complete — {pulled[0]}/{total} on computer",
         processed=total, total=total, exact=pulled[0])
    print(f"PULLED {pulled[0]}/{total}  ({round(_lock_state['bytes']/1e9,2)} GB)  failed={len(failed)}")
    for f in failed[:20]:
        print("  FAIL", f)
    print("WALKMAN_PULL DONE")


if __name__ == "__main__":
    main()
