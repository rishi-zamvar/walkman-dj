"""Generate a rekordbox-compatible XML from the DJ set .m3u8 files + analysis DB.

- COLLECTION: every unique track with Location (file:// URL-encoded), AverageBpm,
  Tonality (musical key), Genre, TotalTime.
- PLAYLISTS: a "DJ Sets" folder node with one playlist per .m3u8, tracks in order.
Camelot -> musical-key conversion so rekordbox shows the right key.
"""
import os, re, sqlite3, glob
from urllib.parse import quote
from xml.sax.saxutils import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "walkman_analysis.db")
SETS = "/Users/rishizamvar/Desktop/DJ Sets"
OUT = os.path.join(SETS, "rekordbox.xml")

# Camelot -> rekordbox key notation (open-key/standard). rekordbox accepts standard
# key names like "Am", "C", "F#m". Map Camelot code -> standard.
CAMELOT_TO_KEY = {
    "1A": "Abm", "1B": "B", "2A": "Ebm", "2B": "F#", "3A": "Bbm", "3B": "Db",
    "4A": "Fm", "4B": "Ab", "5A": "Cm", "5B": "Eb", "6A": "Gm", "6B": "Bb",
    "7A": "Dm", "7B": "F", "8A": "Am", "8B": "C", "9A": "Em", "9B": "G",
    "10A": "Bm", "10B": "D", "11A": "F#m", "11B": "A", "12A": "Dbm", "12B": "E",
}


class _FeatProxy:
    """Lazy per-path feature lookup with cache."""
    def __init__(self, fn):
        self._fn = fn; self._cache = {}
    def get(self, p, default=None):
        if p not in self._cache:
            self._cache[p] = self._fn(p)
        return self._cache[p] or (default if default is not None else {})


def location(path):
    # rekordbox wants file://localhost/ + percent-encoded absolute path
    return "file://localhost" + quote(path)


def norm(s):
    if not s:
        return ""
    s = re.sub(r"\(feat[^)]*\)|\bfeat\b.*|\bft\.?\b.*", " ", str(s).lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def from_name(name):
    stem = re.sub(r"^\d+\s*[-.]\s*", "", os.path.splitext(name)[0])
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        return a.strip(), t.strip()
    return None, stem.strip()


def main():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    by_path = {}
    by_key = {}
    for r in c.execute("SELECT * FROM tracks JOIN features USING(fingerprint) WHERE status='ok'"):
        d = dict(r)
        if d.get("path"):
            by_path[d["path"]] = d
        if d.get("title"):
            by_key.setdefault(f"{norm(d.get('artist'))}|{norm(d['title'])}", d)
    c.close()

    def lookup(p):
        d = by_path.get(p)
        if d and d.get("bpm"):
            return d
        # fall back to artist+title (Walkman-analyzed tracks live under a device path)
        try:
            import mutagen
            m = mutagen.File(p, easy=True)
            a = (m.get("artist") or [None])[0] if m else None
            t = (m.get("title") or [None])[0] if m else None
        except Exception:
            a = t = None
        if not a or not t:
            fa, ft = from_name(os.path.basename(p))
            a = a or fa; t = t or ft
        return by_key.get(f"{norm(a)}|{norm(t)}") or d or {}

    feat = _FeatProxy(lookup)

    # gather playlists (ordered track paths) from the m3u8 files
    playlists = []
    used_paths = {}
    tid = 0
    for f in sorted(glob.glob(SETS + "/*.m3u8")):
        name = os.path.basename(f)[:-5]
        # only absolute paths are real track lines; guards against EXTINF text that
        # wrapped onto its own line (e.g. a stray newline inside a title tag)
        paths = [l.strip() for l in open(f, encoding="utf-8")
                 if l.strip().startswith("/")]
        entries = []
        for p in paths:
            if p not in used_paths:
                tid += 1
                used_paths[p] = tid
            entries.append(used_paths[p])
        playlists.append((name, entries))

    # build COLLECTION
    coll = []
    for p, tid in sorted(used_paths.items(), key=lambda kv: kv[1]):
        d = feat.get(p, {})
        clean = lambda x: " ".join(str(x or "").split())   # strip newlines/tabs
        artist = escape(clean(d.get("artist")), {'"': "&quot;"})
        title = escape(clean(d.get("title") or os.path.splitext(os.path.basename(p))[0]),
                       {'"': "&quot;"})
        bpm = d.get("bpm") or 0
        key = CAMELOT_TO_KEY.get(d.get("camelot") or "", "")
        dur = int(d.get("duration") or 0)
        genre = escape(str(d.get("folder") or ""), {'"': "&quot;"})
        loc = location(p).replace("&", "&amp;")
        coll.append(
            f'    <TRACK TrackID="{tid}" Name="{title}" Artist="{artist}" '
            f'Genre="{genre}" AverageBpm="{bpm:.2f}" Tonality="{key}" '
            f'TotalTime="{dur}" Location="{loc}"/>')

    # build PLAYLISTS tree
    pl_nodes = []
    for name, entries in playlists:
        nm = escape(name, {'"': "&quot;"})
        keys = "".join(f'        <TRACK Key="{tid}"/>\n' for tid in entries)
        pl_nodes.append(
            f'      <NODE Name="{nm}" Type="1" KeyType="0" Entries="{len(entries)}">\n'
            f'{keys}      </NODE>')

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<DJ_PLAYLISTS Version="1.0.0">\n'
        '  <PRODUCT Name="rekordbox" Version="6.0.0" Company="AlphaTheta"/>\n'
        f'  <COLLECTION Entries="{len(coll)}">\n'
        + "\n".join(coll) + "\n"
        '  </COLLECTION>\n'
        '  <PLAYLISTS>\n'
        f'    <NODE Type="0" Name="ROOT" Count="1">\n'
        f'      <NODE Name="DJ Sets" Type="0" Count="{len(pl_nodes)}">\n'
        + "\n".join(pl_nodes) + "\n"
        '      </NODE>\n'
        '    </NODE>\n'
        '  </PLAYLISTS>\n'
        '</DJ_PLAYLISTS>\n')

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(xml)
    print(f"wrote {OUT}")
    print(f"  {len(coll)} unique tracks, {len(playlists)} playlists")
    # quick integrity: how many collection tracks have missing files?
    missing = sum(1 for p in used_paths if not os.path.exists(p))
    withkey = sum(1 for p in used_paths if CAMELOT_TO_KEY.get(feat.get(p).get("camelot") or ""))
    print(f"  tracks with BPM+key: {withkey}  |  missing files: {missing}")


if __name__ == "__main__":
    main()
