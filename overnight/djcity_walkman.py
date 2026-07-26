#!/usr/bin/env python3
"""Walkman-only DJcity cover scan (reliable prefix-pull). Classifies each track's
embedded cover vs the djcity reference: exact (byte sha1) or near (perceptual).
Saves near-match covers + a labelled montage for visual review. Dumps results as
it goes. READ-ONLY on the device."""
import os, sys, io, json, time, hashlib, subprocess
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw

sys.path.insert(0, "/Users/rishizamvar/Documents/Projects/walkman-dj/overnight")
import djcity_find as D  # reuse cover extraction + classify + REF hashes

DEV = "/storage/6439-6561/Music"
MON = os.path.expanduser("~/Desktop/consolidation-monitor/progress.json")
NEARDIR = "/tmp/djcity_near"
OUT = "/tmp/djcity_matches_walkman.json"
os.makedirs(NEARDIR, exist_ok=True)
t0 = time.time()


def emit(**kw):
    kw["elapsed_s"] = round(time.time() - t0, 1)
    json.dump(kw, open(MON + ".tmp", "w")); os.replace(MON + ".tmp", MON)


def main():
    tracks = D.list_walkman()
    emit(stage="djcity", phase_label="Scanning Walkman covers", processed=0, total=len(tracks))
    djcity, review = [], []
    djc_imgs = []
    cnt = [0]

    def scan(i_remote):
        i, remote = i_remote
        try:
            data = D.cover_bytes_prefix(remote, i % 8)
            return remote, D.classify(data), data
        except Exception:
            return remote, "err", None

    from concurrent.futures import as_completed
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(scan, (i, t)) for i, t in enumerate(tracks)]
        for fut in as_completed(futs):
            remote, cls, data = fut.result()
            cnt[0] += 1
            if cls == "djcity":
                idx = len(djcity)
                djcity.append(remote)
                jp = f"{NEARDIR}/{idx:03d}.jpg"
                try:
                    open(jp, "wb").write(data)
                    djc_imgs.append((jp, os.path.basename(remote)))
                except Exception:
                    pass
            elif cls == "review":
                review.append(remote)
            if cnt[0] % 100 == 0:
                emit(stage="djcity", phase_label="Scanning Walkman covers",
                     processed=cnt[0], total=len(tracks), exact=len(djcity), near=len(review))
                json.dump({"exact": djcity, "near": review}, open(OUT, "w"), indent=1)

    json.dump({"exact": djcity, "near": review}, open(OUT, "w"), indent=1)

    # montage of djcity-matched covers (the delete set), labelled with index
    near_imgs = djc_imgs
    if near_imgs:
        cols = 10
        cell, pad, lab = 120, 6, 16
        rows = (len(near_imgs) + cols - 1) // cols
        W = cols * (cell + pad) + pad
        H = rows * (cell + lab + pad) + pad
        sheet = Image.new("RGB", (W, H), (18, 20, 24))
        dr = ImageDraw.Draw(sheet)
        for k, (jp, name) in enumerate(near_imgs):
            r, c = divmod(k, cols)
            x = pad + c * (cell + pad); y = pad + r * (cell + lab + pad)
            try:
                im = Image.open(jp).convert("RGB").resize((cell, cell))
                sheet.paste(im, (x, y))
            except Exception:
                pass
            dr.text((x, y + cell + 2), f"#{k}", fill=(200, 210, 220))
        sheet.save("/tmp/djcity_near_montage.jpg", quality=88)

    emit(stage="done", phase_label=f"Walkman scan done — {len(djcity)} djcity / {len(review)} review",
         processed=len(tracks), total=len(tracks), exact=len(djcity), near=len(review))
    print(f"DJCITY: {len(djcity)}   REVIEW: {len(review)}")
    print("montage -> /tmp/djcity_near_montage.jpg")
    print("DJCITY_WALKMAN DONE")


if __name__ == "__main__":
    main()
