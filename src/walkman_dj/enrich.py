"""Comprehensive metadata enrichment: identify a track, look it up in the best
databases, merge by accuracy. Look it up — don't guess. Audio genre is last resort.

Sources: AcoustID (identify via fingerprint) -> MusicBrainz (canonical/ISRC/label/date)
+ Discogs (electronic genre/style/year/label) + Last.fm (crowd tags). Rekordbox owns
bpm/key/beatgrid — this module NEVER writes those. Every write records provenance.
"""
import json
import subprocess
import time
import urllib.parse
import urllib.request

from rapidfuzz import fuzz

from walkman_dj import genre_map, keys

_UA = "walkman-dj/1.0 (personal music library)"
_TIMEOUT = 10
# Rekordbox-owned fields — merge must never emit these.
_DENY = {"bpm", "camelot", "key", "first_downbeat", "beat_engine", "bpm_confidence", "key_confidence"}
# per-source minimum seconds between calls (politeness / rate limits)
_RATE = {"acoustid": 0.34, "musicbrainz": 1.05, "discogs": 1.05, "lastfm": 0.25}
_last = {}


def _throttle(src):
    now = time.time()
    wait = _RATE.get(src, 0) - (now - _last.get(src, 0))
    if wait > 0:
        time.sleep(wait)
    _last[src] = time.time()


def _get(url, src, headers=None):
    _throttle(src)
    req = urllib.request.Request(url, headers={"User-Agent": _UA, **(headers or {})})
    try:
        return json.load(urllib.request.urlopen(req, timeout=_TIMEOUT))
    except Exception:
        return {}


# ---- identify ------------------------------------------------------------
def _fpcalc(path):
    """(fingerprint_str, duration_int) via fpcalc, or (None, None)."""
    try:
        out = subprocess.run(["fpcalc", "-json", path], capture_output=True,
                             text=True, timeout=30)
        d = json.loads(out.stdout)
        return d.get("fingerprint"), int(d.get("duration", 0))
    except Exception:
        return None, None


def identify(path, artist="", title=""):
    """Best MusicBrainz recording ID for this track. AcoustID via fingerprint first,
    else MusicBrainz text search. Returns {mbid, artist, title} or {}."""
    key = keys.get("ACOUSTID_KEY")
    if key:
        fp, dur = _fpcalc(path)
        if fp and dur:
            url = ("https://api.acoustid.org/v2/lookup?client=" + key +
                   "&meta=recordings&duration=" + str(dur) +
                   "&fingerprint=" + urllib.parse.quote(fp))
            data = _get(url, "acoustid")
            for res in sorted(data.get("results", []),
                              key=lambda r: r.get("score", 0), reverse=True):
                for rec in res.get("recordings", []):
                    if rec.get("id"):
                        a = (rec.get("artists") or [{}])[0].get("name", "")
                        return {"mbid": rec["id"], "artist": a, "title": rec.get("title")}
    # fallback: MusicBrainz text search — REQUIRES a real artist (blank-artist search
    # returns confident garbage), and the hit's artist must fuzzy-match ours.
    if artist and title:
        q = urllib.parse.quote(f'artist:"{artist}" AND recording:"{title}"')
        data = _get(f"https://musicbrainz.org/ws/2/recording?query={q}&fmt=json&limit=3",
                    "musicbrainz")
        for r in data.get("recordings", []):
            a = (r.get("artist-credit") or [{}])[0].get("name", "")
            if fuzz.token_set_ratio(artist, a) / 100 >= 0.72:
                return {"mbid": r["id"], "artist": a, "title": r.get("title")}
    return {}


# ---- per-source lookups (each returns only what it knows; never raises) ----
def mb(mbid):
    if not mbid:
        return {}
    data = _get(f"https://musicbrainz.org/ws/2/recording/{mbid}"
                "?inc=genres+tags+artist-credits+releases+isrcs&fmt=json", "musicbrainz")
    out = {}
    genres = data.get("genres") or []
    if genres:
        out["genre"] = max(genres, key=lambda g: g.get("count", 0)).get("name")
    if data.get("isrcs"):
        out["isrc"] = data["isrcs"][0]
    rel = (data.get("releases") or [{}])[0]
    if rel.get("date"):
        out["release_date"] = rel["date"]
    if rel.get("country"):
        out["country"] = rel["country"]
    lbl = ((rel.get("label-info") or [{}])[0].get("label") or {}).get("name")
    if lbl:
        out["label"] = lbl
    return out


_DISCOGS_MIN = 0.72   # min artist-match confidence — reject loose/wrong hits


def _discogs_artist(hit):
    """Artist from a Discogs release hit — the part before ' - ' in its title."""
    return (hit.get("title") or "").split(" - ")[0].strip()


def discogs(artist, title):
    tok = keys.get("DISCOGS_TOKEN")
    if not tok or not artist:
        return {}
    q = urllib.parse.quote(f"{artist} {title}")
    url = (f"https://api.discogs.com/database/search?q={q}&type=release&per_page=5&token=" + tok)
    hits = _get(url, "discogs").get("results", [])
    # confidence gate: the hit's artist must actually match ours (kills "Various", wrong artist)
    best, best_s = None, 0.0
    for hit in hits:
        s = fuzz.token_set_ratio(artist, _discogs_artist(hit)) / 100
        if s > best_s:
            best, best_s = hit, s
    if not best or best_s < _DISCOGS_MIN:
        return {}
    h = best
    out = {}
    styles = h.get("style") or []
    parents = h.get("genre") or []
    if styles or parents:
        label = f"{(parents or ['Electronic'])[0]}---{(styles or parents)[0]}"
        g, sub = genre_map.map_discogs(label)
        out["genre"], out["subgenre"] = g, sub
    if h.get("year"):
        out["year"] = int(h["year"])
    if h.get("label"):
        out["label"] = h["label"][0]
    if h.get("catno"):
        out["catalog_no"] = h["catno"]
    return out


def lastfm(artist, title):
    key = keys.get("LASTFM_KEY")
    if not key or not (artist and title):
        return {}
    data = _get("https://ws.audioscrobbler.com/2.0/?method=track.gettoptags&artist="
                + urllib.parse.quote(artist) + "&track=" + urllib.parse.quote(title)
                + "&api_key=" + key + "&format=json", "lastfm")
    tags = [t["name"] for t in (data.get("toptags") or {}).get("tag", [])
            if int(t.get("count", 0)) >= 20]
    return {"tags": tags} if tags else {}


# ---- merge (accuracy-ranked) ---------------------------------------------
# field -> source priority (first present wins)
_PRIORITY = {
    "genre": ["discogs", "mb", "lastfm"],
    "subgenre": ["discogs"],
    "label": ["mb", "discogs"],
    "release_date": ["mb"],
    "isrc": ["mb"],
    "year": ["discogs"],
    "catalog_no": ["discogs"],
    "country": ["mb"],
}


def merge(sources):
    """sources = {'mb':{...},'discogs':{...},'lastfm':{...}} -> (updates, provenance).
    Genres normalised; Rekordbox-owned fields never emitted; each field tags its source."""
    updates, prov = {}, {}
    for field, order in _PRIORITY.items():
        if field in _DENY:
            continue
        for src in order:
            val = sources.get(src, {}).get(field)
            if field in ("genre", "subgenre") and val:
                val = genre_map.normalize(val) or val
            if val:
                updates[field], prov[field] = val, src
                if field == "genre":          # keep genre_source consistent with the winner
                    updates["genre_source"] = src
                break
    # mood_tags from last.fm (informational, not genre)
    tags = sources.get("lastfm", {}).get("tags")
    if tags:
        updates["mood_tags"], prov["mood_tags"] = json.dumps(tags[:8]), "lastfm"
    return updates, prov


def enrich_one(path, artist, title):
    """Full pipeline for one track -> (updates, provenance, mbid).
    Skips tracks with no artist tag — they can't be reliably identified, and a
    blank-artist lookup returns confident-but-wrong data."""
    if not (artist or "").strip():
        return {}, {}, None
    ident = identify(path, artist, title)
    a = ident.get("artist") or artist
    t = ident.get("title") or title
    sources = {"mb": mb(ident.get("mbid")), "discogs": discogs(a, t), "lastfm": lastfm(a, t)}
    updates, prov = merge(sources)
    if ident.get("mbid"):
        updates["mbid"], prov["mbid"] = ident["mbid"], "acoustid"
    return updates, prov, ident.get("mbid")
