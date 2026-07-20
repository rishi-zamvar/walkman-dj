"""Build REPORT.md from a stats dict."""


def build_report(stats: dict) -> str:
    lines = ["# Walkman DJ Crate — Run Report", ""]
    lines.append(f"- Tracks analyzed: **{stats.get('analyzed', 0)}**")
    lines.append(f"- Failed: **{stats.get('failed', 0)}**")
    lines.append("")
    lines.append("## Playlists")
    lines.append("")
    lines.append("| Playlist | Arc | Tracks |")
    lines.append("|---|---|---|")
    for p in stats.get("playlists", []):
        lines.append(f"| {p['name']} | {p['arc']} | {p['count']} |")
    lines.append("")
    if stats.get("bpm_hist"):
        lines.append("## BPM distribution")
        lines.append("")
        lines.append("| Band | Count |")
        lines.append("|---|---|")
        for band, n in stats["bpm_hist"].items():
            lines.append(f"| {band} | {n} |")
        lines.append("")
    if stats.get("camelot_hist"):
        lines.append("## Camelot key distribution")
        lines.append("")
        lines.append("| Camelot | Count |")
        lines.append("|---|---|")
        for k, n in stats["camelot_hist"].items():
            lines.append(f"| {k} | {n} |")
        lines.append("")
    return "\n".join(lines) + "\n"
