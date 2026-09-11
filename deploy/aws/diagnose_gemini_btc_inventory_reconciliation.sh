#!/usr/bin/env bash
set -euo pipefail

# READ-ONLY diagnostic for the currently unresolved Gemini BTC inventory campaign.
# No order placement/cancel/sell. No local ledger mutation/release.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-btc-inventory-diagnostic}"
BRANCH="feature/gemini-short-crypto-live-q1"
PINNED_COMMIT="840dc158ff6e75b1a3b63e45d6c1473293453c93"
IMAGE="arb-gemini-btc-inventory:diagnostic-840dc15"
ENV_FILE="${ENV_FILE:-/opt/arb/secrets/gemini-short-crypto.env}"
STATE_DIR="${STATE_DIR:-/opt/arb/gemini-short-crypto-state}"

for cmd in git docker; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd is required" >&2; exit 1; }
done
[ -d "$SOURCE_REPO/.git" ] || { echo "source repo missing: $SOURCE_REPO" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "secure env missing: $ENV_FILE" >&2; exit 1; }

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
echo "INVENTORY_DIAGNOSTIC_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null

docker run -i --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u - <<'PY'
import asyncio, json
from src import gemini_short_crypto_local_budget as local
from src.gemini_prediction_auth import private_rest_request
from src.gemini_short_crypto_btc5m_maker_canary_q1_v3 import maker_client_ids
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base5

KEEP_ORDER = {
    "orderId", "clientOrderId", "symbol", "eventOutcome", "outcome", "side",
    "status", "price", "avgExecutionPrice", "quantity", "filledQuantity",
    "remainingQuantity", "createdAt", "updatedAt"
}
KEEP_POSITION = {
    "instrumentSymbol", "symbol", "eventTicker", "outcome", "positionQuantity",
    "totalQuantity", "costBasis", "payout", "netProfit", "resolutionSide",
    "settledAt", "createdAt", "updatedAt"
}

def slim(row, keep):
    return {k: row.get(k) for k in keep if k in row}

async def post(path, params):
    r = await private_rest_request("POST", path, params=params, timeout=8.0)
    body = r.json() if r.status_code == 200 else None
    return {"http": r.status_code, "body": body}

async def main():
    st = local.local_open_state()
    if st is None:
        print(json.dumps({"type":"INVENTORY_DIAGNOSTIC","local_open_reservation":False}, sort_keys=True))
        return
    trade_id = str(st.get("trade_id") or "")
    symbol = str(st.get("symbol") or "")
    event_ticker = str(st.get("event_ticker") or "")
    yes_id, no_id = maker_client_ids(trade_id)

    filled, cancelled, settled_filtered, settled_all0, settled_all100, settled_all200 = await asyncio.gather(
        post("/v1/prediction-markets/orders/history", {"status":"filled","symbol":symbol,"limit":100}),
        post("/v1/prediction-markets/orders/history", {"status":"cancelled","symbol":symbol,"limit":100}),
        post("/v1/prediction-markets/positions/settled", {"eventTicker":event_ticker,"limit":100,"offset":0}),
        post("/v1/prediction-markets/positions/settled", {"limit":100,"offset":0}),
        post("/v1/prediction-markets/positions/settled", {"limit":100,"offset":100}),
        post("/v1/prediction-markets/positions/settled", {"limit":100,"offset":200}),
    )

    def orders(resp):
        body = resp.get("body") if isinstance(resp, dict) else None
        rows = body.get("orders", []) if isinstance(body, dict) else []
        return [r for r in rows if isinstance(r, dict) and str(r.get("clientOrderId") or "") in {yes_id, no_id}]

    def positions(resp):
        body = resp.get("body") if isinstance(resp, dict) else None
        rows = body.get("positions", []) if isinstance(body, dict) else []
        out=[]
        for r in rows:
            if not isinstance(r, dict):
                continue
            rsym = str(r.get("instrumentSymbol") or r.get("symbol") or "")
            ret = str(r.get("eventTicker") or "")
            if rsym == symbol or ret == event_ticker:
                out.append(r)
        return out

    exact_orders = orders(filled) + orders(cancelled)
    all_positions = positions(settled_filtered) + positions(settled_all0) + positions(settled_all100) + positions(settled_all200)
    dedup=[]; seen=set()
    for r in all_positions:
        key=json.dumps(slim(r, KEEP_POSITION), sort_keys=True, default=str)
        if key not in seen:
            seen.add(key); dedup.append(r)

    available = await base5._gemini_available_usd()
    print(json.dumps({
        "type":"GEMINI_INVENTORY_RECONCILIATION_DIAGNOSTIC",
        "local_open_reservation": True,
        "trade_id": trade_id,
        "symbol": symbol,
        "event_ticker": event_ticker,
        "yes_client_order_id": yes_id,
        "no_client_order_id": no_id,
        "available_usd": str(available),
        "filled_http": filled["http"],
        "cancelled_http": cancelled["http"],
        "settled_filtered_http": settled_filtered["http"],
        "settled_unfiltered_http": [settled_all0["http"], settled_all100["http"], settled_all200["http"]],
        "exact_order_rows": [slim(r, KEEP_ORDER) for r in exact_orders],
        "matching_settled_rows": [slim(r, KEEP_POSITION) for r in dedup],
        "venue_mutation": False,
        "local_mutation": False,
    }, sort_keys=True, default=str), flush=True)

asyncio.run(main())
PY

echo "INVENTORY_DIAGNOSTIC_COMPLETE venue_mutation=false local_mutation=false"
