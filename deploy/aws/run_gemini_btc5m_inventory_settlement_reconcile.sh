#!/usr/bin/env bash
set -euo pipefail

# Venue-read-only settlement reconciliation for one unresolved Gemini BTC5m
# inventory-maker q=1 campaign. This may append authoritative local ledger events
# and clear the AWS-local reservation only after exact client IDs and Gemini settled
# position economics agree. It never places, cancels, or sells an order.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-btc5m-inventory-settlement-reconcile}"
BRANCH="feature/gemini-short-crypto-live-q1"
PINNED_COMMIT="aa0ed03cecaf37e1cf8fc89d88f0d4e6749cfc8f"
IMAGE="arb-gemini-btc5m-inventory-reconcile:aa0ed03"
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
echo "INVENTORY_SETTLEMENT_RECONCILE_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null
echo "INVENTORY_SETTLEMENT_RECONCILE_IMAGE_READY image=$IMAGE"

docker run --rm --network none "$IMAGE" \
  python -m pytest -q \
  tests/test_gemini_short_crypto_inventory_settlement_reconcile.py \
  tests/test_gemini_short_crypto_inventory_maker_q1.py \
  tests/test_gemini_short_crypto_btc5m_canary_v3.py

echo "INVENTORY_SETTLEMENT_RECONCILE_TESTS_OK"

echo "=== INVENTORY SETTLEMENT RECONCILIATION ==="
set +e
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_inventory_settlement_reconcile
rc=$?
set -e

echo "=== POST-RECONCILIATION READ-ONLY AUDIT ==="
docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_local_budget_audit || true

if [ "$rc" -ne 0 ]; then
  echo "INVENTORY_SETTLEMENT_RECONCILE_NOT_COMPLETE rc=$rc -- no venue mutation; do not start another inventory campaign while local_open_reservation=true" >&2
  exit "$rc"
fi

echo "INVENTORY_SETTLEMENT_RECONCILE_COMPLETE venue_mutation=false"
