"""Corrected q=1 entrypoint for the Gemini BTC 5m maker canary.

The original canary incorrectly required quantityMinimum == 1 and
quantityIncrement == 1. Gemini short-duration contracts currently advertise a
0.01 minimum/increment, which still permits an exact quantity of 1.00. This
wrapper patches only the preflight quantity-grid test and reuses all existing
live mutation, cancellation, flatten, reservation, and dual-key arm logic.
"""
from __future__ import annotations

import asyncio

from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src.gemini_short_crypto_quantity import quantity_supported


async def corrected_preflight():
    event = await base.discover_btc5m()
    terms = await base._gemini_terms_ok()
    available = await base._gemini_available_usd()
    if event is None:
        return None, {
            "terms_ok": terms,
            "available_usd": str(available),
            "market": None,
            "armed": base.live_armed(),
            "order_entry": False,
        }

    bid, bid_size, ask, ask_size = await base.fresh_top(event.symbol)
    model = base.pair_model(bid, ask)
    q1_supported = quantity_supported(
        quantity=base.ONE,
        minimum=event.quantity_minimum,
        increment=event.quantity_increment,
    )
    report = {
        "terms_ok": terms,
        "available_usd": str(available),
        "armed": base.live_armed(),
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
        "passes_3c_pair_floor": bool(q1_supported and model["modeled_edge"] >= base.TARGET),
        "order_entry": False,
        "realized_pnl": 0,
    }
    return event, report


async def run() -> None:
    base.preflight = corrected_preflight
    await base.run()


if __name__ == "__main__":
    asyncio.run(run())
