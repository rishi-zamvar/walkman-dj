"""Live telemetry for the analysis run.

Each analysis worker (separate process) writes its current track+stage to
data/live/w_<pid>.json; the main process writes data/live/main.json (pull
phase / counts) and appends finished tracks to data/live/completed.ndjson.
The local live server aggregates these on each poll.
"""
import json
import os
import time
import glob

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIVE_DIR = os.path.join(ROOT, "data", "live")

# Human-readable analysis stages, in order.
STAGES = ["fingerprint", "load", "tempo", "key", "energy", "structure"]
STAGE_LABEL = {
    "fingerprint": "fingerprinting",
    "load": "decoding audio",
    "tempo": "detecting BPM",
    "key": "estimating key",
    "energy": "energy + timbre",
    "structure": "structure + mix points",
    "idle": "idle",
}


def ensure():
    os.makedirs(LIVE_DIR, exist_ok=True)


def _atomic(path, obj):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def reset():
    """Clear stale live files at run start."""
    ensure()
    for p in glob.glob(os.path.join(LIVE_DIR, "w_*.json")):
        try:
            os.remove(p)
        except OSError:
            pass
    for name in ("main.json", "completed.ndjson"):
        try:
            os.remove(os.path.join(LIVE_DIR, name))
        except OSError:
            pass


def set_main(**kw):
    ensure()
    _atomic(os.path.join(LIVE_DIR, "main.json"), {**kw, "ts": time.time()})


def set_worker(wid, track, stage, folder=None):
    ensure()
    _atomic(os.path.join(LIVE_DIR, f"w_{wid}.json"),
            {"wid": wid, "track": track, "stage": stage, "folder": folder,
             "ts": time.time()})


def worker_idle(wid):
    set_worker(wid, None, "idle")


def push_done(track, bpm, camelot, energy, folder):
    ensure()
    line = json.dumps({"track": track, "bpm": bpm, "camelot": camelot,
                       "energy": energy, "folder": folder, "ts": time.time()})
    with open(os.path.join(LIVE_DIR, "completed.ndjson"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_state(active_window=25):
    """Aggregate current state for the server."""
    now = time.time()
    state = {"main": {}, "workers": [], "completed": [], "rate": None, "now": now}
    try:
        with open(os.path.join(LIVE_DIR, "main.json"), encoding="utf-8") as f:
            state["main"] = json.load(f)
    except Exception:  # noqa: BLE001
        pass
    for p in sorted(glob.glob(os.path.join(LIVE_DIR, "w_*.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                w = json.load(f)
            w["age"] = round(now - w.get("ts", now), 1)
            if w["age"] <= active_window:
                state["workers"].append(w)
        except Exception:  # noqa: BLE001
            pass
    state["workers"].sort(key=lambda w: w["wid"])
    try:
        lines = open(os.path.join(LIVE_DIR, "completed.ndjson"),
                     encoding="utf-8").read().splitlines()
        recent = [json.loads(x) for x in lines[-15:]]
        state["completed"] = list(reversed(recent))
        # Honest wall-clock rate: span a wide window so it includes the
        # device-pull gaps between analyze bursts, not just a single burst.
        window = [json.loads(x) for x in lines[-500:]]
        if len(window) >= 2:
            span = window[-1]["ts"] - window[0]["ts"]
            if span > 0:
                state["rate"] = round((len(window) - 1) / (span / 60), 1)
    except Exception:  # noqa: BLE001
        pass
    return state
