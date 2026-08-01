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


def _first_downbeat(db, content):
    """Seconds to the first downbeat (first 'beat 1') from the Rekordbox beatgrid.
    With BPM this reconstructs the whole grid for constant-tempo tracks."""
    try:
        for anlz in db.read_anlz_files(content).values():
            bg = anlz.get("beat_grid")
            if bg:
                nums, _bpms, times = bg
                for n, t in zip(nums, times):
                    if int(n) == 1:
                        return round(float(t), 4)
                return round(float(times[0]), 4)
    except Exception:
        return None
    return None


def import_keys(lib_db, only_paths=None):
    """Overwrite camelot + bpm + first_downbeat from Rekordbox for every matching path.
    only_paths (a set of canonical_paths) scopes the run to just those tracks — much
    faster than the whole library, since the per-track ANLZ beatgrid read is the slow part."""
    from pyrekordbox import Rekordbox6Database
    db = Rekordbox6Database()
    by_path = {}
    for c in db.get_content():
        p = getattr(c, "FolderPath", None)
        if p:
            by_path[p] = c

    con = sqlite3.connect(lib_db, timeout=60)
    matched = updated = grid = 0
    rows = con.execute("SELECT fp, canonical_path FROM recordings").fetchall()
    if only_paths is not None:
        rows = [r for r in rows if r[1] in only_paths]
    for fp, path in rows:
        c = by_path.get(path)
        if c is None:
            continue
        matched += 1
        cam = to_camelot(c.Key.ScaleName if getattr(c, "Key", None) else None)
        bpm = (getattr(c, "BPM", 0) or 0) / 100.0
        fdb = _first_downbeat(db, c)
        sets, vals = [], []
        if cam:
            sets.append("camelot=?"); vals.append(cam)
        if bpm:
            sets.append("bpm=?"); vals.append(bpm)
        if fdb is not None:
            sets.append("first_downbeat=?"); vals.append(fdb); grid += 1
        if sets:
            sets.append("beat_engine=?"); vals.append("rekordbox")   # authoritative provenance
            con.execute(f"UPDATE recordings SET {','.join(sets)} WHERE fp=?", (*vals, fp))
            updated += 1
    con.commit()
    return {"rekordbox_tracks": len(by_path), "matched": matched,
            "updated": updated, "with_beatgrid": grid}
