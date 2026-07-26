"""crate — standardized music-library ingest / output with automatic acoustic dedup.

One canonical library (MusicLake) holds exactly ONE best-quality file per unique
recording. Identity is an acoustic Chromaprint fingerprint (via `fpcalc`) — content
based, so it survives re-encoding, format, bitrate, metadata and filename changes.
This is the fix for the two dedup bugs that bit us: filename-keyed skip-lists and a
non-deterministic librosa hash.

Public surface:
    lib = CrateLib(db_path, library_root)
    lib.ingest(path)                  -> dict(action=..., canonical=...)
    lib.resolve("artist - title")     -> canonical path (or None)
    lib.dedupe_library(on_progress)   -> dict(groups, removed, kept)

All deletions go to the macOS Trash (never rm).
"""
from __future__ import annotations
import os, re, json, sqlite3, subprocess, shutil
from dataclasses import dataclass

AUDIO_EXTS = (".flac", ".wav", ".aiff", ".aif", ".m4a", ".aac", ".mp3", ".ogg", ".opus")
LOSSLESS = (".flac", ".wav", ".aiff", ".aif")
DUR_TOLERANCE = 7.0          # seconds; only compare fingerprints of similar-length tracks
BER_THRESHOLD = 0.20         # <= this fraction of differing bits (with offset search) = same recording
MAX_OFFSET = 4               # fingerprint word offsets to try when aligning (handles small intro diffs)

_POP = bytes(bin(i).count("1") for i in range(256))


# ---------------- fingerprinting ----------------

def fingerprint(path: str) -> tuple[list[int], float]:
    """Return (raw chromaprint words, duration_seconds). Deterministic per file."""
    r = subprocess.run(["fpcalc", "-raw", "-length", "180", path],
                       capture_output=True, text=True, timeout=120)
    fp: list[int] = []
    dur = 0.0
    for line in r.stdout.splitlines():
        if line.startswith("DURATION="):
            try: dur = float(line.split("=", 1)[1])
            except ValueError: pass
        elif line.startswith("FINGERPRINT="):
            fp = [int(x) for x in line.split("=", 1)[1].split(",") if x]
    if not fp:
        raise ValueError(f"no fingerprint for {path}: {r.stderr[:120]}")
    return fp, dur


def _ber_aligned(a: list[int], b: list[int]) -> float:
    """Bit-error-rate over the aligned overlap of two equal-offset fingerprints."""
    n = min(len(a), len(b))
    if n == 0:
        return 1.0
    diff_bits = 0
    for x, y in zip(a[:n], b[:n]):
        v = x ^ y
        diff_bits += _POP[v & 0xFF] + _POP[(v >> 8) & 0xFF] + _POP[(v >> 16) & 0xFF] + _POP[(v >> 24) & 0xFF]
    return diff_bits / (32 * n)


def similarity(a: list[int], b: list[int]) -> float:
    """1 - min bit-error-rate across a small offset search. 1.0 = identical."""
    best = _ber_aligned(a, b)
    for off in range(1, MAX_OFFSET + 1):
        best = min(best, _ber_aligned(a[off:], b), _ber_aligned(a, b[off:]))
        if best == 0.0:
            break
    return 1.0 - best


def same_recording(fp_a, dur_a, fp_b, dur_b) -> bool:
    if abs(dur_a - dur_b) > DUR_TOLERANCE:
        return False
    return similarity(fp_a, fp_b) >= (1.0 - BER_THRESHOLD)


# ---------------- quality ranking ----------------

def _truncated(name: str) -> bool:
    s = os.path.splitext(name)[0].rstrip()
    return s.endswith("(") or s.endswith("[") or s.count("(") > s.count(")") or s.count("[") > s.count("]")


def quality(path: str) -> tuple:
    """Higher tuple = better copy to KEEP. lossless > lossy, then bitrate, then clean name, then size."""
    ext = os.path.splitext(path)[1].lower()
    tier = 3 if ext in LOSSLESS else 2 if ext in (".m4a", ".aac") else 1
    bitrate = 0
    try:
        import mutagen
        m = mutagen.File(path)
        bitrate = getattr(getattr(m, "info", None), "bitrate", 0) or 0
    except Exception:
        pass
    if tier == 3:
        bitrate = max(bitrate, 5_000_000)          # lossless outranks any lossy bitrate
    try: size = os.path.getsize(path)
    except OSError: size = 0
    return (tier, bitrate, 0 if _truncated(os.path.basename(path)) else 1,
            0 if "_dedup-losers" in path else 1, size)


# ---------------- metadata ----------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def tags(path: str) -> tuple[str, str, str]:
    """(artist, title, album) from tags, falling back to 'Artist - Title' filename parsing."""
    artist = title = album = ""
    try:
        import mutagen
        m = mutagen.File(path, easy=True)
        if m:
            artist = (m.get("artist") or [""])[0]
            title = (m.get("title") or [""])[0]
            album = (m.get("albumartist") or m.get("album") or [""])[0]
    except Exception:
        pass
    if not (artist and title):
        stem = re.sub(r"^\s*\d{1,3}\s*[-\.\s]+", "", os.path.splitext(os.path.basename(path))[0])
        if " - " in stem:
            a, t = stem.split(" - ", 1)
            artist = artist or a.strip()
            title = title or t.strip()
        else:
            title = title or stem.strip()
    return artist.strip(), title.strip(), album.strip()


def trash(path: str) -> bool:
    if not os.path.exists(path):
        return False
    r = subprocess.run(["trash", path], capture_output=True, text=True)
    return r.returncode == 0


# ---------------- tag normalization + canonical layout ----------------

def normalize_tags(path: str, write: bool = False) -> dict:
    """Return cleaned {artist,title,album,albumartist}; optionally persist via mutagen."""
    from walkman_dj import crate_tags
    import mutagen
    m = mutagen.File(path, easy=True)
    raw = {k: (m.get(k, [""])[0] if m else "") for k in ("artist", "title", "album", "albumartist")}
    stem = os.path.splitext(os.path.basename(path))[0]
    fa, ft = crate_tags.split_stem(stem)
    out = {
        "title": crate_tags.clean_title(raw["title"] or ft or ""),
        "artist": (raw["artist"] or fa or "").strip(),
        "album": crate_tags.clean_title(raw["album"] or ""),
        "albumartist": (raw["albumartist"] or raw["artist"] or fa or "").strip(),
    }
    if write and m is not None:
        for k, v in out.items():
            if v:
                m[k] = [v]
        m.save()
    return out


def _san(s: str) -> str:
    return re.sub(r"\s{2,}", " ", re.sub(r"[/:]", "-", (s or "").strip())).strip(" .") or "Unknown"


def canonical_path(root: str, src: str, tags: dict, tracknum=None) -> str:
    """Tag-driven target path: album-tagged -> <AlbumArtist>/<Album>/NN - Title; else _Singles/<Artist>/Title."""
    ext = os.path.splitext(src)[1].lower()
    title = _san(tags.get("title") or os.path.splitext(os.path.basename(src))[0])
    album = (tags.get("album") or "").strip()
    if album:
        aa = _san(tags.get("albumartist") or tags.get("artist") or "Unknown")
        nn = f"{int(tracknum):02d} - " if tracknum else ""
        return os.path.join(root, aa, _san(album), f"{nn}{title}{ext}")
    artist = _san(tags.get("artist") or "Unknown")
    return os.path.join(root, "_Singles", artist, f"{title}{ext}")


# ---------------- library ----------------

@dataclass
class IngestResult:
    action: str          # "added" | "replaced" | "discarded"
    canonical: str
    duplicate_of: str | None = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings(
  id INTEGER PRIMARY KEY,
  canonical_path TEXT UNIQUE,
  artist TEXT, title TEXT, album TEXT,
  fmt TEXT, duration REAL, dur_bucket INTEGER,
  fp TEXT,                       -- json list of ints
  akey TEXT                      -- normalized 'artist title' for resolve()
);
CREATE INDEX IF NOT EXISTS idx_bucket ON recordings(dur_bucket);
CREATE INDEX IF NOT EXISTS idx_akey ON recordings(akey);
"""


class CrateLib:
    def __init__(self, db_path: str, library_root: str):
        self.db_path = db_path
        self.root = library_root
        with self._c() as c:
            c.executescript(SCHEMA)

    def _c(self):
        c = sqlite3.connect(self.db_path, timeout=60)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=60000")
        return c

    # --- index helpers ---
    def _candidates(self, dur: float):
        b = int(dur)
        with self._c() as c:
            rows = c.execute(
                "SELECT * FROM recordings WHERE dur_bucket BETWEEN ? AND ?",
                (b - int(DUR_TOLERANCE) - 1, b + int(DUR_TOLERANCE) + 1)).fetchall()
        return rows

    def _match(self, fp: list[int], dur: float):
        for row in self._candidates(dur):
            if same_recording(fp, dur, json.loads(row["fp"]), row["duration"]):
                return row
        return None

    def _index(self, path: str, fp: list[int], dur: float, c=None):
        artist, title, album = tags(path)
        rec = dict(canonical_path=path, artist=artist, title=title, album=album,
                   fmt=os.path.splitext(path)[1].lower().lstrip("."), duration=dur,
                   dur_bucket=int(dur), fp=json.dumps(fp),
                   akey=_norm(f"{artist} {title}"))
        own = c is None
        c = c or self._c()
        try:
            c.execute("""INSERT INTO recordings(canonical_path,artist,title,album,fmt,duration,dur_bucket,fp,akey)
                         VALUES(:canonical_path,:artist,:title,:album,:fmt,:duration,:dur_bucket,:fp,:akey)
                         ON CONFLICT(canonical_path) DO UPDATE SET
                           artist=excluded.artist,title=excluded.title,album=excluded.album,
                           fmt=excluded.fmt,duration=excluded.duration,dur_bucket=excluded.dur_bucket,
                           fp=excluded.fp,akey=excluded.akey""", rec)
            if own: c.commit()
        finally:
            if own: c.close()

    def _reindex_path(self, old: str, new: str):
        with self._c() as c:
            c.execute("UPDATE recordings SET canonical_path=? WHERE canonical_path=?", (new, old))

    def _remove_index(self, path: str):
        with self._c() as c:
            c.execute("DELETE FROM recordings WHERE canonical_path=?", (path,))

    # --- filing ---
    def _dest_for(self, path: str) -> str:
        artist, _, album = tags(path)
        folder = f"{artist} - {album}" if (artist and album) else (artist or "Singles")
        folder = re.sub(r'[/:]', "-", folder).strip() or "Singles"
        d = os.path.join(self.root, folder)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, os.path.basename(path))

    def ingest(self, path: str, into_library: bool = True) -> IngestResult:
        """Fingerprint `path`; if it already exists keep the higher-quality copy and Trash
        the other; if new, file it into the library and index it."""
        path = os.path.abspath(path)
        fp, dur = fingerprint(path)
        match = self._match(fp, dur)
        if match:
            existing = match["canonical_path"]
            if os.path.exists(existing) and quality(existing) >= quality(path):
                # incoming is redundant/worse -> discard it
                if into_library or os.path.commonpath([path, self.root]) != self.root:
                    trash(path)
                return IngestResult("discarded", existing, duplicate_of=existing)
            # incoming is better -> it becomes canonical, old one is trashed
            dest = self._dest_for(path) if into_library else path
            if into_library and os.path.abspath(dest) != path:
                shutil.move(path, dest)
            if os.path.exists(existing) and os.path.abspath(existing) != os.path.abspath(dest):
                trash(existing)
            self._remove_index(existing)
            self._index(dest, fp, dur)
            return IngestResult("replaced", dest, duplicate_of=existing)
        # new recording
        dest = self._dest_for(path) if into_library else path
        if into_library and os.path.abspath(dest) != path:
            shutil.move(path, dest)
        self._index(dest, fp, dur)
        return IngestResult("added", dest)

    def resolve(self, query: str, min_score: float = 70.0) -> str | None:
        """Return the single canonical file best matching a free-text 'artist title' query."""
        from rapidfuzz import fuzz, process
        qk = _norm(query)
        with self._c() as c:
            rows = c.execute("SELECT canonical_path, akey, artist, title FROM recordings").fetchall()
        if not rows:
            return None
        choices = {r["canonical_path"]: r["akey"] for r in rows}
        best = process.extractOne(qk, choices, scorer=fuzz.WRatio)
        if best and best[1] >= min_score:
            return best[2]           # the key = canonical_path
        return None

    def dedupe_library(self, on_progress=None, on_remove=None) -> dict:
        """One-time: fingerprint every audio file under root, collapse dupe groups to the
        best copy (Trash the losers), and (re)build the index. Idempotent.
        on_remove(keeper_path, loser_path) is called for every removal (audit log)."""
        files = []
        for dp, _, fns in os.walk(self.root):
            if "_dedup-losers" in dp:
                continue
            for fn in fns:
                if os.path.splitext(fn)[1].lower() in AUDIO_EXTS and not os.path.islink(os.path.join(dp, fn)):
                    files.append(os.path.join(dp, fn))
        files.sort()
        groups: list[dict] = []     # each: {fp, dur, best}
        removed = kept = failed = 0
        with self._c() as c:
            c.execute("DELETE FROM recordings")
            for i, path in enumerate(files):
                if on_progress and i % 25 == 0:
                    on_progress(i, len(files), kept, removed)
                try:
                    fp, dur = fingerprint(path)
                except Exception:
                    failed += 1
                    continue
                hit = None
                b = int(dur)
                for g in groups:
                    if abs(g["dur"] - dur) <= DUR_TOLERANCE and same_recording(fp, dur, g["fp"], g["dur"]):
                        hit = g
                        break
                if hit is None:
                    groups.append({"fp": fp, "dur": dur, "best": path})
                    kept += 1
                    self._index(path, fp, dur, c=c)
                else:
                    # keep higher quality of (existing best, this)
                    if quality(path) > quality(hit["best"]):
                        loser = hit["best"]; hit["best"] = path
                        c.execute("DELETE FROM recordings WHERE canonical_path=?", (loser,))
                        self._index(path, fp, dur, c=c)
                    else:
                        loser = path
                    if on_remove:
                        on_remove(hit["best"], loser)
                    trash(loser)
                    removed += 1
            c.commit()
        if on_progress:
            on_progress(len(files), len(files), kept, removed)
        return {"scanned": len(files), "unique": kept, "removed": removed, "failed": failed}

    def stats(self) -> dict:
        with self._c() as c:
            n = c.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
        return {"recordings": n}

    def verify(self) -> dict:
        """Integrity check: missing files + accidental same-recording index rows."""
        import json as _json
        with self._c() as c:
            rows = c.execute("SELECT canonical_path, fp, duration FROM recordings").fetchall()
        missing = sum(1 for r in rows if not os.path.exists(r["canonical_path"]))
        by_bucket = {}
        for r in rows:
            by_bucket.setdefault(int(r["duration"]), []).append(r)
        dup = 0
        for grp in by_bucket.values():
            for i in range(len(grp)):
                for j in range(i + 1, len(grp)):
                    if same_recording(_json.loads(grp[i]["fp"]), grp[i]["duration"],
                                      _json.loads(grp[j]["fp"]), grp[j]["duration"]):
                        dup += 1
        return {"recordings": len(rows), "missing": missing, "duplicate_pairs": dup}
