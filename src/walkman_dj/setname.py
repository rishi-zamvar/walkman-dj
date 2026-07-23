"""Factual name for an emergent set: dominant genre + median BPM + energy tier."""
import statistics
from collections import Counter

NICE = {
    "opium_rage": "Opium & Rage", "atlanta_trap": "Atlanta Trap", "travis_rage": "Travis Scott",
    "melodic_trap": "Melodic Trap", "toronto": "Toronto Nights", "lyrical_soul": "Lyrical Hip-Hop",
    "alt_rnb": "Alt R&B", "afrobeats": "Afrobeats", "tech_house": "Tech House",
    "organic_house": "Organic & Afro House", "nu_disco": "Nu-Disco & Funk", "baile_edits": "Baile Funk",
    "ukg_bass": "UK Garage", "jersey_juke": "Jersey Club", "electro_classics": "Electro",
    "global_downtempo": "Downtempo", "rap": "Hip-Hop", "pop": "Pop",
}


def name_set(tracks):
    fams = Counter(t.get("family") for t in tracks if t.get("family"))
    fam = fams.most_common(1)[0][0] if fams else "mixed"
    label = NICE.get(fam, fam.replace("_", " ").title())
    bpms = [t["bpm"] for t in tracks if t.get("bpm")]
    med = int(statistics.median(bpms)) if bpms else 0
    es = [t.get("energy") or 0 for t in tracks]
    me = statistics.median(es) if es else 0
    tier = "Peak" if me >= 0.6 else ("Warm" if me >= 0.45 else "Deep")
    return f"{label} · {med} · {tier}"
