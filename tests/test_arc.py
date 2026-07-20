from walkman_dj.sequence import assign_arc
def test_high_energy_playlist_is_hype():
    assert assign_arc([0.85,0.9,0.88,0.8]) == "hype"
def test_low_energy_playlist_is_soul():
    assert assign_arc([0.2,0.3,0.25,0.35]) == "soul"
def test_mid_is_journey():
    assert assign_arc([0.5,0.55,0.45,0.6]) == "journey"
