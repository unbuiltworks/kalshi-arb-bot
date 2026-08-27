#!/usr/bin/env bash
set -euo pipefail

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/l5-full-history-backtest}"
OUTPUT_DIR="${OUTPUT_DIR:-/opt/arb/l5-full-history-output}"
BRANCH="${BRANCH:-ev-wallet-shadow}"
IMAGE="${IMAGE:-arb-l5-full-history-backtest:latest}"
CONTAINER="${CONTAINER:-arb-l5-full-history-backtest-run}"
START="${START:-2026-01-01T00:00:00Z}"

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

docker build -t "$IMAGE" "$WORKTREE" >/dev/null
echo "L5_BACKTEST_IMAGE_READY image=$IMAGE"

OUT_JSON="$OUTPUT_DIR/l5_full_history_backtest.json"
OUT_LOG="$OUTPUT_DIR/l5_full_history_summary.log"
rm -f "$OUT_JSON" "$OUT_LOG"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

echo "L5_BACKTEST_STARTING container=$CONTAINER start=$START"
docker run -d \
  --name "$CONTAINER" \
  --network host \
  -e BOT_MODE=paper \
  -e PYTHONUNBUFFERED=1 \
  -v "$OUTPUT_DIR:/out" \
  "$IMAGE" \
  bash -lc "set -o pipefail; python -u -m src.polymarket_l5_full_history_backtest --start '$START' --end now --output /out/l5_full_history_backtest.json 2>&1 | tee /out/l5_full_history_summary.log"

sleep 2
if docker ps --filter "name=^/${CONTAINER}$" --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "L5_BACKTEST_RUNNING container=$CONTAINER commit=$COMMIT"
  echo "The job is detached and will continue after SSM disconnects."
  exit 0
fi

STATUS="$(docker inspect -f '{{.State.ExitCode}}' "$CONTAINER" 2>/dev/null || echo unknown)"
if [ "$STATUS" = "0" ] && [ -s "$OUT_JSON" ]; then
  echo "L5_FULL_HISTORY_COMPLETE commit=$COMMIT"
  echo "detail_json=$OUT_JSON"
  exit 0
fi

echo "L5_BACKTEST_FAILED exit_code=$STATUS" >&2
if [ -s "$OUT_LOG" ]; then
  echo "=== SUMMARY LOG ===" >&2
  tail -n 100 "$OUT_LOG" >&2 || true
else
  docker logs --tail 100 "$CONTAINER" >&2 || true
fi
exit 1
