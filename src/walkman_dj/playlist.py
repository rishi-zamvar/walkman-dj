"""Generate .m3u8 with paths relative to the Music root (NW-A300 convention)."""
from walkman_dj.device import MUSIC_ROOT


def _rel(p):
    return p[len(MUSIC_ROOT) + 1:] if p.startswith(MUSIC_ROOT) else p


def build_m3u8(seq) -> str:
    lines = ["#EXTM3U"]
    for t in seq:
        dur = int(t.get("duration") or 0)
        label = f"{t.get('artist') or ''} - {t.get('title') or ''}".strip(" -")
        lines.append(f"#EXTINF:{dur},{label}")
        lines.append(_rel(t["path"]))
    return "\n".join(lines) + "\n"


def write_playlist(seq, out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(build_m3u8(seq))
