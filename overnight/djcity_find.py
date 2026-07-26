#!/usr/bin/env python3
"""Find every track whose embedded cover art is the DJcity generic cover.

Detection: byte-exact sha1 match to the reference djcity cover (primary, zero false
positives), plus a perceptual-hash near-match flag for review (re-encoded variants).
Walkman is scanned via fast 1MB prefix-pull (ID3/FLAC picture sits at file start);
local + USB read covers directly. READ-ONLY — writes only the match list + progress."""
import os, sys, io, json, time, hashlib, subprocess
from concurrent.futures import ThreadPoolExecutor
from PIL import Image

REF_SHA1 = "253892944866af85942ab9a74e3969a066a955ab"
HOME = os.path.expanduser("~")
DEV = "/storage/6439-6561/Music"
USB = "/Volumes/Lexar/Contents"
LOCAL = [HOME + "/Desktop/MusicLake", HOME + "/Desktop/music", HOME + "/Music"]
MON = HOME + "/Desktop/consolidation-monitor/progress.json"
OUT = "/tmp/djcity_matches.json"
PFX = "/tmp/_djc"   # prefix scratch prefix
_STATE = {"started": time.time()}


def emit(**kw):
    _STATE.update(kw); _STATE["ts"] = time.time()
    _STATE["elapsed_s"] = round(_STATE["ts"] - _STATE["started"], 1)
    p, t = _STATE.get("processed", 0), _STATE.get("total", 0)
    if p and _STATE["elapsed_s"] > .5:
        r = p / _STATE["elapsed_s"]; _STATE["rate_per_s"] = round(r, 1)
        _STATE["eta_s"] = round((t - p) / r) if r and t else None
    json.dump(_STATE, open(MON + ".tmp", "w")); os.replace(MON + ".tmp", MON)


def ref_phash():
    im = Image.open("/tmp/djcity_cover_0.jpg").convert("L").resize((16, 16))
    px = list(im.getdata()); avg = sum(px) / len(px)
    return sum(1 << i for i, v in enumerate(px) if v > avg)


REF_PH = ref_phash()


def phash(data):
    try:
        im = Image.open(io.BytesIO(data)).convert("L").resize((16, 16))
        px = list(im.getdata()); avg = sum(px) / len(px)
        return sum(1 << i for i, v in enumerate(px) if v > avg)
    except Exception:
        return None


def cover_bytes_local(path):
    from mutagen import File as MF
    try:
        m = MF(path)
        if not m:
            return None
        tags = getattr(m, "tags", None)
        if hasattr(m, "pictures") and m.pictures:      # FLAC
            return m.pictures[0].data
        if tags:
            for k in tags.keys():
                if k.startswith("APIC"):
                    return tags[k].data
            if "covr" in tags:                          # m4a
                return bytes(tags["covr"][0])
    except Exception:
        return None
    return None


def cover_bytes_prefix(remote, wid):
    # single-quote the remote path (POSIX) so odd chars can't break the device shell,
    # and hard-timeout so one unreadable file can't hang the whole scan.
    q = "'" + remote.replace("'", "'\\''") + "'"
    try:
        raw = subprocess.run(["adb", "exec-out", f"dd if={q} bs=1M count=1 2>/dev/null"],
                             capture_output=True, timeout=20).stdout
    except subprocess.TimeoutExpired:
        return None
    fp = f"{PFX}{wid}.bin"; open(fp, "wb").write(raw)
    return cover_bytes_local(fp)


def classify(data):
    """DJcity covers ship in multiple JPEG encodings but are the SAME image
    (perceptual-hash distance 0). So match on phash, not bytes. Byte-exact is a
    fast-path subset. Returns one of: djcity / review / other / none."""
    if not data:
        return "none"
    if hashlib.sha1(data).hexdigest() == REF_SHA1:
        return "djcity"          # known byte-exact variant
    ph = phash(data)
    if ph is None:
        return "other"
    d = bin(ph ^ REF_PH).count("1")
    if d <= 6:
        return "djcity"          # visually the djcity cover (both known variants = dist 0)
    if d <= 12:
        return "review"          # perceptually close — needs eyeball
    return "other"


def list_walkman():
    exprs = " -o ".join(f"-iname '*{e}'" for e in (".mp3", ".flac", ".m4a", ".wav", ".aac"))
    r = subprocess.run(["adb", "shell", f"find {DEV} -type f \\( {exprs} \\)"],
                       capture_output=True, text=True, timeout=600)
    return [l.strip() for l in r.stdout.replace("\r", "").splitlines() if l.strip()]


def walk_local(root):
    out = []
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if os.path.splitext(fn)[1].lower() in (".mp3", ".flac", ".m4a", ".wav", ".aac", ".aiff"):
                p = os.path.join(dp, fn)
                if not os.path.islink(p):
                    out.append(p)
    return out


def main():
    exact = {"walkman": [], "usb": [], "local": []}
    near = {"walkman": [], "usb": [], "local": []}

    # 1) Walkman via prefix-pull
    emit(stage="djcity", phase_label="Listing Walkman", processed=0, total=0,
         log=["DJcity cover scan started"])
    wtracks = list_walkman()
    emit(stage="djcity", phase_label="Scanning Walkman covers", processed=0, total=len(wtracks))
    cnt = [0]

    def scan_w(i_remote):
        i, remote = i_remote
        try:
            data = cover_bytes_prefix(remote, i % 8)
            return remote, classify(data)
        except Exception:
            return remote, "err"
    with ThreadPoolExecutor(max_workers=8) as ex:
        for remote, cls in ex.map(scan_w, [(i, t) for i, t in enumerate(wtracks)]):
            cnt[0] += 1
            if cls == "exact":
                exact["walkman"].append(remote)
            elif cls == "near":
                near["walkman"].append(remote)
            if cnt[0] % 100 == 0:
                emit(stage="djcity", phase_label="Scanning Walkman covers",
                     processed=cnt[0], total=len(wtracks),
                     exact=len(exact["walkman"]), near=len(near["walkman"]),
                     current=os.path.basename(remote))
    emit(stage="djcity", phase_label="Walkman done",
         processed=len(wtracks), total=len(wtracks), exact=len(exact["walkman"]))

    # 2) USB + local direct reads
    for label, roots in (("usb", [USB] if os.path.isdir(USB) else []), ("local", LOCAL)):
        files = []
        for r in roots:
            files += walk_local(r)
        emit(stage="djcity", phase_label=f"Scanning {label} covers", processed=0, total=len(files))
        c = [0]

        def scan_l(p):
            return p, classify(cover_bytes_local(p))
        with ThreadPoolExecutor(max_workers=16) as ex:
            for p, cls in ex.map(scan_l, files):
                c[0] += 1
                if cls == "exact":
                    exact[label].append(p)
                elif cls == "near":
                    near[label].append(p)
                if c[0] % 200 == 0:
                    emit(stage="djcity", phase_label=f"Scanning {label} covers",
                         processed=c[0], total=len(files),
                         exact_total=sum(len(v) for v in exact.values()))
        emit(stage="djcity", phase_label=f"{label} done", processed=len(files), total=len(files))

    tot_exact = sum(len(v) for v in exact.values())
    tot_near = sum(len(v) for v in near.values())
    json.dump({"exact": exact, "near": near,
               "counts": {k: len(v) for k, v in exact.items()},
               "total_exact": tot_exact, "total_near": tot_near}, open(OUT, "w"), indent=1)
    emit(stage="done", phase_label=f"DJcity scan complete — {tot_exact} exact matches",
         processed=1, total=1, total_exact=tot_exact, total_near=tot_near,
         log=[f"EXACT djcity-cover matches: {tot_exact}", f"near (review): {tot_near}"])
    print(f"\nEXACT djcity matches: {tot_exact}  (walkman {len(exact['walkman'])}, "
          f"usb {len(exact['usb'])}, local {len(exact['local'])})")
    print(f"near matches (review): {tot_near}")
    print(f"list -> {OUT}")
    print("DJCITY_SCAN DONE")


if __name__ == "__main__":
    main()
