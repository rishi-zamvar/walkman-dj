"""Build 50-60 song DJ sets from the Music Lake, each sequenced to its set-type
energy arc with harmonic (Camelot) mixing. Reads analysis from the DB, points
.m3u8 at the Lake, writes to ~/Desktop/DJ Sets/."""
import os, re, sys, json, math, sqlite3
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from walkman_dj import rules as rulesmod          # noqa: E402
from walkman_dj.camelot import compatible          # noqa: E402
from walkman_dj.pipeline import fallback_family, pretty  # noqa: E402
from walkman_dj import genre as genremod           # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "walkman_analysis.db")
CFG = os.path.join(ROOT, "config", "genre_rules.toml")
LAKE = "/Users/rishizamvar/Desktop/MusicLake"
OUT = "/Users/rishizamvar/Desktop/DJ Sets"
# CrateDig's own DB: its "Baile" playlist is the curated vibe/energy set (authoritative
# membership for the Baile crate, even where a track isn't strictly baile genre).
CRATEDIG_DB = ("/Users/rishizamvar/Library/Group Containers/"
               "group.com.crateddig.shared/CrateDig.sqlite")
AUDIO = (".mp3", ".flac", ".m4a", ".wav", ".aac", ".aiff", ".aif", ".ogg")
SET_MIN, SET_TGT, SET_MAX = 45, 55, 62

SETTYPE_LABEL = {"peak": "Peak-Time", "warmup": "Warm-Up",
                 "afterhours": "Afterhours", "sunset": "Sunset"}

# clear genre names for DJ sets (not the Walkman's creative playlist names)
DJ_NAME = {
    "tech_house": "Tech House", "organic_house": "Organic House", "nu_disco": "Nu-Disco",
    "ukg_bass": "UK Garage", "baile_edits": "Baile Funk", "jersey_juke": "Jersey Club",
    "electro_classics": "Electro Classics", "global_downtempo": "Downtempo",
    "afrobeats": "Afrobeats", "opium_rage": "Opium Rage", "atlanta_trap": "Atlanta Trap",
    "melodic_trap": "Melodic Trap", "travis_rage": "Travis Scott", "toronto": "Toronto",
    "lyrical_soul": "Lyrical Rap", "alt_rnb": "Alt-R&B", "club_edits": "Club Edits",
    "rap": "Rap", "latin": "Latin", "pop": "Pop", "rock": "Rock",
}


def djname(fam):
    return DJ_NAME.get(fam, fam.replace("_", " ").title())


def settype_by_rank(idx, nchunks, median_energy, family):
    """Spread set-types across a genre's energy-sorted chunks (low->high)."""
    if nchunks == 1:
        return settype_for(median_energy, family)
    frac = (idx - 1) / (nchunks - 1)          # 0 = lowest energy, 1 = highest
    if frac < 0.20:
        return "afterhours"
    if frac < 0.45:
        return "warmup"
    if frac < 0.72:
        return "sunset"
    return "peak"


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


def load_baile_allow():
    """Curated Baile membership from CrateDig's 'Baile' SoundCloud playlist.
    Returns (artist|title keys, distinctive-title set). Vibe-curated, not
    strict-genre — so it's the authority for who belongs in the Baile crate."""
    keys, titles = set(), set()
    if not os.path.exists(CRATEDIG_DB):
        print("  (CrateDig DB not found — baile falls back to keyword rules)", flush=True)
        return keys, titles
    try:
        cc = sqlite3.connect(f"file:{CRATEDIG_DB}?mode=ro", uri=True)
        for a, t in cc.execute(
                "WITH ids AS (SELECT value tid FROM playlist, json_each(trackIDsJSON) "
                "WHERE name='Baile') SELECT t.artist, t.title FROM ids "
                "JOIN track t ON t.id = ids.tid"):
            keys.add(f"{norm(a)}|{norm(t)}")
            nt = norm(t)
            if len(nt.split()) >= 2:                 # distinctive titles only
                titles.add(nt)
        cc.close()
    except Exception as e:                            # noqa: BLE001
        print(f"  (baile allow-set unavailable: {e})", flush=True)
    return keys, titles


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
        elif arc == "wave":          # peaks & valleys: builds & drops, gently rising
            peaks = max(3, round(n / 18))          # ~one peak every 18 tracks
            e = 0.48 + 0.05 * x + 0.17 * math.sin(peaks * 2 * math.pi * x - math.pi / 2)
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
            efit = 1.0 - abs((tr["energy"] or 0.5) - t_e)      # arc fidelity
            if prev is None:
                return 0.6 * efit + 0.4
            key = 1.0 if (prev.get("camelot") and tr.get("camelot")
                          and tr["camelot"] in compatible(prev["camelot"])) else 0.25
            db = abs((prev.get("bpm") or 0) - (tr.get("bpm") or 0))
            bpm = max(0.0, 1.0 - db / 8.0)
            bpm_pen = -0.6 if db > 8 else 0.0                   # hard penalty for big jumps
            return 0.40 * efit + 0.27 * key + 0.33 * bpm + bpm_pen
        nxt = max(pool, key=score)
        pool.remove(nxt)
        seq.append(nxt)
        prev = nxt
    return seq


def order_climb(tracks):
    """Build-up / build-down: TWO joined energy+tempo peaks with valleys between.
    Tempo tracks energy, so the hype moments are the faster/hotter edits; neighbours
    prefer a gentle ~4-5 BPM step (up to ~8 tolerated) so the set climbs and descends
    smoothly instead of parking at one tempo."""
    n = len(tracks)
    pool = list(tracks)
    bmin = min(t["bpm"] for t in tracks)
    bmax = max(t["bpm"] for t in tracks)
    es = [t["energy"] or 0.5 for t in tracks]
    emin, emax = min(es), max(es)

    def gauss(x, mu, s):
        return math.exp(-((x - mu) / s) ** 2)

    seq, prev = [], None
    for i in range(n):
        x = i / max(1, n - 1)
        shape = max(gauss(x, 0.30, 0.15), gauss(x, 0.75, 0.15))   # peaks ~30% & ~75%
        te = emin + (emax - emin) * shape
        tb = bmin + (bmax - bmin) * shape

        def score(t):
            ef = 1.0 - abs((t["energy"] or 0.5) - te)
            bf = 1.0 - min(1.0, abs(t["bpm"] - tb) / (bmax - bmin + 1e-9))
            cont = 0.0
            if prev is not None:
                db = abs(prev["bpm"] - t["bpm"])
                cont = -0.5 if db > 8 else (0.15 if db <= 5 else 0.0)   # ±4-5 ideal
                if (prev.get("camelot") and t.get("camelot")
                        and t["camelot"] in compatible(prev["camelot"])):
                    cont += 0.20
            return 0.45 * ef + 0.40 * bf + cont
        nxt = max(pool, key=score)
        pool.remove(nxt)
        seq.append(nxt)
        prev = nxt
    return seq


def order_bell(tracks):
    """Order so BPM traces a normal-distribution arch — ramps up from the set's slowest
    to its fastest at the MIDDLE, then back down — stepping smoothly, with harmonic
    (Camelot) key matching as the tie-breaker. Sets are already a tight (<=20) band, so
    where BPM is uniform this becomes a pure key-flow ordering."""
    n = len(tracks)
    if n <= 2:
        return sorted(tracks, key=lambda t: (t["bpm"] or 0))
    bmin = min(t["bpm"] or 0 for t in tracks)
    bmax = max(t["bpm"] or 0 for t in tracks)
    rng = (bmax - bmin) or 1
    pool = list(tracks)
    seq, prev = [], None
    for i in range(n):
        x = i / (n - 1)
        target = bmin + rng * math.sin(math.pi * x)        # slow -> fast(mid) -> slow arch

        def score(t):
            bf = 1.0 - abs((t["bpm"] or 0) - target) / rng          # hold the arch
            kf = 0.0
            if prev is not None and prev.get("camelot") and t.get("camelot"):
                kf = 1.0 if t["camelot"] in compatible(prev["camelot"]) else 0.0
            sf = 1.0
            if prev is not None:
                sf = 1.0 - min(1.0, abs((t["bpm"] or 0) - (prev["bpm"] or 0)) / 6.0)
            return 0.55 * bf + 0.30 * kf + 0.15 * sf                # bpm arch > key > step
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

    def clean(x):                          # collapse newlines/tabs so EXTINF stays 1 line
        return " ".join(str(x or "").split())
    for t in seq:
        dur = int(t.get("dur") or 0)
        lines.append(f"#EXTINF:{dur},{clean(t.get('artist'))} - {clean(t.get('title'))}"
                     .rstrip(" -"))
        lines.append(t["path"])            # absolute Lake path (rekordbox/Serato friendly)
    with open(os.path.join(OUT, safe + ".m3u8"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return safe


def load_genre_signals():
    """Per-track coarse-genre lookups: online cache (iTunes/Deezer/MB) + CrateDig's
    own genre field. Both keyed by norm(artist)|norm(title)."""
    online = {}
    cache = os.path.join(ROOT, "data", "genre_online.json")
    if os.path.exists(cache):
        for k, v in json.load(open(cache)).items():
            if v.get("coarse"):
                online[k] = v["coarse"]
    cd = {}
    if os.path.exists(CRATEDIG_DB):
        try:
            cc = sqlite3.connect(f"file:{CRATEDIG_DB}?mode=ro", uri=True)
            for a, t, g in cc.execute("SELECT artist,title,genre FROM track "
                                      "WHERE genre IS NOT NULL AND genre!=''"):
                c = genremod.coarse_of(g)
                if c:
                    cd.setdefault(f"{norm(a)}|{norm(t)}", c)
            cc.close()
        except Exception:
            pass
    return online, cd


def main():
    rules = rulesmod.load_rules(CFG)
    online_coarse, cd_coarse = load_genre_signals()
    print(f"genre signals: online={len(online_coarse)}  cratedig={len(cd_coarse)}",
          flush=True)
    # analysis lookup: normalized artist|title -> features
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    key2f = {}
    path2f = {}
    for r in c.execute("SELECT * FROM tracks JOIN features USING(fingerprint) WHERE status='ok'"):
        d = dict(r)
        if d.get("path"):
            path2f[d["path"]] = d               # newly-analyzed Lake songs match by exact path
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
            id3_genre = None
            try:
                import mutagen
                m = mutagen.File(p, easy=True)
                a = (m.get("artist") or [None])[0] if m else None
                t = (m.get("title") or [None])[0] if m else None
                id3_genre = (m.get("genre") or [None])[0] if m else None
            except Exception:
                a = t = None
            if not a or not t:
                fa, ft = from_name(f)
                a = a or fa; t = t or ft
            feat = path2f.get(p) or key2f.get(f"{norm(a)}|{norm(t)}")
            if not feat or not feat.get("energy"):
                miss += 1
                continue
            # per-track genre: keyword family (structural) + coarse signals, most-trusted
            # first (online API -> ID3 tag -> CrateDig). resolve_family reconciles them.
            kw = rulesmod.classify_track(rules, folder=os.path.basename(dp),
                                         filename=f, artist=a, features={})
            kw = kw[0] if kw else None
            gkey = f"{norm(a)}|{norm(t)}"
            signals = [online_coarse.get(gkey), genremod.coarse_of(id3_genre),
                       cd_coarse.get(gkey)]
            fam = genremod.resolve_family(kw, signals, feat.get("bpm"), t)
            songs.append({"path": p, "artist": a, "title": t, "energy": feat["energy"],
                          "bpm": feat.get("bpm"), "camelot": feat.get("camelot"),
                          "dur": feat.get("duration"), "family": fam})
    print(f"lake songs with analysis: {len(songs)}  (no-analysis skipped: {miss})", flush=True)

    # CrateDig's 'Baile' SC playlist is a loose *candidate pool* (vibe-curated, not a
    # genre truth — it has Jersey Club etc. mixed in). Within it: route Jersey-titled
    # tracks to Jersey Club, keep true baile (baile keyword OR baile tempo) as baile.
    b_keys, b_titles = load_baile_allow()
    nb = nj = 0
    for s in songs:
        nt = norm(s["title"])
        if not (f"{norm(s['artist'])}|{nt}" in b_keys
                or (len(nt.split()) >= 2 and nt in b_titles)):
            continue
        t = (s["title"] or "").lower()
        b = s["bpm"] or 0
        baile_kw = any(k in t for k in ("baile", "brazil", "favela"))
        if "jersey" in t and not baile_kw:
            s["family"] = "jersey_juke"; nj += 1          # loose tag -> route out
        elif b >= 118 and (b <= 142 or baile_kw):         # mixable baile tempo (drops
            s["family"] = "baile_edits"; nb += 1          # half-tempo <118 misreads)
        # else: not baile-like (off-tempo, no baile word) -> leave to its own genre
    # tempo floor for ALL baile (incl. keyword-classified): drop <118 half-tempo misreads
    nd = 0
    for s in songs:
        if s["family"] == "baile_edits" and (s["bpm"] or 0) < 118:
            s["family"] = fallback_family(s); nd += 1
    print(f"baile crate: {nb} SC-added  |  {nj} jersey routed out  |  {nd} slow dropped",
          flush=True)

    # group by family, chunk into sets by energy, assign type, sequence
    byfam = defaultdict(list)
    for s in songs:
        byfam[s["family"]].append(s)

    # consolidate tiny families into the LARGEST family of the same coarse bucket, so
    # sets stay single-genre without leaving 3-track fragments. Never cross buckets
    # (a small house sub-genre folds into house, never into rap).
    TINY = 16
    for fam in sorted(byfam, key=lambda f: len(byfam[f])):
        if fam == "baile_edits" or not byfam[fam] or len(byfam[fam]) >= TINY:
            continue
        coarse = genremod.FAM_COARSE.get(fam)
        sibs = [f for f in byfam if f != fam and byfam[f]
                and genremod.FAM_COARSE.get(f) == coarse]
        if sibs:
            tgt = max(sibs, key=lambda f: len(byfam[f]))
            byfam[tgt].extend(byfam[fam]); byfam[fam] = []
    byfam = {f: g for f, g in byfam.items() if g}

    MAX_SPREAD = 20                 # hard cap: min..max BPM within a set never exceeds 20
    used_names = {}
    manifest = []
    unmapped = [0]                  # songs deliberately left out (didn't fit a tight set)

    def uniq(name):
        if name not in used_names:
            used_names[name] = 1
            return name
        used_names[name] += 1
        return f"{name} Vol.{used_names[name]}"

    def emit(st, name, seq):
        name = uniq(name)
        write_m3u8(name, seq)
        manifest.append((st, name, seq))
        print(f"  [{st:10}] {name}  ({len(seq)})", flush=True)

    for fam, group in sorted(byfam.items(), key=lambda kv: -len(kv[1])):
        # BPM-band: sort by tempo, cut STRICTLY on tempo spread or full size
        group.sort(key=lambda s: (s["bpm"] or 0))
        chunks, cur = [], []
        for s in group:
            b = s["bpm"] or 0
            if cur and (len(cur) >= SET_TGT or (b - (cur[0]["bpm"] or 0) > MAX_SPREAD)):
                chunks.append(cur); cur = []
            cur.append(s)
        if cur:
            chunks.append(cur)

        # consolidate: no set smaller than MIN_KEEP — merge each into its nearest
        # tempo neighbour (small genres end up fewer/wider; dense genres stay tight)
        MIN_KEEP = 28

        def mid(ch):
            bs = [s["bpm"] for s in ch if s["bpm"]]
            return sum(bs) / len(bs) if bs else 0

        def spread(*chs):
            bs = [s["bpm"] for c in chs for s in c if s["bpm"]]
            return (max(bs) - min(bs)) if bs else 0
        changed = True
        while changed and len(chunks) > 1:
            changed = False
            for i, ch in enumerate(chunks):
                if len(ch) >= MIN_KEEP:
                    continue
                # merge only if it stays under BOTH the size cap AND the 20-BPM spread cap
                cand = [j for j in (i - 1, i + 1)
                        if 0 <= j < len(chunks)
                        and len(chunks[j]) + len(ch) <= SET_MAX
                        and spread(chunks[j], ch) <= MAX_SPREAD]
                if cand:
                    j = min(cand, key=lambda x: abs(mid(chunks[x]) - mid(ch)))
                    chunks[j].extend(ch); chunks.pop(i); changed = True; break
        # a band too small to be a real set is dropped — those songs stay UNMAPPED
        # rather than padding or loosening a playlist
        MIN_EMIT = 15
        kept = [c for c in chunks if len(c) >= MIN_EMIT]
        unmapped[0] += sum(len(c) for c in chunks if len(c) < MIN_EMIT)
        n = len(kept)
        for idx, ch in enumerate(kept, 1):
            bpms = [s["bpm"] for s in ch if s["bpm"]]
            lo, hi = (int(min(bpms)), int(max(bpms))) if bpms else (0, 0)
            med = sorted(s["energy"] or 0 for s in ch)[len(ch) // 2]
            st = settype_by_rank(idx, n, med, fam)         # tempo-rank -> set type (variety)
            band = f" {lo}-{hi}" if bpms else ""
            # BPM normal-distribution arch + key matching (<=20 BPM spread per set)
            emit(st, f"{djname(fam)}{band} — {SETTYPE_LABEL[st]}", order_bell(ch))

    print(f"\nDONE: {len(manifest)} DJ sets -> {OUT}  (every set single-genre, <=20 BPM "
          f"spread; {unmapped[0]} songs left unmapped)", flush=True)
    from collections import Counter
    print("by type:", dict(Counter(m[0] for m in manifest)))


if __name__ == "__main__":
    main()
