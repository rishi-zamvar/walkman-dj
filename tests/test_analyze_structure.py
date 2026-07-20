import numpy as np
from walkman_dj.analyze import structure_of
def test_structure_returns_segments_and_mixpoints():
    sr=22050
    quiet=0.02*np.random.randn(sr*4); loud=0.5*np.random.randn(sr*8)
    y=np.concatenate([quiet,loud,quiet]).astype("float32")
    r=structure_of(y,sr)
    assert r["mix_in"]>=0 and r["mix_out"]<=len(y)/sr
    assert r["mix_out"]>r["mix_in"]
    assert isinstance(r["segments"],list) and len(r["segments"])>=1
    assert set(r["segments"][0]) >= {"idx","label","start","end"}
