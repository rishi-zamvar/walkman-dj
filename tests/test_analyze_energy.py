import numpy as np
from walkman_dj.analyze import energy_timbre_of
def test_energy_and_curve_shape():
    sr=22050; y=(0.5*np.sin(np.linspace(0,2000,sr*4))).astype("float32")
    r=energy_timbre_of(y,sr)
    assert 0.0<=r["energy"]<=1.0
    assert isinstance(r["loudness_curve"],list) and len(r["loudness_curve"])==16
    assert r["duration"]>3.9
    assert 0.0<=r["brightness"]<=1.0 and r["is_vocal"] in (0,1)
