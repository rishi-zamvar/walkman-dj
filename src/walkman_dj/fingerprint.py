"""Content fingerprint: SHA1 of the first 30s of decoded mono audio @22050."""
import hashlib
import numpy as np
import librosa


def fingerprint_file(path: str, seconds: float = 30.0) -> str:
    y, _ = librosa.load(path, sr=22050, mono=True, duration=seconds)
    q = np.round(y * 32767).astype(np.int16)  # quantize -> stable-ish across re-encodes
    return hashlib.sha1(q.tobytes()).hexdigest()
