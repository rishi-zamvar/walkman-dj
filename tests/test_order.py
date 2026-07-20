from walkman_dj.sequence import order_playlist
def test_hype_starts_high_and_ends_on_peak():
    tracks=[{"fingerprint":str(i),"camelot":"8A","bpm":124+i%3,"is_vocal":0,
             "energy":e,"key_confidence":0.9} for i,e in
            enumerate([0.6,0.95,0.7,0.99,0.65,0.8])]
    seq=order_playlist(tracks,"hype")
    assert seq[0]["energy"]>=0.8
    assert seq[-1]["energy"]==max(t["energy"] for t in tracks)
def test_soul_ends_soft():
    tracks=[{"fingerprint":str(i),"camelot":"8A","bpm":90,"is_vocal":1,
             "energy":e,"key_confidence":0.9} for i,e in
            enumerate([0.2,0.5,0.35,0.7,0.25])]
    seq=order_playlist(tracks,"soul")
    assert seq[-1]["energy"]==min(t["energy"] for t in tracks)
