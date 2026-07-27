"""Genre labeling: combine normalized embedded tag + Discogs-400 audio classifier.

Locked policy: on remixes the audio classifier wins (the embedded tag often
carries over from the original). Otherwise a specific tag wins; a generic tag
(Electronic/Dance) is refined by the audio prediction; audio is the last resort.

Distinct from genre.py (Set Sequencer coarse bucketing) — this writes the
per-track genre/subgenre/source/confidence into library.db.
"""
import os
import re

from walkman_dj import genre_map

_REMIX = re.compile(
    r"\b(remix|edit|bootleg|flip|vip|mashup|refix|rework|re-?edit|dub mix)\b|\(.*\bdub\b.*\)",
    re.IGNORECASE)

_REMIX_MIN = 0.40   # audio confidence needed to override a tag on a remix
_AUDIO_MIN = 0.50   # audio confidence needed to commit a genre / beat a generic tag
                    # below this the model is guessing — fall back to the tag, else "Other"


def is_remix(name: str) -> bool:
    return bool(name and _REMIX.search(name))


def _audio(a):
    if not a or not a.get("genre"):
        return None, None, 0.0
    return a["genre"], a.get("subgenre"), float(a.get("genre_confidence") or 0.0)


def decide(tag_genre, audio, remix: bool) -> dict:
    ag, asub, aconf = _audio(audio)
    specific_tag = tag_genre and tag_genre not in genre_map.GENERIC

    if remix and ag and aconf >= _REMIX_MIN:
        return _r(ag, asub, "audio", aconf)
    if specific_tag:
        return _r(tag_genre, ag if ag else tag_genre, "tag", aconf if ag else None)
    if ag and aconf >= _AUDIO_MIN:
        return _r(ag, asub, "audio", aconf)
    if tag_genre:  # generic tag, weak/no audio -> keep the tag
        return _r(tag_genre, tag_genre, "tag", None)
    return _r("Other", None, "none", None)


def _r(genre, subgenre, source, conf):
    return {"genre": genre, "subgenre": subgenre,
            "genre_source": source, "genre_confidence": conf}


def label_track(path: str, tags, audio) -> dict:
    """Glue: normalize the embedded tag, detect remix from title/filename, decide."""
    raw = (tags.get("genre") or [None])[0] if tags else None
    tag_genre = genre_map.normalize(raw)
    title = (tags.get("title") or [None])[0] if tags else None
    remix = is_remix(title or "") or is_remix(os.path.basename(path))
    return decide(tag_genre, audio, remix)
