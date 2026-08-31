#!/bin/bash
# Blind continuous L-series scorer (audit protocol, night of 2026-08-31):
# run the frozen ladder_frontier --future every ~30 min, sealing each result
# as a timestamped artifact. Stdout/health carries NO strategy numbers —
# the 2026-09-01 13:00 UTC read opens finished artifacts instead of waiting
# on processing. Ends 12:55 UTC.
set -u
cd "$(dirname "$0")/.."
SNAP_DIR="analysis/gemini/snapshots"
HEALTH_LOG="$SNAP_DIR/ladder_health.log"
mkdir -p "$SNAP_DIR"
END_TS=$(date -u -d "2026-09-01 12:55" +%s)
while [ "$(date -u +%s)" -lt "$END_TS" ]; do
  TS=$(date -u +%Y%m%dT%H%M%SZ)
  OUT="$SNAP_DIR/ladder_run_$TS.txt"
  python3 -m src.gemini_maker.ladder_frontier --future > "$OUT" 2>&1
  RC=$?
  [ -f analysis/gemini/ladder_frontier_future.json ] && \
    cp analysis/gemini/ladder_frontier_future.json "$SNAP_DIR/ladder_future_$TS.json"
  LABELS=$(grep -ch '"kind":"settlement"' data/ref_lag/reflag_*.jsonl 2>/dev/null | paste -sd+ | bc)
  NEWEST_TAPE=$(ls -t data/ref_lag/reflag_*.jsonl 2>/dev/null | head -1)
  TAPE_AGE=$(( $(date -u +%s) - $(stat -c %Y "$NEWEST_TAPE" 2>/dev/null || echo 0) ))
  echo "$TS rc=$RC labels=$LABELS tape_age_s=$TAPE_AGE snapshot=$(basename "$OUT")" >> "$HEALTH_LOG"
  sleep 1800
done
echo "$(date -u +%Y%m%dT%H%M%SZ) loop_end" >> "$HEALTH_LOG"
