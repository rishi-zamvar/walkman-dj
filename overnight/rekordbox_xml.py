#!/usr/bin/env python3
"""Generate a rekordbox.xml (rekordbox's native interchange format) containing every
DJ set as a playlist + a COLLECTION of all their tracks with local file paths, BPM, key,
genre. This is the reliable way to bulk-import into Rekordbox (m3u8 import is flaky).

Reads set order from the device .m3u8 sheets in ~/Desktop/DJ Sets, maps each track back
to its local MusicLake file + metadata via library.db, writes ~/Desktop/Rekordbox Sets/rekordbox.xml.

Import in Rekordbox: Preferences ▸ View ▸ Layout → enable "rekordbox xml"; Preferences ▸
Advanced ▸ Database ▸ rekordbox xml → set Imported Library to this file. The sets appear
under the "rekordbox xml" tree; right-click a playlist → Import to Collection.
"""
import os, sys, re, glob, json, sqlite3, unicodedata, urllib.parse
from xml.sax.saxutils import quoteattr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "library.db")
SETS_DIR = os.path.expanduser("~/Desktop/DJ Sets")
OUT_DIR = os.path.expanduser("~/Desktop/Rekordbox Sets")
OUT = os.path.join(OUT_DIR, "rekordbox.xml")
AUDIO = (".flac", ".mp3", ".m4a", ".wav", ".aiff", ".aif", ".ogg", ".opus")


def norm(name):
    n = unicodedata.normalize("NFC", os.path.basename(name)).lower().strip().lstrip("._")
    n = re.sub(r"\.[a-z0-9]{2,4}$", "", n)
    return re.sub(r'[<>:"|?*\\]', "_", n).strip()


def lib_index():
    """{norm(basename): row} for every library track that exists on disk."""
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    idx = {}
    for r in con.execute("SELECT id,canonical_path,artist,title,genre,bpm,camelot,duration "
                         "FROM recordings WHERE canonical_path IS NOT NULL"):
        if r["canonical_path"] and os.path.exists(r["canonical_path"]):
            idx[norm(r["canonical_path"])] = r
    return idx


# Camelot -> Rekordbox Tonality (musical key). A = minor, B = major.
CAMELOT_KEY = {
    "1A": "Abm", "2A": "Ebm", "3A": "Bbm", "4A": "Fm", "5A": "Cm", "6A": "Gm",
    "7A": "Dm", "8A": "Am", "9A": "Em", "10A": "Bm", "11A": "F#m", "12A": "Dbm",
    "1B": "B", "2B": "F#", "3B": "Db", "4B": "Ab", "5B": "Eb", "6B": "Bb",
    "7B": "F", "8B": "C", "9B": "G", "10B": "D", "11B": "A", "12B": "E",
}


def tonality(camelot):
    return CAMELOT_KEY.get((camelot or "").strip().upper(), "")


def location(path):
    # rekordbox XML convention: file://localhost/<percent-encoded absolute path>
    return "file://localhost" + urllib.parse.quote(path)


SETS_JSON = os.path.expanduser("~/Desktop/DJ Sets/sets.json")
GENRES_JSON = os.path.expanduser("~/Desktop/DJ Sets/genres.json")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    # sets.json = [{name, ids}] written by dj_sets — exact track identity, NO filename remap.
    # two playlist folders: the massive per-genre digs, and the sequenced DJ sets
    groups = []
    for label, path in (("Genres", GENRES_JSON), ("Walkman DJ Sets", SETS_JSON)):
        if os.path.exists(path):
            groups.append((label, json.load(open(path))))
        else:
            print(f"skipping '{label}' — {os.path.basename(path)} not built yet")
    if not groups:
        sys.exit("nothing to export: build genres.json and/or sets.json first")
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    meta = {r["id"]: dict(r) for r in con.execute(
        "SELECT id,canonical_path,artist,title,genre,bpm,camelot,duration FROM recordings")}

    folders = []            # (folder name, [(playlist name, [TrackID])])
    track_ids = {}          # library id -> TrackID (1-based), shared across BOTH folders
    collection = []
    misses = 0
    for label, sets in groups:
        playlists = []
        for s in sets:
            ids = []
            for lid in s["ids"]:
                row = meta.get(lid)
                if not row or not row["canonical_path"] or not os.path.exists(row["canonical_path"]):
                    misses += 1; continue
                if lid not in track_ids:
                    track_ids[lid] = len(collection) + 1
                    collection.append(row)
                ids.append(track_ids[lid])
            if ids:
                playlists.append((s["name"], ids))
        if playlists:
            folders.append((label, playlists))

    # build XML
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<DJ_PLAYLISTS Version="1.0.0">',
           '  <PRODUCT Name="rekordbox" Version="6.0.0" Company="AlphaTheta"/>',
           f'  <COLLECTION Entries="{len(collection)}">']
    for tid, r in enumerate(collection, 1):
        attrs = {
            "TrackID": str(tid),
            "Name": r["title"] or os.path.basename(r["canonical_path"]),
            "Artist": r["artist"] or "",
            "Genre": r["genre"] or "",
            "TotalTime": str(int(r["duration"] or 0)),
            "AverageBpm": f'{r["bpm"]:.2f}' if r["bpm"] else None,
            "Tonality": tonality(r["camelot"]) or None,
            "Location": location(r["canonical_path"]),
        }
        # OMIT unknown bpm/key rather than writing 0.00 / "" — rekordbox treats a present
        # value as analysed truth and won't re-derive it.
        out.append("    <TRACK " + " ".join(f'{k}={quoteattr(v)}'
                                            for k, v in attrs.items() if v is not None) + "/>")
    out.append("  </COLLECTION>")
    out.append("  <PLAYLISTS>")
    out.append(f'    <NODE Type="0" Name="ROOT" Count="{len(folders)}">')
    for label, playlists in folders:
        out.append(f'      <NODE Type="0" Name={quoteattr(label)} Count="{len(playlists)}">')
        for name, ids in playlists:
            out.append(f'        <NODE Name={quoteattr(name)} Type="1" KeyType="0" Entries="{len(ids)}">')
            for tid in ids:
                out.append(f'          <TRACK Key="{tid}"/>')
            out.append("        </NODE>")
        out.append("      </NODE>")
    out.append("    </NODE>")
    out.append("  </PLAYLISTS>")
    out.append("</DJ_PLAYLISTS>")
    open(OUT, "w", encoding="utf-8").write("\n".join(out) + "\n")
    for label, playlists in folders:
        print(f"  {label}: {len(playlists)} playlists · {sum(len(i) for _, i in playlists)} entries")
    print(f"{len(collection)} unique tracks · {misses} unmatched refs")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
