#!/usr/bin/env bash
set -euo pipefail

OUTPUT_DIR="${OUTPUT_DIR:-/opt/arb/l5-full-history-output}"
CONTAINER="${CONTAINER:-arb-l5-full-history-backtest-run}"
OUT_JSON="$OUTPUT_DIR/l5_full_history_backtest.json"
OUT_LOG="$OUTPUT_DIR/l5_full_history_summary.log"

if docker ps --filter "name=^/${CONTAINER}$" --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "STATUS=RUNNING container=$CONTAINER"
  echo "--- recent log ---"
  docker logs --tail 30 "$CONTAINER" 2>&1 || true
  exit 0
fi

if [ -s "$OUT_JSON" ]; then
  echo "STATUS=COMPLETE"
  echo "detail_json=$OUT_JSON"
  echo "summary_log=$OUT_LOG"
  echo "--- compact summary ---"
  tail -n 5 "$OUT_LOG" 2>/dev/null || true
  exit 0
fi

if docker ps -a --filter "name=^/${CONTAINER}$" --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "STATUS=FAILED_OR_EXITED container=$CONTAINER"
  docker inspect -f 'exit_code={{.State.ExitCode}} finished_at={{.State.FinishedAt}}' "$CONTAINER" || true
  echo "--- logs ---"
  docker logs --tail 100 "$CONTAINER" 2>&1 || true
  exit 1
fi

echo "STATUS=NOT_STARTED"
exit 2
