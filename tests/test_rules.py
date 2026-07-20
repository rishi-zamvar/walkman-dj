from walkman_dj.rules import load_rules, classify_track
def test_album_folder_maps_to_family():
    rules=load_rules("config/genre_rules.toml")
    fams=classify_track(rules, folder="Whole Lotta Red", filename="01 - Rockstar.flac",
                        artist="Playboi Carti", features={"energy":0.9,"bpm":150})
    assert "opium_rage" in fams
def test_singles_artist_keyword_maps():
    rules=load_rules("config/genre_rules.toml")
    fams=classify_track(rules, folder="Singles", filename="FISHER - Stop It.flac",
                        artist="FISHER", features={"energy":0.8,"bpm":126})
    assert "tech_house" in fams
