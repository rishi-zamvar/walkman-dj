"""Fill per-track genre from free online sources (no paid keys). For every track in
the DJ sets we look up a genre STRING and cache it. Priority per track:
  iTunes Search API  ->  Deezer (album genre_id)  ->  MusicBrainz (recording tags).
Cache is keyed by norm(artist)|norm(title) and written incrementally, so the run is
fully resumable. The coarse-bucket mapping lives in walkman_dj.genre (shared with the
set builder) — this tool only collects raw genre strings."""
import os, re, sys, json, time, glob, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj.genre import coarse_of                      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETS = "/Users/rishizamvar/Desktop/DJ Sets"
CACHE = os.path.join(ROOT, "data", "genre_online.json")
UA = "walkman-dj/1.0 (personal music library; contact local)"


def norm(s):
    if not s:
        return ""
    s = re.sub(r"\(feat[^)]*\)|\bfeat\b.*|\bft\.?\b.*", " ", str(s).lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def base_title(t):
    """Strip edit/remix parentheticals so the search matches the ORIGINAL recording
    (whose genre iTunes knows) — e.g. 'Sticky (JABBENS Edit)' -> 'Sticky'."""
    t = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", t or "")
    t = re.sub(r"\b(edit|remix|bootleg|flip|refix|rework|vip|mashup|blend|mix|"
               r"extended|radio|clean|dirty|feat|ft)\b.*", " ", t, flags=re.I)
    return " ".join(t.split())


def _get(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def itunes(artist, title):
    q = urllib.parse.quote(f"{artist} {title}".strip())
    try:
        d = _get(f"https://itunes.apple.com/search?term={q}&entity=song&limit=1")
        r = (d.get("results") or [None])[0]
        if r and r.get("primaryGenreName"):
            return r["primaryGenreName"], r.get("artistName"), r.get("trackName")
    except Exception:
        pass
    return None, None, None


def deezer(artist, title):
    q = urllib.parse.quote(f'artist:"{artist}" track:"{title}"')
    try:
        d = _get(f"https://api.deezer.com/search?q={q}&limit=1")
        r = (d.get("data") or [None])[0]
        if r and r.get("album", {}).get("id"):
            alb = _get(f"https://api.deezer.com/album/{r['album']['id']}")
            g = (alb.get("genres", {}).get("data") or [None])[0]
            if g and g.get("name"):
                return g["name"]
    except Exception:
        pass
    return None


def musicbrainz(artist, title):
    q = urllib.parse.quote(f'recording:"{title}" AND artist:"{artist}"')
    try:
        d = _get(f"https://musicbrainz.org/ws/2/recording?query={q}&limit=1&fmt=json"
                 f"&inc=genres")
        recs = d.get("recordings") or []
        if recs:
            gs = recs[0].get("genres") or []
            if gs:
                return max(gs, key=lambda g: g.get("count", 0)).get("name")
        time.sleep(1.0)                       # MB asks 1 req/sec
    except Exception:
        pass
    return None


def resolve(artist, title):
    bt = base_title(title) or title
    g, ma, mt = itunes(artist, bt)
    if g:
        return {"genre": g, "coarse": coarse_of(g), "src": "itunes"}
    g = deezer(artist, bt)
    if g:
        return {"genre": g, "coarse": coarse_of(g), "src": "deezer"}
    g = musicbrainz(artist, bt)
    if g:
        return {"genre": g, "coarse": coarse_of(g), "src": "musicbrainz"}
    return {"genre": None, "coarse": None, "src": "none"}


def from_name(name):
    stem = re.sub(r"^\d+\s*[-.]\s*", "", os.path.splitext(name)[0])
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        return a.strip(), t.strip()
    return None, stem.strip()


def meta(p):
    try:
        import mutagen
        m = mutagen.File(p, easy=True)
        a = (m.get("artist") or [None])[0] if m else None
        t = (m.get("title") or [None])[0] if m else None
    except Exception:
        a = t = None
    if not a or not t:
        fa, ft = from_name(os.path.basename(p))
        a = a or fa
        t = t or ft
    return a, t


def main():
    paths = set()
    for f in glob.glob(SETS + "/*.m3u8"):
        for l in open(f, encoding="utf-8"):
            l = l.strip()
            if l.startswith("/"):
                paths.add(l)
    cache = {}
    if os.path.exists(CACHE):
        cache = json.load(open(CACHE))
    # build worklist of uncached (artist,title,key)
    work = []
    for p in paths:
        a, t = meta(p)
        k = f"{norm(a)}|{norm(t)}"
        if k and k not in cache and (a or t):
            work.append((k, a or "", t or ""))
    # dedup keys
    seen = set()
    work = [w for w in work if not (w[0] in seen or seen.add(w[0]))]
    print(f"tracks: {len(paths)} | cached: {len(cache)} | to look up: {len(work)}",
          flush=True)

    done = [0]

    def job(item):
        k, a, t = item
        res = resolve(a, t)
        res["artist"] = a
        res["title"] = t
        return k, res
    with ThreadPoolExecutor(max_workers=8) as ex:
        for k, res in ex.map(job, work):
            cache[k] = res
            done[0] += 1
            if done[0] % 25 == 0:
                json.dump(cache, open(CACHE, "w"))
                hit = sum(1 for v in cache.values() if v.get("coarse"))
                print(f"  {done[0]}/{len(work)}  (coarse-resolved so far: {hit})",
                      flush=True)
    json.dump(cache, open(CACHE, "w"))
    from collections import Counter
    cov = Counter(v.get("coarse") or "??" for v in cache.values())
    src = Counter(v.get("src") for v in cache.values())
    print(f"\nDONE. cache={len(cache)}  sources={dict(src)}")
    print("coarse coverage:", dict(cov.most_common()))


if __name__ == "__main__":
    main()
