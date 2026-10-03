#!/bin/sh
# Prospective shadow of the FROZEN rule. Re-runs both arms every 15 minutes as
# new complete BTC15m windows land on the tape, and keeps a timestamped
# snapshot per run. SHADOW ONLY: btc15m_shadow_policy.py has no transport.
# The rule is NOT re-fitted between runs.
cd "$(dirname "$0")/../../.." || exit 1
mkdir -p data/gemini_15m_shadow
while :; do
  S=$(date -u +%Y%m%dT%H%M%SZ)
  PYTHONPATH=. python analysis/gemini/info_edge/btc15m_shadow_policy.py \
    > "data/gemini_15m_shadow/shadow_$S.txt" 2>&1
  cp analysis/gemini/info_edge/btc15m_shadow_result.json \
     "data/gemini_15m_shadow/shadow_$S.json" 2>/dev/null
  echo "$(date -u +%FT%TZ) ran prospective shadow -> shadow_$S"
  sleep 900
done
