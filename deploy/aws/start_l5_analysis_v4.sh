#!/usr/bin/env bash
set -euo pipefail

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/l5-full-history-backtest}"
OUTPUT_DIR="${OUTPUT_DIR:-/opt/arb/l5-full-history-output}"
BRANCH="${BRANCH:-ev-wallet-shadow}"
IMAGE="${IMAGE:-arb-l5-full-history-backtest:latest}"
OLD_CONTAINER="${OLD_CONTAINER:-arb-l5-full-history-backtest-run}"
CONTAINER="${CONTAINER:-arb-l5-full-history-analyze-v4}"
DB="$OUTPUT_DIR/l5_full_history_spool.sqlite3"
OUT_JSON="$OUTPUT_DIR/l5_full_history_backtest_v4.json"
OUT_MARKETS="$OUTPUT_DIR/l5_full_history_markets_v4.jsonl"
OUT_LOG="$OUTPUT_DIR/l5_full_history_v4.log"

if [ ! -s "$DB" ]; then
  echo "L5_V4_MISSING_SPOOL db=$DB" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR" "$(dirname "$WORKTREE")"
git -C "$SOURCE_REPO" config --global --add safe.directory "$SOURCE_REPO" || true
git -C "$SOURCE_REPO" fetch origin "$BRANCH"

if [ -e "$WORKTREE/.git" ] || [ -f "$WORKTREE/.git" ]; then
  git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE" || true
fi
rm -rf "$WORKTREE"
git -C "$SOURCE_REPO" worktree prune
git -C "$SOURCE_REPO" worktree add --detach "$WORKTREE" "origin/$BRANCH"
COMMIT="$(git -C "$WORKTREE" rev-parse HEAD)"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null

# Stop only the obsolete full-history research analyzer. Never touch arb-short-* or sports containers.
docker rm -f "$OLD_CONTAINER" >/dev/null 2>&1 || true
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
rm -f "$OUT_JSON" "$OUT_MARKETS" "$OUT_LOG"

echo "L5_V4_STARTING commit=$COMMIT spool=$DB mode=single_pass_existing_spool"
docker run -d \
  --name "$CONTAINER" \
  --memory 384m \
  -e BOT_MODE=paper \
  -e PYTHONUNBUFFERED=1 \
  -v "$OUTPUT_DIR:/out" \
  "$IMAGE" \
  bash -lc "set -o pipefail; python -u -m src.polymarket_l5_full_history_analyze_v4 --db /out/l5_full_history_spool.sqlite3 --output /out/l5_full_history_backtest_v4.json --markets-jsonl /out/l5_full_history_markets_v4.jsonl 2>&1 | tee /out/l5_full_history_v4.log"

sleep 2
if docker ps --filter "name=^/${CONTAINER}$" --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "L5_V4_RUNNING container=$CONTAINER commit=$COMMIT memory_cap=384m"
  echo "Existing 680k-row spool preserved; no API refetch."
  exit 0
fi

STATUS="$(docker inspect -f '{{.State.ExitCode}}' "$CONTAINER" 2>/dev/null || echo unknown)"
OOM="$(docker inspect -f '{{.State.OOMKilled}}' "$CONTAINER" 2>/dev/null || echo unknown)"
if [ "$STATUS" = "0" ] && [ -s "$OUT_JSON" ]; then
  echo "L5_V4_COMPLETE commit=$COMMIT output=$OUT_JSON"
  exit 0
fi

echo "L5_V4_FAILED exit_code=$STATUS oom_killed=$OOM" >&2
tail -n 100 "$OUT_LOG" >&2 || true
exit 1
