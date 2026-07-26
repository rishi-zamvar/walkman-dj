#!/usr/bin/env python3
"""Copy the USB-only KEEPER tracks (real music that exists nowhere else) into
MusicLake before the USB is wiped. Folders by the artist segment of the USB path.
Verifies each copy by size. Read-only on the USB; only writes into MusicLake."""
import os, sys, json, time, shutil

LAKE = os.path.expanduser("~/Desktop/MusicLake")
KEEPERS = "/tmp/usb_keepers.txt"
MON = os.path.expanduser("~/Desktop/consolidation-monitor/progress.json")
DEST_ROOT = LAKE
t0 = time.time()


def emit(**kw):
    kw["elapsed_s"] = round(time.time() - t0, 1)
    json.dump(kw, open(MON + ".tmp", "w")); os.replace(MON + ".tmp", MON)


def artist_of(usb_path):
    # /Volumes/Lexar/Contents/<Artist>/.../<file>
    parts = usb_path.split("/Contents/", 1)
    if len(parts) == 2:
        seg = parts[1].split("/")
        if len(seg) >= 2 and seg[0]:
            return seg[0]
    return "USB Rescue"


def main():
    keepers = [l.strip() for l in open(KEEPERS) if l.strip()]
    total = len(keepers)
    emit(stage="rescue", phase_label="Copying USB-only keepers to MusicLake",
         processed=0, total=total)
    copied, skipped, failed = 0, 0, []
    for i, src in enumerate(keepers):
        if not os.path.exists(src):
            failed.append(("missing-src", src)); continue
        art = artist_of(src)
        destdir = os.path.join(DEST_ROOT, art)
        os.makedirs(destdir, exist_ok=True)
        dest = os.path.join(destdir, os.path.basename(src))
        if os.path.exists(dest) and os.path.getsize(dest) == os.path.getsize(src):
            skipped += 1
        else:
            try:
                shutil.copyfile(src, dest)               # data only (USB/SMB-safe)
                if os.path.getsize(dest) == os.path.getsize(src) and os.path.getsize(dest) > 0:
                    copied += 1
                else:
                    failed.append(("size-mismatch", src))
            except Exception as e:                        # noqa: BLE001
                failed.append((str(e)[:40], src))
        if (i + 1) % 10 == 0 or i + 1 == total:
            emit(stage="rescue", phase_label="Copying USB-only keepers to MusicLake",
                 processed=i + 1, total=total, exact=copied,
                 current=os.path.basename(src))
    # verify: every keeper now on disk with matching size
    verified = 0
    for src in keepers:
        dest = os.path.join(DEST_ROOT, artist_of(src), os.path.basename(src))
        if os.path.exists(dest) and os.path.exists(src) and \
           os.path.getsize(dest) == os.path.getsize(src) and os.path.getsize(dest) > 0:
            verified += 1
    json.dump({"copied": copied, "skipped": skipped, "verified": verified,
               "total": total, "failed": failed}, open("/tmp/usb_rescue_result.json", "w"), indent=1)
    emit(stage="done", phase_label=f"Rescue done — {verified}/{total} keepers verified on disk",
         processed=total, total=total, exact=copied)
    print(f"copied={copied} skipped={skipped} failed={len(failed)}  VERIFIED={verified}/{total}")
    for why, p in failed[:20]:
        print("  FAIL", why, os.path.basename(p))
    print("SAFE-TO-WIPE" if verified == total else "NOT-SAFE — some keepers missing")
    print("USB_RESCUE DONE")


if __name__ == "__main__":
    main()
