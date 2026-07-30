"""crate CLI — standardized ingest / output with automatic acoustic dedup.

    crate ingest <file> [<file> ...]     file(s) -> library, deduped on the way in
    crate ingest --get "<query>"         download the query (yt-dlp) then ingest+dedupe
    crate get "<query>" [--to DIR]        resolve to the exact canonical file; copy out if --to
    crate dedupe                          fingerprint whole library, collapse dupes to best copy
    crate stats                           index size

Library root defaults to ~/Desktop/MusicLake; index at data/library.db (override with
$CRATE_ROOT / $CRATE_DB). All deletions go to Trash, never rm.
"""
import os, sys, argparse, subprocess, tempfile, glob
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from walkman_dj import crate

ROOT = os.environ.get("CRATE_ROOT", os.path.expanduser("~/Documents/MusicLake"))
DB = os.environ.get("CRATE_DB", os.path.join(os.path.dirname(__file__), "..", "..", "data", "library.db"))


def _lib():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    return crate.CrateLib(DB, ROOT)


def _download(query: str, dest_dir: str, fmt: str = "m4a") -> str | None:
    out = os.path.join(dest_dir, "%(title)s.%(ext)s")
    subprocess.run(["yt-dlp", "-x", "--audio-format", fmt, "--audio-quality", "0",
                    "--no-playlist", "--no-warnings", "-o", out, f"ytsearch1:{query}"],
                   capture_output=True, text=True, timeout=600)
    hits = [f for f in glob.glob(os.path.join(dest_dir, "*")) if os.path.splitext(f)[1].lower() in crate.AUDIO_EXTS]
    return hits[0] if hits else None


def cmd_ingest(args):
    lib = _lib()
    if args.get:
        with tempfile.TemporaryDirectory() as td:
            print(f"[download] {args.get}")
            f = _download(args.get, td, args.format)
            if not f:
                sys.exit("download failed / no result")
            r = lib.ingest(f)
            _report(r)
        return
    for path in args.files:
        if not os.path.exists(path):
            print(f"[skip] not found: {path}"); continue
        r = lib.ingest(path)
        _report(r)


def _report(r: "crate.IngestResult"):
    verb = {"added": "＋ added", "replaced": "↑ replaced (kept better)", "discarded": "✓ already have (kept best)"}[r.action]
    print(f"  {verb}: {os.path.relpath(r.canonical, ROOT)}")


def cmd_get(args):
    lib = _lib()
    hit = lib.resolve(args.query)
    if not hit:
        sys.exit(f"no match for: {args.query}")
    if not args.to:
        print(hit); return
    os.makedirs(args.to, exist_ok=True)
    base = os.path.splitext(os.path.basename(hit))[0]
    if args.format and not hit.lower().endswith("." + args.format):
        dest = os.path.join(args.to, base + "." + args.format)
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", hit,
                        "-c:a", "aac" if args.format == "m4a" else args.format,
                        "-b:a", "256k", "-map_metadata", "0", dest], check=False)
    else:
        dest = os.path.join(args.to, os.path.basename(hit))
        subprocess.run(["cp", hit, dest], check=False)
    print(dest)


def cmd_export(args):
    os.makedirs(args.to, exist_ok=True)
    entries = [l.strip() for l in open(args.playlist) if l.strip() and not l.startswith("#")]
    done = 0
    for src in entries:
        if not os.path.exists(src):
            continue
        base = os.path.splitext(os.path.basename(src))[0]
        if args.format and not src.lower().endswith("." + args.format):
            dst = os.path.join(args.to, base + "." + args.format)
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src, "-map", "0:a", "-map", "0:v?",
                            "-c:v", "copy", "-c:a", "aac" if args.format == "m4a" else args.format,
                            "-b:a", "256k", "-map_metadata", "0", "-disposition:v", "attached_pic", dst],
                           capture_output=True)
            if not os.path.exists(dst):
                subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src,
                                "-c:a", "aac", "-b:a", "256k", dst], capture_output=True)
        else:
            dst = os.path.join(args.to, os.path.basename(src)); subprocess.run(["cp", src, dst])
        if os.path.exists(dst):
            done += 1
    print(f"exported {done}/{len(entries)} -> {args.to}")


def cmd_enrich(args):
    adb = args.analysis or os.path.join(os.path.dirname(DB), "walkman_analysis.db")
    print("enriched", _lib().enrich_from_analysis(adb), "recordings from", os.path.basename(adb))


def cmd_organize(args):
    lib = _lib(); logf = open(args.log, "w") if args.log else None
    n = 0
    def mv(s, d):
        nonlocal n; n += 1
        if logf: logf.write(f"{s}\t->\t{d}\n"); logf.flush()
    moves = lib.organize(dry_run=not args.apply, on_move=mv)
    if args.apply:
        for dp, _, _ in os.walk(ROOT, topdown=False):        # prune emptied dirs
            if dp != ROOT and not os.listdir(dp):
                try: os.rmdir(dp)
                except OSError: pass
        print(f"moved {n} files into canonical layout")
    else:
        for s, d in moves[:40]:
            print(f"  {os.path.relpath(s, ROOT)}  ->  {os.path.relpath(d, ROOT)}")
        print(f"... {len(moves)} planned moves (run with --apply to execute)")
    if logf: logf.close()


def cmd_dedupe(args):
    lib = _lib()
    logf = open(args.log, "w") if args.log else None
    def prog(i, n, kept, removed):
        pct = round(i / n * 100) if n else 100
        print(f"\r[dedupe] {i}/{n} ({pct}%)  unique={kept} removed={removed}", end="", flush=True)
    def rm(keeper, loser):
        if logf:
            logf.write(f"KEEP\t{keeper}\nTRASH\t{loser}\n"); logf.flush()
    res = lib.dedupe_library(on_progress=prog, on_remove=rm)
    if logf: logf.close()
    print(f"\nDONE: scanned {res['scanned']}, unique {res['unique']}, removed {res['removed']} dupes, failed {res['failed']}")
    if args.log: print(f"removal log: {args.log}")


def cmd_stats(args):
    print(_lib().stats())


def cmd_verify(args):
    print(_lib().verify())


def cmd_backfill(args):
    import sqlite3
    from walkman_dj import schema, intelligence
    schema.ensure_schema(DB)
    n = 0
    with sqlite3.connect(DB) as c:
        rows = c.execute("SELECT fp, canonical_path FROM recordings WHERE analysis_version IS NULL OR analysis_version!=?",
                         (intelligence.ANALYSIS_VERSION,)).fetchall()
        for fp, path in rows:
            schema.enqueue(DB, fp, path); n += 1
    print(f"enqueued {n} songs for analysis — run: crate analyzer --once")


def cmd_backfill_genre(args):
    """Enqueue every row missing a genre (incl. already-analyzed rows) so the
    analyzer adds genre/valence and re-runs the fixed acousticness."""
    import sqlite3
    from walkman_dj import schema
    schema.ensure_schema(DB)
    n = 0
    with sqlite3.connect(DB) as c:
        rows = c.execute("SELECT fp, canonical_path FROM recordings WHERE genre IS NULL").fetchall()
        for fp, path in rows:
            schema.enqueue(DB, fp, path); n += 1
    print(f"enqueued {n} songs for genre backfill — run: crate analyzer --daemon")


def cmd_refix_genre(args):
    """Re-decide every audio-classified genre under the current confidence floor.
    Uses the stored audio prediction + re-read tag — no re-analysis needed."""
    import sqlite3
    from walkman_dj import genre_label, schema
    from mutagen import File as MF
    schema.ensure_schema(DB)
    c = sqlite3.connect(DB, timeout=60); c.row_factory = sqlite3.Row
    rows = c.execute("SELECT fp,genre,subgenre,genre_confidence,canonical_path,title "
                     "FROM recordings WHERE genre_source='audio'").fetchall()
    changed = 0
    for r in rows:
        audio = {"genre": r["genre"], "subgenre": r["subgenre"],
                 "genre_confidence": r["genre_confidence"]}
        try:
            tags = MF(r["canonical_path"], easy=True) or {}
        except Exception:
            tags = {}
        new = genre_label.label_track(r["canonical_path"], tags, audio)
        if new["genre"] != r["genre"] or new["genre_source"] != "audio":
            c.execute("UPDATE recordings SET genre=?,subgenre=?,genre_source=?,genre_confidence=? WHERE fp=?",
                      (new["genre"], new["subgenre"], new["genre_source"], new["genre_confidence"], r["fp"]))
            changed += 1
    c.commit()
    print(f"refixed {changed}/{len(rows)} audio genres (floor={genre_label._AUDIO_MIN})")


def cmd_import_rekordbox(args):
    """Import authoritative key + BPM from the live Rekordbox DB (close Rekordbox first)."""
    from walkman_dj import rekordbox_import
    try:
        r = rekordbox_import.import_keys(DB)
    except Exception as e:
        print(f"could not read Rekordbox DB ({type(e).__name__}: {e}).")
        print("Make sure Rekordbox is installed and CLOSED, then retry.")
        return
    print(f"Rekordbox: {r['rekordbox_tracks']} tracks | matched {r['matched']} by path | "
          f"updated {r['updated']} with authoritative key+BPM")


def cmd_enrich_meta(args):
    """Fill genre/label/release_date/ISRC/year from AcoustID+MusicBrainz+Discogs+Last.fm.
    Rekordbox owns key/bpm — never touched. Targets 'Other'/untagged/missing-catalog rows."""
    import sqlite3, json
    from walkman_dj import enrich, schema
    schema.ensure_schema(DB)
    c = sqlite3.connect(DB, timeout=60); c.row_factory = sqlite3.Row
    if args.only_unlabeled:
        where = "genre='Other' OR genre IS NULL"
    else:
        where = ("genre='Other' OR genre IS NULL OR label IS NULL "
                 "OR release_date IS NULL OR isrc IS NULL")
    # only rows with a real artist tag — blank-artist tracks can't be reliably identified
    rows = c.execute(f"SELECT fp,canonical_path,artist,title,provenance FROM recordings "
                     f"WHERE ({where}) AND artist IS NOT NULL AND TRIM(artist)!='' "
                     f"ORDER BY artist LIMIT ?", (args.limit,)).fetchall()
    print(f"enriching {len(rows)} tracks...")
    done = filled = 0
    for r in rows:
        try:
            up, prov, _ = enrich.enrich_one(r["canonical_path"], r["artist"] or "", r["title"] or "")
        except Exception:
            up, prov = {}, {}
        if up:
            merged_prov = {**(json.loads(r["provenance"]) if r["provenance"] else {}), **prov}
            up["provenance"] = json.dumps(merged_prov)
            sets = ",".join(f"{k}=?" for k in up)
            c.execute(f"UPDATE recordings SET {sets} WHERE fp=?", (*up.values(), r["fp"]))
            filled += 1
        done += 1
        if done % 25 == 0:
            c.commit(); print(f"  {done}/{len(rows)} ({filled} enriched)", flush=True)
    c.commit()
    print(f"done: {filled}/{len(rows)} tracks enriched")


def cmd_insights(args):
    from walkman_dj import insights, schema
    schema.ensure_schema(DB)
    txt = insights.report(DB)
    print(txt)
    out = os.path.join(os.path.dirname(DB), "insights.txt")
    open(out, "w").write(txt)
    print(f"(saved -> {out})")


def cmd_vibe_path(args):
    from walkman_dj import vibe_path
    r = vibe_path.run(DB, args.genre, vibe=args.vibe, k=args.min_vibe, min_conf=args.min_conf,
                      pct=args.pct, do_rationalize=args.rationalize)
    if r.get("error"):
        print(r["error"]); return
    print(f"{args.genre} [{r['vibe']}]: {r['candidates']} candidates -> {r['selected']} selected")
    print(f"  {r['m3u8']}\n  {r['md']}")


def cmd_analyzer(args):
    import sqlite3, time
    from walkman_dj import schema, intelligence, spotify_meta
    schema.ensure_schema(DB)
    _FEAT = [c for c, _ in schema.NEW_COLUMNS]
    genre_only = getattr(args, "genre_only", False)

    def analyze_one(fp, path):
        con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
        tr = con.execute("SELECT artist,title,duration,bpm,key,camelot,beat_engine FROM recordings WHERE fp=?", (fp,)).fetchone()
        con.close()
        # fast path: MIR already migrated (or --genre-only) -> Essentia only, skip ~25s SOTA beatgrid
        if (tr and tr["bpm"] is not None and tr["beat_engine"]) or genre_only:
            from walkman_dj import essentia_features
            import datetime
            r = {"status": "ok", "analysis_version": intelligence.ANALYSIS_VERSION,
                 "analyzed_at": datetime.datetime.now().isoformat(timespec="seconds")}
            if tr and tr["camelot"]:
                r["spotify_mode"] = 0 if tr["camelot"].endswith("A") else 1
                r["spotify_key"] = intelligence._PITCH.get(tr["key"], -1)
            r.update(essentia_features.extract(path))
        else:
            r = intelligence.analyze_song(path)
        if r.get("status") != "ok":
            schema.bump_attempt(DB, fp)
            return False
        if tr:
            try:
                r.update(spotify_meta.match(tr["artist"] or "", tr["title"] or "", tr["duration"]))
            except Exception:
                pass
        # genre + valence share one EffNet embedding
        try:
            from walkman_dj import genre_audio, genre_label
            from mutagen import File as _MF
            emb = genre_audio.embedding(path)
            audio_g = genre_audio.predict_from_embedding(emb)
            tags = _MF(path, easy=True) or {}
            r.update(genre_label.label_track(path, tags, audio_g))
            from walkman_dj import mood
            v = mood.valence_from_embedding(emb)
            if v is not None:
                r["valence"] = v
        except Exception:
            pass
        segs = r.pop("segments", [])
        fields = {k: v for k, v in r.items() if k in _FEAT}
        sets = ",".join(f"{k}=?" for k in fields)
        with sqlite3.connect(DB, timeout=60) as c:
            if sets:
                c.execute(f"UPDATE recordings SET {sets} WHERE fp=?", (*fields.values(), fp))
            c.execute("DELETE FROM segments WHERE fingerprint=?", (fp,))
            c.executemany("INSERT INTO segments(fingerprint,idx,label,start,end) VALUES(?,?,?,?,?)",
                          [(fp, s["idx"], s["label"], s["start"], s["end"]) for s in segs])
        schema.dequeue(DB, fp)
        return True

    # optional "i/N" sharding so N workers process disjoint slices with no contention
    shard_i, shard_n = None, None
    if getattr(args, "shard", None):
        import zlib
        shard_i, shard_n = (int(x) for x in args.shard.split("/"))

    def _mine(fp):
        return shard_n is None or zlib.crc32(fp.encode()) % shard_n == shard_i

    while True:
        pend = [(fp, p) for fp, p in schema.pending(DB, 2000)
                if _mine(fp) and sqlite3.connect(DB).execute(
                    "SELECT attempts FROM pending_analysis WHERE fingerprint=?", (fp,)).fetchone()[0] < 3]
        if not pend:
            if args.daemon:
                time.sleep(15); continue
            break
        for i, (fp, path) in enumerate(pend, 1):
            analyze_one(fp, path)
            if i % 10 == 0:
                print(f"  analyzed {i}/{len(pend)}", flush=True)
        if not args.daemon:
            break
    print("analyzer done")


def cmd_spotify_match(args):
    from walkman_dj import spotify_meta
    import json
    a, _, t = args.query.partition(" - ")
    print(json.dumps(spotify_meta.match(a.strip(), t.strip() or a.strip()), indent=1))


def cmd_analyze(args):
    from walkman_dj import intelligence
    import json
    print(json.dumps(intelligence.analyze_song(args.file), indent=1, default=str))


def cmd_migrate_analysis(args):
    from walkman_dj import schema
    schema.ensure_schema(DB)
    ana = args.analysis or os.path.join(os.path.dirname(DB), "walkman_analysis.db")
    print("migrated MIR for", schema.migrate_analysis(DB, ana), "recordings from", os.path.basename(ana))


def cmd_tagfix(args):
    for p in args.files:
        if not os.path.exists(p):
            print(f"[skip] {p}"); continue
        t = crate.normalize_tags(p, write=args.write)
        print(("wrote " if args.write else "would fix "), os.path.basename(p), "->", t["artist"], "-", t["title"])


def build_parser():
    p = argparse.ArgumentParser(prog="crate", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("ingest"); pi.add_argument("files", nargs="*")
    pi.add_argument("--get", help="download this query then ingest")
    pi.add_argument("--format", default="m4a"); pi.set_defaults(func=cmd_ingest)
    pg = sub.add_parser("get"); pg.add_argument("query")
    pg.add_argument("--to", help="copy/transcode the resolved file into this dir")
    pg.add_argument("--format", help="transcode to this ext when copying out"); pg.set_defaults(func=cmd_get)
    pd = sub.add_parser("dedupe"); pd.add_argument("--log", help="write KEEP/TRASH audit log here"); pd.set_defaults(func=cmd_dedupe)
    sub.add_parser("stats").set_defaults(func=cmd_stats)
    sub.add_parser("verify").set_defaults(func=cmd_verify)
    pmig = sub.add_parser("migrate-analysis"); pmig.add_argument("--analysis"); pmig.set_defaults(func=cmd_migrate_analysis)
    pan = sub.add_parser("analyze"); pan.add_argument("file"); pan.set_defaults(func=cmd_analyze)
    psm = sub.add_parser("spotify-match"); psm.add_argument("query"); psm.set_defaults(func=cmd_spotify_match)
    pz = sub.add_parser("analyzer"); pz.add_argument("--once", action="store_true"); pz.add_argument("--daemon", action="store_true"); pz.add_argument("--shard", help="i/N: process only shard i of N (parallel workers)"); pz.add_argument("--genre-only", action="store_true", help="skip SOTA beatgrid; only genre/valence/essentia"); pz.set_defaults(func=cmd_analyzer)
    sub.add_parser("backfill-genre").set_defaults(func=cmd_backfill_genre)
    sub.add_parser("refix-genre").set_defaults(func=cmd_refix_genre)
    sub.add_parser("import-rekordbox").set_defaults(func=cmd_import_rekordbox)
    pem = sub.add_parser("enrich-meta"); pem.add_argument("--limit", type=int, default=100000)
    pem.add_argument("--only-unlabeled", action="store_true"); pem.set_defaults(func=cmd_enrich_meta)
    sub.add_parser("insights").set_defaults(func=cmd_insights)
    pvp = sub.add_parser("vibe-path"); pvp.add_argument("genre")
    pvp.add_argument("--vibe", help="curate toward a set vibe profile (e.g. boiler_room)")
    pvp.add_argument("--min-vibe", type=float, default=1.3, help="std cutoff for vibe outliers (lower=stricter)")
    pvp.add_argument("--min-conf", type=float, default=0.12, help="min genre_confidence for audio-classified tracks")
    pvp.add_argument("--pct", type=float, default=90, help="hard-transition percentile cutoff")
    pvp.add_argument("--rationalize", action="store_true", help="LLM-review the ordered set")
    pvp.set_defaults(func=cmd_vibe_path)
    sub.add_parser("backfill").set_defaults(func=cmd_backfill)
    pt = sub.add_parser("tagfix"); pt.add_argument("files", nargs="+"); pt.add_argument("--write", action="store_true"); pt.set_defaults(func=cmd_tagfix)
    po = sub.add_parser("organize"); po.add_argument("--apply", action="store_true"); po.add_argument("--log"); po.set_defaults(func=cmd_organize)
    pe = sub.add_parser("enrich"); pe.add_argument("--analysis"); pe.set_defaults(func=cmd_enrich)
    px = sub.add_parser("export"); px.add_argument("playlist"); px.add_argument("--to", required=True); px.add_argument("--format", default="m4a"); px.set_defaults(func=cmd_export)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
