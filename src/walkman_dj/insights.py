"""Library insights: genre/era/label/key/vibe breakdown with source coverage.
Plain text, computed from library.db. Trustworthy because it shows provenance."""
import json
import sqlite3


def report(db):
    c = sqlite3.connect(db)
    tot = c.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
    L = [f"# Library Insights — {tot} tracks", ""]

    def dist(sql, title, n=12):
        rows = c.execute(sql).fetchall()
        L.append(f"## {title}")
        for k, v in rows[:n]:
            L.append(f"  {v:5}  {k if k is not None else '(none)'}")
        L.append("")

    dist("SELECT genre, COUNT(*) FROM recordings GROUP BY genre ORDER BY 2 DESC", "Genres")
    dist("SELECT subgenre, COUNT(*) FROM recordings WHERE subgenre IS NOT NULL "
         "GROUP BY subgenre ORDER BY 2 DESC", "Subgenres")
    dist("SELECT label, COUNT(*) FROM recordings WHERE label IS NOT NULL "
         "GROUP BY label ORDER BY 2 DESC", "Top labels")
    dist("SELECT (year/10)*10 AS dec, COUNT(*) FROM recordings WHERE year IS NOT NULL "
         "GROUP BY dec ORDER BY dec", "By decade", 20)

    # BPM bands
    L.append("## BPM bands")
    for lo, hi in [(0, 90), (90, 110), (110, 125), (125, 135), (135, 150), (150, 999)]:
        n = c.execute("SELECT COUNT(*) FROM recordings WHERE bpm>=? AND bpm<?", (lo, hi)).fetchone()[0]
        L.append(f"  {n:5}  {lo}-{hi} bpm")
    L.append("")

    # provenance coverage — how much is DB-sourced vs audio-guessed
    L.append("## Source coverage (trust)")
    src = dict(c.execute("SELECT genre_source, COUNT(*) FROM recordings "
                         "WHERE genre_source IS NOT NULL GROUP BY genre_source"))
    L.append(f"  genre: {src}")
    for col in ("bpm", "camelot", "label", "isrc", "year", "mbid", "valence"):
        n = c.execute(f"SELECT COUNT(*) FROM recordings WHERE {col} IS NOT NULL").fetchone()[0]
        L.append(f"  {col:10} {n:5}/{tot}  ({100*n//tot}%)")
    rb = c.execute("SELECT COUNT(*) FROM recordings WHERE beat_engine='rekordbox'").fetchone()[0]
    L.append(f"  key/bpm from Rekordbox: {rb}/{tot} ({100*rb//tot}%)")
    return "\n".join(L) + "\n"
