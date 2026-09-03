#!/usr/bin/env python3
"""Build harmonically-ordered playlists from library.db and push them to the Walkman.

Groups tracks by genre x vibe (energy/valence), runs each group through the Set
Sequencer's order_family() (penalty-completed TSP-path + energy template), and writes
a device-native .m3u8 (paths = each track's REAL on-device location, relative to Music/).
Songs are only REORDERED, never deleted. Existing device playlists are left alone.

Usage:
  python3 order_walkman_playlists.py --min 25 [--dry] [--only "Peak-Time Trap"]
"""
import os, sys, re, sqlite3, subprocess, argparse, unicodedata, tomllib, statistics as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from walkman_dj.sequence import order_family

DB = os.path.join(ROOT, "data", "library.db")
CFG = os.path.join(ROOT, "config", "sequencer.toml")
MUSIC_ROOT = "/storage/6439-6561/Music"
OUT_DIR = os.path.expanduser("~/Desktop/DJ Sets")
AUDIO = (".flac", ".mp3", ".m4a", ".wav", ".aiff", ".aif", ".ogg", ".opus")

# vibe-tuned energy templates (light per-set tuning): hyped sets ride a wave, chill sets a single arc
VIBE_TEMPLATE = {"Euphoric": "wave", "Driving": "wave", "Feel-good": "single_peak", "Moody": "single_peak"}


def norm(name):
    """Song-identity key: the file's basename (KEEP the track-number prefix — the same
    file has an identical basename in MusicLake and on the device, so keeping it avoids
    'Forever' vs '01 - Forever' collisions), minus extension, ._ prefix and exFAT-illegal
    chars (< > : \" | ? * -> _)."""
    n = unicodedata.normalize("NFC", os.path.basename(name)).lower().strip().lstrip("._")
    n = re.sub(r"\.[a-z0-9]{2,4}$", "", n)
    n = re.sub(r'[<>:"|?*\\]', "_", n)
    return n.strip()


def device_index():
    """{norm_basename: [Music-relative paths]} for every audio file on the device
    (a list, so genuine duplicate basenames get disambiguated at resolve time)."""
    r = subprocess.run(["adb", "shell", f"find {MUSIC_ROOT!r} -type f"],
                       capture_output=True, text=True)
    idx = {}
    for p in r.stdout.splitlines():
        p = p.strip()
        if p.lower().endswith(AUDIO) and p.startswith(MUSIC_ROOT + "/"):
            idx.setdefault(norm(p), []).append(p[len(MUSIC_ROOT) + 1:])
    return idx


def _toks(s):
    return [w for w in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(w) >= 3]


def resolve(t, devidx):
    """Map a library track to its real device path. On a basename collision, disambiguate
    by artist-in-path, then album-in-path; only fall back to first (ambiguous) as a last resort."""
    cands = devidx.get(norm(t["canonical_path"]))
    if not cands:
        return None, "miss"
    if len(cands) == 1:
        return cands[0], "ok"
    for keytoks in (_toks(t.get("artist")),
                    _toks(os.path.basename(os.path.dirname(t["canonical_path"])))):  # artist, then album
        for c in cands:
            cl = c.lower()
            if keytoks and any(w in cl for w in keytoks):
                return c, "ok"
    return cands[0], "ambiguous"


def vibe(e, v, e_med, v_med):
    if v is None: return None
    if e is None: e = e_med
    if e >= e_med and v >= v_med: return "Euphoric"
    if e >= e_med and v < v_med:  return "Driving"
    if e < e_med and v >= v_med:  return "Feel-good"
    return "Moody"

# descriptor shown after the genre: "Reggaeton - Dark / Driving", "Drum & Bass - Sunset"
DESC = {"Euphoric": "Peak-Time", "Driving": "Dark / Driving", "Feel-good": "Sunset", "Moody": "Late-Night"}


def order_clean(tracks, cfg, template, max_iter=8):
    """Order for ZERO hard jumps: iteratively park tracks that can't bridge (the isolated
    ones + the incoming side of each residual hard cut) and re-order the rest until the
    sequence is fully smooth. Parked tracks stay on the device — just not in this set."""
    cur = list(tracks); parked = []
    for _ in range(max_iter):
        ordered, hard, isolated = order_family(cur, cfg, template=template)
        remove = set(isolated) | {h["to"] for h in hard}
        if not remove:
            return ordered, parked
        parked += [t for t in cur if t["fingerprint"] in remove]
        cur = [t for t in cur if t["fingerprint"] not in remove]
        if len(cur) <= 1:
            return cur, parked
    ordered, _, _ = order_family(cur, cfg, template=template)
    return ordered, parked


def render_m3u8(ordered, devidx, misses, ambig):
    lines = ["#EXTM3U"]
    for t in ordered:
        rel, how = resolve(t, devidx)
        if how == "miss":
            misses.append(t.get("title") or "?"); continue
        if how == "ambiguous":
            ambig.append(t.get("title") or "?")
        dur = int(t.get("duration") or 0)
        lines.append(f"#EXTINF:{dur},{(t.get('artist') or '')} - {(t.get('title') or '')}".rstrip(" -"))
        lines.append(rel)
    return "\n".join(lines) + "\n"


def render_md(name, ordered, template, hard):
    hardset = {(h["from"], h["to"]) for h in hard}
    out = [f"# {name}", f"_{len(ordered)} tracks · template: {template}_", ""]
    for i, t in enumerate(ordered):
        out.append(f"{i+1:>2}. **{t.get('title') or '?'}** — {t.get('artist') or ''}  "
                   f"· {round(t['bpm']) if t.get('bpm') else '?'} BPM · {t.get('camelot') or '?'} "
                   f"· E={float(t.get('energy') or 0):.2f}")
        if i + 1 < len(ordered) and (t["id"], ordered[i+1]["id"]) in hardset:
            out.append("     ↳ hard cut — mix manually")
    return "\n".join(out) + "\n"


def safe_name(s):
    return re.sub(r'[/\\:*?"<>|]', "-", s).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=25, help="min tracks for a playlist")
    ap.add_argument("--dry", action="store_true", help="build locally, don't push")
    ap.add_argument("--only", help="only build the set with this exact name")
    a = ap.parse_args()

    with open(CFG, "rb") as f:
        cfg = tomllib.load(f)
    os.makedirs(OUT_DIR, exist_ok=True)

    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT id,canonical_path,artist,title,duration,bpm,camelot,energy,valence,genre "
        "FROM recordings WHERE TRIM(COALESCE(genre,''))<>'' AND bpm IS NOT NULL "
        "AND camelot IS NOT NULL AND canonical_path IS NOT NULL")]
    e_med = st.median([r["energy"] for r in rows if r["energy"] is not None])
    v_med = st.median([r["valence"] for r in rows if r["valence"] is not None])

    # group by genre x vibe
    groups = {}
    for r in rows:
        r["fingerprint"] = str(r["id"])          # order_family keys on 'fingerprint'
        vb = vibe(r["energy"], r["valence"], e_med, v_med)
        if not vb or r["genre"] in ("Other", "Alternative"):
            continue
        groups.setdefault((r["genre"], vb), []).append(r)

    print(f"loaded {len(rows)} orderable tracks · e_med={e_med:.2f} v_med={v_med:.2f}")
    print("indexing device…")
    devidx = device_index()
    print(f"device has {len(devidx)} audio files\n")

    sets = sorted(((f"{g} - {DESC[vb]}", g, vb, ts) for (g, vb), ts in groups.items()
                   if len(ts) >= a.min), key=lambda x: -len(x[3]))
    if a.only:
        sets = [s for s in sets if s[0] == a.only]

    made = []
    for name, genre, vb, tracks in sets:
        template = VIBE_TEMPLATE.get(vb, cfg["default_template"])
        ordered, parked = order_clean(tracks, cfg, template=template)   # 0 hard jumps
        misses, ambig = [], []
        m3u8 = render_m3u8(ordered, devidx, misses, ambig)
        base = os.path.join(OUT_DIR, safe_name(name))
        open(base + ".m3u8", "w").write(m3u8)
        open(base + ".md", "w").write(render_md(name, ordered, template, []))
        pushed = "(dry)"
        if not a.dry:
            pr = subprocess.run(["adb", "push", base + ".m3u8",
                                 f"{MUSIC_ROOT}/{safe_name(name)}.m3u8"], capture_output=True, text=True)
            pushed = "pushed" if pr.returncode == 0 else f"PUSH FAIL: {pr.stderr.strip()[:60]}"
        n_lines = m3u8.count("#EXTINF")
        made.append((name, n_lines, len(parked), len(ambig), len(misses)))
        print(f"  {name:26} {n_lines:>3} in set · {len(parked):>2} parked (unmixable) · "
              f"{len(ambig)} ambig · {len(misses)} unmatched · {pushed}")

    print(f"\n{len(made)} playlists · 0 hard jumps · sheets in {OUT_DIR}")


if __name__ == "__main__":
    main()
