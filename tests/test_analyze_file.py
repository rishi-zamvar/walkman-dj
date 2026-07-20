import numpy as np, soundfile as sf, tempfile, os
from walkman_dj.analyze import analyze_file
def test_analyze_file_produces_full_feature_dict():
    with tempfile.TemporaryDirectory() as d:
        sr=22050; y=(0.4*np.sin(np.linspace(0,3000,sr*6))).astype("float32")
        p=os.path.join(d,"x.wav"); sf.write(p,y,sr)
        f=analyze_file(p, prior=(118,132))
        for k in ("bpm","camelot","energy","loudness_curve","brightness",
                  "is_vocal","mix_in","mix_out","duration","segments","status"):
            assert k in f
        assert f["status"]=="ok"
