"""Fill per-track popularity (`deezer_rank`) from Deezer's keyless search API.
Deezer returns a global `rank` per track (higher = more streamed) — our mainstream-
popularity proxy. Resumable (skips rows that already have a rank), threaded fetch +
single-writer commit, with a light artist-overlap guard to reject loose matches.

Usage: python3 tools/enrich_deezer_rank.py [--limit N] [--workers 8]
"""
import os, re, sys, json, time, argparse, sqlite3, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "library.db")
UA = "walkman-dj/1.0 (personal music library)"


def norm(s):
    if not s:
        return ""
    s = re.sub(r"\(feat[^)]*\)|\bfeat\b.*|\bft\.?\b.*", " ", str(s).lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def base_title(t):
    t = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", t or "")
    t = re.sub(r"\b(edit|remix|bootleg|flip|refix|rework|vip|mashup|blend|mix|"
               r"extended|radio|clean|dirty|feat|ft)\b.*", " ", t, flags=re.I)
    return " ".join(t.split())


def _get(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def deezer_rank(artist, title):
    """Return (rank, matched_artist) or (None, None). Guards against loose matches by
    requiring the matched artist to share a token with ours (or be a substring)."""
    bt = base_title(title) or title or ""
    q = urllib.parse.quote(f'artist:"{artist}" track:"{bt}"')
    for attempt in range(3):
        try:
            d = _get(f"https://api.deezer.com/search?q={q}&limit=1")
            if isinstance(d, dict) and d.get("error"):     # rate limit etc.
                time.sleep(1.5 * (attempt + 1)); continue
            r = (d.get("data") or [None])[0]
            if not r or "rank" not in r:
                return None, None
            ma = (r.get("artist") or {}).get("name", "")
            na, nm = set(norm(artist).split()), set(norm(ma).split())
            if na and nm and not (na & nm) and norm(ma) not in norm(artist):
                return None, ma            # matched a different artist -> reject
            return int(r["rank"]), ma
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    q = ("SELECT id, artist, title FROM recordings "
         "WHERE (deezer_rank IS NULL OR deezer_rank=0) AND artist IS NOT NULL AND title IS NOT NULL")
    todo = con.execute(q).fetchall()
    if a.limit:
        todo = todo[:a.limit]
    print(f"[start] {len(todo)} tracks missing deezer_rank")
    done = hit = rej = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(deezer_rank, r[1], r[2]): r for r in todo}
        for fut in as_completed(futs):
            rid, artist, title = futs[fut]
            try:
                rank, ma = fut.result()
            except Exception:
                rank, ma = None, None
            done += 1
            if rank is not None:
                con.execute("UPDATE recordings SET deezer_rank=? WHERE id=?", (rank, rid))
                hit += 1
            elif ma:                       # found but rejected as a different artist
                rej += 1
            if done % 100 == 0:
                con.commit()
                print(f"  {done}/{len(todo)}  hits={hit}  rejected={rej}")
    con.commit()
    print(f"[done] {done} processed, {hit} ranked, {rej} loose-match rejected")


if __name__ == "__main__":
    main()
