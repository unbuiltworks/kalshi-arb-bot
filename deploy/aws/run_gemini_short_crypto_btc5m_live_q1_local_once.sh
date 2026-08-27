#!/usr/bin/env bash
set -euo pipefail

# ONE-SHOT LIVE Gemini BTC 5m q=1 maker canary using AWS-local durable accounting.
# This runner is only for the temporary Command Center / Neon outage path.
# USER LIVE AUTHORIZATION REQUIRED BEFORE RUNNING.
#
# Safety invariants:
# - strategy source pinned to an exact reviewed commit;
# - BTC 5m only, q=1 only (enforced in Python);
# - exact fractional-fill flatten logic retained;
# - clean Gemini short-crypto orders/positions check before arming;
# - the same clean-account check repeats inside reserve() immediately before entry;
# - reservation + append-only event journal are fsynced to host disk before order session connects;
# - fresh >=3c modeled pair-edge gate is rechecked inside the live process;
# - one process / one campaign / no restart;
# - no Command Center event is called while Neon is unavailable;
# - local events remain NOT REALIZED and are marked for later Command Center replay;
# - does not stop/modify sports maker, Railway services, or arb-short-* shadows.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-short-crypto-live-q1}"
BRANCH="feature/gemini-short-crypto-live-q1"
PINNED_COMMIT="b961c74f8284db824a0d121173a58780fa1f6016"
IMAGE="arb-gemini-short-btc5m-q1:local-live-b961c74"
CONTAINER="arb-gemini-short-btc5m-q1-local-live"
PREFLIGHT_CONTAINER="arb-gemini-short-btc5m-q1-local-preflight"
AUDIT_CONTAINER="arb-gemini-short-btc5m-q1-local-audit"
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

echo "LOCAL_LIVE_Q1_ENV_OK names=GEMINI_API_KEY,GEMINI_API_SECRET mode=$perms state_dir=$STATE_DIR"

if docker ps --filter "name=^/${CONTAINER}$" --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "local live canary container already running; refusing duplicate" >&2
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
echo "LOCAL_LIVE_Q1_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE"
echo "LOCAL_LIVE_Q1_IMAGE_READY image=$IMAGE"

# Offline regression suite on the exact image used live.
docker run --rm --network none "$IMAGE" \
  python -m pytest -q \
  tests/test_gemini_short_crypto_quantity.py \
  tests/test_gemini_short_crypto_btc5m_canary_v3.py \
  tests/test_gemini_short_crypto_local_budget.py
echo "LOCAL_LIVE_Q1_SAFETY_TESTS_OK"

# Authenticated market preflight, forced disarmed. This cannot enter reserve/order code.
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
  echo "LOCAL_LIVE_Q1_PREFLIGHT_FAILED rc=$preflight_rc" >&2
  exit "$preflight_rc"
fi
echo "LOCAL_LIVE_Q1_PREFLIGHT_OK order_entry=false"

# Explicit read-only account/local-state audit just before arming.
docker run --name "$AUDIT_CONTAINER" --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u - <<'PY'
import asyncio, json
from src import gemini_short_crypto_local_budget as b
async def main():
    snap = await b.assert_clean_account()
    print(json.dumps({"type":"LOCAL_LIVE_Q1_CLEAN_ACCOUNT","clean":True,**snap,"order_entry":False,"mutation":False}, sort_keys=True), flush=True)
asyncio.run(main())
PY
audit_rc=$?
docker rm "$AUDIT_CONTAINER" >/dev/null 2>&1 || true
if [ "$audit_rc" -ne 0 ]; then
  echo "LOCAL_LIVE_Q1_CLEAN_ACCOUNT_FAILED rc=$audit_rc" >&2
  exit "$audit_rc"
fi
echo "LOCAL_LIVE_Q1_CLEAN_ACCOUNT_OK"

CANARY_ID="aws-local-btc5m-q1-$(date -u +%Y%m%dT%H%M%SZ)-$$"
echo "LOCAL_LIVE_Q1_STARTING canary_id=$CANARY_ID commit=$PINNED_COMMIT q=1 venue=gemini asset=BTC horizon=5m ledger=AWS_LOCAL_FSYNC"

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

echo "=== LOCAL LIVE Q1 CANARY LOG ==="
docker logs "$CONTAINER" 2>&1 || true
echo "=== LOCAL LIVE Q1 CANARY END ==="
echo "LOCAL_LIVE_Q1_CONTAINER_EXIT docker_wait_rc=$wait_rc process_rc=$rc canary_id=$CANARY_ID"

echo "=== POST-RUN READ-ONLY EXPOSURE / LOCAL-STATE AUDIT ==="
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_local_budget_audit || true

echo "=== LOCAL JOURNAL METADATA ==="
if [ -f "$STATE_DIR/gemini_btc5m_q1_events.jsonl" ]; then
  echo "journal_lines=$(wc -l < "$STATE_DIR/gemini_btc5m_q1_events.jsonl")"
  echo "journal_bytes=$(stat -c '%s' "$STATE_DIR/gemini_btc5m_q1_events.jsonl")"
else
  echo "journal_absent=true"
fi
if [ -f "$STATE_DIR/gemini_btc5m_q1_open.json" ]; then
  echo "open_reservation_present=true"
else
  echo "open_reservation_present=false"
fi

if [ "$wait_rc" -ne 0 ] || [ "$rc" != "0" ]; then
  echo "LOCAL_LIVE_Q1_PROCESS_NONZERO -- do not rerun; reconcile Gemini + local state first" >&2
  exit 1
fi

echo "LOCAL_LIVE_Q1_ONE_SHOT_COMPLETE canary_id=$CANARY_ID -- no result is realized profit until settlement/reconciliation"
