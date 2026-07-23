from walkman_dj.seqcli import render_m3u8, render_md


def _ordered():
    return [
        {"fingerprint": "a", "path": "/m/A.flac", "title": "A", "bpm": 128,
         "camelot": "8A", "energy": 0.4, "duration": 200},
        {"fingerprint": "b", "path": "/m/B.flac", "title": "B", "bpm": 129,
         "camelot": "8A", "energy": 0.6, "duration": 210},
    ]


def test_render_m3u8_has_extinf_and_relative_paths():
    out = render_m3u8(_ordered(), music_root="/m")
    assert out.startswith("#EXTM3U")
    assert "#EXTINF:200,A" in out
    assert "A.flac" in out and "/m/A.flac" not in out   # relativized


def test_render_md_lists_transitions_and_sections():
    out = render_md(_ordered(), hard=[], isolated=[], template="single_peak")
    assert "single_peak" in out
    assert "8A" in out
    assert "## No clean transition" in out
