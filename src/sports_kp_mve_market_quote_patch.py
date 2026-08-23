"""Normalize Kalshi market-list rows for targeted MVE manufacturing.

Kalshi list responses can omit explicit asks while still exposing the economically
identical opposite bid.  For a binary market:

    YES ask = 1 - NO bid
    NO ask  = 1 - YES bid

with executable ask depth equal to the opposite bid depth.  The production
same-market scanner already relies on this exact book identity.  This module
installs the same fail-closed normalization into the targeted monotonicity seed
without changing fees, q=1 depth requirements, horizons, or any order semantics.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping

from src import sports_kp_mve_combo_generator as g
from src import sports_kp_mve_monotonicity_seed as seed

D = Decimal
ONE = D("1")


def selected_from_market(
    market: Mapping[str, Any],
    event_ticker: str,
    side: str,
    horizon: datetime,
) -> seed.Selected | None:
    side = str(side or "").lower()
    if side not in {"yes", "no"}:
        return None
    if str(market.get("status") or "").lower() not in {"active", "open"}:
        return None
    close = g._parse_dt(
        market.get("close_time")
        or market.get("expected_expiration_time")
        or market.get("expiration_time")
    )
    if close is None or close <= seed.datetime.now(seed.timezone.utc) or close > horizon:
        return None
    ticker = str(market.get("ticker") or "")
    if not ticker:
        return None

    ask = depth = None
    if side == "yes":
        ask = g._d(market.get("yes_ask_dollars"))
        depth = g._d(market.get("yes_ask_size_fp") or market.get("yes_ask_size"))
        if ask is None or depth is None:
            no_bid = g._d(market.get("no_bid_dollars"))
            no_depth = g._d(market.get("no_bid_size_fp") or market.get("no_bid_size"))
            if no_bid is not None and no_depth is not None:
                ask = ONE - no_bid
                depth = no_depth
    else:
        ask = g._d(market.get("no_ask_dollars"))
        depth = g._d(market.get("no_ask_size_fp") or market.get("no_ask_size"))
        if ask is None or depth is None:
            yes_bid = g._d(market.get("yes_bid_dollars"))
            yes_depth = g._d(market.get("yes_bid_size_fp") or market.get("yes_bid_size"))
            if yes_bid is not None and yes_depth is not None:
                ask = ONE - yes_bid
                depth = yes_depth

    if ask is None or depth is None or depth < ONE or not (D("0") < ask < ONE):
        return None
    return seed.Selected(
        event_ticker=event_ticker,
        market_ticker=ticker,
        side=side,
        ask=ask,
        depth=depth,
        fee=g._fee(ask),
        close_time=close,
    )


def install() -> None:
    seed._selected_from_market = selected_from_market
