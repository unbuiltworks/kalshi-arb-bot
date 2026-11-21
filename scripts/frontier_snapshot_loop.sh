#!/bin/bash
# Blind periodic scoring loop (user/ChatGPT protocol, 2026-08-31):
# run the future-evidence scorer every ~25 min until 21:05 UTC, save each
# result as a timestamped artifact, expose ONLY health facts. Strategy
# numbers (P&L, flatten rates, rankings) are written to files, not stdout —
# nobody peeks until the 21:10 UTC decision read.
set -u
cd "$(dirname "$0")/.."
SNAP_DIR="analysis/gemini/snapshots"
HEALTH_LOG="$SNAP_DIR/health.log"
mkdir -p "$SNAP_DIR"
END_TS=$(date -u -d "2026-08-31 21:05" +%s)
while [ "$(date -u +%s)" -lt "$END_TS" ]; do
  TS=$(date -u +%Y%m%dT%H%M%SZ)
  OUT="$SNAP_DIR/frontier_run_$TS.txt"
  python3 -m src.gemini_maker.rescue_frontier --future > "$OUT" 2>&1
  RC=$?
  [ -f analysis/gemini/rescue_frontier_future.json ] && \
    cp analysis/gemini/rescue_frontier_future.json "$SNAP_DIR/frontier_future_$TS.json"
  LABELS=$(grep -ch '"kind":"settlement"' data/ref_lag/reflag_*.jsonl 2>/dev/null | paste -sd+ | bc)
  NEWEST_TAPE=$(ls -t data/ref_lag/reflag_*.jsonl 2>/dev/null | head -1)
  TAPE_AGE=$(( $(date -u +%s) - $(stat -c %Y "$NEWEST_TAPE" 2>/dev/null || echo 0) ))
  echo "$TS rc=$RC labels=$LABELS tape_age_s=$TAPE_AGE snapshot=$(basename "$OUT")" >> "$HEALTH_LOG"
  sleep 1500
done
echo "$(date -u +%Y%m%dT%H%M%SZ) loop_end" >> "$HEALTH_LOG"
