"""Glue: join tracks(metadata)+features(audio) -> per-track fine genre family via
config/genre_rules.toml (same source rebuild.py uses) -> family.assign_and_persist.
Fine families (tech_house, ukg_bass, baile_edits, rap...) are the ordering scope,
so sets are tightly genre-specific; coarse buckets are the never-mix guardrail."""
import os
from walkman_dj import genre, family, rules as rulesmod

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "archive_walkman_analysis.db")
DEFAULT_RULES = os.path.join(ROOT, "config", "genre_rules.toml")


def family_for(rules, folder, filename, artist, bpm):
    """Fine genre family for a track from its folder/filename/artist metadata.
    Returns (family, confidence). Falls back to tempo-based coarse when no rule hits."""
    labels = rulesmod.classify_track(rules, folder=folder, filename=filename,
                                     artist=artist, features={})
    if labels:
        return labels[0], 0.8
    fam = genre.resolve_family(None, [None], bpm, filename)   # tempo/last-resort bucket
    return fam, 0.3


def build_track_records(rows, rules):
    """rows: dicts with fingerprint/folder/filename/artist + bpm/energy/bass_weight/brightness."""
    recs = []
    for r in rows:
        fam, conf = family_for(rules, r.get("folder") or "", r.get("filename") or "",
                               r.get("artist") or "", r.get("bpm"))
        recs.append({
            "fingerprint": r["fingerprint"],
            "label_family": fam,
            "canon_genre": genre.canon(fam),
            "label_conf": conf,
            "bpm": r.get("bpm"), "energy": r.get("energy"),
            "bass_weight": r.get("bass_weight"), "brightness": r.get("brightness"),
        })
    return recs


def run(db_path=DB, rules_path=DEFAULT_RULES, reclassify=False):
    """Join tracks+features (status='ok'), classify each, persist frozen."""
    import sqlite3
    rules = rulesmod.load_rules(rules_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT f.fingerprint AS fingerprint, t.folder AS folder, t.filename AS filename, "
        "t.artist AS artist, f.bpm AS bpm, f.energy AS energy, f.bass_weight AS bass_weight, "
        "f.brightness AS brightness FROM features f JOIN tracks t USING(fingerprint) "
        "WHERE f.status='ok'")]
    con.close()
    recs = build_track_records(rows, rules)
    n = family.assign_and_persist(db_path, recs, reclassify=reclassify)
    print(f"assigned {n} tracks ({'reclassify' if reclassify else 'frozen'})")
    return n


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--rules", default=DEFAULT_RULES)
    ap.add_argument("--reclassify", action="store_true")
    a = ap.parse_args()
    run(a.db, a.rules, a.reclassify)
