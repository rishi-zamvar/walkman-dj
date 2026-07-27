"""Spotify catalog matcher. Reuses the spotify-playlists skill's OAuth access token.
Confidence-gated: never stores a wrong match. Access-token only — never rotates the
skill's refresh token out-of-band."""
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


def _token():
    sys.path.insert(0, _SKILL)
    try:
        import spotify_cli
        return spotify_cli.get_access_token()
    except Exception:
        try:
            return json.load(open(_TOKENS)).get("access_token")
        except Exception:
            return None


def _get(url):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_token()}"})
    return json.load(urllib.request.urlopen(req, timeout=20))


def _search(q):
    for _ in range(3):
        try:
            return _get("https://api.spotify.com/v1/search?type=track&limit=5&q=" + urllib.parse.quote(q))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(int(e.headers.get("Retry-After", "2")) + 1)
                continue
            return {}
        except Exception:
            return {}
    return {}


def _artist_genres(aid):
    try:
        return _get(f"https://api.spotify.com/v1/artists/{aid}").get("genres", [])
    except Exception:
        return []


def match(artist: str, title: str, duration: float | None = None, min_conf: float = 0.7) -> dict:
    data = _search(f'artist:"{artist}" track:"{title}"') or _search(f"{artist} {title}")
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
    aid = (best.get("artists") or [{}])[0].get("id")
    return {
        "spotify_id": best["id"],
        "isrc": (best.get("external_ids") or {}).get("isrc"),
        "spotify_popularity": best.get("popularity"),
        "release_date": (best.get("album") or {}).get("release_date"),
        "label": (best.get("album") or {}).get("label"),
        "artist_genres": json.dumps(_artist_genres(aid) if aid else []),
        "spotify_match_confidence": round(best_score, 3),
    }
