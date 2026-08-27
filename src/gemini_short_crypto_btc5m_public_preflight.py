"""Public-only preflight for the Gemini BTC 5m q=1 maker canary.

No credentials, account reads, order placement, signing, cancellation, or mutation.
It validates current contract shape and live top-of-book economics only.
"""
from __future__ import annotations

import asyncio
import json

from src.gemini_short_crypto_btc5m_maker_canary_q1 import discover_btc5m, fresh_top, pair_model, ONE, TARGET
from src.gemini_short_crypto_quantity import quantity_supported


async def run() -> None:
    event = await discover_btc5m()
    if event is None:
        print(json.dumps({
            "type": "GEMINI_BTC5M_PUBLIC_PREFLIGHT",
            "market": None,
            "q1_supported": False,
            "passes_3c_pair_floor": False,
            "order_entry": False,
            "credentials_used": False,
            "realized_pnl": 0,
        }, sort_keys=True), flush=True)
        return

    bid, bid_size, ask, ask_size = await fresh_top(event.symbol)
    model = pair_model(bid, ask)
    q1_supported = quantity_supported(
        quantity=ONE,
        minimum=event.quantity_minimum,
        increment=event.quantity_increment,
    )
    print(json.dumps({
        "type": "GEMINI_BTC5M_PUBLIC_PREFLIGHT",
        "market": event.ticker,
        "symbol": event.symbol,
        "seconds_to_expiry": event.seconds_to_expiry,
        "quantity_minimum": str(event.quantity_minimum),
        "quantity_increment": str(event.quantity_increment),
        "q1_supported": q1_supported,
        "price_increment": str(event.price_increment),
        "yes_bid": str(bid),
        "yes_bid_size": str(bid_size),
        "yes_ask": str(ask),
        "yes_ask_size": str(ask_size),
        **{k: str(v) for k, v in model.items()},
        "passes_3c_pair_floor": bool(q1_supported and model["modeled_edge"] >= TARGET),
        "order_entry": False,
        "credentials_used": False,
        "realized_pnl": 0,
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    asyncio.run(run())
