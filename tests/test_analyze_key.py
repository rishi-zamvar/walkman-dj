import numpy as np
from walkman_dj.analyze import key_of
def test_c_major_triad_detects_tonal():
    sr=22050; t=np.linspace(0,3,sr*3,endpoint=False)
    y=sum(np.sin(2*np.pi*f*t) for f in (261.63,329.63,392.0)).astype("float32")
    r=key_of(y,sr)
    assert isinstance(r["key"], str)
    assert r["camelot"][-1] in ("A","B")
    assert 0.0 <= r["key_confidence"] <= 1.0
    assert r["key_agree"] in (0,1)
