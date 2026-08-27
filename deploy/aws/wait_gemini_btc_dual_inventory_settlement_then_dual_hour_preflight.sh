#!/usr/bin/env bash
set -euo pipefail

# DISARMED readiness gate for one real BTC5m + BTC15m q=1 inventory-maker hour.
# Venue mutations are impossible here. It may only perform authoritative local
# settlement bookkeeping/release for an already-executed unresolved campaign.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-btc-dual-hour-gate}"
BRANCH="feature/gemini-short-crypto-live-q1"
PINNED_COMMIT="840dc158ff6e75b1a3b63e45d6c1473293453c93"
IMAGE="arb-gemini-btc-dual-hour:gate-840dc15"
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
echo "DUAL_HOUR_GATE_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null

docker run --rm --network none "$IMAGE" python -m pytest -q \
  tests/test_gemini_short_crypto_inventory_dual_hour_supervisor.py \
  tests/test_gemini_short_crypto_inventory_settlement_reconcile.py \
  tests/test_gemini_short_crypto_inventory_rest_v3.py \
  tests/test_gemini_short_crypto_inventory_maker_q1.py \
  tests/test_gemini_short_crypto_btc5m_canary_v3.py

echo "DUAL_HOUR_GATE_SAFETY_TESTS_OK"

# Prove legacy one-shot / 5m-hour / overnight arm namespaces cannot arm dual-hour.
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
  "$IMAGE" python - <<'PY'
from src import gemini_short_crypto_inventory_dual_hour_supervisor as s
assert s.live_armed() is False
print("DUAL_HOUR_ARM_NAMESPACE_OK dual_hour_armed=false")
PY

# Clear only a legitimate already-executed 5m/15m reservation from exact Gemini settlement.
echo "=== DUAL HOUR GATE SETTLEMENT / LOCAL STATE ==="
docker run -i --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -e SETTLEMENT_WAIT_SECONDS="$SETTLEMENT_WAIT_SECONDS" \
  -e SETTLEMENT_POLL_SECONDS="$SETTLEMENT_POLL_SECONDS" \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u - <<'PY'
import asyncio, json, os, time
from src import gemini_short_crypto_dual_horizon_settlement_reconcile as settle
from src import gemini_short_crypto_local_budget as local
WAIT=max(30.0,min(1800.0,float(os.getenv("SETTLEMENT_WAIT_SECONDS","900"))))
POLL=max(2.0,min(30.0,float(os.getenv("SETTLEMENT_POLL_SECONDS","5"))))
async def main():
    state=local.local_open_state()
    if state is None:
        print("DUAL_HOUR_GATE_NO_EXISTING_RESERVATION local_open_reservation=false", flush=True)
        return
    deadline=time.monotonic()+WAIT
    attempt=0
    while True:
        attempt += 1
        try:
            result=await settle.reconcile()
            print("DUAL_HOUR_GATE_SETTLEMENT_RECONCILED "+json.dumps(result,sort_keys=True), flush=True)
            assert result.get("local_reservation_cleared") is True
            assert local.local_open_state() is None
            return
        except RuntimeError as exc:
            if "settlement not posted yet" not in str(exc).lower():
                raise
            remaining=deadline-time.monotonic()
            print("DUAL_HOUR_GATE_SETTLEMENT_PENDING "+json.dumps({"attempt":attempt,"seconds_remaining":max(0,int(remaining)),"venue_mutation":False},sort_keys=True), flush=True)
            if remaining <= 0:
                raise RuntimeError("bounded settlement wait expired")
            await asyncio.sleep(min(POLL,max(0.1,remaining)))
asyncio.run(main())
PY

# Hard local-state proof after reconciliation process.
docker run -i --rm --network host \
  --env-file "$ENV_FILE" \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python - <<'PY'
from src import gemini_short_crypto_local_budget as b
state=b.local_open_state()
assert state is None, f"reservation still present: {state}"
print("DUAL_HOUR_LOCAL_RELEASE_PROVEN local_open_reservation=false")
PY

# Independent disarmed BTC5m REST-book preflight.
echo "=== BTC5M DISARMED PREFLIGHT ==="
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_LIVE_Q1=NO \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_btc5m_inventory_maker_canary_q1_v3

echo "DUAL_HOUR_BTC5M_PREFLIGHT_OK order_entry=false"

# Independent disarmed BTC15m REST-book preflight.
echo "=== BTC15M DISARMED PREFLIGHT ==="
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_INVENTORY15_LIVE_Q1=NO \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_btc15m_inventory_maker_canary_q1_v3

echo "DUAL_HOUR_BTC15M_PREFLIGHT_OK order_entry=false"

# Final clean account/local gate.
docker run -i --rm --network host \
  --env-file "$ENV_FILE" \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u - <<'PY'
import asyncio, json
from src import gemini_short_crypto_inventory_local_budget as b
async def main():
    snap=await b.assert_clean_account()
    print(json.dumps({
      "type":"DUAL_HOUR_CLEAN_START","clean":True,**snap,
      "horizons":["5m","15m"],"q_per_outcome":1,
      "max_executed_total":4,"max_executed_per_horizon":2,
      "realized_loss_cap":"-1.00","order_entry":False,"mutation":False
    },sort_keys=True),flush=True)
asyncio.run(main())
PY

echo "DUAL_HOUR_READY_FOR_LIVE_APPROVAL order_entry=false mutation=false commit=$PINNED_COMMIT"
