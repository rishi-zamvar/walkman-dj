"""Genre normalization: messy embedded tags + Discogs-400 labels -> one controlled vocabulary.

normalize(raw)  : an embedded ID3/Vorbis genre string -> canonical genre (or None).
map_discogs(lbl): a Discogs-400 "Parent---Style" label -> (canonical, subgenre).

Strategy: take the tagger's PRIMARY genre (first token, splitting multi-genre
strings) then match it against a priority-ordered keyword table, specific->general.
"""
import re

# Canonical vocabulary (DJ-relevant, tuned to this library's actual tags).
VOCAB = [
    "Hip-Hop", "Trap", "R&B", "Soul/Funk", "Pop", "Afrobeats", "Amapiano",
    "Reggaeton", "Moombahton", "Latin", "Dancehall", "Reggae",
    "House", "Tech House", "Deep House", "Bass House", "Afro House",
    "UK Garage", "Grime", "Jersey Club", "Baile Funk", "Drum & Bass", "Jungle",
    "Footwork", "Dubstep", "Breakbeat", "Techno", "Trance", "Disco", "Electro",
    "Downtempo", "Electronic", "Rock", "Indie", "Pop", "Jazz", "Blues", "Country",
]

# Buckets too broad to be DJ-useful -> refine via the audio classifier.
GENERIC = {"Electronic", "Dance", "World", "Other"}

# Priority-ordered (canonical, [substrings]). First canonical with any hit wins.
# Order matters: specific subgenres BEFORE their parents.
_PATTERNS = [
    ("Jersey Club", ["jersey club", "jersey"]),
    ("Baile Funk", ["baile", "favela funk", "funk carioca", "funk melody",
                    "brazilian funk", "funk brasileiro", "favela"]),
    ("Bass House", ["bass house"]),
    ("Deep House", ["deep house"]),
    ("Tech House", ["tech house"]),
    ("Afro House", ["afro house"]),
    ("Disco", ["nu disco", "nu-disco", "disco"]),
    ("House", ["lo fi house", "stutter house", "jazz house", "french house",
               "progressive house", "acid house", "garage house", "ghetto house",
               "jackin", "melodic house", "electro house", "future house",
               "soulful house", "tribal house", "house"]),
    ("UK Garage", ["uk garage", "speed garage", "2 step", "2-step", "breakstep",
                   "uk funky", "ukg", "bassline", "garage"]),
    ("Grime", ["grime"]),
    ("Drum & Bass", ["drum & bass", "drum and bass", "drum n bass", "d&b", "dnb",
                     "liquid funk", "neurofunk", "jungle dnb"]),
    ("Jungle", ["jungle"]),
    ("Footwork", ["footwork", "juke"]),
    ("Dubstep", ["dubstep", "riddim", "brostep"]),
    ("Breakbeat", ["big beat", "breakbeat", "broken beat", "breaks"]),
    ("Techno", ["minimal techno", "dub techno", "melodic techno", "minimal", "techno"]),
    ("Trance", ["psytrance", "trance"]),
    ("Moombahton", ["moombahton"]),
    ("Amapiano", ["amapiano"]),
    ("Afrobeats", ["afrobeats", "afrobeat", "afro beat", "afropop", "afro pop"]),
    ("Reggaeton", ["reggaeton", "reggeton"]),
    ("Dancehall", ["dancehall"]),
    ("Latin", ["bachata", "salsa", "cumbia", "corrido", "latino", "latin",
               "brazilian", "samba", "merengue", "sertanejo", "mpb", "electrocumbia"]),
    ("Reggae", ["roots reggae", "reggae", "ska", "dub"]),
    ("Trap", ["trap", "drill", "phonk", "plugg"]),
    ("Hip-Hop", ["boom bap", "cloud rap", "emo rap", "gangsta rap", "jazz rap",
                 "conscious hip hop", "southern hip hop", "west coast hip hop",
                 "east coast hip hop", "alternative hip hop", "hardcore hip hop",
                 "political hip hop", "pop rap", "hip hop", "hip-hop", "rap"]),
    ("R&B", ["contemporary r&b", "alternative r&b", "r&b", "r & b", "rnb",
             "rhythm and blues"]),
    ("Soul/Funk", ["neo soul", "nu soul", "soul", "funk", "motown", "gospel"]),
    ("Pop", ["synthpop", "electropop", "dance pop", "art pop", "hyperpop",
             "indie pop", "k-pop", "kpop", "pop rock", "pop"]),
    ("Downtempo", ["downtempo", "trip hop", "chillout", "ambient", "lo-fi", "lofi"]),
    ("Electronic", ["electronica", "electronic", "edm", "idm", "future bass",
                    "bass music", "synthwave", "dance", "electronique"]),
    ("Electro", ["electro swing", "electroclash", "electro"]),
    ("Rock", ["heavy metal", "metal", "punk", "grunge", "rock"]),
    ("Indie", ["indie", "alternative"]),
    ("Jazz", ["acid jazz", "nu jazz", "jazz"]),
    ("Blues", ["blues"]),
    ("Country", ["country", "americana", "bluegrass"]),
]

# Discogs-400 parent -> canonical fallback when the style doesn't normalize.
_PARENT = {
    "electronic": "Electronic", "hip hop": "Hip-Hop", "rock": "Rock",
    "pop": "Pop", "funk / soul": "Soul/Funk", "jazz": "Jazz",
    "reggae": "Reggae", "latin": "Latin", "blues": "Blues",
    "folk, world, & country": "Country",
}

_SPLIT = re.compile(r"[;,|/\n]|\s-\s")


def _first_token(raw: str) -> str:
    parts = _SPLIT.split(raw)
    tok = next((p for p in parts if p.strip()), "")
    tok = tok.replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", tok).strip().lower()


def normalize(raw):
    """Messy embedded genre string -> canonical genre, or None if unrecognized."""
    if not raw or not str(raw).strip():
        return None
    tok = _first_token(str(raw))
    if not tok:
        return None
    for canonical, subs in _PATTERNS:
        for s in subs:
            if s in tok:
                return canonical
    return None


def map_discogs(label: str):
    """Discogs-400 'Parent---Style' -> (canonical, subgenre)."""
    parent, _, style = label.partition("---")
    parent, style = parent.strip(), style.strip()
    canonical = normalize(style) or normalize(parent) or _PARENT.get(parent.lower())
    return (canonical or "Other", style or parent)
