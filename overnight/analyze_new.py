#!/usr/bin/env python3
"""Targeted analysis of the NEW (unanalyzed) tracks sitting in the device genre
playlists — the ones deploy_sets had to append unordered. Avoids re-pulling the
thousands of reorg-renamed-but-already-analyzed files (path-based skip would).
Feeds ONLY the unmatched playlist tracks to pipeline.run_analysis at max workers."""
import os, sys, re, subprocess
sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/src")
from walkman_dj import device, rules as rulesmod, pipeline
from walkman_dj.db import CrateDB

ROOT = "/Users/rishizamvar/Documents/Projects/walkman-dj"
DB = os.path.join(ROOT, "data", "walkman_analysis.db")
CFG = os.path.join(ROOT, "config", "genre_rules.toml")
TMP = os.path.join(ROOT, "audio_tmp")
DEV = "/storage/6439-6561/Music"
WORKERS = 10
sys.path.insert(0, os.path.join(ROOT, "overnight"))
import deploy_sets  # reuse features_by_filename + parse + GENRE_PLAYLISTS

_norm = deploy_sets._norm


def unmatched_device_paths():
    feat = deploy_sets.features_by_filename()
    paths = []
    seen = set()
    for remote in deploy_sets.list_playlists():
        name = os.path.splitext(remote.rsplit("/", 1)[-1])[0]
        if name not in deploy_sets.GENRE_PLAYLISTS:
            continue
        r = subprocess.run(["adb", "shell", f'cat "{remote}"'], capture_output=True, text=True)
        for _extinf, path in deploy_sets.parse_m3u8(r.stdout):
            key = _norm(os.path.splitext(os.path.basename(path))[0])
            if key in feat:
                continue
            full = path if path.startswith(DEV) else f"{DEV}/{path}"
            if full in seen:
                continue
            seen.add(full)
            paths.append(full)
    return paths


def to_devicefiles(paths):
    files = []
    for full in paths:
        rel = full[len(DEV) + 1:] if full.startswith(DEV) else full
        parts = rel.split("/")
        folder = parts[0] if len(parts) > 1 else "(root)"
        files.append(device.DeviceFile(path=full, folder=folder, filename=parts[-1]))
    return files


def main():
    paths = unmatched_device_paths()
    print(f"[analyze_new] {len(paths)} unmatched (new) tracks to analyze", flush=True)
    if not paths:
        print("[analyze_new] nothing to do", flush=True)
        return
    files = to_devicefiles(paths)
    db = CrateDB(DB)
    rules = rulesmod.load_rules(CFG)
    pipeline.run_analysis(db, rules, files, TMP, batch=120, workers=WORKERS,
                          log=lambda m: print(m, flush=True))
    print("ANALYZE_NEW DONE", flush=True)


if __name__ == "__main__":
    main()
