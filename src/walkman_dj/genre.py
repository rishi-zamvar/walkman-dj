"""Per-track coarse-genre resolution, shared by the online enricher and the set
builder. The whole point: genre is decided PER TRACK (a Drake house edit is house,
not rap), primarily from external sources, never propagated from the artist."""

# family -> coarse genre bucket (sets never mix two coarse buckets)
FAM_COARSE = {
    "tech_house": "HOUSE", "organic_house": "HOUSE", "nu_disco": "HOUSE",
    "electro_classics": "HOUSE", "club_edits": "HOUSE",
    "ukg_bass": "BASS", "jersey_juke": "BASS",
    "baile_edits": "BAILE",
    "global_downtempo": "DOWNTEMPO",
    "afrobeats": "AFRO",
    "atlanta_trap": "RAP", "opium_rage": "RAP", "melodic_trap": "RAP",
    "travis_rage": "RAP", "toronto": "RAP", "lyrical_soul": "RAP", "rap": "RAP",
    "alt_rnb": "R&B", "latin": "LATIN", "pop": "POP", "rock": "ROCK",
}

# display names for the new coarse families the builder can emit
COARSE_DJ_NAME = {
    "rap": "Rap", "latin": "Latin", "pop": "Pop", "rock": "Rock",
}


def coarse_of(genre):
    """Map a messy genre STRING (iTunes/ID3/MusicBrainz/CrateDig) to a coarse bucket."""
    if not genre:
        return None
    g = genre.lower()
    if "baile" in g:
        return "BAILE"
    if any(k in g for k in ("footwork", "juke", "jersey")):
        return "BASS"
    if any(k in g for k in ("garage", "uk funky", "bassline", "2-step", "2 step", "dubstep",
                            "drum and bass", "drum & bass", "dnb", "d&b", "breakbeat",
                            "jungle", "grime")):
        return "BASS"
    if any(k in g for k in ("reggaeton", "dembow", "latin", "tropical", "bachata", "salsa",
                            "bollywood", "bhangra", "desi", "punjabi", "cumbia")):
        return "LATIN"
    if any(k in g for k in ("amapiano", "afrobeat", "afro beat", "afro-beat", "afropop",
                            "afro pop", "afro house", "afroswing", "afro swing")):
        return "AFRO"
    if any(k in g for k in ("r&b", "r & b", "rnb", "soul", "neo-soul", "neo soul")):
        return "R&B"
    if any(k in g for k in ("house", "disco", "techno", "trance", "edm", "electronica",
                            "dance", "indie dance", "big beat", "nu-disco", "nu disco",
                            "electro")):
        return "HOUSE"
    if any(k in g for k in ("hip-hop", "hip hop", "rap", "trap", "drill", "cloud rap")):
        return "RAP"
    if any(k in g for k in ("rock", "metal", "punk", "grunge", "emo")):
        return "ROCK"
    if any(k in g for k in ("jazz", "lo-fi", "lofi", "ambient", "downtempo", "chillout",
                            "chill", "trip hop", "trip-hop", "reggae", "dancehall", "funk")):
        return "DOWNTEMPO"
    if "pop" in g:
        return "POP"
    if "electronic" in g:
        return "HOUSE"
    return None


_EDIT_KW = ("edit", "remix", "bootleg", "flip", "refix", "rework", " vip", "mashup",
            "blend", "(mix", "funk brasil")


def is_edit(title):
    t = (title or "").lower()
    return any(k in t for k in _EDIT_KW)


def coarse_to_family(coarse, bpm):
    """Coarse bucket + tempo -> a specific family (house is sub-split by vibe/tempo)."""
    b = bpm or 0
    simple = {"BAILE": "baile_edits", "RAP": "rap", "R&B": "alt_rnb", "LATIN": "latin",
              "AFRO": "afrobeats", "DOWNTEMPO": "global_downtempo", "POP": "pop",
              "ROCK": "rock"}
    if coarse in simple:
        return simple[coarse]
    if coarse == "BASS":
        return "jersey_juke" if b >= 148 else "ukg_bass"
    if coarse == "HOUSE":
        if b and b < 116:
            return "nu_disco"          # groovier / disco vibe
        if b and b < 124:
            return "organic_house"     # deep / melodic vibe
        return "tech_house"            # driving / peak vibe
    return None


def resolve_family(kw_family, coarse_signals, bpm, title):
    """Decide a track's family PER TRACK. `coarse_signals` is an ordered list of
    coarse strings from most to least trusted (external API, ID3 tag, CrateDig).
    The keyword family is kept only when it AGREES with the resolved coarse bucket,
    so a rap-artist house track (folder says 'toronto') still lands in house."""
    if kw_family == "baile_edits":
        return "baile_edits"                       # producer-labeled baile is authoritative
    base = next((c for c in coarse_signals if c), None)
    # DJ-edit structural override: a fast 4/4 edit reading rap/pop/none is a club edit
    if is_edit(title) and base in (None, "RAP", "POP") and bpm and 120 <= bpm <= 136:
        base = "HOUSE"
    if not base and kw_family:
        base = FAM_COARSE.get(kw_family)
    if not base:                                   # conservative last resort by tempo
        base = "HOUSE" if bpm and 118 <= bpm <= 135 else "DOWNTEMPO"
    if kw_family and FAM_COARSE.get(kw_family) == base:
        return kw_family                           # keep the specific sub-genre label
    return coarse_to_family(base, bpm) or kw_family or "global_downtempo"


import re as _re

# Tier-0 canonicalization: (regex, canonical). Order matters; first match wins.
CANON = [
    (r"dnb|d ?& ?b|drum.*bass", "drum and bass"),
    (r"funk carioca|brazilian funk|baile ?funk|\bbaile\b", "baile funk"),
    (r"uk ?garage|\bukg\b|2 ?-?step", "uk garage"),
    (r"hip ?-?hop|hiphop|\brap\b", "hip-hop"),
    (r"r ?& ?b|\brnb\b", "rnb"),
    (r"jersey club", "jersey club"),
    (r"amapiano", "amapiano"),
    (r"dembow", "dembow"),
    (r"tech ?house", "tech house"),
    (r"deep ?house", "deep house"),
    (r"afro ?house", "afro house"),
]
_STOP = {"the", "a", "an", "and", "music", "genre"}


def canon(s: str) -> str:
    """Normalize a messy genre string to a canonical form (lowercase, alias-mapped)."""
    if not s:
        return ""
    g = _re.sub(r"[^a-z0-9& /-]+", " ", s.lower()).strip()
    for pat, rep in CANON:
        if _re.search(pat, g):
            return rep
    return " ".join(w for w in _re.split(r"[^a-z0-9]+", g) if w and w not in _STOP)


def _tokens(s: str) -> set:
    return {w for w in _re.split(r"[^a-z0-9]+", canon(s)) if w and w not in _STOP}


def jaccard(g1: str, g2: str) -> float:
    """Token-set Jaccard on canonicalized genre strings."""
    a, b = _tokens(g1), _tokens(g2)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
