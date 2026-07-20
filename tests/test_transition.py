from walkman_dj.sequence import transition_score
def test_key_compatible_scores_higher():
    a={"camelot":"8A","bpm":124,"is_vocal":0,"energy":0.7,"key_confidence":0.9}
    b_ok={"camelot":"9A","bpm":125,"is_vocal":0,"energy":0.72,"key_confidence":0.9}
    b_bad={"camelot":"2B","bpm":124,"is_vocal":0,"energy":0.72,"key_confidence":0.9}
    w={"arc":0.3,"bpm":0.3,"key":0.4,"vocal":0.2}
    assert transition_score(a,b_ok,w) > transition_score(a,b_bad,w)
def test_vocal_clash_penalized():
    a={"camelot":"8A","bpm":124,"is_vocal":1,"energy":0.7,"key_confidence":0.9}
    b={"camelot":"8A","bpm":124,"is_vocal":1,"energy":0.7,"key_confidence":0.9}
    w={"arc":0.3,"bpm":0.3,"key":0.4,"vocal":0.5}
    assert transition_score(a,b,w) < transition_score(a,{**b,"is_vocal":0},w)
