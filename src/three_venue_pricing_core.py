"""Conservative q=1 pricing primitives for the three-venue sports router.

No network I/O, authentication, or order entry. Prices are taker costs including
current fee formulas. Gemini depth is intentionally limited to one contract until
WebSocket book depth is revalidated immediately before a live probe.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from typing import Any, Mapping

from src.mlb_game_side_normalization import NormalizedSide
from src.strict_mlb_prop_snapshot import (
    TopBook,
    conservative_kalshi_fee,
    polymarket_fee,
)
from src.universal_prediction_quant import MarketQuote

D = Decimal
ONE = D("1")
GEMINI_TAKER_RATE = D("0.07")


@dataclass(frozen=True)
class PricedQuote:
    quote: MarketQuote
    raw_yes_ask: Decimal
    raw_no_ask: Decimal
    yes_fee: Decimal
    no_fee: Decimal
    depth_source: str
    source_side: str

    def as_dict(self) -> dict[str, Any]:
        q = self.quote
        return {
            "venue": q.venue,
            "market_id": q.market_id,
            "settlement_key": q.settlement_key,
            "raw_yes_ask": str(self.raw_yes_ask),
            "raw_no_ask": str(self.raw_no_ask),
            "yes_fee": str(self.yes_fee),
            "no_fee": str(self.no_fee),
            "all_in_yes_cost": str(q.yes_cost),
            "all_in_no_cost": str(q.no_cost),
            "yes_depth": str(q.yes_depth),
            "no_depth": str(q.no_depth),
            "received_ms": q.received_ms,
            "depth_source": self.depth_source,
            "source_side": self.source_side,
            "settlement_confidence": q.settlement_confidence,
        }


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def gemini_one_contract_fee(price: Decimal) -> Decimal:
    if not D("0") <= price <= ONE:
        raise ValueError("price outside [0,1]")
    raw = GEMINI_TAKER_RATE * price * (ONE - price)
    return (raw * D("100")).to_integral_value(rounding=ROUND_CEILING) / D("100")


def gemini_contract_book(contract: Mapping[str, Any]) -> TopBook | None:
    prices = contract.get("prices") if isinstance(contract.get("prices"), Mapping) else {}
    buy = prices.get("buy") if isinstance(prices.get("buy"), Mapping) else {}
    yes, no = _d(buy.get("yes")), _d(buy.get("no"))
    if yes is None or no is None:
        return None
    if not (D("0") < yes <= ONE and D("0") < no <= ONE):
        return None
    return TopBook(yes_ask=yes, yes_depth=ONE, no_ask=no, no_depth=ONE)


def orient_book(book: TopBook, source_side: str) -> TopBook:
    if source_side in {"YES", "CONTRACT"}:
        return book
    if source_side == "NO":
        return TopBook(
            yes_ask=book.no_ask,
            yes_depth=book.no_depth,
            no_ask=book.yes_ask,
            no_depth=book.yes_depth,
        )
    raise ValueError(f"unknown source side: {source_side}")


def _all_in(price: Decimal, fee: Decimal) -> Decimal | None:
    value = price + fee
    return value if D("0") <= value <= ONE else None


def make_priced_quote(
    side: NormalizedSide,
    book: TopBook,
    *,
    received_ms: int,
    kalshi_fee_multiplier: Decimal | None = None,
    depth_source: str,
) -> PricedQuote | None:
    oriented = orient_book(book, side.side)
    venue = side.identity.venue
    if venue == "gemini":
        yes_fee = gemini_one_contract_fee(oriented.yes_ask)
        no_fee = gemini_one_contract_fee(oriented.no_ask)
    elif venue == "polymarket":
        yes_fee = polymarket_fee(1, oriented.yes_ask)
        no_fee = polymarket_fee(1, oriented.no_ask)
    elif venue == "kalshi":
        if kalshi_fee_multiplier is None:
            return None
        yes_fee = conservative_kalshi_fee(1, oriented.yes_ask, kalshi_fee_multiplier)
        no_fee = conservative_kalshi_fee(1, oriented.no_ask, kalshi_fee_multiplier)
    else:
        return None
    yes_cost, no_cost = _all_in(oriented.yes_ask, yes_fee), _all_in(oriented.no_ask, no_fee)
    if yes_cost is None or no_cost is None:
        return None
    yes_depth, no_depth = min(ONE, oriented.yes_depth), min(ONE, oriented.no_depth)
    if yes_depth <= 0 or no_depth <= 0:
        return None
    quote = MarketQuote(
        venue=venue,
        market_id=f"{side.venue_market_id}#{side.side}",
        settlement_key=side.contract_key,
        category="sports_mlb_game",
        yes_cost=yes_cost,
        no_cost=no_cost,
        yes_depth=yes_depth,
        no_depth=no_depth,
        received_ms=received_ms,
        settlement_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
        expected_settlement_minutes=None,
    )
    return PricedQuote(
        quote=quote,
        raw_yes_ask=oriented.yes_ask,
        raw_no_ask=oriented.no_ask,
        yes_fee=yes_fee,
        no_fee=no_fee,
        depth_source=depth_source,
        source_side=side.side,
    )
