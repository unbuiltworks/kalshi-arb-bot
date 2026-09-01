#!/usr/bin/env bash
set -euo pipefail

# Read-only one-shot launcher for the V4 pair-first Gemini l5 inventory shadow.
# Public market data only: no credential/env mounts and no authenticated APIs.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-l5-inventory-shadow-v4}"
STATE_DIR="${STATE_DIR:-/home/ssm-user/gemini-l5-inventory-shadow-v4-state}"
BRANCH="feature/l5-gemini-inventory-v4"
PINNED_COMMIT="${PINNED_COMMIT:-}"
CONTAINER="arb-gemini-l5-inventory-shadow-v4"
DURATION_SECONDS="${SHADOW_SECONDS:-1800}"
DISCOVER_S="${SHADOW_DISCOVER_S:-10}"
CHECKPOINT_S="${SHADOW_CHECKPOINT_S:-5}"
RECONNECT_S="${SHADOW_RECONNECT_S:-1}"

[ -n "$PINNED_COMMIT" ] || { echo "PINNED_COMMIT is required" >&2; exit 1; }
[[ "$PINNED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid PINNED_COMMIT" >&2; exit 1; }
[[ "$DURATION_SECONDS" =~ ^[0-9]+$ ]] || { echo "invalid SHADOW_SECONDS" >&2; exit 1; }

for cmd in git docker; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd is required" >&2; exit 1; }
done
[ -d "$SOURCE_REPO/.git" ] || { echo "source repo missing: $SOURCE_REPO" >&2; exit 1; }

if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  running="$(docker inspect -f '{{.State.Running}}' "$CONTAINER")"
  if [ "$running" = "true" ]; then
    echo "blocked: $CONTAINER already running" >&2
    exit 1
  fi
  docker rm "$CONTAINER" >/dev/null
fi

mkdir -p "$STATE_DIR"
chmod 0750 "$STATE_DIR" || true
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-${PINNED_COMMIT:0:8}"
RUN_DIR="$STATE_DIR/$RUN_ID"
mkdir -p "$RUN_DIR"
chmod 0750 "$RUN_DIR" || true

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

echo "L5_INVENTORY_V4_SOURCE_READY commit=$actual version=v4"

SHORT_SHA="${PINNED_COMMIT:0:12}"
IMAGE="arb-gemini-l5-inventory-shadow:v4-${SHORT_SHA}"
docker build -t "$IMAGE" "$WORKTREE" >/dev/null

# Pair-first strategy + online reconnect tests, with no network access.
docker run --rm --network none "$IMAGE" \
  python -m pytest -q \
    tests/test_gemini_l5_inventory_replay_v4.py \
    tests/test_gemini_l5_inventory_shadow_v4.py \
    tests/test_gemini_l5_inventory_policy.py \
    tests/test_gemini_l5_fair_value.py

echo "L5_INVENTORY_V4_TESTS_OK"
printf '%s\n' \
  "run_id=$RUN_ID" \
  "commit=$PINNED_COMMIT" \
  "version=v4" \
  "seconds=$DURATION_SECONDS" \
  "discover_s=$DISCOVER_S" \
  "checkpoint_s=$CHECKPOINT_S" \
  "reconnect_s=$RECONNECT_S" \
  "pair_floor_after_fees=0.03" \
  "max_naked_units_per_market=1" \
  "classification=SHADOW_NOT_REALIZED" \
  "credentials_loaded=false" \
  "order_entry=false" \
  > "$RUN_DIR/manifest.txt"

# Public network only. No --env-file and no credential mounts.
docker run -d --name "$CONTAINER" --network host --restart no \
  -e PYTHONUNBUFFERED=1 \
  -v "$RUN_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_l5_inventory_shadow_v4 \
    --seconds "$DURATION_SECONDS" \
    --discover-s "$DISCOVER_S" \
    --checkpoint-s "$CHECKPOINT_S" \
    --reconnect-s "$RECONNECT_S" \
    --output /state/capture.jsonl \
    --summary-output /state/summary.json \
    > "$RUN_DIR/container-id.txt"

container_id="$(cat "$RUN_DIR/container-id.txt")"
echo "L5_INVENTORY_V4_STARTED container=$CONTAINER container_id=${container_id:0:12} run_id=$RUN_ID seconds=$DURATION_SECONDS order_entry=false credentials_loaded=false"
sleep 2
docker ps -a --filter "name=^/${CONTAINER}$" --format 'L5_INVENTORY_V4_CONTAINER {{.Names}} {{.Status}} {{.Image}}'
docker logs --tail 20 "$CONTAINER" 2>&1 || true

echo "L5_INVENTORY_V4_RUN_DIR=$RUN_DIR"
