"""Create exactly one bounded Kalshi sports MVE market, then print returned metadata.

This creates a market definition only. It does NOT place an order or spend funds.
This production probe is intentionally pinned to the user-authorized live
49ers + Braves candidate.
"""
from __future__ import annotations

import asyncio
import json

from src.auth import KalshiClient

BASE = "https://external-api.kalshi.com/trade-api/v2"
COLLECTION = "KXMVECROSSCATEGORY-SHARD1-R"
SELECTED = [
    {
        "event_ticker": "KXNFLGAME-26AUG20SFLAC",
        "market_ticker": "KXNFLGAME-26AUG20SFLAC-SF",
        "side": "yes",
    },
    {
        "event_ticker": "KXMLBGAME-26AUG211610ATLMIL",
        "market_ticker": "KXMLBGAME-26AUG211610ATLMIL-ATL",
        "side": "yes",
    },
]


async def main_async() -> dict:
    client = KalshiClient(base_url=BASE)
    try:
        response = await client.post(
            f"/multivariate_event_collections/{COLLECTION}",
            json={"selected_markets": SELECTED, "with_market_payload": True},
        )
        body = response.json() if response.content else {}
        market = body.get("market") if isinstance(body, dict) else None
        exact = False
        if isinstance(market, dict):
            got = {
                (str(x.get("event_ticker")), str(x.get("market_ticker")), str(x.get("side", "")).lower())
                for x in (market.get("mve_selected_legs") or []) if isinstance(x, dict)
            }
            want = {(x["event_ticker"], x["market_ticker"], x["side"]) for x in SELECTED}
            exact = got == want
        return {
            "status": "SPORTS_KP_LIVE_PAIR_CREATE",
            "http_status": response.status_code,
            "collection_ticker": COLLECTION,
            "selected_markets": SELECTED,
            "event_ticker": body.get("event_ticker") if isinstance(body, dict) else None,
            "market_ticker": body.get("market_ticker") if isinstance(body, dict) else None,
            "market": market,
            "exact_legs_match": exact,
            "error": body if response.status_code >= 400 else None,
            "order_entry": False,
            "funds_at_risk": False,
        }
    finally:
        await client.close()


def main() -> None:
    print("SPORTS_KP_MVE_CREATE_PROBE " + json.dumps(asyncio.run(main_async()), separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
