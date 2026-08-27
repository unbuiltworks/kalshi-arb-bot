#!/usr/bin/env bash
set -euo pipefail

# ONE-SHOT LIVE Gemini BTC5m q=1 inventory-maker v3.
# USER LIVE AUTHORIZATION REQUIRED BEFORE RUNNING.
# v3 uses Gemini REST book for public market data, private WS for order/fill transport,
# and may wait read-only for one eligible window. No automatic second campaign.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-btc5m-inventory-maker-q1-v3-live}"
BRANCH="feature/gemini-short-crypto-live-q1"
PINNED_COMMIT="5a131b5d755d8b59155d6b3845d9fba161656f0c"
IMAGE="arb-gemini-btc5m-inventory-q1:v3-live-5a131b5"
CONTAINER="arb-gemini-btc5m-inventory-q1-v3-live"
PREFLIGHT_CONTAINER="arb-gemini-btc5m-inventory-q1-v3-live-preflight"
AUDIT_CONTAINER="arb-gemini-btc5m-inventory-q1-v3-live-audit"
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

perms="$(stat -c '%a' "$ENV_FILE")"
group_digit="${perms: -2:1}"
other_digit="${perms: -1}"
if (( other_digit != 0 || group_digit == 2 || group_digit == 3 || group_digit == 6 || group_digit == 7 )); then
  echo "secure env permissions too broad: mode=$perms" >&2
  exit 1
fi

mkdir -p "$STATE_DIR"
chmod 0750 "$STATE_DIR" || true

echo "INVENTORY_Q1_V3_LIVE_ENV_OK names=GEMINI_API_KEY,GEMINI_API_SECRET mode=$perms state_dir=$STATE_DIR"

if docker ps --filter "name=^/${CONTAINER}$" --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "inventory q1 v3 live container already running; refusing duplicate" >&2
  exit 1
fi
docker rm -f "$CONTAINER" "$PREFLIGHT_CONTAINER" "$AUDIT_CONTAINER" >/dev/null 2>&1 || true

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
echo "INVENTORY_Q1_V3_LIVE_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null
echo "INVENTORY_Q1_V3_LIVE_IMAGE_READY image=$IMAGE"

# Exact-image safety suite.
docker run --rm --network none "$IMAGE" \
  python -m pytest -q \
  tests/test_gemini_short_crypto_quantity.py \
  tests/test_gemini_short_crypto_btc5m_canary_v3.py \
  tests/test_gemini_short_crypto_local_budget.py \
  tests/test_gemini_short_crypto_inventory_maker_q1.py \
  tests/test_gemini_short_crypto_inventory_fresh_top_retry.py \
  tests/test_gemini_short_crypto_inventory_rest_v3.py

echo "INVENTORY_Q1_V3_LIVE_SAFETY_TESTS_OK"

# Old canary namespace cannot arm this strategy.
docker run --rm --network none \
  -e GEMINI_SHORT_CRYPTO_LIVE_Q1=YES \
  -e GEMINI_SHORT_CRYPTO_CANARY_ID=old-canary \
  -e GEMINI_SHORT_CRYPTO_CANARY_ARM=old-canary \
  "$IMAGE" python - <<'PY'
from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1 as inv
assert inv.live_armed() is False
print("INVENTORY_Q1_V3_LIVE_ARM_NAMESPACE_OK inventory_armed=false")
PY

# Authenticated DISARMED REST-book preflight.
docker run --name "$PREFLIGHT_CONTAINER" --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_LIVE_Q1=NO \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ID= \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ARM= \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_btc5m_inventory_maker_canary_q1_v3
preflight_rc=$?
docker rm "$PREFLIGHT_CONTAINER" >/dev/null 2>&1 || true
if [ "$preflight_rc" -ne 0 ]; then
  echo "INVENTORY_Q1_V3_LIVE_PREFLIGHT_FAILED rc=$preflight_rc" >&2
  exit "$preflight_rc"
fi
echo "INVENTORY_Q1_V3_LIVE_PREFLIGHT_OK order_entry=false market_data=GEMINI_REST_BOOK"

# Fail closed on any existing short-crypto exposure or local reservation.
docker run -i --name "$AUDIT_CONTAINER" --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u - <<'PY'
import asyncio, json
from src import gemini_short_crypto_local_budget as b
async def main():
    snap = await b.assert_clean_account()
    print(json.dumps({"type":"INVENTORY_Q1_V3_LIVE_CLEAN_ACCOUNT","clean":True,**snap,"order_entry":False,"mutation":False}, sort_keys=True), flush=True)
asyncio.run(main())
PY
audit_rc=$?
docker rm "$AUDIT_CONTAINER" >/dev/null 2>&1 || true
if [ "$audit_rc" -ne 0 ]; then
  echo "INVENTORY_Q1_V3_LIVE_CLEAN_ACCOUNT_FAILED rc=$audit_rc" >&2
  exit "$audit_rc"
fi
echo "INVENTORY_Q1_V3_LIVE_CLEAN_ACCOUNT_OK"

CANARY_ID="aws-btc5m-inv-q1-v3-$(date -u +%Y%m%dT%H%M%SZ)-$$"

# Prove actual campaign IDs and inventory arm before mutation.
docker run --rm --network none \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_LIVE_Q1=YES \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ID="$CANARY_ID" \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ARM="$CANARY_ID" \
  "$IMAGE" python - <<'PY'
import os
from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1 as inv
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src.gemini_short_crypto_btc5m_maker_canary_q1_v3 import maker_client_ids
cid = base._clean_id(os.environ["GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ID"])
trade_id = f"crypto-gem-btc5m-inventory-{cid}"[:64]
y, n = maker_client_ids(trade_id)
assert inv.live_armed() is True
assert y != n and y.endswith("-yes") and n.endswith("-no")
assert len(y) <= 36 and len(n) <= 36
print(f"INVENTORY_Q1_V3_LIVE_IDS_ARM_OK trade_id={trade_id} yes={y} no={n} distinct=true inventory_armed=true")
PY

echo "INVENTORY_Q1_V3_LIVE_STARTING canary_id=$CANARY_ID commit=$PINNED_COMMIT q=1_per_outcome venue=gemini asset=BTC horizon=5m strategy=MAKER_FIRST_TEMPORAL_INVENTORY market_data=REST_BOOK order_transport=PRIVATE_WS ledger=AWS_LOCAL_FSYNC"

# Detached so SSM/browser disconnect cannot kill the campaign. v3 may wait read-only
# for one eligible window, then can run at most one campaign and exit.
docker run -d --name "$CONTAINER" --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_LIVE_Q1=YES \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ID="$CANARY_ID" \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ARM="$CANARY_ID" \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_btc5m_inventory_maker_canary_q1_v3

set +e
rc="$(docker wait "$CONTAINER")"
wait_rc=$?
set -e

echo "=== INVENTORY Q1 V3 LIVE LOG ==="
docker logs "$CONTAINER" 2>&1 || true
echo "=== INVENTORY Q1 V3 LIVE END ==="
echo "INVENTORY_Q1_V3_LIVE_CONTAINER_EXIT docker_wait_rc=$wait_rc process_rc=$rc canary_id=$CANARY_ID"

echo "=== INVENTORY Q1 V3 POST-RUN READ-ONLY AUDIT ==="
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_local_budget_audit || true

if [ "$wait_rc" -ne 0 ] || [ "$rc" != "0" ]; then
  echo "INVENTORY_Q1_V3_LIVE_PROCESS_NONZERO -- do not rerun; reconcile Gemini + local state first" >&2
  exit 1
fi

echo "INVENTORY_Q1_V3_LIVE_ONE_SHOT_COMPLETE canary_id=$CANARY_ID -- executed inventory is not realized profit until authoritative Gemini settlement/reconciliation"
