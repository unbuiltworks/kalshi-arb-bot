#!/usr/bin/env bash
set -Eeuo pipefail

python -u -m src.gemini_q10_v5_2_deadman_sidecar &
WATCHDOG_PID=$!

cleanup() {
  kill "$WATCHDOG_PID" 2>/dev/null || true
  wait "$WATCHDOG_PID" 2>/dev/null || true
}
trap cleanup EXIT

set +e
python -u -m src.gemini_q10_v5_2_runtime
RC=$?
set -e

# If the executor dies unexpectedly, keep the independently armed watchdog alive
# long enough to cross V5.2's stale-heartbeat threshold and attempt bounded recovery.
if [[ "$RC" -ne 0 ]]; then
  sleep 20
fi

cleanup
trap - EXIT
exit "$RC"
