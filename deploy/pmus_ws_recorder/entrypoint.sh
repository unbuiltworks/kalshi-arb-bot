#!/bin/sh
set -eu

# Optional one-shot diagnostic over the already-mounted authenticated tape.
# This entrypoint contains no order path.  It runs before the recorder opens
# its websocket, then execs the normal Railway/Docker command unchanged.
if [ -n "${PMUS_WS_TAPE_READ_SLUG:-}" ]; then
  marker="${PMUS_WS_ROOT:-/state/pmus-ws}/.tape-read-${PMUS_WS_TAPE_READ_SLUG}"
  if [ ! -e "$marker" ]; then
    echo "PMUS_TAPE_READ_START:${PMUS_WS_TAPE_READ_SLUG}"
    if PYTHONPATH=/app python3 -m scripts.pmus_candidate_tape_readonly \
      --slug "$PMUS_WS_TAPE_READ_SLUG" \
      --tick "${PMUS_WS_TAPE_READ_TICK:-0.005}"; then
      : > "$marker"
      echo "PMUS_TAPE_READ_DONE:${PMUS_WS_TAPE_READ_SLUG}"
    else
      rc=$?
      echo "PMUS_TAPE_READ_FAILED:${rc}:${PMUS_WS_TAPE_READ_SLUG}"
    fi
  else
    echo "PMUS_TAPE_READ_ALREADY_DONE:${PMUS_WS_TAPE_READ_SLUG}"
  fi
fi

# Optional one-shot AUTHENTICATED identity/market preflight, read-only.
# Same shape as the tape-read above: it runs before the recorder opens its
# websocket, then execs the normal command unchanged.  A failure is REPORTED
# and never blocks capture -- the preflight gates money, not evidence, and a
# market closed between windows is a normal FAILED result, so it re-runs on
# every boot rather than leaving a marker.  It emits one PMUS_PREFLIGHT line
# in every case, including its own failure, so silence means it did not run.
if [ -n "${PMUS_PREFLIGHT_ON_START:-}" ]; then
  echo "PMUS_PREFLIGHT_START"
  if PYTHONPATH=/app python3 -m scripts.pmus_auth_preflight_readonly; then
    echo "PMUS_PREFLIGHT_PASSED"
  else
    echo "PMUS_PREFLIGHT_FAILED:$?"
  fi
fi

exec "$@"
