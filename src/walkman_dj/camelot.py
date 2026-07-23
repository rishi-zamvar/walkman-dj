"""Camelot wheel mapping and harmonic-compatibility rules."""
# Camelot: number 1-12 + letter (A=minor, B=major). Standard wheel.
_MAJOR = {"B": 1, "F#": 2, "Gb": 2, "Db": 3, "C#": 3, "Ab": 4, "G#": 4, "Eb": 5,
          "D#": 5, "Bb": 6, "A#": 6, "F": 7, "C": 8, "G": 9, "D": 10, "A": 11, "E": 12}
_MINOR = {"Ab": 1, "G#": 1, "Eb": 2, "D#": 2, "Bb": 3, "A#": 3, "F": 4, "C": 5,
          "G": 6, "D": 7, "A": 8, "E": 9, "B": 10, "F#": 11, "Gb": 11, "Db": 12, "C#": 12}


def to_camelot(pitch: str, mode: str) -> str:
    is_minor = mode.lower().startswith("min")
    table = _MINOR if is_minor else _MAJOR
    letter = "A" if is_minor else "B"
    return f"{table[pitch]}{letter}"


def compatible(code: str) -> list[str]:
    num = int(code[:-1])
    letter = code[-1]
    up = num % 12 + 1
    down = (num - 2) % 12 + 1
    relative = f"{num}{'B' if letter == 'A' else 'A'}"
    return [f"{num}{letter}", f"{down}{letter}", f"{up}{letter}", relative]


def camelot_edge_ok(a: str, b: str, allow_boost: bool = False) -> bool:
    """Harmonic mix gate: same code, ±1 same letter (wrap 12->1), or relative
    major/minor (same number, letter swap). allow_boost also permits +/-2 same letter."""
    if not a or not b:
        return False
    na, la = int(a[:-1]), a[-1]
    nb, lb = int(b[:-1]), b[-1]
    if la == lb:
        step = min((na - nb) % 12, (nb - na) % 12)
        return step <= (2 if allow_boost else 1)
    return na == nb
