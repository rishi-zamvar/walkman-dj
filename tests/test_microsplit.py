from walkman_dj.microsplit import split_family
def test_rage_vs_melodic_by_energy():
    tracks=[{"fingerprint":"a","energy":0.92,"is_vocal":0,"bpm":150},
            {"fingerprint":"b","energy":0.35,"is_vocal":1,"bpm":140}]
    out=split_family("opium_rage", tracks)
    assert out["a"].endswith("rage") and out["b"].endswith("melodic")
def test_non_splittable_passthrough():
    tracks=[{"fingerprint":"c","energy":0.5,"is_vocal":0,"bpm":126}]
    out=split_family("tech_house", tracks)
    assert out["c"]=="tech_house"
