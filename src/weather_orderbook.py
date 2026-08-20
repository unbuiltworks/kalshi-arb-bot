"""Public, read-only Kalshi orderbook helpers for weather shadow evaluation.

Kalshi returns bid books for YES and NO. A NO bid at p is an executable YES ask
at 1-p with the same quantity, and vice versa. This module never authenticates or
submits orders; it only calculates executable buy prices for small shadow quantities.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal

ORDERBOOK_BASE = "https://external-api.kalshi.com/trade-api/v2"


@dataclass(frozen=True)
class ExecutableFill:
    side: str
    requested_contracts: float
    filled_contracts: float
    fully_fillable: bool
    total_cost_cents: float | None
    average_price_cents: float | None
    worst_price_cents: float | None
    levels_used: int


def fetch_orderbook(ticker: str, depth: int = 100) -> dict[str, Any]:
    query = urllib.parse.urlencode({"depth": max(0, min(100, int(depth)))})
    url = f"{ORDERBOOK_BASE}/markets/{urllib.parse.quote(ticker, safe='')}/orderbook?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb-weather-shadow/1.0"})
    with urllib.request.urlopen(req, timeout=10) as response:
        payload = json.load(response)
    book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    return book if isinstance(book, dict) else {}


def _bid_levels(book: dict[str, Any], side: Literal["yes", "no"]) -> list[tuple[float, float]]:
    raw = book.get(f"{side}_dollars") or []
    levels: list[tuple[float, float]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            price = float(item[0])
            qty = float(item[1])
        except (TypeError, ValueError):
            continue
        if 0 <= price <= 1 and qty > 0:
            levels.append((price, qty))
    # Highest bid is best, regardless of payload ordering.
    return sorted(levels, key=lambda x: x[0], reverse=True)


def executable_buy(book: dict[str, Any], side: Literal["yes", "no"], contracts: float) -> ExecutableFill:
    """Calculate a marketable buy using the opposite-side bid book."""
    requested = max(0.0, float(contracts))
    if requested <= 0:
        return ExecutableFill(side, requested, 0.0, True, 0.0, 0.0, 0.0, 0)

    opposing = "no" if side == "yes" else "yes"
    bids = _bid_levels(book, opposing)
    remaining = requested
    filled = 0.0
    cost_dollars = 0.0
    worst_ask = None
    used = 0
    for bid_price, available in bids:
        ask_price = 1.0 - bid_price
        take = min(remaining, available)
        if take <= 0:
            continue
        cost_dollars += take * ask_price
        filled += take
        remaining -= take
        worst_ask = ask_price
        used += 1
        if remaining <= 1e-9:
            break

    fully = remaining <= 1e-9
    avg_cents = (cost_dollars / filled * 100.0) if filled > 0 else None
    total_cents = cost_dollars * 100.0 if filled > 0 else None
    worst_cents = worst_ask * 100.0 if worst_ask is not None else None
    return ExecutableFill(
        side=side,
        requested_contracts=requested,
        filled_contracts=round(filled, 6),
        fully_fillable=fully,
        total_cost_cents=round(total_cents, 4) if total_cents is not None else None,
        average_price_cents=round(avg_cents, 4) if avg_cents is not None else None,
        worst_price_cents=round(worst_cents, 4) if worst_cents is not None else None,
        levels_used=used,
    )


def executable_grid(book: dict[str, Any], max_q: int = 5) -> dict[str, dict[int, ExecutableFill]]:
    max_q = max(1, min(20, int(max_q)))
    return {
        "yes": {q: executable_buy(book, "yes", q) for q in range(1, max_q + 1)},
        "no": {q: executable_buy(book, "no", q) for q in range(1, max_q + 1)},
    }
