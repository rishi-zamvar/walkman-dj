from walkman_dj.device import parse_find_output
def test_parse_find_filters_audio_and_splits_folder():
    raw = ("/storage/6439-6561/Music/Singles/A - B.mp3\n"
           "/storage/6439-6561/Music/Die Lit/01 - X.flac\n"
           "/storage/6439-6561/Music/cover.jpg\n")
    files = parse_find_output(raw)
    assert len(files) == 2
    assert files[0].folder == "Singles" and files[0].filename == "A - B.mp3"
    assert files[1].folder == "Die Lit"
