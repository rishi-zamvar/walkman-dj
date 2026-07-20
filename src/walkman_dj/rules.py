"""Load genre rules and classify a track into one or more families."""
import tomllib


def load_rules(path):
    with open(path, "rb") as f:
        return tomllib.load(f)["families"]


def bpm_prior(rules, family):
    return tuple(rules[family].get("bpm_prior", [70, 180]))


def classify_track(rules, *, folder, filename, artist, features) -> list[str]:
    hay = f"{artist or ''} {filename}".lower()
    fams = []
    for fam, spec in rules.items():
        if folder in spec.get("folders", []):
            fams.append(fam)
            continue
        if any(k.lower() in hay for k in spec.get("artist_keywords", [])):
            fams.append(fam)
            continue
        if any(k.lower() in filename.lower() for k in spec.get("filename_keywords", [])):
            fams.append(fam)
    return fams
