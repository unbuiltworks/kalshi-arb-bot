#!/usr/bin/env bash
set -euo pipefail

# ONE-SHOT LIVE Gemini BTC5m canary #2 using the corrected distinct client IDs.
# USER LIVE AUTHORIZATION REQUIRED BEFORE RUNNING.
#
# This is intentionally isolated and one-shot:
# - exact feature commit pinned;
# - BTC 5m, q=1 only;
# - distinct deterministic YES/NO client IDs checked for the actual canary ID;
# - offline quantity/client-ID/fractional-flatten/local-ledger tests on exact image;
# - authenticated DISARMED market preflight before mutation;
# - clean Gemini short-crypto account + AWS-local reservation gate immediately before arming;
# - fresh >=3c modeled pair-edge gate is rechecked again inside the live process;
# - AWS-local fsynced reservation happens before order-session connection;
# - one process, one campaign, no restart;
# - post-run audit is read-only;
# - does not touch sports maker, Railway services, or arb-short-* shadows.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-short-crypto-live-q1}"
BRANCH="feature/gemini-short-crypto-live-q1"
PINNED_COMMIT="282522639047b3d41b2f37cb29758b4fe7e39e65"
IMAGE="arb-gemini-short-btc5m-q1:canary2-live-2825226"
CONTAINER="arb-gemini-short-btc5m-q1-canary2-live"
PREFLIGHT_CONTAINER="arb-gemini-short-btc5m-q1-canary2-preflight"
AUDIT_CONTAINER="arb-gemini-short-btc5m-q1-canary2-audit"
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

echo "CANARY2_LIVE_ENV_OK names=GEMINI_API_KEY,GEMINI_API_SECRET mode=$perms state_dir=$STATE_DIR"

if docker ps --filter "name=^/${CONTAINER}$" --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "canary #2 container already running; refusing duplicate" >&2
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
echo "CANARY2_LIVE_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null
echo "CANARY2_LIVE_IMAGE_READY image=$IMAGE"

# Offline regression suite on the exact image used live.
docker run --rm --network none "$IMAGE" \
  python -m pytest -q \
  tests/test_gemini_short_crypto_quantity.py \
  tests/test_gemini_short_crypto_btc5m_canary_v3.py \
  tests/test_gemini_short_crypto_local_budget.py

echo "CANARY2_LIVE_SAFETY_TESTS_OK"

# Authenticated market preflight, explicitly forced DISARMED.
docker run --name "$PREFLIGHT_CONTAINER" --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_LIVE_Q1=NO \
  -e GEMINI_SHORT_CRYPTO_CANARY_ID= \
  -e GEMINI_SHORT_CRYPTO_CANARY_ARM= \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_btc5m_maker_canary_q1_v4
preflight_rc=$?
docker rm "$PREFLIGHT_CONTAINER" >/dev/null 2>&1 || true
if [ "$preflight_rc" -ne 0 ]; then
  echo "CANARY2_LIVE_PREFLIGHT_FAILED rc=$preflight_rc" >&2
  exit "$preflight_rc"
fi
echo "CANARY2_LIVE_PREFLIGHT_OK order_entry=false"

# Clean-account/local-state check that FAILS CLOSED on any existing exposure.
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
    print(json.dumps({"type":"CANARY2_LIVE_CLEAN_ACCOUNT","clean":True,**snap,"order_entry":False,"mutation":False}, sort_keys=True), flush=True)
asyncio.run(main())
PY
audit_rc=$?
docker rm "$AUDIT_CONTAINER" >/dev/null 2>&1 || true
if [ "$audit_rc" -ne 0 ]; then
  echo "CANARY2_LIVE_CLEAN_ACCOUNT_FAILED rc=$audit_rc" >&2
  exit "$audit_rc"
fi
echo "CANARY2_LIVE_CLEAN_ACCOUNT_OK"

CANARY_ID="aws-local-btc5m-q1-c2-$(date -u +%Y%m%dT%H%M%SZ)-$$"

# Prove the ACTUAL campaign will use distinct Gemini client IDs before arming.
docker run --rm --network none \
  -e GEMINI_SHORT_CRYPTO_CANARY_ID="$CANARY_ID" \
  "$IMAGE" python - <<'PY'
import os
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src.gemini_short_crypto_btc5m_maker_canary_q1_v3 import maker_client_ids
cid = base._clean_id(os.environ["GEMINI_SHORT_CRYPTO_CANARY_ID"])
trade_id = f"crypto-gem-btc5m-{cid}"[:64]
y, n = maker_client_ids(trade_id)
assert y != n and y.endswith("-yes") and n.endswith("-no")
assert len(y) <= 36 and len(n) <= 36
print(f"CANARY2_LIVE_CLIENT_IDS_OK trade_id={trade_id} yes={y} no={n} distinct=true")
PY

echo "CANARY2_LIVE_STARTING canary_id=$CANARY_ID commit=$PINNED_COMMIT q=1 venue=gemini asset=BTC horizon=5m ledger=AWS_LOCAL_FSYNC"

# Detached so an SSM/browser disconnect cannot kill resting maker orders.
docker run -d --name "$CONTAINER" --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_LIVE_Q1=YES \
  -e GEMINI_SHORT_CRYPTO_CANARY_ID="$CANARY_ID" \
  -e GEMINI_SHORT_CRYPTO_CANARY_ARM="$CANARY_ID" \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_btc5m_maker_canary_q1_v4

set +e
rc="$(docker wait "$CONTAINER")"
wait_rc=$?
set -e

echo "=== CANARY2 LIVE LOG ==="
docker logs "$CONTAINER" 2>&1 || true
echo "=== CANARY2 LIVE END ==="
echo "CANARY2_LIVE_CONTAINER_EXIT docker_wait_rc=$wait_rc process_rc=$rc canary_id=$CANARY_ID"

echo "=== CANARY2 POST-RUN READ-ONLY AUDIT ==="
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_local_budget_audit || true

if [ "$wait_rc" -ne 0 ] || [ "$rc" != "0" ]; then
  echo "CANARY2_LIVE_PROCESS_NONZERO -- do not rerun; reconcile Gemini + local state first" >&2
  exit 1
fi

echo "CANARY2_LIVE_ONE_SHOT_COMPLETE canary_id=$CANARY_ID -- no result is realized profit until authoritative Gemini reconciliation/settlement"
