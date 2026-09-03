"""Deterministic artist/album normalization for crate ingest.
Seeded maps (agent-reviewed) fix known cases; generic patterns catch new ones on ingest.
Rule files live in data/rules/. Adding a correction there makes it permanent."""
import json, os, re, unicodedata

_D = os.path.join(os.path.dirname(__file__), "..", "..", "data", "rules")
def _load_json(f, default):
    try: return json.load(open(os.path.join(_D, f)))
    except Exception: return default
def _load_lines(f):
    try: return [l.strip() for l in open(os.path.join(_D, f)) if l.strip()]
    except Exception: return []

ALIASES   = _load_json("artist_aliases.json", {})           # variant/junk -> canonical (sets artist+albumartist)
COLLAB    = _load_json("collab_primary.json", {})           # "X feat. Y" -> primary (sets albumartist only)
PL_PAIRS  = {tuple(x) for x in _load_json("playlist_albums.json", [])}  # (artist, album) playlist
SINGLE_ACTS = {s.lower() for s in _load_lines("single_acts.txt")}
NONARTIST_RE = [re.compile(p, re.I) for p in _load_lines("nonartist_patterns.txt")]
PLAYLIST_RE  = [re.compile(p, re.I) for p in _load_lines("playlist_patterns.txt")]
_SEP = re.compile(r"\s*(?:feat\.?|ft\.?|featuring|with|,|&|;|\sx\s|\svs\.?)\s*", re.I)

def clean(s):
    if not s: return s
    return unicodedata.normalize("NFC", "".join(c for c in str(s) if unicodedata.category(c)[0] != "C")).strip()

def _primary(name):
    parts = _SEP.split(name)
    return parts[0].strip() if parts and parts[0].strip() else name

def is_playlist_album(artist, album, use_patterns=False):
    if not album: return False
    if (clean(artist), clean(album)) in PL_PAIRS: return True
    return use_patterns and any(r.search(album) for r in PLAYLIST_RE)

def looks_nonartist(name):
    return bool(name) and any(r.search(name) for r in NONARTIST_RE)

def normalize(artist, albumartist, album, title="", generic=False):
    """Return (artist, albumartist, album, notes[]). Seeded maps always apply;
    generic pattern splitting/nonartist only when generic=True (i.e. fresh ingest)."""
    a = clean(artist); aa = clean(albumartist) or a; al = clean(album); notes = []
    # 1. reassign (dup-spelling merges + song/label/uploader-as-artist)
    if a in ALIASES and ALIASES[a] != a:
        aa = ALIASES[a]; a = ALIASES[a]; notes.append("alias")
    if aa in ALIASES and ALIASES[aa] != aa:
        aa = ALIASES[aa]
    # 2. collab consolidation — albumartist becomes the primary, artist keeps the full credit
    if artist and clean(artist) in COLLAB:
        aa = COLLAB[clean(artist)]; notes.append("collab")
    elif generic and aa and aa.lower() not in SINGLE_ACTS and _SEP.search(aa) and aa not in ALIASES:
        p = _primary(aa)
        if p and p != aa: aa = p; notes.append("collab-generic")
    # 3. playlist posing as album -> demote to single (clear album tag)
    if al and is_playlist_album(a, al, use_patterns=generic):
        al = ""; notes.append("playlist->single")
    # 4. (generic ingest only) flag label/uploader junk artist for fingerprint fallback
    if generic and looks_nonartist(a):
        notes.append("nonartist?")
    return a, aa, al, notes
