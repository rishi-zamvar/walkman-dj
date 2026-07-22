from walkman_dj.genre import canon, jaccard


def test_canon_collapses_dnb_aliases():
    assert canon("Drum & Bass") == canon("dnb") == canon("drum n bass") == "drum and bass"


def test_canon_collapses_baile_aliases():
    assert canon("Funk Carioca") == canon("brazilian funk") == "baile funk"


def test_canon_idempotent():
    once = canon("Hip-Hop/Rap")
    assert canon(once) == once


def test_jaccard_partial_overlap():
    assert abs(jaccard("deep house", "tech house") - 1 / 3) < 1e-9


def test_jaccard_disjoint_zero():
    assert jaccard("deep house", "jungle") == 0.0


def test_jaccard_identical_one():
    assert jaccard("uk garage", "ukg") == 1.0   # canon first -> identical token sets
