#!/usr/bin/env bash
set -euo pipefail
NAME="${NAME:-arb-gemini-paired-maker-live10}"
image="$(docker inspect -f '{{.Config.Image}}' "$NAME")"
ENV_FILE=/opt/arb/secrets/gemini-short-crypto.env
[ -f "$ENV_FILE" ] || ENV_FILE=/home/ssm-user/kalshi-arb-bot/.env
STATE_DIR=/home/ssm-user/gemini-short-crypto-state
[ -d "$STATE_DIR" ] || STATE_DIR=/opt/arb/gemini-short-crypto-state

docker run -i --rm --network host \
  --env-file "$ENV_FILE" \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$image" python -u - <<'PY'
import asyncio, json, time
import httpx
from src.gemini_prediction_auth import private_rest_request
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base

PREFIXES = ("GEMI-BTC05M", "GEMI-ETH05M", "GEMI-SOL05M", "GEMI-XRP05M")
TARGET_SYMBOL = "GEMI-BTC05M2608311245-UP"
TARGET_TICKER = "BTC05M2608311245"
ORDER_KEYS = (
    "symbol","eventOutcome","side","price","quantity","filledQuantity",
    "remainingQuantity","avgExecutionPrice","fee","fees","status","clientOrderId",
)
POSITION_KEYS = (
    "symbol","eventOutcome","outcome","side","totalQuantity","availableQuantity",
    "avgEntryPrice","averageEntryPrice","cost","status",
)

def keep(row, keys):
    return {k: row.get(k) for k in keys if k in row}

def short(row):
    return str(row.get("symbol") or "").upper().startswith(PREFIXES)

async def post(path, params):
    r = await private_rest_request("POST", path, params=params, timeout=5.0)
    if r.status_code != 200:
        raise RuntimeError(f"snapshot failed {path} http={r.status_code}")
    return r.json()

async def main():
    active = await post("/v1/prediction-markets/orders/active", {"limit":100,"offset":0})
    positions = await post("/v1/prediction-markets/positions", {"limit":1000,"offset":0,"sort":"+expiryDate"})
    now_ms = int(time.time()*1000)
    filled = await post("/v1/prediction-markets/orders/history", {
        "status":"filled","symbol":TARGET_SYMBOL,"limit":100,"from":now_ms-30*60*1000,"to":now_ms+1000,
    })
    cancelled = await post("/v1/prediction-markets/orders/history", {
        "status":"cancelled","symbol":TARGET_SYMBOL,"limit":100,"from":now_ms-30*60*1000,"to":now_ms+1000,
    })
    available = str(await base._gemini_available_usd())
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as h:
        r = await h.get(f"https://api.gemini.com/v1/prediction-markets/events/{TARGET_TICKER}")
        r.raise_for_status()
        event = r.json()
    contract = next((c for c in event.get("contracts",[]) if isinstance(c,dict) and c.get("instrumentSymbol") == TARGET_SYMBOL), {})
    orders = [keep(x, ORDER_KEYS) for x in active.get("orders",[]) if isinstance(x,dict) and short(x)]
    pos = [keep(x, POSITION_KEYS) for x in positions.get("positions",[]) if isinstance(x,dict) and short(x) and str(x.get("totalQuantity") or "0") != "0"]
    hist = [keep(x, ORDER_KEYS) for x in filled.get("orders",[]) if isinstance(x,dict)]
    canc = [keep(x, ORDER_KEYS) for x in cancelled.get("orders",[]) if isinstance(x,dict)]
    out = {
        "active_orders": orders,
        "positions": pos,
        "available_usd": available,
        "target_event": {
            "ticker": TARGET_TICKER,
            "status": event.get("status"),
            "volume": event.get("volume"),
            "resolvedAt": event.get("resolvedAt"),
            "contract": {k: contract.get(k) for k in ("instrumentSymbol","status","resolutionSide","resolvedAt") if k in contract},
        },
        "filled_history": hist,
        "cancelled_history_count": len(canc),
    }
    print("PAIRED_MAKER_LIVE10_RECONCILE " + json.dumps(out, sort_keys=True))

asyncio.run(main())
PY
