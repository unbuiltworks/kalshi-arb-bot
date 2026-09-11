#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${CONTAINER:-arb-sports-maker-shadow-aws}"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR docker_not_found"
  exit 1
fi

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "ERROR container_not_found name=$CONTAINER"
  exit 1
fi

running="$(docker inspect -f '{{.State.Running}}' "$CONTAINER")"
started_at="$(docker inspect -f '{{.State.StartedAt}}' "$CONTAINER")"
restart_count="$(docker inspect -f '{{.RestartCount}}' "$CONTAINER")"

echo "AWS_SHADOW_CONTAINER name=$CONTAINER running=$running started_at=$started_at restart_count=$restart_count"

if [[ "$running" != "true" ]]; then
  echo "ERROR container_not_running"
  docker logs --tail 80 "$CONTAINER" 2>&1 || true
  exit 1
fi

LOG_FILE="$(mktemp)"
trap 'rm -f "$LOG_FILE"' EXIT
docker logs "$CONTAINER" >"$LOG_FILE" 2>&1

python3 - "$LOG_FILE" <<'PY'
import json
import sys

path = sys.argv[1]
fill = None
mark = None
with open(path, "r", encoding="utf-8", errors="replace") as fh:
    for line in fh:
        if "KPMM_FILL_SHADOW_SUMMARY " in line:
            try:
                fill = json.loads(line.split("KPMM_FILL_SHADOW_SUMMARY ", 1)[1])
            except Exception:
                pass
        elif "KPMM_DELAYED_MARKOUT_SUMMARY " in line:
            try:
                mark = json.loads(line.split("KPMM_DELAYED_MARKOUT_SUMMARY ", 1)[1])
            except Exception:
                pass

if fill is None:
    print("AWS_SHADOW_SCORE status=no_fill_summary_yet")
    raise SystemExit(0)

print(
    "AWS_SHADOW_FILL "
    f"pairs={fill.get('matched_moneyline_pairs')} "
    f"active_quotes={fill.get('active_shadow_quotes')} "
    f"fills={fill.get('simulated_q1_fills')} "
    f"trade_through={fill.get('trade_through_fills')} "
    f"exact_price={fill.get('exact_price_fills')} "
    f"median_quote_age_ms={fill.get('median_quote_age_at_fill_ms')} "
    f"poly_connected={fill.get('poly_connected')} "
    f"poly_ws_updates={fill.get('poly_ws_updates')} "
    f"hedge_missing_at_fill={fill.get('hedge_missing_at_fill')} "
    f"read_only={fill.get('read_only')} "
    f"order_entry={fill.get('order_entry')}"
)

if mark is None:
    print("AWS_SHADOW_MARKOUT status=no_markout_summary_yet")
    raise SystemExit(0)

print(
    "AWS_SHADOW_IMMEDIATE "
    f"fills={mark.get('q1_sweep_immediate_executable')} "
    f"pnl_usd={mark.get('q1_sweep_immediate_pnl_usd')}"
)

for key in ("t80ms", "t250ms", "t500ms"):
    row = mark.get(key) or {}
    print(
        f"AWS_SHADOW_{key.upper()} "
        f"completed={row.get('completed')} "
        f"executable={row.get('executable')} "
        f"coverage={row.get('executable_coverage')} "
        f"ws_unhealthy={row.get('ws_unhealthy')} "
        f"depth_insufficient={row.get('depth_insufficient')} "
        f"pnl_usd={row.get('executable_pnl_usd')} "
        f"avg_pnl_per_executable_fill={row.get('avg_pnl_per_executable_fill')} "
        f"gte_3c={row.get('gte_3c')}"
    )
PY
