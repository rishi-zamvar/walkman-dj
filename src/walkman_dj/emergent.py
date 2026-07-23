"""Orchestrate emergent sets: whole-library graph -> communities -> ordered sets."""
from walkman_dj import libgraph, community, setname
from walkman_dj.sequence import order_family

_CLUB_BREAKS = {"baile_edits", "jersey_juke", "ukg_bass"}


def _template_for(tracks, cfg):
    from collections import Counter
    fam = Counter(t.get("family") for t in tracks).most_common(1)
    if fam and fam[0][0] in _CLUB_BREAKS:
        return "wave"
    return cfg.get("default_template", "single_peak")


def build_sets(tracks, cfg):
    """Return list of {name, ordered, hard, isolated, template, n} emergent sets."""
    by_fp = {t["fingerprint"]: t for t in tracks}
    G = libgraph.build_graph(tracks, cfg)
    comms, loose = community.detect(
        G, set_min=cfg["set_min"], set_max=cfg["set_max"],
        resolution=cfg["community_resolution"], seed=cfg["louvain_seed"])
    sets = []
    for c in comms:
        ct = [by_fp[fp] for fp in c if fp in by_fp]
        if len(ct) < 2:
            continue
        tmpl = _template_for(ct, cfg)
        ordered, hard, isolated = order_family(ct, cfg, template=tmpl)
        sets.append({"name": setname.name_set(ct), "ordered": ordered,
                     "hard": hard, "isolated": isolated, "template": tmpl,
                     "n": len(ordered)})
    if loose:
        lt = [by_fp[fp] for fp in loose if fp in by_fp]
        sets.append({"name": "Loose · Unsorted", "ordered": lt, "hard": [],
                     "isolated": list(loose), "template": None, "n": len(lt)})
    sets.sort(key=lambda s: -s["n"])
    return sets
