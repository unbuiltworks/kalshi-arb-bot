"""The venue's OWN order constraints, verified rather than assumed. PURE.

WHAT WAS WRONG. `l5_btc5m_controller.Params` carries `tick = 0.01` and
`min_qty = 0.01` as DEFAULTS and nothing ever checked them against the venue.
Meanwhile `l5_btc5m_runner.book()` fetches `/v1/market/slug/{slug}` -- whose
response carries `minimumTradeQty`, `orderPriceMinTickSize` and
`feeCoefficient` -- and returns only bid, ask, age and closed. The authority
was already on the wire, in the same response, and was being thrown away.

VERIFIED AGAINST THE LIVE VENUE 2026-11-19, cpc-btc-updown-5m-...-1620z:

    minimumTradeQty        0.01
    orderPriceMinTickSize  0.01
    feeCoefficient         0.095

So the hardcoded values are currently RIGHT, which is exactly why this is
worth gating rather than ignoring: a silent future change to either would be
invisible until orders started being rejected or, worse, accepted at a size
we did not intend.

WHY NOT A HARDCODED EQUALITY CHECK. `pmus_micro_executor` records the measured
cost of that: it required a 0.005 tick, PMUS sports serve
`orderPriceMinTickSize = 0.01`, and the check "REFUSED EVERY SPORTS MARKET
with MICRO_TICK_UNSUPPORTED:0.01 -- the live path could not have placed a
sports order at all". So the venue's own per-market grid is authoritative here
and prices are validated against THAT, never against a constant.

IT NEVER ROUNDS UP. A quantity below the venue minimum is REFUSED, not raised
to the minimum: raising it is how a $0.05 intent becomes a $0.50 order. The
brief's rule -- never round upward if that would breach a limit -- is
implemented as never rounding upward at all.

OFF BY DEFAULT. `required()` is false unless the environment says REQUIRE, so
an unset variable leaves every existing decision byte-identical. When it IS
required, missing or implausible constraints refuse the decision rather than
falling back to the defaults, because a default that silently stands in for
the venue is the defect this module exists to close.
"""
from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional

D = Decimal
ZERO = D("0")
ONE = D("1")

REQUIRE_ENV = "L5_BTC5M_VENUE_CONSTRAINTS"
REQUIRE_TOKEN = "REQUIRE"

#: the keys the venue actually serves, and the names we carry them under
VENUE_MIN_QTY = "minimumTradeQty"
VENUE_TICK = "orderPriceMinTickSize"
VENUE_FEE = "feeCoefficient"
BOOK_MIN_QTY = "min_trade_qty"
BOOK_TICK = "venue_tick"
BOOK_FEE = "fee_coefficient"

#: a tick outside this is not a price grid we understand
TICK_MIN, TICK_MAX = D("0.0001"), D("0.5")
#: a minimum trade quantity at or above one share is not a FRACTIONAL venue,
#: and every size this lane computes is fractional. `pmus_micro_executor`
#: makes the same demand: "market metadata must explicitly advertise
#: minimumTradeQty < 1".
MIN_QTY_CEIL = ONE


def required() -> bool:
    """Must the venue's constraints be present and valid to trade?"""
    return (os.environ.get(REQUIRE_ENV) or "").strip().upper() == REQUIRE_TOKEN


def _dec(x: Any) -> Optional[Decimal]:
    if isinstance(x, Decimal):
        return x if x.is_finite() else None
    if x is None or isinstance(x, bool):
        return None
    try:
        v = D(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return v if v.is_finite() else None


def from_market(market: Any) -> dict:
    """Parse the venue's market object. Never raises.

    Returns {"ok": bool, "reason": str|None, "min_qty", "tick", "fee_coef"}.
    """
    def no(reason: str) -> dict:
        return {"ok": False, "reason": reason, "min_qty": None,
                "tick": None, "fee_coef": None}

    if not isinstance(market, Mapping):
        return no("VENUE_MARKET_UNPARSEABLE")
    minq = _dec(market.get(VENUE_MIN_QTY))
    tick = _dec(market.get(VENUE_TICK))
    if minq is None or tick is None:
        return no("VENUE_CONSTRAINTS_MISSING")
    if not (TICK_MIN <= tick <= TICK_MAX):
        return no("VENUE_TICK_IMPLAUSIBLE:%s" % tick)
    if not (ZERO < minq < MIN_QTY_CEIL):
        return no("VENUE_MIN_QTY_IMPLAUSIBLE:%s" % minq)
    fee = _dec(market.get(VENUE_FEE))
    if fee is not None and (fee < ZERO or fee > ONE):
        return no("VENUE_FEE_IMPLAUSIBLE:%s" % fee)
    return {"ok": True, "reason": None, "min_qty": minq, "tick": tick,
            "fee_coef": fee}


def carry(market: Any) -> dict:
    """The fields `l5_btc5m_runner.book()` should carry through.

    Always the same three keys, so a consumer can tell "the venue did not
    say" (None) from "nobody looked" (absent).
    """
    spec = from_market(market)
    return {BOOK_MIN_QTY: spec["min_qty"], BOOK_TICK: spec["tick"],
            BOOK_FEE: spec["fee_coef"]}


def from_book(book: Any) -> dict:
    """The spec as a book dict carries it, with the same shape as
    `from_market`. A book that carries nothing is a REFUSAL, not a default."""
    def no(reason: str) -> dict:
        return {"ok": False, "reason": reason, "min_qty": None,
                "tick": None, "fee_coef": None}

    if not isinstance(book, Mapping):
        return no("VENUE_CONSTRAINTS_UNVERIFIED")
    if BOOK_MIN_QTY not in book and BOOK_TICK not in book:
        return no("VENUE_CONSTRAINTS_UNVERIFIED")
    return from_market({VENUE_MIN_QTY: book.get(BOOK_MIN_QTY),
                        VENUE_TICK: book.get(BOOK_TICK),
                        VENUE_FEE: book.get(BOOK_FEE)})


def check_order(price: Any, qty: Any, spec: Mapping) -> Optional[str]:
    """Why the venue would refuse this order, or None.

    NEVER ROUNDS. A sub-minimum quantity or an off-grid price is refused as
    it stands; the caller may recompute a different order, but nothing here
    quietly makes one bigger.
    """
    if not isinstance(spec, Mapping) or not spec.get("ok"):
        return str((spec or {}).get("reason") or "VENUE_CONSTRAINTS_UNVERIFIED")
    p, q = _dec(price), _dec(qty)
    tick, minq = spec.get("tick"), spec.get("min_qty")
    if p is None or q is None:
        return "ORDER_UNREADABLE"
    if not (ZERO < p < ONE):
        return "PRICE_OUT_OF_RANGE:%s" % p
    if (p / tick) % 1 != 0:
        return "PRICE_OFF_TICK:%s/%s" % (p, tick)
    if q < minq:
        #: refused, not raised. Raising it is how a penny intent becomes a
        #: ten-penny order.
        return "QTY_BELOW_VENUE_MINIMUM:%s<%s" % (q, minq)
    if (q / minq) % 1 != 0:
        return "QTY_OFF_LOT:%s/%s" % (q, minq)
    return None


def smallest_order_usd(spec: Mapping, price: Any) -> Optional[Decimal]:
    """What the smallest permitted order at `price` actually costs.

    This is the number that decides whether a given budget can trade at all:
    at minimumTradeQty 0.01 and a 0.99 price it is under one cent, so a $5
    window budget is bounded by OUR limits, not by the venue's.
    """
    if not isinstance(spec, Mapping) or not spec.get("ok"):
        return None
    p = _dec(price)
    if p is None or not (ZERO < p < ONE):
        return None
    return spec["min_qty"] * p
