#!/usr/bin/env bash
set -euo pipefail

# LIVE-CAPABLE, one-shot AWS launcher for the bounded Gemini incentive-MM q=1 probe.
# No workflow/trigger should invoke this without fresh user approval. The launcher
# requires an exact commit plus a one-use approval ID, then runs one BTC 5m campaign.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-incentive-mm-live-probe-q1}"
BRANCH="feature/gemini-incentive-mm-live-probe-q1"
PINNED_COMMIT="${PINNED_COMMIT:-}"
CONTAINER="arb-gemini-incentive-mm-live-probe-q1"

[ -n "$PINNED_COMMIT" ] || { echo "PINNED_COMMIT is required" >&2; exit 1; }
[[ "$PINNED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid PINNED_COMMIT" >&2; exit 1; }
[ "${ARB_GEMINI_INCENTIVE_MM_Q1_APPROVED:-NO}" = "YES" ] || {
  echo "fresh incentive-MM q1 approval flag missing" >&2; exit 1;
}
APPROVAL_ID="${ARB_GEMINI_INCENTIVE_MM_Q1_APPROVAL_ID:-}"
[ -n "$APPROVAL_ID" ] || { echo "fresh incentive-MM q1 approval ID missing" >&2; exit 1; }
SAFE_APPROVAL_ID="$(printf '%s' "$APPROVAL_ID" | tr -cd 'A-Za-z0-9_-')"
[ -n "$SAFE_APPROVAL_ID" ] || { echo "invalid incentive-MM q1 approval ID" >&2; exit 1; }

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

# Never overlap known Gemini short-crypto live/canary campaigns. Read-only shadows
# are intentionally not matched here.
RUNNING_LIVE="$(docker ps --format '{{.Names}}' | grep -E '^arb-gemini-(btc-dual-hour|btc5m-inventory-hour|l5-threshold|incentive-mm-live-probe|.*live-canary)' || true)"
if [ -n "$RUNNING_LIVE" ]; then
  echo "blocked: another Gemini short-crypto live container is running" >&2
  printf '%s\n' "$RUNNING_LIVE" >&2
  exit 1
fi

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
USED_MARKER="$STATE_DIR/incentive_mm_q1_approval_${SAFE_APPROVAL_ID}.used"
[ ! -e "$USED_MARKER" ] || { echo "approval ID already consumed: $SAFE_APPROVAL_ID" >&2; exit 1; }

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
SHORT_SHA="${PINNED_COMMIT:0:12}"
IMAGE="arb-gemini-incentive-mm-live-probe:q1-${SHORT_SHA}"
echo "INCENTIVE_MM_Q1_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null

# Exact-image safety regressions with network disabled.
docker run --rm --network none "$IMAGE" python -m pytest -q \
  tests/test_gemini_incentive_mm_live_probe_q1.py \
  tests/test_gemini_short_crypto_inventory_maker_q1.py \
  tests/test_gemini_short_crypto_btc5m_canary_v3.py \
  tests/test_gemini_short_crypto_inventory_fresh_top_retry.py \
  tests/test_gemini_short_crypto_inventory_rest_v3.py \
  tests/test_gemini_short_crypto_inventory_settlement_reconcile.py \
  tests/test_gemini_short_crypto_dual_horizon_settlement_reconcile_v2.py \
  tests/test_gemini_short_crypto_local_budget.py

echo "INCENTIVE_MM_Q1_SAFETY_TESTS_OK"

# Exact-image clean-account preflight. This reads account/order state but does not
# submit, modify, or cancel orders.
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
    print(json.dumps({"type":"INCENTIVE_MM_Q1_CLEAN_START","clean":True,**snap,"order_entry":False,"mutation":False}, sort_keys=True), flush=True)
asyncio.run(main())
PY

echo "INCENTIVE_MM_Q1_CLEAN_START_OK"

# Consume this exact approval immediately before the live process can mutate venue state.
printf '%s\n' \
  "approval_id=$SAFE_APPROVAL_ID commit=$PINNED_COMMIT consumed_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "$USED_MARKER.tmp"
chmod 0640 "$USED_MARKER.tmp"
sync "$USED_MARKER.tmp"
mv "$USED_MARKER.tmp" "$USED_MARKER"
sync "$STATE_DIR"

PROBE_ID="immq1-${SAFE_APPROVAL_ID:0:12}-$(date -u +%H%M%S)"
echo "INCENTIVE_MM_Q1_LIVE_STARTING probe_id=$PROBE_ID approval_id=$SAFE_APPROVAL_ID commit=$PINNED_COMMIT q=1_per_outcome asset=BTC horizon=5m max_campaigns=1 max_initial_pair_loss=0.01 liquidity_rewards_eligible=false auto_restart=false"

docker run -d --name "$CONTAINER" --network host --restart no \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -e GEMINI_INCENTIVE_MM_LIVE_PROBE_Q1=YES \
  -e GEMINI_INCENTIVE_MM_PROBE_ID="$PROBE_ID" \
  -e GEMINI_INCENTIVE_MM_PROBE_ARM="$PROBE_ID" \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_incentive_mm_live_probe_q1

echo "INCENTIVE_MM_Q1_LIVE_DETACHED container=$CONTAINER probe_id=$PROBE_ID"
sleep 2
docker ps -a --filter "name=^/${CONTAINER}$" --format 'INCENTIVE_MM_Q1_CONTAINER {{.Names}} {{.Status}} {{.Image}}'
docker logs --tail 40 "$CONTAINER" 2>&1 || true

echo "Emergency stop: docker stop $CONTAINER"
