#!/usr/bin/env bash
set -euo pipefail

# DISARMED overnight-supervisor readiness gate. No order entry.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-btc5m-inventory-overnight-preflight}"
BRANCH="feature/gemini-short-crypto-live-q1"
PINNED_COMMIT="692c2deb6a5681e43e15c6c24b91f0484a460ac5"
IMAGE="arb-gemini-btc5m-inventory-overnight:preflight-692c2de"
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
echo "OVERNIGHT_Q1_PREFLIGHT_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null

docker run --rm --network none "$IMAGE" python -m pytest -q \
  tests/test_gemini_short_crypto_inventory_overnight_supervisor.py \
  tests/test_gemini_short_crypto_inventory_settlement_reconcile.py \
  tests/test_gemini_short_crypto_inventory_rest_v3.py \
  tests/test_gemini_short_crypto_inventory_maker_q1.py \
  tests/test_gemini_short_crypto_btc5m_canary_v3.py

echo "OVERNIGHT_Q1_PREFLIGHT_SAFETY_TESTS_OK"

# Old live namespaces cannot arm overnight.
docker run --rm --network none \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_LIVE_Q1=YES \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ID=old \
  -e GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ARM=old \
  "$IMAGE" python - <<'PY'
from src import gemini_short_crypto_inventory_overnight_supervisor as s
assert s.live_armed() is False
print("OVERNIGHT_Q1_ARM_NAMESPACE_OK overnight_armed=false")
PY

# Exact-image authenticated DISARMED v3 market preflight.
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

# Overnight may start only from a completely clean account + local state.
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
    print(json.dumps({"type":"OVERNIGHT_Q1_CLEAN_START","clean":True,**snap,"order_entry":False,"mutation":False}, sort_keys=True))
asyncio.run(main())
PY

echo "OVERNIGHT_Q1_PREFLIGHT_COMPLETE order_entry=false mutation=false commit=$PINNED_COMMIT"
