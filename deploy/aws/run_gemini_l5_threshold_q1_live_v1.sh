#!/usr/bin/env bash
set -euo pipefail

# LIVE-CAPABLE, one-shot AWS launcher for the bounded Gemini l5-threshold v1 probe.
# The launcher is deliberately pinned to an exact commit and consumes an explicit
# approval ID once. It never restarts the container automatically.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-l5-threshold-q1-live-v1}"
BRANCH="feature/l5-gemini-aws-live-probe-v1"
PINNED_COMMIT="${PINNED_COMMIT:-}"
CONTAINER="arb-gemini-l5-threshold-q1-live-v1"

[ -n "$PINNED_COMMIT" ] || { echo "PINNED_COMMIT is required" >&2; exit 1; }
[ "${ARB_GEMINI_L5_Q1_APPROVED:-NO}" = "YES" ] || {
  echo "fresh l5 q1 approval flag missing" >&2; exit 1;
}
APPROVAL_ID="${ARB_GEMINI_L5_Q1_APPROVAL_ID:-}"
[ -n "$APPROVAL_ID" ] || { echo "fresh l5 q1 approval ID missing" >&2; exit 1; }
SAFE_APPROVAL_ID="$(printf '%s' "$APPROVAL_ID" | tr -cd 'A-Za-z0-9_-')"
[ -n "$SAFE_APPROVAL_ID" ] || { echo "invalid l5 q1 approval ID" >&2; exit 1; }

if [ -n "${ENV_FILE:-}" ]; then
  :
elif [ -f /opt/arb/secrets/gemini-short-crypto.env ]; then
  ENV_FILE=/opt/arb/secrets/gemini-short-crypto.env
elif [ -f "$SOURCE_REPO/.env" ]; then
  ENV_FILE="$SOURCE_REPO/.env"
else
  echo "no Gemini secure env file found" >&2
  exit 1
fi

if [ -n "${STATE_DIR:-}" ]; then
  :
elif [ -d /home/ssm-user/gemini-short-crypto-state ]; then
  STATE_DIR=/home/ssm-user/gemini-short-crypto-state
else
  STATE_DIR=/opt/arb/gemini-short-crypto-state
fi

SHORT_SHA="${PINNED_COMMIT:0:12}"
IMAGE="arb-gemini-l5-threshold-q1:v1-${SHORT_SHA}"

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

# Never overlap another known Gemini short-crypto live campaign.
RUNNING_LIVE="$(docker ps --format '{{.Names}}' | grep -E '^arb-gemini-(btc-dual-hour|btc5m-inventory-hour|l5-threshold)' || true)"
if [ -n "$RUNNING_LIVE" ]; then
  echo "blocked: another Gemini short-crypto live container is running" >&2
  printf '%s\n' "$RUNNING_LIVE" >&2
  exit 1
fi

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

USED_MARKER="$STATE_DIR/l5_q1_approval_${SAFE_APPROVAL_ID}.used"
[ ! -e "$USED_MARKER" ] || { echo "l5 q1 approval ID already consumed: $SAFE_APPROVAL_ID" >&2; exit 1; }

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
echo "L5_Q1_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null

docker run --rm --network none "$IMAGE" python -m pytest -q \
  tests/test_gemini_l5_threshold_model.py \
  tests/test_short_crypto_incentive_snapshot.py \
  tests/test_gemini_short_crypto_l5_threshold_supervisor_v1.py \
  tests/test_gemini_short_crypto_dual_horizon_settlement_reconcile_v2.py \
  tests/test_gemini_short_crypto_inventory_maker_q1.py \
  tests/test_gemini_short_crypto_inventory_rest_v3.py \
  tests/test_gemini_short_crypto_btc5m_canary_v3.py

echo "L5_Q1_SAFETY_TESTS_OK"

# Exact-image read-only market/account/incentive preflight.
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_l5_threshold_preflight

echo "L5_Q1_PREFLIGHT_COMPLETE order_entry=false"

# Hard clean-start gate immediately before mutation.
docker run -i --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u - <<'PY'
import asyncio, json
from src import gemini_short_crypto_inventory_local_budget as b
from src import gemini_short_crypto_local_budget as local
async def main():
    if local.local_open_state() is not None:
        raise RuntimeError("local open reservation present")
    snap = await b.assert_clean_account()
    print(json.dumps({"type":"L5_Q1_CLEAN_START","clean":True,**snap,"order_entry":False,"mutation":False}, sort_keys=True), flush=True)
asyncio.run(main())
PY

echo "L5_Q1_CLEAN_START_OK"

# Consume this exact approval before any live order mutation.
printf '%s\n' "approval_id=$SAFE_APPROVAL_ID commit=$PINNED_COMMIT consumed_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$USED_MARKER.tmp"
chmod 0640 "$USED_MARKER.tmp"
sync "$USED_MARKER.tmp"
mv "$USED_MARKER.tmp" "$USED_MARKER"
sync "$STATE_DIR"

SESSION_ID="l5v1-${SAFE_APPROVAL_ID:0:8}-$(date -u +%H%M%S)"
echo "L5_Q1_LIVE_STARTING session_id=$SESSION_ID approval_id=$SAFE_APPROVAL_ID commit=$PINNED_COMMIT q=1_per_outcome threshold=0.60 max_executed_total=2 max_per_horizon=1 max_runtime=60m realized_loss_cap=-1.00 auto_restart=false"

docker run -d --name "$CONTAINER" --network host --restart no \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -e GEMINI_L5_THRESHOLD_LIVE_Q1=YES \
  -e GEMINI_L5_THRESHOLD_SESSION_ID="$SESSION_ID" \
  -e GEMINI_L5_THRESHOLD_SESSION_ARM="$SESSION_ID" \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_l5_threshold_supervisor_v1

echo "L5_Q1_LIVE_DETACHED container=$CONTAINER session_id=$SESSION_ID"
sleep 2
docker ps -a --filter "name=^/${CONTAINER}$" --format 'L5_Q1_CONTAINER {{.Names}} {{.Status}} {{.Image}}'
docker logs --tail 30 "$CONTAINER" 2>&1 || true

echo "Emergency stop: docker stop $CONTAINER"
