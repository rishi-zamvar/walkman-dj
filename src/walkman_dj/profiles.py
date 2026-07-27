"""Named DJ-set vibe targets. Curation picks the tracks that fit ONE of these,
then orders them into that set's energy arc. Tune the numbers here; future: expose
as a skill so the vibe is dialable per set.

Each profile:
  bpm      : [lo, hi] hard tempo gate (kills half-time misreads + off-tempo)
  target   : desired value per vibe feature (raw 0..1)
  weights  : how much each feature matters when scoring fit (higher = stricter)
  size     : how many tracks the set should be
  template : energy arc (see templates.py) — single_peak = build to a late peak
"""
PROFILES = {
    # Underground, rolling, instrumental-leaning, groove-driven, builds to a peak.
    # Deliberately excludes pop vocals (low instrumentalness), rap edits (speechiness),
    # acoustic/meme material (acousticness), and anything off the 128-140 grid.
    "boiler_room": {
        "bpm": [128, 140],
        "target": {"danceability": 0.65, "energy": 0.62, "valence": 0.45,
                   "acousticness": 0.20, "instrumentalness": 0.72,
                   "speechiness": 0.10, "liveness": 0.45},
        "weights": {"danceability": 1.5, "energy": 1.1, "valence": 0.7,
                    "acousticness": 1.3, "instrumentalness": 1.4,
                    "speechiness": 1.6, "liveness": 0.4},
        "size": 44,          # vibe prune is the big cut — this defines the set
        "drum_min": 0.92,    # min drum/energy mix confidence — tuned to the smoothness knee
        "template": "single_peak",
    },
}
