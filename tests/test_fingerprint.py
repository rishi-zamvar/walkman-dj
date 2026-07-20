import numpy as np, soundfile as sf, tempfile, os
from walkman_dj.fingerprint import fingerprint_file
def test_same_audio_same_fp_regardless_of_filename():
    with tempfile.TemporaryDirectory() as d:
        y = (0.1*np.sin(np.linspace(0,880,44100))).astype("float32")
        a=os.path.join(d,"a.wav"); b=os.path.join(d,"b_renamed.wav")
        sf.write(a,y,44100); sf.write(b,y,44100)
        assert fingerprint_file(a) == fingerprint_file(b)
