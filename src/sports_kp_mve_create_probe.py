"""Create exactly one bounded Kalshi sports MVE market, then print returned metadata.

This creates a market definition only. It does NOT place an order or spend funds.
The leg set is hard-coded for a single controlled production probe and must be
replaced by the optimizer before autonomous use.
"""
from __future__ import annotations

import asyncio
import json

from src.auth import KalshiClient

BASE = "https://external-api.kalshi.com/trade-api/v2"
COLLECTION = "KXMVECROSSCATEGORY-SHARD1-R"
SELECTED = [
    {
        "event_ticker": "KXNFLGAME-26AUG21NYJPIT",
        "market_ticker": "KXNFLGAME-26AUG21NYJPIT-PIT",
        "side": "yes",
    },
    {
        "event_ticker": "KXLIGUE1TOTAL-26AUG21OLMRCS",
        "market_ticker": "KXLIGUE1TOTAL-26AUG21OLMRCS-5",
        "side": "no",
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
        return {
            "http_status": response.status_code,
            "collection_ticker": COLLECTION,
            "selected_markets": SELECTED,
            "event_ticker": body.get("event_ticker") if isinstance(body, dict) else None,
            "market_ticker": body.get("market_ticker") if isinstance(body, dict) else None,
            "market": body.get("market") if isinstance(body, dict) else None,
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
