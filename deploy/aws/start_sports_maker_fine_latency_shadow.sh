#!/usr/bin/env bash
set -euo pipefail

IMAGE="${IMAGE:-arb-sports-maker-shadow:aws-parity}"
ENV_FILE="${ENV_FILE:-/etc/arb/sports-maker-shadow.env}"
OLD_CONTAINER="${OLD_CONTAINER:-arb-sports-maker-shadow-aws}"
CONTAINER="${CONTAINER:-arb-sports-maker-fine-latency-aws}"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR docker_not_found" >&2
  exit 1
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "ERROR parity_image_missing image=$IMAGE" >&2
  exit 1
fi
if [[ ! -r "$ENV_FILE" ]]; then
  echo "ERROR env_file_unreadable path=$ENV_FILE" >&2
  exit 1
fi

# Preserve the completed 80/250/500ms checkpoint in the old container logs, but
# stop it so we do not maintain duplicate read-only venue connections while the
# finer latency curve runs.
if docker inspect "$OLD_CONTAINER" >/dev/null 2>&1; then
  docker stop "$OLD_CONTAINER" >/dev/null 2>&1 || true
  echo "COARSE_SHADOW_STOPPED name=$OLD_CONTAINER"
fi

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

# Strategy code is the exact already-built parity image. Only the telemetry delay
# tuple is overridden after importing the read-only markout module. No order
# transport is enabled; BOT_MODE is forced to paper.
docker run -d \
  --name "$CONTAINER" \
  --network host \
  --restart unless-stopped \
  --env-file "$ENV_FILE" \
  -e BOT_MODE=paper \
  -e PYTHONUNBUFFERED=1 \
  "$IMAGE" \
  python -c 'import logging; import src.sports_kp_generic_mlb_moneyline_maker_markout_shadow as m; m.DELAYS_MS=(20,40,60,80,120); logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s"); m.logger.warning("KPMM_FINE_LATENCY_ENABLED delays_ms=20,40,60,80,120 full_poly_ladder=true q1_vwap_sweep=true read_only=true order_entry=false"); m.impl.main()'

sleep 2
if ! docker ps --filter "name=^/${CONTAINER}$" --filter status=running --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "ERROR fine_latency_container_failed" >&2
  docker logs --tail 100 "$CONTAINER" >&2 || true
  exit 1
fi

echo "FINE_LATENCY_SHADOW_STARTED name=$CONTAINER delays_ms=20,40,60,80,120"
echo "READ_ONLY=TRUE"
echo "ORDER_ENTRY=FALSE"
echo "status: sudo /tmp/aws-fine-latency-status.sh"
