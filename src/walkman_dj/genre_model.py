"""Genre classifier from metadata — from scratch. Trains on the web-validated genre labels
using the tabular audio features (and the deep ml_features vector when available). Reports
accuracy, macro-F1, the confusion matrix (which genres bleed), and feature importances
(which metadata predicts genre). Run: python3 src/walkman_dj/genre_model.py
"""
import os, sys, json, sqlite3
import numpy as np
DB = os.path.expanduser("~/Documents/Projects/walkman-dj/data/library.db")

TAB = ["bpm", "sub_bass", "low_mid", "air", "perc_frac", "onset_rate", "energy", "valence",
       "acousticness", "speechiness", "instrumentalness", "liveness", "loudness_db",
       "brightness", "bass_weight", "tempo_cv", "essentia_danceability", "deezer_rank",
       "year", "is_vocal", "duration", "key_confidence"]
TRUST = ("sonnet_web", "discogs", "mb", "haiku_handpick", "audio")   # trusted label sources
MIN_PER_GENRE = 70


def _tag_vocab(c, sources, top_k=40):
    """Most common tags per source across the library -> multi-hot column vocab."""
    from collections import Counter
    vocab = []
    for src in sources:
        cnt = Counter(r[0] for r in c.execute(
            "select tag from track_tags where source=?", (src,)))
        vocab += [(src, t) for t, _ in cnt.most_common(top_k)]
    return vocab


def load(use_deep=True, use_tags=True):
    c = sqlite3.connect(DB)
    rows = list(c.execute(
        f"select id,genre,genre_source,{','.join(TAB)} from recordings "
        f"where genre is not null and genre!='' and genre!='Other'"))
    deep = {}
    if use_deep:
        deep = {r[0]: json.loads(r[1]) for r in c.execute("select id,vec from ml_features")}
    deep_keys = sorted(next(iter(deep.values())).keys()) if deep else []

    # cultural-tag multi-hot (Discogs styles/label + Last.fm crowd tags)
    tvocab, tag_by_id = [], {}
    if use_tags and c.execute("select count(*) from sqlite_master where name='track_tags'").fetchone()[0]:
        tvocab = _tag_vocab(c, ("discogs_style", "lastfm", "discogs_label"))
        if tvocab:
            for rid, src, tag, w in c.execute("select id,source,tag,weight from track_tags"):
                tag_by_id.setdefault(rid, {})[(src, tag)] = w or 1.0
    tag_names = [f"{src[:2]}:{t}" for src, t in tvocab]

    X, y, feat_names, ids = [], [], TAB + deep_keys + tag_names, []
    for r in rows:
        rid, genre, src = r[0], r[1], r[2]
        if src not in TRUST:
            continue
        tab = list(r[3:])
        d = deep.get(rid)
        if deep and d is None:
            continue                                   # need the deep vec if we're using it
        tg = tag_by_id.get(rid, {})
        tagvec = [tg.get(key, 0.0) for key in tvocab]
        vec = tab + ([d[k] for k in deep_keys] if deep else []) + tagvec
        X.append(vec); y.append(genre); ids.append(rid)
    X = np.array(X, dtype=float)
    # keep only genres with enough samples
    from collections import Counter
    cnt = Counter(y)
    keep = {g for g, n in cnt.items() if n >= MIN_PER_GENRE}
    mask = np.array([g in keep for g in y])
    X, y = X[mask], [g for g, m in zip(y, mask) if m]
    return X, np.array(y), feat_names, sorted(keep)


def median_impute(X):
    med = np.nanmedian(np.where(np.isfinite(X), X, np.nan), axis=0)
    idx = np.where(~np.isfinite(X))
    X[idx] = np.take(med, idx[1])
    return X


def _model(kind):
    if kind == "lgbm":
        from lightgbm import LGBMClassifier
        return LGBMClassifier(n_estimators=600, learning_rate=0.04, num_leaves=63,
                              subsample=0.8, colsample_bytree=0.7, n_jobs=-1,
                              class_weight="balanced", verbose=-1, random_state=42)
    from sklearn.ensemble import RandomForestClassifier
    return RandomForestClassifier(n_estimators=400, n_jobs=-1,
                                  class_weight="balanced", random_state=42)


def main():
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score, f1_score, confusion_matrix
    X, y, names, genres = load(use_deep=True, use_tags=True)
    X = median_impute(X)
    names = np.array(names)
    is_tab = np.array([n in set(TAB) for n in names])
    is_tag = np.array([":" in n for n in names])
    is_deep = ~is_tab & ~is_tag
    n_tag = int(is_tag.sum())
    print(f"training set: {len(X)} tracks, {len(genres)} genres, {len(names)} features "
          f"({int(is_tab.sum())} tabular + {int(is_deep.sum())} deep + {n_tag} cultural tags)\n")
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, random_state=42, stratify=y)

    groups = [("tabular", is_tab),
              ("+deep", is_tab | is_deep)]
    if n_tag:
        groups.append(("+deep+tags", is_tab | is_deep | is_tag))
    print(f"{'model × features':<26}{'accuracy':>10}{'macro-F1':>10}")
    print("-" * 46)
    best = None
    for kind in ("lgbm",) if "--fast" in sys.argv else ("rf", "lgbm"):
        for feats, mask in groups:
            m = _model(kind)
            m.fit(Xtr[:, mask], ytr)
            p = m.predict(Xte[:, mask])
            acc = accuracy_score(yte, p); f1 = f1_score(yte, p, average="macro")
            print(f"{kind.upper()+' '+feats:<26}{acc:>9.1%}{f1:>10.3f}")
            if best is None or acc > best[0]:
                best = (acc, f1, kind, feats, m, mask, p)
    acc, f1, kind, feats, clf, mask, pred = best
    print(f"\nBEST: {kind.upper()} {feats} — ACCURACY {acc:.1%} | macro-F1 {f1:.3f} "
          f"(random ~{1/len(genres):.1%})\n")
    names = list(names[mask])

    # feature importances (normalized so RF and LGBM print comparably)
    fi = np.array(clf.feature_importances_, dtype=float)
    fi = fi / (fi.sum() or 1)
    imp = sorted(zip(fi, names), reverse=True)[:15]
    print("top features (what predicts genre):")
    for v, n in imp:
        print(f"  {n:22} {v:.3f}  {'█'*int(v*220)}")

    # worst confusions
    labels = sorted(set(y))
    cm = confusion_matrix(yte, pred, labels=labels)
    print("\nbiggest genre confusions (true → predicted):")
    conf = []
    for i, a in enumerate(labels):
        for j, b in enumerate(labels):
            if i != j and cm[i][j] > 0:
                conf.append((cm[i][j], a, b, cm[i].sum()))
    for n, a, b, tot in sorted(conf, reverse=True)[:10]:
        print(f"  {a:16} → {b:16} {n}/{tot} ({100*n//tot}%)")

    # per-genre recall
    print("\nper-genre recall:")
    for i, g in enumerate(labels):
        tot = cm[i].sum()
        print(f"  {g:20} {cm[i][i]}/{tot} ({100*cm[i][i]//max(1,tot)}%)")


if __name__ == "__main__":
    main()
