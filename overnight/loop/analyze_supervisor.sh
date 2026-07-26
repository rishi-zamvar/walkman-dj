#!/bin/bash
# Self-healing supervisor for the SOTA analysis: relaunch analyze_sota.py whenever it
# exits (OOM crashes), resuming automatically (it skips already-beatgridded tracks),
# until no unanalyzed tracks remain OR 3 consecutive runs make zero forward progress
# (the remainder is unanalyzable — corrupt/too-short). Single process at all times.
cd /Users/rishizamvar/Documents/Projects/walkman-dj || exit 1
PY=/Users/rishizamvar/.venvs/analysis/bin/python
DB=data/walkman_analysis.db
LOG=overnight/loop/analyze.log
count() { sqlite3 "$DB" "SELECT COUNT(*) FROM features WHERE beat_engine IS NOT NULL;"; }
total() { sqlite3 "$DB" "SELECT COUNT(*) FROM features;"; }

stall=0
while true; do
  done=$(count); tot=$(total)
  remaining=$(( tot - done ))
  echo "[supervisor $(date +%H:%M:%S)] $done/$tot done, $remaining remaining, stall=$stall" >> "$LOG"
  if [ "$remaining" -le 0 ]; then
    echo "[supervisor] COMPLETE — all tracks analyzed" >> "$LOG"; break
  fi
  if [ "$stall" -ge 3 ]; then
    echo "[supervisor] STOP — 3 runs with no progress; $remaining tracks unanalyzable" >> "$LOG"; break
  fi
  before=$done
  # one bounded pass; it processes until it crashes or finishes, then we loop
  FAST_ANALYZE=1 "$PY" -u overnight/analyze_sota.py >> "$LOG" 2>&1
  after=$(count)
  if [ "$after" -le "$before" ]; then stall=$(( stall + 1 )); else stall=0; fi
  sleep 3
done
echo "[supervisor] exited at $(count)/$(total)" >> "$LOG"
