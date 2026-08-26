#!/usr/bin/env bash
set -euo pipefail

# AWS parity runner for the current Railway sports maker shadow.
# Safety: read-only/paper only; does not alter the existing repo checkout or Railway.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/aws-sports-maker-shadow}"
ENV_FILE="${ENV_FILE:-/etc/arb/sports-maker-shadow.env}"
BRANCH="${BRANCH:-aws-migration-foundation}"
IMAGE="${IMAGE:-arb-sports-maker-shadow:aws-parity}"
CONTAINER="${CONTAINER:-arb-sports-maker-shadow-aws}"
BASE_STRATEGY_COMMIT="044204d31c98a098a180e726899162180556ef63"

if ! command -v git >/dev/null 2>&1; then
  echo "git is required" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required; install/start Docker on the EC2 host first" >&2
  exit 1
fi
if [ ! -d "$SOURCE_REPO/.git" ]; then
  echo "source repo not found at $SOURCE_REPO" >&2
  exit 1
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "missing $ENV_FILE; create it from secure AWS-managed values before starting" >&2
  exit 1
fi

sudo mkdir -p "$(dirname "$WORKTREE")"

git -C "$SOURCE_REPO" fetch origin "$BRANCH"
if [ -e "$WORKTREE/.git" ]; then
  git -C "$WORKTREE" fetch origin "$BRANCH"
  git -C "$WORKTREE" checkout --detach "origin/$BRANCH"
else
  sudo rm -rf "$WORKTREE"
  git -C "$SOURCE_REPO" worktree add --detach "$WORKTREE" "origin/$BRANCH"
fi

# The parity branch may contain AWS deployment files, but the trading code must
# remain byte-for-byte equivalent to the Railway strategy commit for this test.
for path in src config requirements.txt Dockerfile; do
  if ! git -C "$WORKTREE" diff --quiet "$BASE_STRATEGY_COMMIT" -- "$path"; then
    echo "strategy parity check failed: $path differs from $BASE_STRATEGY_COMMIT" >&2
    exit 1
  fi
done

docker build -t "$IMAGE" "$WORKTREE"

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

docker run -d \
  --name "$CONTAINER" \
  --network host \
  --restart unless-stopped \
  --env-file "$ENV_FILE" \
  -e BOT_MODE=paper \
  -e PYTHONUNBUFFERED=1 \
  "$IMAGE" \
  python -m src.sports_kp_generic_mlb_moneyline_maker_markout_shadow \
    --scan-ms 50 \
    --min-zero-fee-edge 0.001

echo "started $CONTAINER"
echo "logs: docker logs -f $CONTAINER"
