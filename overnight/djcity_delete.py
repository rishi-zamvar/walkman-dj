#!/usr/bin/env python3
"""Delete the confirmed DJcity tracks from the Walkman — but BACK UP each file
locally first (device has no Trash), so the whole set is recoverable. Then remove
from the device. Emits progress to the monitor."""
import os, sys, json, time, subprocess

DEV = "/storage/6439-6561/Music"
BACKUP = os.path.expanduser("~/Desktop/djcity-removed-2026-07-23")
MATCHES = "/tmp/djcity_matches_walkman.json"
MON = os.path.expanduser("~/Desktop/consolidation-monitor/progress.json")
t0 = time.time()


def emit(**kw):
    kw["elapsed_s"] = round(time.time() - t0, 1)
    json.dump(kw, open(MON + ".tmp", "w")); os.replace(MON + ".tmp", MON)


def main():
    remotes = json.load(open(MATCHES))["exact"]
    os.makedirs(BACKUP, exist_ok=True)
    total = len(remotes)
    emit(stage="delete", phase_label="Backing up + deleting DJcity tracks",
         processed=0, total=total, exact=0)
    backed, deleted, failed = 0, 0, []
    for i, remote in enumerate(remotes):
        rel = remote[len(DEV) + 1:] if remote.startswith(DEV) else os.path.basename(remote)
        dest = os.path.join(BACKUP, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # 1) back up (pull). Only delete if the backup succeeds and is non-empty.
        pr = subprocess.run(["adb", "pull", remote, dest], capture_output=True, text=True)
        ok = os.path.exists(dest) and os.path.getsize(dest) > 0
        if ok:
            backed += 1
            q = "'" + remote.replace("'", "'\\''") + "'"
            rr = subprocess.run(["adb", "shell", f"rm {q}"], capture_output=True, text=True)
            gone = subprocess.run(["adb", "shell", f"[ -f {q} ] && echo EXISTS || echo GONE"],
                                  capture_output=True, text=True).stdout
            if "GONE" in gone:
                deleted += 1
            else:
                failed.append(("rm-failed", remote))
        else:
            failed.append(("backup-failed", remote))
        if (i + 1) % 10 == 0 or i + 1 == total:
            emit(stage="delete", phase_label="Backing up + deleting DJcity tracks",
                 processed=i + 1, total=total, exact=deleted,
                 current=os.path.basename(remote))
    json.dump({"deleted": deleted, "backed_up": backed, "failed": failed,
               "backup_dir": BACKUP}, open("/tmp/djcity_delete_result.json", "w"), indent=1)
    emit(stage="done", phase_label=f"Deleted {deleted} DJcity tracks (backed up)",
         processed=total, total=total, exact=deleted)
    print(f"backed up: {backed}/{total}  deleted from device: {deleted}  failed: {len(failed)}")
    print(f"backup dir: {BACKUP}")
    for why, r in failed[:20]:
        print("  FAIL", why, r)
    print("DJCITY_DELETE DONE")


if __name__ == "__main__":
    main()
