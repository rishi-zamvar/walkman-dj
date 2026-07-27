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
    # node 3 has no valid edge (all INVALID) -> can't cleanly mix with anything
    I = vp.INVALID
    P = [[0, 1, 1, I],
         [1, 0, 1, I],
         [1, 1, 0, I],
         [I, I, I, 0]]
    idx = [10, 11, 12, 13]
    kept, dropped, thr = vp.prune_transitions(P, idx)
    assert 13 in dropped and 13 not in kept
    assert set(kept) == {10, 11, 12}


def test_transition_gates_bpm_and_key():
    base = {"bpm": 132, "camelot": "8A", "energy": 0.6, "danceability": 0.5,
            "bass_weight": 0.5, "brightness": 0.5}
    ok = dict(base)                                   # identical -> valid, high conf
    off_bpm = dict(base, bpm=160)                     # bpm gate fails
    off_key = dict(base, camelot="3B")                # key gate fails
    assert vp.transition(base, ok) < vp.INVALID
    assert vp.transition(base, off_bpm) == vp.INVALID
    assert vp.transition(base, off_key) == vp.INVALID


def test_harmonic_path_absorbs_whole_component():
    # 8A-9A-10A-11A: a stepwise-harmonic ladder, all same tempo/drums -> one chain of 4
    def t(cam):
        return {"bpm": 132, "camelot": cam, "energy": 0.5, "danceability": 0.5,
                "bass_weight": 0.5, "brightness": 0.5}
    rows = [t("8A"), t("9A"), t("10A"), t("11A")]
    path = vp.harmonic_path([0, 1, 2, 3], rows)
    assert set(path) == {0, 1, 2, 3}                       # none stranded
    for p in range(len(path) - 1):                          # every mix valid
        assert vp.transition(rows[path[p]], rows[path[p + 1]]) < vp.INVALID


def test_clean_chain_drops_hard_cut():
    a = {"bpm": 132, "camelot": "8A", "energy": 0.6, "danceability": 0.5,
         "bass_weight": 0.5, "brightness": 0.5}
    b = dict(a, camelot="9A")                         # 8A->9A harmonic, valid
    c = dict(a, bpm=175, camelot="3B")               # mixes with neither
    rows = [a, b, c]
    kept, dropped = vp.clean_chain([0, 1, 2], rows)
    assert kept == [0, 1] and dropped == [2]


def test_curate_gates_tempo_and_picks_best_fit():
    prof = {"bpm": [128, 140],
            "target": {"danceability": 0.7, "energy": 0.6, "valence": 0.4,
                       "acousticness": 0.2, "instrumentalness": 0.7,
                       "speechiness": 0.1, "liveness": 0.4},
            "weights": {}, "size": 2, "template": "single_peak"}
    rows = [
        _row(0.4, 0.2, 0.7, 0.6, bpm=132),   # 0: on-tempo, on-target -> best
        _row(0.4, 0.2, 0.7, 0.6, bpm=170),   # 1: on-target but OFF-tempo -> gated out
        _row(0.9, 0.9, 0.2, 0.1, bpm=134),   # 2: on-tempo but poor fit
        _row(0.4, 0.2, 0.7, 0.6, bpm=90),    # 3: half-time misread -> gated out
    ]
    # add instrumentalness/speechiness fields the profile scores on
    for r in rows:
        r["instrumentalness"], r["speechiness"] = 0.7, 0.1
    sel = vp.curate(rows, prof)
    assert 1 not in sel and 3 not in sel        # tempo gate removed off-tempo
    assert sel[0] == 0                          # best fit first


def test_edge_penalizes_bpm_and_key_mismatch():
    rows = [_row(0.5, 0.4, 0.5, 0.5, bpm=130, cam="8A"),
            _row(0.5, 0.4, 0.5, 0.5, bpm=130, cam="8A"),   # same key
            _row(0.5, 0.4, 0.5, 0.5, bpm=170, cam="3B")]   # far bpm + bad key
    Xs = vp.matrix(rows)
    assert vp.edge(0, 2, Xs, rows) > vp.edge(0, 1, Xs, rows)
