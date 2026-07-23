#!/usr/bin/env python3
"""Close the coverage gap: analyze EVERY device track not yet analyzed-and-matched
(list in /tmp/unmatched_device.json), then auto-chain: assign families -> re-deploy
emergent sets -> re-render graphs. Device-resilient (pipeline waits through offline).
Re-pulling a filename-mismatched file also refreshes its metadata, fixing matching."""
import os, sys, json
sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/src")
sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/overnight")
from walkman_dj import device, rules as rulesmod, pipeline, assign_families
from walkman_dj.db import CrateDB

ROOT = "/Users/rishizamvar/Documents/Projects/walkman-dj"
DB = os.path.join(ROOT, "data", "walkman_analysis.db")
CFG = os.path.join(ROOT, "config", "genre_rules.toml")
TMP = os.path.join(ROOT, "audio_tmp")
DEV = "/storage/6439-6561/Music"


def to_devicefiles(paths):
    files = []
    for rel in paths:
        full = rel if rel.startswith(DEV) else f"{DEV}/{rel}"
        parts = rel.split("/")
        folder = parts[0] if len(parts) > 1 else "(root)"
        files.append(device.DeviceFile(path=full, folder=folder, filename=parts[-1]))
    return files


def main():
    paths = json.load(open("/tmp/unmatched_device.json"))
    print(f"[analyze_all] {len(paths)} unmatched device tracks to analyze", flush=True)
    files = to_devicefiles(paths)
    db = CrateDB(DB)
    rules = rulesmod.load_rules(CFG)
    pipeline.run_analysis(db, rules, files, TMP, batch=120, workers=10,
                          log=lambda m: print(m, flush=True))
    print("ANALYZE_ALL DONE", flush=True)

    # chain: assign families to the new fingerprints (frozen)
    print("[chain] assigning families...", flush=True)
    assign_families.run(DB)
    print("ASSIGN DONE", flush=True)

    # chain: re-deploy emergent sets (backs up + replaces) and re-render graphs
    print("[chain] deploying emergent sets...", flush=True)
    import deploy_emergent
    deploy_emergent.main(push=True)
    print("[chain] rendering graphs...", flush=True)
    import graph_viz
    graph_viz.main_emergent()
    print("ALL_DONE", flush=True)


if __name__ == "__main__":
    main()
