#!/usr/bin/env bash
set -euo pipefail

# Dedicated AWS runner for the short-duration crypto research stack.
# SAFETY:
# - creates a separate worktree from ev-wallet-shadow
# - runs only public/read-only shadow modules
# - no credentials or env-file required
# - forces BOT_MODE=paper
# - does not stop, rename, or modify any sports-maker container

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/aws-short-crypto-shadow}"
BRANCH="${BRANCH:-ev-wallet-shadow}"
IMAGE="${IMAGE:-arb-short-crypto-shadow:latest}"

containers=(
  arb-short-poly-maker-ws
  arb-short-gemini-maker
  arb-short-gemini-fair-value
  arb-short-kalshi-temporal
  arb-short-kalshi-incentive
)

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
echo "SHORT_CRYPTO_SOURCE_READY branch=$BRANCH commit=$COMMIT"

docker build -t "$IMAGE" "$WORKTREE"
echo "SHORT_CRYPTO_IMAGE_READY image=$IMAGE"

for c in "${containers[@]}"; do
  docker rm -f "$c" >/dev/null 2>&1 || true
done

common=(
  --network host
  --restart unless-stopped
  -e BOT_MODE=paper
  -e PYTHONUNBUFFERED=1
)

docker run -d --name arb-short-poly-maker-ws "${common[@]}" "$IMAGE" \
  python -u -m src.polymarket_maker_ws_shadow \
    --sample-seconds 0.25 \
    --discovery-seconds 30 \
    --reward-seconds 30 \
    --summary-seconds 10 \
    --quote-size 10 \
    --min-rest-seconds 1 \
    --max-book-age-seconds 3

docker run -d --name arb-short-gemini-maker "${common[@]}" "$IMAGE" \
  python -u -m src.gemini_prediction_maker_shadow_v4 \
    --quote-size 10 \
    --min-rest-seconds 1 \
    --quote-refresh-seconds 8 \
    --discovery-seconds 30 \
    --summary-seconds 10

docker run -d --name arb-short-gemini-fair-value "${common[@]}" "$IMAGE" \
  python -u -m src.gemini_prediction_fair_value_shadow_v2 \
    --discovery-seconds 30 \
    --summary-seconds 5 \
    --vol-lookback-seconds 300 \
    --lag-seconds 30

docker run -d --name arb-short-kalshi-temporal "${common[@]}" "$IMAGE" \
  python -u -m src.kalshi_temporal_live_shadow_v2 \
    --poll-seconds 2.5 \
    --discovery-seconds 30 \
    --summary-seconds 15 \
    --min-net-edge 0.03 \
    --first-leg-max-price 0.45

docker run -d --name arb-short-kalshi-incentive "${common[@]}" "$IMAGE" \
  python -u -m src.kalshi_incentive_shadow \
    --poll-seconds 5 \
    --discovery-seconds 15 \
    --incentive-seconds 30 \
    --summary-seconds 10

sleep 5
failed=0
for c in "${containers[@]}"; do
  if docker ps --filter "name=^/${c}$" --filter status=running --format '{{.Names}}' | grep -qx "$c"; then
    echo "RUNNING $c"
  else
    echo "FAILED $c" >&2
    docker logs --tail 80 "$c" >&2 || true
    failed=1
  fi
done

if [ "$failed" -ne 0 ]; then
  echo "SHORT_CRYPTO_SHADOW_PARTIAL_FAILURE" >&2
  exit 1
fi

echo "SHORT_CRYPTO_SHADOW_STARTED commit=$COMMIT mode=paper"
echo "All five containers are read-only. No live order authority is enabled."
