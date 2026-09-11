#!/usr/bin/env bash
set -euo pipefail

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
BRANCH="${BRANCH:-aws-migration-foundation}"
BASE_STRATEGY_COMMIT="044204d31c98a098a180e726899162180556ef63"
IMAGE="${IMAGE:-arb-sports-maker-shadow:aws-parity}"
ENV_FILE="${ENV_FILE:-/etc/arb/sports-maker-shadow.env}"
CONTAINER="${CONTAINER:-arb-poly-maker-kalshi-hedge-shadow-aws}"
SCRIPT_PATH="${SCRIPT_PATH:-/opt/arb/poly_maker_kalshi_hedge_shadow.py}"
STATUS_PATH="${STATUS_PATH:-/tmp/aws-poly-maker-kalshi-status.sh}"

for cmd in git docker; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR ${cmd}_not_found" >&2; exit 1; }
done
[[ -d "$SOURCE_REPO/.git" ]] || { echo "ERROR source_repo_missing path=$SOURCE_REPO" >&2; exit 1; }
[[ -r "$ENV_FILE" ]] || { echo "ERROR env_file_unreadable path=$ENV_FILE" >&2; exit 1; }
docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "ERROR parity_image_missing image=$IMAGE" >&2; exit 1; }

git -C "$SOURCE_REPO" fetch origin "$BRANCH" feature/sports-kp-live-executor-impl
for path in src config requirements.txt Dockerfile; do
  if ! git -C "$SOURCE_REPO" diff --quiet "$BASE_STRATEGY_COMMIT" "origin/$BRANCH" -- "$path"; then
    echo "ERROR strategy_parity_failed path=$path base=$BASE_STRATEGY_COMMIT" >&2
    exit 1
  fi
done
echo "STRATEGY_PARITY_OK commit=$BASE_STRATEGY_COMMIT"

mkdir -p "$(dirname "$SCRIPT_PATH")"
git -C "$SOURCE_REPO" show "origin/$BRANCH:deploy/aws/poly_maker_kalshi_hedge_shadow.py" > "$SCRIPT_PATH"
git -C "$SOURCE_REPO" show "origin/$BRANCH:deploy/aws/status_poly_maker_kalshi_hedge_shadow.sh" > "$STATUS_PATH"
chmod 0444 "$SCRIPT_PATH"
chmod 0755 "$STATUS_PATH"

# Validate parser/fill semantics inside the exact parity image before starting network work.
docker run --rm --network none \
  -e BOT_MODE=paper \
  -v "$SCRIPT_PATH:/tmp/poly_maker_kalshi_hedge_shadow.py:ro" \
  "$IMAGE" \
  python /tmp/poly_maker_kalshi_hedge_shadow.py --self-test

echo "PKMM_RUNTIME_SELF_TEST_OK"

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

docker run -d \
  --name "$CONTAINER" \
  --network host \
  --restart unless-stopped \
  --env-file "$ENV_FILE" \
  -e BOT_MODE=paper \
  -e PYTHONUNBUFFERED=1 \
  -v "$SCRIPT_PATH:/tmp/poly_maker_kalshi_hedge_shadow.py:ro" \
  "$IMAGE" \
  python /tmp/poly_maker_kalshi_hedge_shadow.py \
    --scan-ms 50 \
    --min-edge 0.001

sleep 3
if ! docker ps --filter "name=^/${CONTAINER}$" --filter status=running --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "ERROR pkmm_container_failed" >&2
  docker logs --tail 120 "$CONTAINER" >&2 || true
  exit 1
fi

echo "PKMM_SHADOW_STARTED name=$CONTAINER"
echo "ROUTE=POLYMARKET_MAKER_TO_KALSHI_TAKER"
echo "DELAYS_MS=0,20,40,60,80,120,250,500"
echo "READ_ONLY=TRUE"
echo "ORDER_ENTRY=FALSE"
echo "CAPACITY_CREDIT_USD_PER_DAY=0"
echo "status: sudo $STATUS_PATH"
