#!/usr/bin/env bash
set -euo pipefail

# DISARMED preflight for Gemini BTC5m canary #2.
# No order entry, no venue mutation, no local-ledger mutation.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-short-crypto-live-q1}"
BRANCH="feature/gemini-short-crypto-live-q1"
PINNED_COMMIT="282522639047b3d41b2f37cb29758b4fe7e39e65"
IMAGE="arb-gemini-short-btc5m-q1:canary2-preflight-2825226"
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
echo "CANARY2_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null
echo "CANARY2_IMAGE_READY image=$IMAGE"

# Offline safety regression suite on exact image.
docker run --rm --network none "$IMAGE" \
  python -m pytest -q \
  tests/test_gemini_short_crypto_quantity.py \
  tests/test_gemini_short_crypto_btc5m_canary_v3.py \
  tests/test_gemini_short_crypto_local_budget.py

echo "CANARY2_SAFETY_TESTS_OK"

# Prove the formerly-colliding long ID now yields distinct IDs.
docker run --rm --network none "$IMAGE" python - <<'PY'
from src.gemini_short_crypto_btc5m_maker_canary_q1_v3 import maker_client_ids
trade_id = "crypto-gem-btc5m-aws-local-btc5m-q1-20260827T172716Z-1253"
y, n = maker_client_ids(trade_id)
assert y != n and y.endswith("-yes") and n.endswith("-no")
assert len(y) <= 36 and len(n) <= 36
print(f"CANARY2_CLIENT_IDS_OK yes={y} no={n} distinct=true")
PY

# Authenticated BTC5m preflight forced DISARMED.
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_LIVE_Q1=NO \
  -e GEMINI_SHORT_CRYPTO_CANARY_ID= \
  -e GEMINI_SHORT_CRYPTO_CANARY_ARM= \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_btc5m_maker_canary_q1_v4

echo "CANARY2_MARKET_PREFLIGHT_OK order_entry=false"

# Read-only Gemini exposure + local reservation audit.
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_LIVE_Q1=NO \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_local_budget_audit

echo "CANARY2_PREFLIGHT_COMPLETE order_entry=false mutation=false commit=$PINNED_COMMIT"
