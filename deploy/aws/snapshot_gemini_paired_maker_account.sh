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
import asyncio, json
from src.gemini_prediction_auth import private_rest_request

PREFIXES = ("GEMI-BTC05M", "GEMI-ETH05M", "GEMI-SOL05M", "GEMI-XRP05M")
ORDER_KEYS = ("symbol","eventOutcome","side","price","quantity","filledQuantity","remainingQuantity","status","clientOrderId")
POSITION_KEYS = ("symbol","eventOutcome","outcome","side","totalQuantity","availableQuantity","avgEntryPrice","averageEntryPrice","cost","status")

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
    orders = [keep(x, ORDER_KEYS) for x in active.get("orders",[]) if isinstance(x,dict) and short(x)]
    pos = [keep(x, POSITION_KEYS) for x in positions.get("positions",[]) if isinstance(x,dict) and short(x) and str(x.get("totalQuantity") or "0") != "0"]
    print("PAIRED_MAKER_LIVE10_EXPOSURE " + json.dumps({"active_orders":orders,"positions":pos}, sort_keys=True))

asyncio.run(main())
PY
