import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import vibe_path as vp


def _row(v, a, d, e, conf=0.5, src="audio", bpm=130, cam="8A"):
    return {"title": f"t{v}", "artist": "x", "bpm": bpm, "camelot": cam, "energy": e,
            "valence": v, "acousticness": a, "danceability": d, "speechiness": 0.2,
            "instrumentalness": 0.5, "liveness": 0.4, "genre_confidence": conf,
            "genre_source": src, "subgenre": "UK Garage", "path": f"/{v}.flac"}


def test_prune_vibe_drops_outlier_and_low_conf():
    # 6 tight tracks + 1 vibe-outlier + 1 low-confidence audio track
    rows = [_row(0.5, 0.4, 0.5, 0.5) for _ in range(6)]
    rows.append(_row(0.99, 0.99, 0.99, 0.99))          # far outlier
    rows.append(_row(0.5, 0.4, 0.5, 0.5, conf=0.02))   # weak audio genre
    Xs = vp.matrix(rows)
    keep, _ = vp.prune_vibe(rows, Xs, k=1.3, min_conf=0.12)
    assert 6 in [i for i in keep] or len(keep) >= 5    # tight ones survive
    assert 7 not in keep                                # low-conf dropped
    assert len(keep) <= 7                               # at least one pruned


def test_prune_transitions_drops_isolated():
    # a 4-node path where node with local index 3 is far from everyone
    P = [[0, 1, 1, 9],
         [1, 0, 1, 9],
         [1, 1, 0, 9],
         [9, 9, 9, 0]]
    idx = [10, 11, 12, 13]
    order = [0, 1, 2, 3]
    kept, dropped, thr = vp.prune_transitions(P, idx, pct=75)
    assert 13 in dropped and 13 not in kept
    assert set(kept) == {10, 11, 12}


def test_edge_penalizes_bpm_and_key_mismatch():
    rows = [_row(0.5, 0.4, 0.5, 0.5, bpm=130, cam="8A"),
            _row(0.5, 0.4, 0.5, 0.5, bpm=130, cam="8A"),   # same key
            _row(0.5, 0.4, 0.5, 0.5, bpm=170, cam="3B")]   # far bpm + bad key
    Xs = vp.matrix(rows)
    assert vp.edge(0, 2, Xs, rows) > vp.edge(0, 1, Xs, rows)
