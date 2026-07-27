"""Audio genre classification via Essentia Discogs-EffNet + genre_discogs400.

Hears the actual audio, so it genres remixes/edits/untagged files correctly.
Models load once (module-level cache). predict() never raises -> {} on failure.
Also exposes embedding() so mood heads (valence) can reuse the EffNet embedding.
"""
import json
import os

MODELS = os.path.expanduser("~/Library/Mobile Documents/_internal/models")
_EMB = os.path.join(MODELS, "discogs-effnet-bs64-1.pb")
_GENRE = os.path.join(MODELS, "genre_discogs400-discogs-effnet-1.pb")
_LABELS = os.path.join(MODELS, "genre_discogs400-discogs-effnet-1.json")

_state = {"emb": None, "clf": None, "classes": None, "loaded": False}


def available() -> bool:
    if not (os.path.exists(_EMB) and os.path.exists(_GENRE) and os.path.exists(_LABELS)):
        return False
    try:
        import essentia.standard  # noqa: F401
        return True
    except Exception:
        return False


def _load():
    if _state["loaded"]:
        return _state["emb"] is not None
    _state["loaded"] = True
    try:
        import essentia
        essentia.log.warningActive = False  # silence TF backend noise during backfill
        import essentia.standard as es
        _state["emb"] = es.TensorflowPredictEffnetDiscogs(
            graphFilename=_EMB, output="PartitionedCall:1")
        _state["clf"] = es.TensorflowPredict2D(
            graphFilename=_GENRE, input="serving_default_model_Placeholder",
            output="PartitionedCall:0")
        _state["classes"] = json.load(open(_LABELS))["classes"]
        return True
    except Exception:
        _state["emb"] = None
        return False


def embedding(path: str):
    """EffNet-Discogs embedding (frames x 1280), or None on failure."""
    if not _load():
        return None
    try:
        import essentia.standard as es
        audio = es.MonoLoader(filename=path, sampleRate=16000, resampleQuality=4)()
        return _state["emb"](audio)
    except Exception:
        return None


def predict_from_embedding(emb) -> dict:
    """{'genre','subgenre','genre_confidence'} from a precomputed EffNet embedding."""
    from walkman_dj import genre_map
    if emb is None or not _load():
        return {}
    try:
        import numpy as np
        preds = _state["clf"](emb)          # frames x 400
        avg = np.mean(preds, axis=0)
        idx = int(np.argmax(avg))
        label = _state["classes"][idx]
        genre, subgenre = genre_map.map_discogs(label)
        return {"genre": genre, "subgenre": subgenre,
                "genre_confidence": round(float(avg[idx]), 3)}
    except Exception:
        return {}


def predict(path: str) -> dict:
    """{'genre','subgenre','genre_confidence'} from the Discogs-400 head, or {}."""
    return predict_from_embedding(embedding(path))
