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
