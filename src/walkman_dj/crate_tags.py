"""Pure tag-string cleaning. No I/O — trivially unit-testable."""
import re

_JUNK = re.compile(
    r"\s*[\(\[]\s*(?:youtube|0fficial\s*mp3|official\s*(?:audio|video|mp3)|"
    r"www\.[\w.]+|slider\.kz|digitaldripped[\w.]*|flac(?:\s*\d+\s*bit)?|web|deluxe\s*edition|"
    r"free\s*dl|\d{3,4}\s*kbps|hq|lyrics?)\s*[\)\]]", re.I)


def clean_title(t: str) -> str:
    if not t:
        return t
    prev = None
    while prev != t:
        prev = t
        t = _JUNK.sub("", t)
    return re.sub(r"\s{2,}", " ", t).strip(" -_")


def split_stem(stem: str):
    """('Artist','Title') from 'NN - Artist - Title' / 'Artist - Title'; (None,title) otherwise."""
    s = re.sub(r"^\s*\d{1,3}\s*[-\.\s]+", "", stem).strip()
    parts = [p.strip() for p in s.split(" - ")]
    if len(parts) >= 2:
        return parts[0], " - ".join(parts[1:])
    return None, s
