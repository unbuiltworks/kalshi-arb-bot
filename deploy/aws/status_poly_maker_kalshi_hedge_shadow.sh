#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${CONTAINER:-arb-poly-maker-kalshi-hedge-shadow-aws}"
command -v docker >/dev/null 2>&1 || { echo "ERROR docker_not_found"; exit 1; }
docker inspect "$CONTAINER" >/dev/null 2>&1 || { echo "ERROR container_not_found name=$CONTAINER"; exit 1; }

running="$(docker inspect -f '{{.State.Running}}' "$CONTAINER")"
started_at="$(docker inspect -f '{{.State.StartedAt}}' "$CONTAINER")"
restart_count="$(docker inspect -f '{{.RestartCount}}' "$CONTAINER")"
echo "AWS_PKMM_CONTAINER name=$CONTAINER running=$running started_at=$started_at restart_count=$restart_count"
if [[ "$running" != "true" ]]; then
  docker logs --tail 120 "$CONTAINER" 2>&1 || true
  exit 1
fi

log="$(mktemp)"; trap 'rm -f "$log"' EXIT
docker logs "$CONTAINER" > "$log" 2>&1
python3 - "$log" <<'PY'
import json, sys
fill = mark = start = None
with open(sys.argv[1], encoding="utf-8", errors="replace") as fh:
    for line in fh:
        if "PKMM_START " in line:
            try: start = json.loads(line.split("PKMM_START ", 1)[1])
            except Exception: pass
        elif "PKMM_FILL_SHADOW_SUMMARY " in line:
            try: fill = json.loads(line.split("PKMM_FILL_SHADOW_SUMMARY ", 1)[1])
            except Exception: pass
        elif "PKMM_MARKOUT_SUMMARY " in line:
            try: mark = json.loads(line.split("PKMM_MARKOUT_SUMMARY ", 1)[1])
            except Exception: pass

if start:
    print("AWS_PKMM_CONFIG " + " ".join([
        f"pairs={start.get('matched_pairs')}", f"poly_slugs={start.get('poly_slugs')}",
        f"kalshi_tickers={start.get('kalshi_tickers')}", f"scan_ms={start.get('scan_ms')}",
        f"min_edge={start.get('min_edge')}", f"read_only={start.get('read_only')}",
        f"order_entry={start.get('order_entry')}",
    ]))
if fill is None:
    print("AWS_PKMM_SCORE status=no_summary_yet")
    raise SystemExit(0)
print("AWS_PKMM_FILL " + " ".join([
    f"pairs={fill.get('matched_pairs')}", f"active_quotes={fill.get('active_shadow_quotes')}",
    f"gte_1c_quotes={fill.get('current_quote_gte_1c')}", f"gte_2c_quotes={fill.get('current_quote_gte_2c')}",
    f"gte_3c_quotes={fill.get('current_quote_gte_3c')}", f"fills={fill.get('simulated_q1_fills')}",
    f"fills_by_family={json.dumps(fill.get('fills_by_family'), separators=(',', ':'))}",
    f"trade_through={fill.get('trade_through_fills')}", f"exact_price={fill.get('exact_price_fills')}",
    f"median_quote_age_ms={fill.get('median_quote_age_at_fill_ms')}",
    f"poly_trades={fill.get('poly_trade_updates')}", f"poly_connected={fill.get('poly_connected')}",
    f"poly_reconnects={fill.get('poly_ws_reconnects')}", f"kalshi_connected={fill.get('kalshi_connected')}",
    f"kalshi_reconnects={fill.get('kalshi_ws_reconnects')}", f"hedge_missing_at_fill={fill.get('hedge_missing_at_fill')}",
    f"read_only={fill.get('read_only')}", f"order_entry={fill.get('order_entry')}",
]))
if mark is None:
    print("AWS_PKMM_MARKOUT status=no_markout_summary_yet")
    raise SystemExit(0)
for delay in (0,20,40,60,80,120,250,500):
    row = mark.get(f"t{delay}ms") or {}
    print(f"AWS_PKMM_T{delay}MS " + " ".join([
        f"completed={row.get('completed')}", f"executable={row.get('executable')}",
        f"coverage={row.get('executable_coverage')}", f"ws_unhealthy={row.get('ws_unhealthy')}",
        f"hedge_missing={row.get('hedge_missing')}", f"depth_insufficient={row.get('depth_insufficient')}",
        f"pnl_usd={row.get('executable_pnl_usd')}", f"avg_pnl_per_executable_fill={row.get('avg_pnl_per_executable_fill')}",
        f"gte_1c={row.get('gte_1c')}", f"gte_2c={row.get('gte_2c')}", f"gte_3c={row.get('gte_3c')}",
    ]))
PY
