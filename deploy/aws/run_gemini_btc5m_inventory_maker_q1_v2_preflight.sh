#!/usr/bin/env bash
set -euo pipefail

# DISARMED AWS preflight for Gemini BTC5m inventory-maker q=1 v2.
# v2 adds bounded fresh-depth reconnect/retry only. No order entry.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-btc5m-inventory-maker-q1-v2-preflight}"
BRANCH="feature/gemini-short-crypto-live-q1"
PINNED_COMMIT="f1c70209a57764695b45fec586859a05d7640770"
IMAGE="arb-gemini-btc5m-inventory-q1:v2-preflight-f1c7020"
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
echo "INVENTORY_Q1_V2_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null
echo "INVENTORY_Q1_V2_IMAGE_READY image=$IMAGE"

# Exact-image offline safety suite.
docker run --rm --network none "$IMAGE" \
  python -m pytest -q \
  tests/test_gemini_short_crypto_quantity.py \
  tests/test_gemini_short_crypto_btc5m_canary_v3.py \
  tests/test_gemini_short_crypto_local_budget.py \
  tests/test_gemini_short_crypto_inventory_maker_q1.py \
  tests/test_gemini_short_crypto_inventory_fresh_top_retry.py

echo "INVENTORY_Q1_V2_SAFETY_TESTS_OK"

# Prove old arm namespace cannot arm inventory v2.
docker run --rm --network none \
  -e GEMINI_SHORT_CRYPTO_LIVE_Q1=YES \
  -e GEMINI_SHORT_CRYPTO_CANARY_ID=old-canary \
  -e GEMINI_SHORT_CRYPTO_CANARY_ARM=old-canary \
  "$IMAGE" python - <<'PY'
from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1 as inv
assert inv.live_armed() is False
print("INVENTORY_Q1_V2_ARM_NAMESPACE_OK inventory_armed=false")
PY

# Authenticated market preflight, explicitly DISARMED, through v2 retry entrypoint.
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_LIVE_Q1=NO \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ID= \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ARM= \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_btc5m_inventory_maker_canary_q1_v2

echo "INVENTORY_Q1_V2_MARKET_PREFLIGHT_OK order_entry=false"

# Read-only Gemini exposure + local reservation audit.
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_local_budget_audit

echo "INVENTORY_Q1_V2_PREFLIGHT_COMPLETE order_entry=false mutation=false commit=$PINNED_COMMIT"
