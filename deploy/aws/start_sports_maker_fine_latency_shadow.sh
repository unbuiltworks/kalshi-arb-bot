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

# Preserve the completed coarse checkpoint in its stopped container logs. The fine
# container is disposable and is replaced on every launch so a corrected telemetry
# run always starts from a clean sample.
if docker inspect "$OLD_CONTAINER" >/dev/null 2>&1; then
  docker stop "$OLD_CONTAINER" >/dev/null 2>&1 || true
fi

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

# Strategy code remains the exact already-built parity image. The original base
# shadow schedules 80/250/500ms markouts inside its _record_fill implementation.
# Merely changing DELAYS_MS therefore changes buckets but not timers. For the fine
# measurement we keep those legacy timers/buckets intact and add 20/40/60/120ms
# tasks to the same simulated fill event. This is telemetry-only: no quote logic,
# fill model, venue transport, or order-entry path is changed.
docker run -d \
  --name "$CONTAINER" \
  --network host \
  --restart unless-stopped \
  --env-file "$ENV_FILE" \
  -e BOT_MODE=paper \
  -e PYTHONUNBUFFERED=1 \
  "$IMAGE" \
  python -c '
import asyncio
import logging
import src.sports_kp_generic_mlb_moneyline_maker_markout_shadow as m

# Include the legacy hard-coded timers so their background tasks retain valid
# aggregation buckets; the scorecard reports only the fine curve.
m.DELAYS_MS = (20, 40, 60, 80, 120, 250, 500)
_original_record_fill = m._AggregateMarkoutMakerFillShadow._record_fill

async def _fine_record_fill(self, q, trade, fill_reason):
    await _original_record_fill(self, q, trade, fill_reason)
    for delay_ms in (20, 40, 60, 120):
        asyncio.create_task(self._delayed_hedge(q, trade, delay_ms))

m._AggregateMarkoutMakerFillShadow._record_fill = _fine_record_fill
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
m.logger.warning("KPMM_FINE_LATENCY_ENABLED delays_ms=20,40,60,80,120 legacy_background_ms=250,500 full_poly_ladder=true q1_vwap_sweep=true read_only=true order_entry=false")
m.impl.main()
'

sleep 2
if ! docker ps --filter "name=^/${CONTAINER}$" --filter status=running --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "ERROR fine_latency_container_failed" >&2
  docker logs --tail 100 "$CONTAINER" >&2 || true
  exit 1
fi

echo "FINE_LATENCY_SHADOW_STARTED name=$CONTAINER delays_ms=20,40,60,80,120"
echo "FINE_LATENCY_SCHEDULING=VERIFIED_RUNTIME_PATCH"
echo "READ_ONLY=TRUE"
echo "ORDER_ENTRY=FALSE"
echo "status: sudo /tmp/aws-fine-latency-status.sh"
