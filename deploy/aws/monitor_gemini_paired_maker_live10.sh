#!/usr/bin/env bash
set -euo pipefail

NAME="${NAME:-arb-gemini-paired-maker-live10}"
MAX_CHECKS="${MAX_CHECKS:-320}"
SLEEP_SECONDS="${SLEEP_SECONDS:-15}"

if ! docker inspect "$NAME" >/dev/null 2>&1; then
  echo "PAIRED_MAKER_LIVE10_CONTAINER_MISSING"
  exit 2
fi

for i in $(seq 1 "$MAX_CHECKS"); do
  running="$(docker inspect -f '{{.State.Running}}' "$NAME")"
  echo "PAIRED_MAKER_LIVE10_MONITOR n=$i at=$(date -u +%Y-%m-%dT%H:%M:%SZ) running=$running"
  docker logs --tail 20 "$NAME" 2>&1 | grep 'GEMINI_PAIRED_MAKER_LIVE_10' | tail -5 || true
  if [ "$running" != "true" ]; then
    break
  fi
  sleep "$SLEEP_SECONDS"
done

running="$(docker inspect -f '{{.State.Running}}' "$NAME")"
status="$(docker inspect -f '{{.State.Status}}' "$NAME")"
exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$NAME")"
image="$(docker inspect -f '{{.Config.Image}}' "$NAME")"
echo "PAIRED_MAKER_LIVE10_FINAL status=$status running=$running exit=$exit_code image=$image started=$(docker inspect -f '{{.State.StartedAt}}' "$NAME") finished=$(docker inspect -f '{{.State.FinishedAt}}' "$NAME")"
echo "PAIRED_MAKER_LIVE10_LOGS_BEGIN"
logs="$(docker logs --tail 3000 "$NAME" 2>&1 || true)"
printf '%s\n' "$logs"
echo "PAIRED_MAKER_LIVE10_LOGS_END"

if [ "$running" = "true" ]; then
  echo "PAIRED_MAKER_LIVE10_MONITOR_TIMEOUT_CONTAINER_STILL_RUNNING"
  exit 3
fi
if [ "$status" != "exited" ] || [ "$exit_code" != "0" ]; then
  echo "PAIRED_MAKER_LIVE10_PROCESS_NOT_CLEAN"
  exit 4
fi
if ! printf '%s\n' "$logs" | grep -Eq '"stage":"(SETTLED|FLATTENED|COMPLETE_NO_SETTLED_TRADE)"'; then
  echo "PAIRED_MAKER_LIVE10_TERMINAL_STAGE_MISSING"
  exit 5
fi

ENV_FILE=/opt/arb/secrets/gemini-short-crypto.env
if [ ! -f "$ENV_FILE" ]; then
  ENV_FILE=/home/ssm-user/kalshi-arb-bot/.env
fi
STATE_DIR=/home/ssm-user/gemini-short-crypto-state
if [ ! -d "$STATE_DIR" ]; then
  STATE_DIR=/opt/arb/gemini-short-crypto-state
fi

snapshot="$(docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$image" \
  python -c 'import asyncio,json; from src import gemini_short_crypto_local_budget as b; s=asyncio.run(b.account_exposure_snapshot()); print(json.dumps(s,sort_keys=True)); raise SystemExit(1 if s["active_short_crypto_orders"] or s["active_short_crypto_positions"] else 0)')"
echo "PAIRED_MAKER_LIVE10_TERMINAL_ACCOUNT $snapshot"
echo "PAIRED_MAKER_LIVE10_CLEAN_TERMINAL_OK"
