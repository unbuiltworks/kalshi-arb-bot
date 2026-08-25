#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SECONDS_TO_RUN="${AWS_LATENCY_SECONDS:-120}"
INTERVAL_S="${AWS_LATENCY_INTERVAL_S:-1}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="aws-transport-latency-${STAMP}.log"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required" >&2
  exit 2
fi

VENV="${AWS_LATENCY_VENV:-.venv-aws-latency}"
if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r requirements.txt

echo "=== AWS public network-floor benchmark ==="
echo "branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
echo "commit=$(git rev-parse HEAD 2>/dev/null || true)"
echo "seconds=$SECONDS_TO_RUN interval_s=$INTERVAL_S"
echo "log=$LOG"

"$VENV/bin/python" scripts/aws_transport_probe.py \
  --seconds "$SECONDS_TO_RUN" \
  --interval-s "$INTERVAL_S" \
  2>&1 | tee "$LOG"

echo
FINAL_LINE="$(grep 'AWS_TRANSPORT_FINAL ' "$LOG" | tail -n 1 || true)"
if [[ -z "$FINAL_LINE" ]]; then
  echo "ERROR: benchmark completed without AWS_TRANSPORT_FINAL" >&2
  exit 3
fi

echo "=== FINAL PUBLIC SUMMARY ==="
echo "$FINAL_LINE"

# Exact apples-to-apples authenticated Kalshi comparison if credentials were
# provisioned locally on the EC2 instance. We check presence only and never print
# secret values.
if [[ -n "${KALSHI_API_KEY_ID:-}" && -n "${KALSHI_PRIVATE_KEY_PEM:-}" ]]; then
  AUTH_LOG="aws-transport-auth-${STAMP}.log"
  echo
  echo "=== Authenticated Kalshi/Gemini harness ==="
  echo "Kalshi credentials detected locally; secret values will not be printed."
  "$VENV/bin/python" -m src.gemini_kalshi_transport_latency_shadow \
    --seconds "$SECONDS_TO_RUN" \
    --interval-s "$INTERVAL_S" \
    2>&1 | tee "$AUTH_LOG"
  echo
  echo "=== FINAL AUTHENTICATED SAMPLE ==="
  grep 'CRYPTO_TRANSPORT_LATENCY_SAMPLE ' "$AUTH_LOG" | tail -n 1 || true
else
  echo
  echo "Authenticated Kalshi comparison skipped: KALSHI_API_KEY_ID and/or KALSHI_PRIVATE_KEY_PEM not set on this EC2 host."
  echo "Do not paste credentials into chat. Provision them directly on the instance or through AWS Secrets Manager/SSM, then rerun this script."
fi
