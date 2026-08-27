#!/usr/bin/env bash
set -euo pipefail

# One-off public-data backtest for the l5 Polymarket wallet.
# SAFETY:
# - separate worktree and Docker image from sports and short-crypto hot shadows
# - read-only public Polymarket APIs only
# - does not stop/restart/modify any running container
# - writes detailed results under /opt/arb/l5-full-history-output

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/l5-full-history-backtest}"
OUTPUT_DIR="${OUTPUT_DIR:-/opt/arb/l5-full-history-output}"
BRANCH="${BRANCH:-ev-wallet-shadow}"
IMAGE="${IMAGE:-arb-l5-full-history-backtest:latest}"
START="${START:-2026-01-01T00:00:00Z}"

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

mkdir -p "$(dirname "$WORKTREE")" "$OUTPUT_DIR"
git -C "$SOURCE_REPO" config --global --add safe.directory "$SOURCE_REPO" || true
git -C "$SOURCE_REPO" fetch origin "$BRANCH"

if [ -e "$WORKTREE/.git" ] || [ -f "$WORKTREE/.git" ]; then
  git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE" || true
fi
rm -rf "$WORKTREE"
git -C "$SOURCE_REPO" worktree prune
git -C "$SOURCE_REPO" worktree add --detach "$WORKTREE" "origin/$BRANCH"

COMMIT="$(git -C "$WORKTREE" rev-parse HEAD)"
echo "L5_BACKTEST_SOURCE_READY branch=$BRANCH commit=$COMMIT"

docker build -t "$IMAGE" "$WORKTREE"
echo "L5_BACKTEST_IMAGE_READY image=$IMAGE"

OUT_JSON="$OUTPUT_DIR/l5_full_history_backtest.json"
OUT_LOG="$OUTPUT_DIR/l5_full_history_summary.log"
rm -f "$OUT_JSON" "$OUT_LOG"

# This may take several minutes because the wallet is high volume and the script
# refuses to silently truncate Polymarket's public activity pagination.
docker run --rm \
  --network host \
  -e BOT_MODE=paper \
  -e PYTHONUNBUFFERED=1 \
  -v "$OUTPUT_DIR:/out" \
  "$IMAGE" \
  python -u -m src.polymarket_l5_full_history_backtest \
    --start "$START" \
    --end now \
    --output /out/l5_full_history_backtest.json \
  | tee "$OUT_LOG"

if [ ! -s "$OUT_JSON" ]; then
  echo "L5_FULL_HISTORY_FAILED output_missing=$OUT_JSON" >&2
  exit 1
fi

echo "L5_FULL_HISTORY_COMPLETE commit=$COMMIT"
echo "detail_json=$OUT_JSON"
echo "summary_log=$OUT_LOG"
echo "No orders were placed. Public-data backtest only."
