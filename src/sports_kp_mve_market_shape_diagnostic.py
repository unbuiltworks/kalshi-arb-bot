"""Read-only Kalshi MVE component market field-shape diagnostic.

When targeted MVE manufacturing sees market rows but no q=1 selected sides, this
captures the public quote/status/time field names actually present in the list
payload. It never creates an MVE, opens an RFQ, accepts a quote, or submits an
order. Values are limited to public market metadata; credentials are never logged.
"""
from __future__ import annotations

from collections import Counter
import json
from typing import Any, Mapping

from src.auth import KalshiClient
from src import sports_kp_mve_combo_generator as g

QUOTE_KEYS = (
    "yes_bid", "yes_bid_dollars", "yes_bid_size", "yes_bid_size_fp",
    "yes_ask", "yes_ask_dollars", "yes_ask_size", "yes_ask_size_fp",
    "no_bid", "no_bid_dollars", "no_bid_size", "no_bid_size_fp",
    "no_ask", "no_ask_dollars", "no_ask_size", "no_ask_size_fp",
    "last_price", "last_price_dollars",
)
TIME_KEYS = (
    "open_time", "close_time", "expected_expiration_time", "expiration_time",
    "latest_expiration_time", "settlement_timer_seconds",
)


def _safe_sample(market: Mapping[str, Any]) -> dict[str, Any]:
    keep = (
        "ticker", "event_ticker", "series_ticker", "status", "market_type",
        "strike_type", "title", "yes_sub_title", "no_sub_title",
        *QUOTE_KEYS, *TIME_KEYS,
    )
    return {key: market.get(key) for key in keep if key in market}


async def run_once() -> dict[str, Any]:
    client = KalshiClient(base_url=g.BASE)
    try:
        collections = [
            c for c in await g._open_collections(client)
            if g._sportsish(c.get("collection_ticker"), c.get("title"), c.get("description"), c.get("series_ticker"))
        ]
        key_counts: Counter[str] = Counter()
        status_counts: Counter[str] = Counter()
        quote_presence: Counter[str] = Counter()
        time_presence: Counter[str] = Counter()
        samples: list[dict[str, Any]] = []
        events_visited = 0
        market_rows = 0

        for collection in collections[:3]:
            associated = [dict(x) for x in (collection.get("associated_events") or []) if isinstance(x, Mapping)]
            if not associated:
                associated = [{"ticker": x} for x in (collection.get("associated_event_tickers") or [])]
            for erow in associated[:4]:
                event = str(erow.get("ticker") or "")
                if not event:
                    continue
                events_visited += 1
                markets = await g._event_markets(client, event)
                for market in markets:
                    market_rows += 1
                    key_counts.update(str(k) for k in market.keys())
                    status_counts[str(market.get("status") or "<missing>")] += 1
                    for key in QUOTE_KEYS:
                        if market.get(key) not in (None, ""):
                            quote_presence[key] += 1
                    for key in TIME_KEYS:
                        if market.get(key) not in (None, ""):
                            time_presence[key] += 1
                    if len(samples) < 8:
                        samples.append(_safe_sample(market))

        out = {
            "status": "SPORTS_MVE_MARKET_SHAPE_DIAGNOSTIC_V1",
            "collections_seen": min(len(collections), 3),
            "events_visited": events_visited,
            "market_rows": market_rows,
            "status_counts": dict(status_counts.most_common()),
            "quote_field_presence": dict(quote_presence.most_common()),
            "time_field_presence": dict(time_presence.most_common()),
            "top_field_names": [name for name, _ in key_counts.most_common(80)],
            "samples": samples,
            "market_creation": False,
            "rfq": False,
            "quote_acceptance": False,
            "order_entry": False,
            "realized": False,
        }
        print("SPORTS_MVE_MARKET_SHAPE_DIAGNOSTIC " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out
    finally:
        await client.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_once())
