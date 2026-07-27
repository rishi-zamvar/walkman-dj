"""Trained mood heads on the Discogs-EffNet embedding.

valence_from_embedding(emb) -> 0..1 from the mood_happy model (real trained
classifier), replacing the crude spectral valence proxy. Reuses the embedding
already computed for genre, so it's nearly free. Returns None if unavailable.
"""
import os

MODELS = os.path.expanduser("~/Library/Mobile Documents/_internal/models")
_HAPPY = os.path.join(MODELS, "mood_happy-discogs-effnet-1.pb")

_state = {"happy": None, "loaded": False}


def _load():
    if _state["loaded"]:
        return _state["happy"] is not None
    _state["loaded"] = True
    if not os.path.exists(_HAPPY):
        return False
    try:
        import essentia
        essentia.log.warningActive = False
        import essentia.standard as es
        _state["happy"] = es.TensorflowPredict2D(
            graphFilename=_HAPPY, input="serving_default_model_Placeholder",
            output="PartitionedCall:0")
        return True
    except Exception:
        _state["happy"] = None
        return False


def valence_from_embedding(emb):
    """mood_happy P(happy), averaged over frames -> valence in [0,1], or None."""
    if emb is None or not _load():
        return None
    try:
        import numpy as np
        preds = _state["happy"](emb)     # frames x 2, col 0 = happy
        return round(float(np.mean(preds, axis=0)[0]), 3)
    except Exception:
        return None
