#!/usr/bin/env python3
"""MAC-SIDE taste analysis. Reads the raw plays.db pulled from the Walkman + joins library.db
(bpm/camelot/energy/genre/subgenre) to learn preferences, then feeds dj_sets.py:
  - play-weight & completion → boost tracks you finish, demote skip-prone ones
  - skip-at-ms by feature    → which tempos/energies/genres you bail on
  - hour_of_day → energy     → time-aware energy templates (morning chill, night peak)
  - after_transition skips   → REAL-WORLD validation of the harmonic ordering
All heavy lifting is HERE, not on the device. (stub — filled in Phase B / integration.)"""
import sqlite3, os
TASTE = os.path.expanduser("~/Documents/Projects/walkman-dj/data/taste.db")
LIB   = os.path.expanduser("~/Documents/Projects/walkman-dj/data/library.db")
def profile():
    ...  # join taste↔library, compute the taste vectors, return weights for dj_sets
if __name__ == "__main__":
    print("taste_analyze: reads", TASTE, "+", LIB, "-> playlist weights (Mac-side only)")
