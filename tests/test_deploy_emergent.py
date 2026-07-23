import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "overnight"))
from deploy_emergent import render_set_m3u8

def test_render_uses_device_paths_and_extinf():
    ordered = [{"path": "/storage/6439-6561/Music/A/01 - x.flac", "title": "x", "duration": 200},
               {"path": "/storage/6439-6561/Music/B/02 - y.mp3", "title": "y", "duration": 190}]
    out = render_set_m3u8(ordered, "/storage/6439-6561/Music")
    assert out.startswith("#EXTM3U")
    assert "#EXTINF:200,x" in out
    assert "A/01 - x.flac" in out and "/storage" not in out.split("\n", 2)[2]
