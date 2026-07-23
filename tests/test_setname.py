from walkman_dj.setname import name_set


def test_name_uses_dominant_family_bpm_energy():
    tracks = [{"family": "baile_edits", "bpm": 131, "energy": 0.7},
              {"family": "baile_edits", "bpm": 133, "energy": 0.72},
              {"family": "ukg_bass", "bpm": 132, "energy": 0.68}]
    nm = name_set(tracks)
    assert "Baile" in nm
    assert "132" in nm or "131" in nm or "133" in nm
