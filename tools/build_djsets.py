"""Build 50-60 song DJ sets from the Music Lake, each sequenced to its set-type
energy arc with harmonic (Camelot) mixing. Reads analysis from the DB, points
.m3u8 at the Lake, writes to ~/Desktop/DJ Sets/."""
import os, re, sys, math, sqlite3
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import rules as rulesmod          # noqa: E402
from walkman_dj.camelot import compatible          # noqa: E402
from walkman_dj.pipeline import fallback_family, pretty  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "walkman_analysis.db")
CFG = os.path.join(ROOT, "config", "genre_rules.toml")
LAKE = "/Users/rishizamvar/Desktop/MusicLake"
OUT = "/Users/rishizamvar/Desktop/DJ Sets"
AUDIO = (".mp3", ".flac", ".m4a", ".wav", ".aac", ".aiff", ".aif", ".ogg")
SET_MIN, SET_TGT, SET_MAX = 45, 55, 62

SETTYPE_LABEL = {"peak": "Peak-Time", "warmup": "Warm-Up",
                 "afterhours": "Afterhours", "sunset": "Sunset"}


def norm(s):
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"\(feat[^)]*\)|\bfeat\b.*|\bft\.?\b.*", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def from_name(name):
    stem = re.sub(r"^\d+\s*[-.]\s*", "", os.path.splitext(name)[0])
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        return a.strip(), t.strip()
    return None, stem.strip()


# ---- energy-arc target curves (0..1 over set positions) ----

def target_curve(n, arc):
    out = []
    for i in range(n):
        x = i / max(1, n - 1)
        if arc == "peak":            # front-load high, sustain, peak finish
            e = 0.82 + 0.18 * x
        elif arc == "warmup":        # patient rising build
            e = 0.25 + 0.65 * x
        elif arc == "afterhours":    # mid -> deep -> comedown
            e = 0.62 - 0.34 * x + 0.06 * math.sin(math.pi * x)
        else:                        # sunset: bell, peak ~68% through
            e = 0.35 + 0.63 * math.exp(-((x - 0.68) / 0.27) ** 2)
        out.append(max(0.0, min(1.0, e)))
    return out


def order_set(tracks, arc):
    """Greedy fill along the energy curve, breaking ties by harmonic + BPM flow."""
    n = len(tracks)
    curve = target_curve(n, arc)
    pool = list(tracks)
    seq = []
    prev = None
    for i in range(n):
        t_e = curve[i]

        def score(tr):
            efit = 1.0 - abs((tr["energy"] or 0.5) - t_e)      # arc fidelity (dominant)
            if prev is None:
                return 0.7 * efit + 0.3
            key = 1.0 if (prev.get("camelot") and tr.get("camelot")
                          and tr["camelot"] in compatible(prev["camelot"])) else 0.25
            bpm = max(0.0, 1.0 - abs((prev.get("bpm") or 0) - (tr.get("bpm") or 0)) / 10.0)
            return 0.62 * efit + 0.24 * key + 0.14 * bpm
        nxt = max(pool, key=score)
        pool.remove(nxt)
        seq.append(nxt)
        prev = nxt
    return seq


def settype_for(chunk_median_energy, family):
    e = chunk_median_energy
    chill = family in ("global_downtempo", "alt_rnb", "toronto", "lyrical_soul",
                       "afrobeats", "organic_house", "nu_disco")
    if e >= 0.60:
        return "peak"
    if e >= 0.53:
        return "sunset"
    if e >= 0.46:
        return "warmup" if not chill else "sunset"
    return "afterhours"


def rel(p):
    return p[len(LAKE) + 1:] if p.startswith(LAKE) else p


def write_m3u8(name, seq):
    os.makedirs(OUT, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in " -&#'." else "_" for c in name).strip()
    lines = ["#EXTM3U", f"#PLAYLIST:{name}"]
    for t in seq:
        dur = int(t.get("dur") or 0)
        lines.append(f"#EXTINF:{dur},{t.get('artist') or ''} - {t.get('title') or ''}".rstrip(" -"))
        lines.append(t["path"])            # absolute Lake path (rekordbox/Serato friendly)
    with open(os.path.join(OUT, safe + ".m3u8"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return safe


def main():
    rules = rulesmod.load_rules(CFG)
    # analysis lookup: normalized artist|title -> features
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    key2f = {}
    for r in c.execute("SELECT * FROM tracks JOIN features USING(fingerprint) WHERE status='ok'"):
        d = dict(r)
        if d.get("title"):
            key2f.setdefault(f"{norm(d['artist'])}|{norm(d['title'])}", d)
    c.close()

    # gather Lake songs with analysis
    songs = []
    miss = 0
    for dp, _, fs in os.walk(LAKE):
        if os.path.basename(dp).startswith("_"):
            continue
        for f in fs:
            if not f.lower().endswith(AUDIO):
                continue
            fl = f.lower()
            if any(x in fl for x in ("sound effect", "bleep", "blooper", "freesound",
                                     "sfx")) or re.match(r"audio\d{6,}", fl):
                continue                       # skip SFX / junk folded in during cleanup
            p = os.path.join(dp, f)
            try:
                import mutagen
                m = mutagen.File(p, easy=True)
                a = (m.get("artist") or [None])[0] if m else None
                t = (m.get("title") or [None])[0] if m else None
            except Exception:
                a = t = None
            if not a or not t:
                fa, ft = from_name(f)
                a = a or fa; t = t or ft
            feat = key2f.get(f"{norm(a)}|{norm(t)}")
            if not feat or not feat.get("energy"):
                miss += 1
                continue
            fam = rulesmod.classify_track(rules, folder=os.path.basename(dp),
                                          filename=f, artist=a, features={})
            fam = fam[0] if fam else fallback_family(feat)
            songs.append({"path": p, "artist": a, "title": t, "energy": feat["energy"],
                          "bpm": feat.get("bpm"), "camelot": feat.get("camelot"),
                          "dur": feat.get("duration"), "family": fam})
    print(f"lake songs with analysis: {len(songs)}  (no-analysis skipped: {miss})", flush=True)

    # group by family, chunk into sets by energy, assign type, sequence
    byfam = defaultdict(list)
    for s in songs:
        byfam[s["family"]].append(s)

    manifest = []
    for fam, group in sorted(byfam.items(), key=lambda kv: -len(kv[1])):
        if len(group) < SET_MIN:
            # too small for its own set -> hold for crossover pool
            manifest.append(("_small", fam, group))
            continue
        group.sort(key=lambda s: s["energy"] or 0)      # energy-sorted -> coherent chunks
        nsets = max(1, round(len(group) / SET_TGT))
        size = math.ceil(len(group) / nsets)
        chunks = [group[i:i + size] for i in range(0, len(group), size)]
        for idx, ch in enumerate(chunks, 1):
            med = sorted(s["energy"] or 0 for s in ch)[len(ch) // 2]
            st = settype_for(med, fam)
            vol = f" Vol.{idx}" if len(chunks) > 1 else ""
            name = f"{pretty(fam)} — {SETTYPE_LABEL[st]}{vol}"
            seq = order_set(ch, st)
            saved = write_m3u8(name, seq)
            manifest.append((st, name, seq))
            print(f"  [{st:10}] {name}  ({len(seq)})", flush=True)

    # crossover journey sets from leftovers + curated blends
    small = [g for tag, fam, g in manifest if tag == "_small"]
    manifest = [m for m in manifest if m[0] != "_small"]
    pool = [s for g in small for s in g]
    if len(pool) >= SET_MIN:
        pool.sort(key=lambda s: s["energy"] or 0)
        size = math.ceil(len(pool) / max(1, round(len(pool) / SET_TGT)))
        for i in range(0, len(pool), size):
            ch = pool[i:i + size]
            if len(ch) < SET_MIN:
                if manifest:  # fold remainder into previous
                    break
            med = sorted(s["energy"] or 0 for s in ch)[len(ch) // 2]
            st = settype_for(med, "mix")
            name = f"Crossover Journey — {SETTYPE_LABEL[st]}"
            k = 2
            while os.path.exists(os.path.join(OUT, name + ".m3u8")):
                name = f"Crossover Journey — {SETTYPE_LABEL[st]} Vol.{k}"; k += 1
            write_m3u8(name, order_set(ch, st))
            manifest.append((st, name, ch))
            print(f"  [{st:10}] {name}  ({len(ch)})", flush=True)

    print(f"\nDONE: {len(manifest)} DJ sets -> {OUT}", flush=True)
    from collections import Counter
    print("by type:", dict(Counter(m[0] for m in manifest)))


if __name__ == "__main__":
    main()
