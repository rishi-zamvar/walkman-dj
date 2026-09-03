"""Gather cultural metadata — Discogs styles/label + Last.fm crowd tags — into `track_tags`
(id, source, tag, weight). These become multi-hot features for the genre model (the granular
'style' + human 'vibe' signals audio can't capture). Rate-limited + resumable.

Run: python3 src/walkman_dj/metadata_tags.py discogs [--limit N]
     python3 src/walkman_dj/metadata_tags.py lastfm  [--limit N]   # needs LASTFM_KEY
"""
import os, sys, time, json, sqlite3, urllib.request, urllib.parse
DB = os.path.expanduser("~/Documents/Projects/walkman-dj/data/library.db")
UA = "walkman-dj/1.0 +local"


def _keys():
    env = {}
    p = os.path.expanduser("~/.config/keys/enrich.env")
    for line in open(p):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); env[k] = v.strip().strip('"')
    return env


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return json.load(urllib.request.urlopen(req, timeout=15))


def discogs_tags(artist, title, tok):
    q = urllib.parse.urlencode({"q": f"{artist} {title}", "type": "release",
                                "token": tok, "per_page": 5})
    try:
        res = _get("https://api.discogs.com/database/search?" + q).get("results", [])
    except Exception:
        return []
    if not res:
        return []
    h = res[0]
    out = []
    for s in (h.get("style") or []):
        out.append(("discogs_style", s, 1.0))
    for g in (h.get("genre") or []):
        out.append(("discogs_genre", g, 1.0))
    for lb in (h.get("label") or [])[:1]:
        out.append(("discogs_label", lb, 1.0))
    return out


def lastfm_tags(artist, title, key):
    q = urllib.parse.urlencode({"method": "track.gettoptags", "artist": artist,
                                "track": title, "api_key": key, "format": "json"})
    try:
        data = _get("https://ws.audioscrobbler.com/2.0/?" + q)
    except Exception:
        return []
    tags = (data.get("toptags") or {}).get("tag") or []
    return [("lastfm", t["name"].lower(), float(t.get("count", 0)) / 100.0)
            for t in tags[:10] if int(t.get("count", 0)) >= 10]


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else "discogs"
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    keys = _keys()
    if source == "discogs":
        cred = keys.get("DISCOGS_TOKEN"); fetch = discogs_tags; sleep = 1.15   # ~52/min
    elif source == "lastfm":
        cred = keys.get("LASTFM_KEY"); fetch = lastfm_tags; sleep = 0.22
    else:
        raise SystemExit("source must be discogs|lastfm")
    if not cred:
        raise SystemExit(f"no credential for {source} in ~/.config/keys/enrich.env")

    con = sqlite3.connect(DB, timeout=120)
    con.execute("create table if not exists track_tags "
                "(id integer, source text, tag text, weight real, primary key(id,source,tag))")
    con.commit()
    done_ids = {r[0] for r in con.execute("select distinct id from track_tags where source like ?",
                                          (source.split('_')[0] + "%",))}
    rows = [(rid, a, t) for rid, a, t in con.execute(
        "select id,artist,title from recordings where artist is not null and title is not null "
        "and genre is not null and genre!='' ") if rid not in done_ids]
    if limit:
        rows = rows[:limit]
    print(f"{source}: {len(rows)} tracks to fetch", flush=True)
    n = hits = 0
    for rid, a, t in rows:
        tags = fetch(a, t, cred)
        for src, tag, w in tags:
            con.execute("insert or ignore into track_tags(id,source,tag,weight) values(?,?,?,?)",
                        (rid, src, tag, w))
        if tags:
            hits += 1
        n += 1
        if n % 25 == 0:
            con.commit(); print(f"{n}/{len(rows)} with-tags={hits}", flush=True)
        time.sleep(sleep)
    con.commit(); con.close()
    print(f"{source.upper()} DONE: {n} fetched, {hits} had tags", flush=True)


if __name__ == "__main__":
    main()
