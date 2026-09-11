#!/usr/bin/env bash
set -euo pipefail

# Venue-read-only bounded wait for the unresolved Gemini BTC5m inventory campaign,
# followed by the DISARMED overnight-supervisor readiness gate.
#
# This script CANNOT place/cancel/sell an order. The only local mutation it permits
# is authoritative ledger reconciliation + reservation release after exact Gemini
# client IDs and settled-position economics agree.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-btc5m-inventory-settle-overnight-gate}"
BRANCH="feature/gemini-short-crypto-live-q1"
PINNED_COMMIT="692c2deb6a5681e43e15c6c24b91f0484a460ac5"
IMAGE="arb-gemini-btc5m-inventory-overnight:gate-692c2de"
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
echo "SETTLEMENT_OVERNIGHT_GATE_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null
echo "SETTLEMENT_OVERNIGHT_GATE_IMAGE_READY image=$IMAGE"

docker run --rm --network none "$IMAGE" python -m pytest -q \
  tests/test_gemini_short_crypto_inventory_overnight_supervisor.py \
  tests/test_gemini_short_crypto_inventory_settlement_reconcile.py \
  tests/test_gemini_short_crypto_inventory_rest_v3.py \
  tests/test_gemini_short_crypto_inventory_maker_q1.py \
  tests/test_gemini_short_crypto_btc5m_canary_v3.py

echo "SETTLEMENT_OVERNIGHT_GATE_SAFETY_TESTS_OK"

echo "=== WAITING FOR AUTHORITATIVE GEMINI SETTLEMENT ==="
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
    deadline = time.monotonic() + WAIT
    attempt = 0
    while True:
        attempt += 1
        try:
            result = await settle.reconcile()
            print("SETTLEMENT_RECONCILED " + json.dumps(result, sort_keys=True), flush=True)
            if not result.get("local_reservation_cleared"):
                raise RuntimeError("authoritative reconciliation did not clear local reservation")
            if local.local_open_state() is not None:
                raise RuntimeError("local reservation file still present after reconciliation")
            return
        except RuntimeError as exc:
            msg = str(exc)
            if "settlement not posted yet" not in msg.lower():
                raise
            remaining = deadline - time.monotonic()
            state = local.local_open_state()
            print(
                "SETTLEMENT_PENDING " + json.dumps({
                    "attempt": attempt,
                    "seconds_remaining": max(0, int(remaining)),
                    "local_open_reservation": state is not None,
                    "trade_id": None if state is None else state.get("trade_id"),
                    "venue_mutation": False,
                }, sort_keys=True),
                flush=True,
            )
            if remaining <= 0:
                raise RuntimeError("bounded settlement wait expired with reservation still open")
            await asyncio.sleep(min(POLL, max(0.1, remaining)))

asyncio.run(main())
PY

# Prove release before printing success.
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u - <<'PY'
from src import gemini_short_crypto_local_budget as b
state = b.local_open_state()
assert state is None, f"reservation still present: {state}"
print("SETTLEMENT_LOCAL_RELEASE_PROVEN local_open_reservation=false")
PY

echo "SETTLEMENT_RECONCILED_AND_RESERVATION_RELEASED"

echo "=== POST-SETTLEMENT CLEAN AUDIT ==="
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_local_budget_audit

docker run --rm --network none \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_LIVE_Q1=YES \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ID=old \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ARM=old \
  "$IMAGE" python - <<'PY'
from src import gemini_short_crypto_inventory_overnight_supervisor as s
assert s.live_armed() is False
print("OVERNIGHT_Q1_ARM_NAMESPACE_OK overnight_armed=false")
PY

echo "=== OVERNIGHT DISARMED MARKET PREFLIGHT ==="
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_LIVE_Q1=NO \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ID= \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ARM= \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_btc5m_inventory_maker_canary_q1_v3

echo "OVERNIGHT_Q1_DISARMED_MARKET_PREFLIGHT_OK order_entry=false"

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
        "type":"OVERNIGHT_Q1_CLEAN_START",
        "clean":True,
        **snap,
        "order_entry":False,
        "mutation":False,
    }, sort_keys=True), flush=True)
asyncio.run(main())
PY

echo "OVERNIGHT_Q1_READY_FOR_FRESH_APPROVAL order_entry=false mutation=false commit=$PINNED_COMMIT"
