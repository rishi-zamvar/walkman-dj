"""Spotify catalog matcher. Reuses the spotify-playlists skill's OAuth access token.
Confidence-gated: never stores a wrong match. Access-token only — never rotates the
skill's refresh token out-of-band. Hard timeouts + token cache so a slow/rate-limited
Spotify never stalls the analyzer daemon."""
import os
import sys
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from rapidfuzz import fuzz

_SKILL = os.path.expanduser("~/.claude/skills/spotify-playlists")
_TOKENS = os.path.expanduser("~/.config/spotify-playlists/tokens.json")
_TIMEOUT = 8                       # hard cap per request — never hang the daemon
_tok = {"val": None, "exp": 0.0}   # cache the access token (~50 min)
_label_cache = {}                  # album_id -> label (one fetch per release)
_cool = {"until": 0.0}             # after a 429, skip Spotify until this time


def _token():
    if _tok["val"] and time.time() < _tok["exp"]:
        return _tok["val"]
    now = time.time()
    # Prefer the cached token file (instant); only invoke the skill's refresh
    # when the file token is actually expired (that call can block).
    try:
        d = json.load(open(_TOKENS))
        if d.get("access_token") and float(d.get("expires_at", 0)) > now + 60:
            _tok["val"], _tok["exp"] = d["access_token"], min(float(d["expires_at"]), now + 3000)
            return _tok["val"]
    except Exception:
        pass
    val = None
    sys.path.insert(0, _SKILL)
    try:
        import spotify_cli
        val = spotify_cli.get_access_token()
    except Exception:
        try:
            val = json.load(open(_TOKENS)).get("access_token")
        except Exception:
            val = None
    _tok["val"], _tok["exp"] = val, now + 3000
    return val


def _get(url):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_token()}"})
    return json.load(urllib.request.urlopen(req, timeout=_TIMEOUT))


def _search(q):
    if time.time() < _cool["until"]:      # in penalty window -> skip, don't stall
        return {}
    try:
        return _get("https://api.spotify.com/v1/search?type=track&limit=5&q=" + urllib.parse.quote(q))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            wait = min(int(e.headers.get("Retry-After", "30")), 300)
            _cool["until"] = time.time() + max(wait, 30)
        return {}
    except Exception:
        return {}


def _album_label(album_id):
    """Label lives on the full album object (search's album is simplified). Cached."""
    if not album_id:
        return None
    if album_id in _label_cache:
        return _label_cache[album_id]
    try:
        lbl = _get(f"https://api.spotify.com/v1/albums/{album_id}").get("label")
    except Exception:
        lbl = None
    _label_cache[album_id] = lbl
    return lbl


def match(artist: str, title: str, duration: float | None = None, min_conf: float = 0.7) -> dict:
    try:
        data = _search(f'artist:"{artist}" track:"{title}"') or _search(f"{artist} {title}")
    except Exception:
        return {}
    best, best_score = None, 0.0
    for t in data.get("tracks", {}).get("items", []):
        a = (t.get("artists") or [{}])[0].get("name", "")
        name_s = fuzz.token_set_ratio(title, t.get("name", "")) / 100
        art_s = fuzz.token_set_ratio(artist, a) / 100
        dur_s = 1.0
        if duration and t.get("duration_ms"):
            dur_s = max(0.0, 1 - abs(duration - t["duration_ms"] / 1000) / 30)
        score = 0.45 * art_s + 0.4 * name_s + 0.15 * dur_s
        if score > best_score:
            best, best_score = t, score
    if not best or best_score < min_conf:
        return {}
    album = best.get("album") or {}
    return {
        "spotify_id": best["id"],
        "isrc": (best.get("external_ids") or {}).get("isrc"),
        "spotify_popularity": best.get("popularity"),
        "release_date": album.get("release_date"),
        "label": _album_label(album.get("id")),
        "spotify_match_confidence": round(best_score, 3),
    }
