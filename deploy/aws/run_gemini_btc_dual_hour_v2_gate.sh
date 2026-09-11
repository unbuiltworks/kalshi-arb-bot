#!/usr/bin/env bash
set -euo pipefail

# DISARMED readiness/reconciliation gate for Gemini BTC5m+BTC15m dual-hour v2.
# No venue order mutation. May append authoritative local reconciliation events and
# release an existing reservation only after exact order history + settled event agree.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-btc-dual-hour-v2-gate}"
BRANCH="feature/gemini-short-crypto-live-q1"
PINNED_COMMIT="052808d87fee1152486dc13d0bee62034e1550dd"
IMAGE="arb-gemini-btc-dual-hour:v2-gate-052808d"
ENV_FILE="${ENV_FILE:-/opt/arb/secrets/gemini-short-crypto.env}"
STATE_DIR="${STATE_DIR:-/opt/arb/gemini-short-crypto-state}"

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
echo "DUAL_HOUR_V2_GATE_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null

docker run --rm --network none "$IMAGE" python -m pytest -q \
  tests/test_gemini_short_crypto_dual_horizon_settlement_reconcile_v2.py \
  tests/test_gemini_short_crypto_inventory_dual_hour_supervisor_v2.py \
  tests/test_gemini_short_crypto_inventory_rest_v3.py \
  tests/test_gemini_short_crypto_inventory_maker_q1.py \
  tests/test_gemini_short_crypto_btc5m_canary_v3.py

echo "DUAL_HOUR_V2_GATE_SAFETY_TESTS_OK"

# Old one-shot/hour/overnight/dual-hour namespaces cannot arm v2.
docker run -i --rm --network none \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_LIVE_Q1=YES \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ID=old \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ARM=old \
  -e GEMINI_SHORT_CRYPTO_HOUR_LIVE_Q1=YES \
  -e GEMINI_SHORT_CRYPTO_HOUR_SESSION_ID=old \
  -e GEMINI_SHORT_CRYPTO_HOUR_SESSION_ARM=old \
  -e GEMINI_SHORT_CRYPTO_OVERNIGHT_LIVE_Q1=YES \
  -e GEMINI_SHORT_CRYPTO_OVERNIGHT_SESSION_ID=old \
  -e GEMINI_SHORT_CRYPTO_OVERNIGHT_SESSION_ARM=old \
  -e GEMINI_SHORT_CRYPTO_DUAL_HOUR_LIVE_Q1=YES \
  -e GEMINI_SHORT_CRYPTO_DUAL_HOUR_SESSION_ID=old \
  -e GEMINI_SHORT_CRYPTO_DUAL_HOUR_SESSION_ARM=old \
  "$IMAGE" python - <<'PY'
from src import gemini_short_crypto_inventory_dual_hour_supervisor_v2 as s
assert s.live_armed() is False
print("DUAL_HOUR_V2_ARM_NAMESPACE_OK v2_armed=false")
PY

# Reconcile the already-executed reservation if present. No venue mutation.
echo "=== DUAL HOUR V2 EXISTING RESERVATION RECONCILIATION ==="
docker run -i --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u - <<'PY'
import asyncio, json
from src import gemini_short_crypto_local_budget as local
from src import gemini_short_crypto_dual_horizon_settlement_reconcile_v2 as settle
async def main():
    state = local.local_open_state()
    if state is None:
        print("DUAL_HOUR_V2_NO_EXISTING_RESERVATION local_open_reservation=false", flush=True)
        return
    result = await settle.reconcile()
    print("DUAL_HOUR_V2_RECONCILED " + json.dumps(result, sort_keys=True), flush=True)
    if not result.get("local_reservation_cleared") or local.local_open_state() is not None:
        raise RuntimeError("v2 reconciliation did not actually clear local reservation")
asyncio.run(main())
PY

# Hard local proof plus venue read-only exposure audit.
docker run -i --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u - <<'PY'
import asyncio, json
from src import gemini_short_crypto_local_budget as b
async def main():
    state = b.local_open_state()
    assert state is None, f"reservation still present: {state}"
    snap = await b.account_exposure_snapshot()
    if snap["active_short_crypto_orders"] or snap["active_short_crypto_positions"]:
        raise RuntimeError(f"short-crypto exposure still active: {snap}")
    print(json.dumps({"type":"DUAL_HOUR_V2_POST_RECONCILE_CLEAN","local_open_reservation":False,**snap,"order_entry":False,"venue_mutation":False},sort_keys=True),flush=True)
asyncio.run(main())
PY

# Read-only maker-rebate payout snapshot; failure is informational, not a trading gate.
echo "=== MAKER REBATE PAYOUT SNAPSHOT (READ ONLY) ==="
docker run -i --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  "$IMAGE" python -u - <<'PY'
import asyncio, json
from src.gemini_prediction_auth import private_rest_request
async def main():
    try:
        r = await private_rest_request("POST", "/v1/prediction-markets/maker-rebate/payouts", params={"limit": 5, "offset": 0}, timeout=8.0)
        body = r.json() if r.status_code == 200 else None
        payouts = body.get("payouts", [])[:5] if isinstance(body, dict) and isinstance(body.get("payouts"), list) else []
        print(json.dumps({"type":"MAKER_REBATE_PAYOUT_SNAPSHOT","http":r.status_code,"payouts":payouts,"venue_mutation":False},sort_keys=True),flush=True)
    except Exception as exc:
        print(json.dumps({"type":"MAKER_REBATE_PAYOUT_SNAPSHOT","available":False,"reason":type(exc).__name__,"venue_mutation":False},sort_keys=True),flush=True)
asyncio.run(main())
PY

# Independent disarmed BTC5m preflight.
echo "=== BTC5M V3 DISARMED PREFLIGHT ==="
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_LIVE_Q1=NO \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ID= \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ARM= \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_btc5m_inventory_maker_canary_q1_v3

echo "DUAL_HOUR_V2_BTC5M_PREFLIGHT_OK order_entry=false"

# Independent disarmed BTC15m preflight.
echo "=== BTC15M V3 DISARMED PREFLIGHT ==="
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_INVENTORY15_LIVE_Q1=NO \
  -e GEMINI_SHORT_CRYPTO_INVENTORY15_CANARY_ID= \
  -e GEMINI_SHORT_CRYPTO_INVENTORY15_CANARY_ARM= \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_btc15m_inventory_maker_canary_q1_v3

echo "DUAL_HOUR_V2_BTC15M_PREFLIGHT_OK order_entry=false"

# Final clean-start gate.
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
      "type":"DUAL_HOUR_V2_CLEAN_START","clean":True,**snap,
      "horizons":["5m","15m"],"q_per_outcome":1,
      "max_executed_total":4,"max_executed_per_horizon":2,
      "realized_loss_cap":"-1.00","maker_rebate_in_session_pnl":False,
      "order_entry":False,"mutation":False
    },sort_keys=True),flush=True)
asyncio.run(main())
PY

echo "DUAL_HOUR_V2_READY_FOR_LIVE_APPROVAL order_entry=false mutation=false commit=$PINNED_COMMIT"
