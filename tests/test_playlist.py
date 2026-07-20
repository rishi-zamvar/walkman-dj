from walkman_dj.playlist import build_m3u8
def test_m3u8_uses_relative_paths_and_extinf():
    seq=[{"path":"/storage/6439-6561/Music/Singles/A - B.mp3","title":"B","artist":"A","duration":180},
         {"path":"/storage/6439-6561/Music/Die Lit/01 - X.flac","title":"X","artist":"Carti","duration":200}]
    txt=build_m3u8(seq)
    assert txt.startswith("#EXTM3U")
    assert "#EXTINF:180,A - B" in txt
    assert "Singles/A - B.mp3" in txt and "/storage/" not in txt
