#!/usr/bin/env bash
set -euo pipefail

# Read-only venue gate for the one-hour Gemini BTC5m inventory-maker session.
# It first reconciles any existing valid inventory reservation from authoritative
# Gemini settlement, proves the local reservation is actually gone, then runs a
# fully DISARMED one-hour readiness gate. It cannot place/cancel/sell an order.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-btc5m-inventory-hour-gate}"
BRANCH="feature/gemini-short-crypto-live-q1"
PINNED_COMMIT="7faabda1e9cedf00e3e93b1238ee5ed0cc5ee99a"
IMAGE="arb-gemini-btc5m-inventory-hour:gate-7faabda"
ENV_FILE="${ENV_FILE:-/opt/arb/secrets/gemini-short-crypto.env}"
STATE_DIR="${STATE_DIR:-/opt/arb/gemini-short-crypto-state}"
SETTLEMENT_WAIT_SECONDS="${SETTLEMENT_WAIT_SECONDS:-900}"
SETTLEMENT_POLL_SECONDS="${SETTLEMENT_POLL_SECONDS:-5}"

for cmd in git docker; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd is required" >&2; exit 1; }
done
[ -d "$SOURCE_REPO/.git" ] || { echo "source repo missing: $SOURCE_REPO" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "secure env missing: $ENV_FILE" >&2; exit 1; }
for key in GEMINI_API_KEY GEMINI_API_SECRET; do
  grep -q "^${key}=." "$ENV_FILE" || { echo "required env missing or empty: $key" >&2; exit 1; }
done
mkdir -p "$STATE_DIR"
chmod 0750 "$STATE_DIR" || true

git -C "$SOURCE_REPO" config --global --add safe.directory "$SOURCE_REPO" || true
git -C "$SOURCE_REPO" fetch origin "$BRANCH"
git -C "$SOURCE_REPO" cat-file -e "${PINNED_COMMIT}^{commit}"
if [ -e "$WORKTREE/.git" ] || [ -f "$WORKTREE/.git" ]; then
  git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE" || true
fi
rm -rf "$WORKTREE"
git -C "$SOURCE_REPO" worktree prune
git -C "$SOURCE_REPO" worktree add --detach "$WORKTREE" "$PINNED_COMMIT"
actual="$(git -C "$WORKTREE" rev-parse HEAD)"
test "$actual" = "$PINNED_COMMIT"
echo "HOUR_Q1_GATE_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null
echo "HOUR_Q1_GATE_IMAGE_READY image=$IMAGE"

docker run --rm --network none "$IMAGE" python -m pytest -q \
  tests/test_gemini_short_crypto_inventory_hour_supervisor.py \
  tests/test_gemini_short_crypto_inventory_settlement_reconcile.py \
  tests/test_gemini_short_crypto_inventory_rest_v3.py \
  tests/test_gemini_short_crypto_inventory_maker_q1.py \
  tests/test_gemini_short_crypto_btc5m_canary_v3.py

echo "HOUR_Q1_GATE_SAFETY_TESTS_OK"

# Prove previous one-shot and overnight namespaces cannot arm the hour supervisor.
docker run -i --rm --network none \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_LIVE_Q1=YES \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ID=old \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ARM=old \
  -e GEMINI_SHORT_CRYPTO_OVERNIGHT_LIVE_Q1=YES \
  -e GEMINI_SHORT_CRYPTO_OVERNIGHT_SESSION_ID=old-night \
  -e GEMINI_SHORT_CRYPTO_OVERNIGHT_SESSION_ARM=old-night \
  "$IMAGE" python - <<'PY'
from src import gemini_short_crypto_inventory_hour_supervisor as s
assert s.live_armed() is False
print("HOUR_Q1_ARM_NAMESPACE_OK hour_armed=false")
PY

# If an inventory reservation exists, wait for authoritative settlement and reconcile.
echo "=== HOUR GATE SETTLEMENT / LOCAL STATE ==="
docker run -i --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -e SETTLEMENT_WAIT_SECONDS="$SETTLEMENT_WAIT_SECONDS" \
  -e SETTLEMENT_POLL_SECONDS="$SETTLEMENT_POLL_SECONDS" \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u - <<'PY'
import asyncio
import json
import os
import time
from src import gemini_short_crypto_inventory_settlement_reconcile as settle
from src import gemini_short_crypto_local_budget as local

WAIT = max(30.0, min(1800.0, float(os.getenv("SETTLEMENT_WAIT_SECONDS", "900"))))
POLL = max(2.0, min(30.0, float(os.getenv("SETTLEMENT_POLL_SECONDS", "5"))))

async def main():
    state = local.local_open_state()
    if state is None:
        print("HOUR_GATE_NO_EXISTING_RESERVATION local_open_reservation=false", flush=True)
        return
    deadline = time.monotonic() + WAIT
    attempt = 0
    while True:
        attempt += 1
        try:
            result = await settle.reconcile()
            print("HOUR_GATE_SETTLEMENT_RECONCILED " + json.dumps(result, sort_keys=True), flush=True)
            if not result.get("local_reservation_cleared"):
                raise RuntimeError("authoritative reconciliation did not clear local reservation")
            if local.local_open_state() is not None:
                raise RuntimeError("local reservation file still present after reconciliation")
            return
        except RuntimeError as exc:
            if "settlement not posted yet" not in str(exc).lower():
                raise
            remaining = deadline - time.monotonic()
            current = local.local_open_state()
            print("HOUR_GATE_SETTLEMENT_PENDING " + json.dumps({
                "attempt": attempt,
                "seconds_remaining": max(0, int(remaining)),
                "local_open_reservation": current is not None,
                "trade_id": None if current is None else current.get("trade_id"),
                "venue_mutation": False,
            }, sort_keys=True), flush=True)
            if remaining <= 0:
                raise RuntimeError("bounded settlement wait expired with reservation still open")
            await asyncio.sleep(min(POLL, max(0.1, remaining)))

asyncio.run(main())
PY

# Hard proof: do not continue merely because the previous process exited zero.
docker run -i --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u - <<'PY'
from src import gemini_short_crypto_local_budget as b
state = b.local_open_state()
assert state is None, f"reservation still present: {state}"
print("HOUR_Q1_LOCAL_RELEASE_PROVEN local_open_reservation=false", flush=True)
PY

# Exact-image authenticated DISARMED market preflight.
echo "=== HOUR DISARMED MARKET PREFLIGHT ==="
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_LIVE_Q1=NO \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ID= \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ARM= \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_btc5m_inventory_maker_canary_q1_v3

echo "HOUR_Q1_DISARMED_MARKET_PREFLIGHT_OK order_entry=false"

# Final account and local-state gate.
docker run -i --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u - <<'PY'
import asyncio, json
from src import gemini_short_crypto_inventory_local_budget as b
async def main():
    snap = await b.assert_clean_account()
    print(json.dumps({
        "type":"HOUR_Q1_CLEAN_START",
        "clean":True,
        **snap,
        "max_runtime_seconds":3600,
        "max_executed_campaigns":4,
        "realized_loss_cap":"-1.00",
        "q_per_outcome":1,
        "order_entry":False,
        "mutation":False,
    }, sort_keys=True), flush=True)
asyncio.run(main())
PY

echo "HOUR_Q1_READY_FOR_FRESH_APPROVAL order_entry=false mutation=false commit=$PINNED_COMMIT"
