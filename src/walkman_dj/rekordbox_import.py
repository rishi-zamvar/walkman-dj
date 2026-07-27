"""Import authoritative key + BPM from the live Rekordbox database (via pyrekordbox).

Rekordbox's key detection (~82% on electronic) beats our librosa analysis (~25%), and
— more importantly — it's the ground truth you mix against, so imported keys match what
you SEE in Rekordbox. Matches tracks by exact file path; only overwrites tracks you've
added + analysed in Rekordbox. Re-run after analysing more. Read-only on Rekordbox's side.
"""
import re
import sqlite3

# musical key name -> Camelot (fallback; analysed tracks usually already store Camelot)
_MUSICAL = {
    "B": "1B", "F#": "2B", "Gb": "2B", "Db": "3B", "C#": "3B", "Ab": "4B", "G#": "4B",
    "Eb": "5B", "D#": "5B", "Bb": "6B", "A#": "6B", "F": "7B", "C": "8B", "G": "9B",
    "D": "10B", "A": "11B", "E": "12B",
    "G#m": "1A", "Abm": "1A", "D#m": "2A", "Ebm": "2A", "A#m": "3A", "Bbm": "3A",
    "Fm": "4A", "Cm": "5A", "Gm": "6A", "Dm": "7A", "Am": "8A", "Em": "9A", "Bm": "10A",
    "F#m": "11A", "Gbm": "11A", "C#m": "12A", "Dbm": "12A",
}


def to_camelot(name):
    if not name:
        return None
    if re.match(r"^\d{1,2}[AB]$", name):      # already Camelot
        return name
    return _MUSICAL.get(name)


def read_rekordbox():
    """{file_path: (camelot, bpm)} from the live Rekordbox DB."""
    from pyrekordbox import Rekordbox6Database
    db = Rekordbox6Database()
    out = {}
    for c in db.get_content():
        p = getattr(c, "FolderPath", None)
        if not p:
            continue
        key = c.Key.ScaleName if getattr(c, "Key", None) else None
        bpm = (getattr(c, "BPM", 0) or 0) / 100.0
        out[p] = (to_camelot(key), bpm if bpm > 0 else None)
    return out


def import_keys(lib_db):
    """Overwrite camelot + bpm from Rekordbox for every matching path. Returns stats."""
    rb = read_rekordbox()
    con = sqlite3.connect(lib_db, timeout=60)
    matched = updated = 0
    for fp, path in con.execute("SELECT fp, canonical_path FROM recordings").fetchall():
        if path not in rb:
            continue
        matched += 1
        cam, bpm = rb[path]
        sets, vals = [], []
        if cam:
            sets.append("camelot=?"); vals.append(cam)
        if bpm:
            sets.append("bpm=?"); vals.append(bpm)
        if sets:
            sets.append("beat_engine=?"); vals.append("rekordbox")   # provenance = authoritative
            con.execute(f"UPDATE recordings SET {','.join(sets)} WHERE fp=?", (*vals, fp))
            updated += 1
    con.commit()
    return {"rekordbox_tracks": len(rb), "matched": matched, "updated": updated}
