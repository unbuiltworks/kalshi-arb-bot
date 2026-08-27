#!/usr/bin/env bash
set -euo pipefail

# Isolated AWS preflight for the Gemini BTC 5m q=1 maker canary.
# SAFETY:
# - separate worktree/image/container from sports and other short-crypto shadows
# - public-only if secure env file is absent
# - if secure env exists, authenticated canary is still forcibly DISARMED
# - never uses --restart and never enables live order authority

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-short-crypto-live-q1}"
BRANCH="${BRANCH:-feature/gemini-short-crypto-live-q1}"
IMAGE="${IMAGE:-arb-gemini-short-btc5m-q1:latest}"
CONTAINER="${CONTAINER:-arb-gemini-short-btc5m-q1-preflight}"
ENV_FILE="${ENV_FILE:-/opt/arb/secrets/gemini-short-crypto.env}"

if ! command -v git >/dev/null 2>&1; then
  echo "git is required" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required" >&2
  exit 1
fi
if [ ! -d "$SOURCE_REPO/.git" ]; then
  echo "source repo not found at $SOURCE_REPO" >&2
  exit 1
fi

mkdir -p "$(dirname "$WORKTREE")"
git -C "$SOURCE_REPO" config --global --add safe.directory "$SOURCE_REPO" || true
git -C "$SOURCE_REPO" fetch origin "$BRANCH"

if [ -e "$WORKTREE/.git" ] || [ -f "$WORKTREE/.git" ]; then
  git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE" || true
fi
rm -rf "$WORKTREE"
git -C "$SOURCE_REPO" worktree prune
git -C "$SOURCE_REPO" worktree add --detach "$WORKTREE" "origin/$BRANCH"
COMMIT="$(git -C "$WORKTREE" rev-parse HEAD)"
echo "GEMINI_BTC5M_SOURCE_READY branch=$BRANCH commit=$COMMIT"

docker build -t "$IMAGE" "$WORKTREE"
echo "GEMINI_BTC5M_IMAGE_READY image=$IMAGE"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

common=(
  --network host
  --name "$CONTAINER"
  -e PYTHONUNBUFFERED=1
  -e BOT_MODE=paper
  -e GEMINI_SHORT_CRYPTO_LIVE_Q1=NO
  -e GEMINI_SHORT_CRYPTO_CANARY_ID=
  -e GEMINI_SHORT_CRYPTO_CANARY_ARM=
)

if [ -f "$ENV_FILE" ]; then
  mode="AUTHENTICATED_DISARMED"
  perms="$(stat -c '%a' "$ENV_FILE")"
  group_digit="${perms: -2:1}"
  other_digit="${perms: -1}"
  # Owner-only is ideal; group-read (640/440) is tolerated. Group write/execute
  # and all world permissions are rejected.
  if (( other_digit != 0 || group_digit == 2 || group_digit == 3 || group_digit == 6 || group_digit == 7 )); then
    echo "secure env file permissions too broad: $ENV_FILE mode=$perms" >&2
    exit 1
  fi
  echo "GEMINI_BTC5M_SECURE_ENV_PRESENT path=$ENV_FILE mode=$perms"
  docker run "${common[@]}" --env-file "$ENV_FILE" \
    -e GEMINI_SHORT_CRYPTO_LIVE_Q1=NO \
    -e GEMINI_SHORT_CRYPTO_CANARY_ID= \
    -e GEMINI_SHORT_CRYPTO_CANARY_ARM= \
    "$IMAGE" python -u -m src.gemini_short_crypto_btc5m_maker_canary_q1
else
  mode="PUBLIC_ONLY"
  echo "GEMINI_BTC5M_SECURE_ENV_ABSENT path=$ENV_FILE"
  docker run "${common[@]}" "$IMAGE" \
    python -u -m src.gemini_short_crypto_btc5m_public_preflight
fi

echo "GEMINI_BTC5M_PREFLIGHT_COMPLETE mode=$mode commit=$COMMIT order_entry=false"
