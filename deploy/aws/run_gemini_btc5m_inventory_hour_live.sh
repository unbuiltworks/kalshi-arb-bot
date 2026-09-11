#!/usr/bin/env bash
set -euo pipefail

# LIVE-CAPABLE one-hour Gemini BTC5m inventory-maker supervisor launcher.
# Requires a fresh explicit one-hour approval ID and consumes it exactly once.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-btc5m-inventory-hour-live}"
BRANCH="feature/gemini-short-crypto-live-q1"
PINNED_COMMIT="7faabda1e9cedf00e3e93b1238ee5ed0cc5ee99a"
IMAGE="arb-gemini-btc5m-inventory-hour:live-7faabda"
CONTAINER="arb-gemini-btc5m-inventory-hour-live"
ENV_FILE="${ENV_FILE:-/opt/arb/secrets/gemini-short-crypto.env}"
STATE_DIR="${STATE_DIR:-/opt/arb/gemini-short-crypto-state}"

[ "${ARB_GEMINI_HOUR_Q1_APPROVED:-NO}" = "YES" ] || {
  echo "fresh one-hour approval flag missing" >&2; exit 1;
}
APPROVAL_ID="${ARB_GEMINI_HOUR_APPROVAL_ID:-}"
[ -n "$APPROVAL_ID" ] || { echo "fresh one-hour approval ID missing" >&2; exit 1; }
SAFE_APPROVAL_ID="$(printf '%s' "$APPROVAL_ID" | tr -cd 'A-Za-z0-9_-')"
[ -n "$SAFE_APPROVAL_ID" ] || { echo "invalid one-hour approval ID" >&2; exit 1; }

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

USED_MARKER="$STATE_DIR/hour_approval_${SAFE_APPROVAL_ID}.used"
[ ! -e "$USED_MARKER" ] || { echo "one-hour approval ID already consumed: $SAFE_APPROVAL_ID" >&2; exit 1; }
if docker ps --filter "name=^/${CONTAINER}$" --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "one-hour supervisor already running" >&2
  exit 1
fi
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

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
echo "HOUR_Q1_LIVE_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null

docker run --rm --network none "$IMAGE" python -m pytest -q \
  tests/test_gemini_short_crypto_inventory_hour_supervisor.py \
  tests/test_gemini_short_crypto_inventory_settlement_reconcile.py \
  tests/test_gemini_short_crypto_inventory_rest_v3.py \
  tests/test_gemini_short_crypto_inventory_maker_q1.py \
  tests/test_gemini_short_crypto_btc5m_canary_v3.py

echo "HOUR_Q1_LIVE_SAFETY_TESTS_OK"

# Exact-image disarmed market preflight.
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_LIVE_Q1=NO \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ID= \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ARM= \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_btc5m_inventory_maker_canary_q1_v3

echo "HOUR_Q1_LIVE_MARKET_PREFLIGHT_OK order_entry=false"

# Clean start mandatory: no active short-crypto exposure and no local reservation.
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
    print(json.dumps({"type":"HOUR_Q1_LIVE_CLEAN_START","clean":True,**snap,"order_entry":False,"mutation":False}, sort_keys=True), flush=True)
asyncio.run(main())
PY

echo "HOUR_Q1_LIVE_CLEAN_START_OK"

# Consume this exact approval before mutation. Any failed launch requires new approval.
printf '%s\n' "approval_id=$SAFE_APPROVAL_ID commit=$PINNED_COMMIT consumed_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$USED_MARKER.tmp"
chmod 0640 "$USED_MARKER.tmp"
sync "$USED_MARKER.tmp"
mv "$USED_MARKER.tmp" "$USED_MARKER"
sync "$STATE_DIR"

SESSION_ID="h1-${SAFE_APPROVAL_ID:0:10}-$(date -u +%H%M%S)"
echo "HOUR_Q1_LIVE_STARTING session_id=$SESSION_ID approval_id=$SAFE_APPROVAL_ID commit=$PINNED_COMMIT q=1_per_outcome max_runtime=60m max_executed_campaigns=4 realized_loss_cap=-1.00"

docker run -d --name "$CONTAINER" --network host --restart no \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -e GEMINI_SHORT_CRYPTO_HOUR_LIVE_Q1=YES \
  -e GEMINI_SHORT_CRYPTO_HOUR_SESSION_ID="$SESSION_ID" \
  -e GEMINI_SHORT_CRYPTO_HOUR_SESSION_ARM="$SESSION_ID" \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_inventory_hour_supervisor

echo "HOUR_Q1_LIVE_DETACHED container=$CONTAINER session_id=$SESSION_ID"
echo "View logs: docker logs -f $CONTAINER"
echo "Stop immediately if needed: docker stop $CONTAINER"
