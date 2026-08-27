#!/usr/bin/env bash
set -euo pipefail

echo "LOCAL_LIVE_Q1_BLOCKED reason=CLIENT_ORDER_ID_COLLISION_RECONCILIATION_REQUIRED"
echo "Do not rerun live canary until authoritative Gemini settlement is reconciled and a fixed pinned build receives fresh approval."
exit 1
