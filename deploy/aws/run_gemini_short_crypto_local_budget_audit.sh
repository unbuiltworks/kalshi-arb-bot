#!/usr/bin/env bash
set -euo pipefail

# Read-only AWS audit for the Gemini BTC5m local-ledger fallback.
# No order entry, no account mutation, no local ledger mutation.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-short-crypto-live-q1}"
BRANCH="feature/gemini-short-crypto-live-q1"
PINNED_COMMIT="b961c74f8284db824a0d121173a58780fa1f6016"
IMAGE="arb-gemini-short-btc5m-q1:audit-b961c74"
ENV_FILE="${ENV_FILE:-/opt/arb/secrets/gemini-short-crypto.env}"
STATE_DIR="${STATE_DIR:-/opt/arb/gemini-short-crypto-state}"

for cmd in git docker; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd is required" >&2; exit 1; }
done
[ -d "$SOURCE_REPO/.git" ] || { echo "source repo missing: $SOURCE_REPO" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "secure env missing: $ENV_FILE" >&2; exit 1; }

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

docker build -t "$IMAGE" "$WORKTREE" >/dev/null

docker run --rm --network none "$IMAGE" \
  python -m pytest -q \
  tests/test_gemini_short_crypto_quantity.py \
  tests/test_gemini_short_crypto_local_budget.py

echo "LOCAL_BUDGET_TESTS_OK"

docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_LIVE_Q1=NO \
  -e GEMINI_SHORT_CRYPTO_CANARY_ID= \
  -e GEMINI_SHORT_CRYPTO_CANARY_ARM= \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_short_crypto_local_budget_audit

echo "LOCAL_BUDGET_AUDIT_COMPLETE order_entry=false mutation=false commit=$PINNED_COMMIT"
