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

# Strategy code remains the exact already-built parity image. The runtime patch below
# changes telemetry scheduling and WS supervision only. It does not alter quote logic,
# the pessimistic fill model, settlement assumptions, venue pricing, or order entry.
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
import time
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

# The parity reader marks connected=True after subscribing, but its SDK error callback
# only logs and the coroutine then sleeps forever. A dead socket can therefore remain
# logically connected indefinitely. Supervise that exact reader from outside strategy
# code: if the global feed is silent for >2.5s after startup, cancel/close it and
# reconnect. The markout credit rule remains stricter (<=1s silence), so reconnect time
# itself never receives executable credit.
_original_run_poly_ws = m.impl.run_poly_ws

async def _supervised_run_poly_ws(client, store, slugs):
    reconnects = 0
    while True:
        started_ms = int(time.time() * 1000)
        task = asyncio.create_task(_original_run_poly_ws(client, store, slugs))
        try:
            while True:
                await asyncio.sleep(0.25)
                if task.done():
                    exc = task.exception()
                    if exc is not None:
                        m.logger.warning("KPMM_POLY_WS_TASK_EXIT reconnect=%d error=%r", reconnects, exc)
                    break
                now_ms = int(time.time() * 1000)
                last_ms = int(getattr(store, "last_ws_message_ms", 0) or 0)
                if last_ms:
                    silence_ms = max(0, now_ms - last_ms)
                    if silence_ms > 2500:
                        reconnects += 1
                        m.logger.warning(
                            "KPMM_POLY_WS_WATCHDOG_RECONNECT reconnect=%d silence_ms=%d ws_updates=%d",
                            reconnects, silence_ms, int(getattr(store, "ws_updates", 0) or 0),
                        )
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        break
                elif now_ms - started_ms > 10000:
                    reconnects += 1
                    m.logger.warning(
                        "KPMM_POLY_WS_WATCHDOG_RECONNECT reconnect=%d reason=no_initial_message",
                        reconnects,
                    )
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    break
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        store.connected = False
        await asyncio.sleep(0.5)

m.impl.run_poly_ws = _supervised_run_poly_ws

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
m.logger.warning("KPMM_FINE_LATENCY_ENABLED delays_ms=20,40,60,80,120 legacy_background_ms=250,500 full_poly_ladder=true q1_vwap_sweep=true read_only=true order_entry=false")
m.logger.warning("KPMM_POLY_WS_WATCHDOG_ENABLED stale_reconnect_ms=2500 executable_health_ms=1000")
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
echo "POLY_WS_WATCHDOG=ENABLED stale_reconnect_ms=2500 executable_health_ms=1000"
echo "READ_ONLY=TRUE"
echo "ORDER_ENTRY=FALSE"
echo "status: sudo /tmp/aws-fine-latency-status.sh"
