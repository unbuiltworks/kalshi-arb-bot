"""Future-proof read-only crypto HOT layer using Kalshi's unified YES-price scale.

Kalshi documents ``use_yes_price=true`` as the forward-compatible orderbook mode.
In this mode NO-side orderbook levels are reported in YES-price units, so a NO-side
level is directly an executable YES ask. This wrapper changes only Kalshi price-scale
normalization; all settlement, fee, q<=5, <=$5, latency, and no-order safety gates stay
inside the validated hot scanner.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src import gemini_kalshi_multi_ws_hot as hot
from src.gemini_kalshi_execution_shadow import TopOfBook

D = Decimal


class UnifiedYesPriceKalshiOrderBook(hot.GuardedKalshiOrderBook):
    """Interpret both YES and NO orderbook levels on Kalshi's YES-price scale."""

    def top(self, receive_ts_ms: int) -> TopOfBook | None:
        if not self.yes and not self.no:
            return None

        if self.yes:
            yes_bid = max(self.yes)
            yes_bid_size = self.yes[yes_bid]
        else:
            yes_bid = D("0")
            yes_bid_size = D("0")

        # With use_yes_price=true, NO-side levels are expressed as the YES price
        # they would cross. The lowest such level is the best executable YES ask.
        if self.no:
            yes_ask = min(self.no)
            yes_ask_size = self.no[yes_ask]
        else:
            yes_ask = D("1")
            yes_ask_size = D("0")

        if yes_bid < 0 or yes_bid > 1 or yes_ask < 0 or yes_ask > 1:
            return None
        return TopOfBook(
            venue="kalshi",
            market_id=self.ticker,
            ts_ms=receive_ts_ms,
            yes_bid=yes_bid,
            yes_bid_size=yes_bid_size,
            yes_ask=yes_ask,
            yes_ask_size=yes_ask_size,
        )


def _unified_subscription(ticker: str) -> dict[str, Any]:
    return {
        "id": 1,
        "cmd": "subscribe",
        "params": {
            "channels": ["orderbook_delta"],
            "market_ticker": ticker,
            "use_yes_price": True,
        },
    }


# _kalshi_stream resolves these module globals at runtime, so this wrapper leaves
# the proven event loop/economics implementation untouched.
hot.GuardedKalshiOrderBook = UnifiedYesPriceKalshiOrderBook
hot._kalshi_subscription = _unified_subscription


if __name__ == "__main__":
    hot.main()
