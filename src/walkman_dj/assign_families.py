"""Glue: features + external genre signals -> track records -> family.assign_and_persist.
Reuses genre.resolve_family (per-track, structural) + genre.canon."""
import os
from walkman_dj import genre, family

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "walkman_analysis.db")


def build_track_records(features, genre_signals):
    """features: list of feature dicts. genre_signals: {fp: {genre, conf, title}}.
    Returns records with label_family (coarse bucket), canon_genre, label_conf + envelope feats."""
    recs = []
    for f in features:
        fp = f["fingerprint"]
        sig = genre_signals.get(fp, {})
        gstr = sig.get("genre")
        coarse = genre.coarse_of(gstr)
        fam = genre.resolve_family(None, [coarse], f.get("bpm"), sig.get("title"))
        bucket = genre.FAM_COARSE.get(fam, coarse or "DOWNTEMPO")
        recs.append({
            "fingerprint": fp,
            "label_family": bucket,
            "canon_genre": genre.canon(gstr) if gstr else "",
            "label_conf": sig.get("conf") or (0.7 if coarse else 0.3),
            "bpm": f.get("bpm"), "energy": f.get("energy"),
            "bass_weight": f.get("bass_weight"), "brightness": f.get("brightness"),
        })
    return recs


def run(db_path=DB, reclassify=False):
    """Load features + any persisted genre signals, assign, persist frozen."""
    import sqlite3
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    features = [dict(r) for r in con.execute("SELECT * FROM features WHERE status='ok'")]
    signals = {}
    try:
        for r in con.execute("SELECT fingerprint, genre, confidence, title FROM track_meta"):
            signals[r["fingerprint"]] = {"genre": r["genre"], "conf": r["confidence"], "title": r["title"]}
    except sqlite3.OperationalError:
        pass
    con.close()
    recs = build_track_records(features, signals)
    n = family.assign_and_persist(db_path, recs, reclassify=reclassify)
    print(f"assigned {n} new tracks ({'reclassify' if reclassify else 'frozen'})")
    return n


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--reclassify", action="store_true")
    a = ap.parse_args()
    run(a.db, a.reclassify)
