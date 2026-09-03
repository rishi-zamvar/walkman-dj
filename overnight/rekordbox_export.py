#!/usr/bin/env python3
"""Rewrite a device .m3u8 (Walkman-relative paths) into a Rekordbox-ready .m3u8 with
ABSOLUTE local MusicLake paths, so the set can be imported + test-mixed on the Mac.

Usage:
  python3 rekordbox_export.py "House - Deep Rollers" ["House - Peak-Time" ...]
  python3 rekordbox_export.py --pick-house N     # auto-pick N varied House sets

Reads each set's .m3u8 from ~/Desktop/DJ Sets, maps every track back to its local file
by normalized basename, and writes "<name> (Rekordbox).m3u8" to ~/Desktop.
"""
import os, sys, re, glob, unicodedata, urllib.parse

SETS_DIR = os.path.expanduser("~/Desktop/DJ Sets")
LAKE = os.path.expanduser("~/Music/MusicLake")
DESKTOP = os.path.expanduser("~/Desktop")
AUDIO = (".flac", ".mp3", ".m4a", ".wav", ".aiff", ".aif", ".ogg", ".opus")


def norm(name):
    n = unicodedata.normalize("NFC", os.path.basename(name)).lower().strip().lstrip("._")
    n = re.sub(r"\.[a-z0-9]{2,4}$", "", n)
    return re.sub(r'[<>:"|?*\\]', "_", n).strip()


def lake_index():
    idx = {}
    for root, _, files in os.walk(LAKE):
        for f in files:
            if f.lower().endswith(AUDIO) and not f.startswith("._"):
                idx.setdefault(norm(f), os.path.join(root, f))
    return idx


def export(name, idx):
    src = os.path.join(SETS_DIR, name + ".m3u8")
    if not os.path.exists(src):
        print(f"  ! not found: {name}"); return False
    out, miss, total = ["#EXTM3U"], 0, 0
    lines = open(src, encoding="utf-8").read().splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("#EXTINF"):
            dev = lines[i + 1] if i + 1 < len(lines) else ""
            total += 1
            local = idx.get(norm(dev))
            if local:
                uri = "file://" + urllib.parse.quote(local)   # Rekordbox macOS wants file:// URIs
                out.append(ln); out.append(uri)
            else:
                miss += 1
            i += 2
        else:
            i += 1
    dst = os.path.join(DESKTOP, f"{name} (Rekordbox).m3u8")
    open(dst, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print(f"  ✓ {name}: {total - miss}/{total} tracks -> {dst}")
    return True


def main():
    args = sys.argv[1:]
    idx = lake_index()
    print(f"MusicLake index: {len(idx)} files")
    if args and args[0] == "--pick-house":
        n = int(args[1]) if len(args) > 1 else 4
        house = sorted(os.path.basename(p)[:-5] for p in glob.glob(f"{SETS_DIR}/House - *.m3u8"))
        # spread the pick across the list so vibes vary
        picks = [house[round(k * (len(house) - 1) / max(1, n - 1))] for k in range(n)] if house else []
        picks = list(dict.fromkeys(picks))
        args = picks
    for name in args:
        export(name, idx)


if __name__ == "__main__":
    main()
