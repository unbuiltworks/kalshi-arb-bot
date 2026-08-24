"""Production combo supervisor v6: v5 plus live leg-eligibility diagnostics.

Keeps v5's Polymarket-first prioritization, request budget/caching/backoff and
action-only fill notifications.  Adds aggregate, non-sensitive diagnostics for
why current Kalshi component markets do or do not become eligible MVE legs, and
accepts complementary price fallbacks when Kalshi omits a redundant NO/YES quote
field.  Execution remains fail-closed and independently revalidates q=1.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from typing import Any

from src import sports_kp_live_combo_supervisor_v5 as v5

base = v5.base
generator = v5.generator
existing_scan = v5.existing_scan
live_base = v5.live_base
logger = v5.logger

_ORIGINAL_EVENT_MARKETS = v5._paced_event_markets
_ORIGINAL_GENERATE = base.generate_mves
_DIAG: Counter[str] = Counter()
_SAMPLE: dict[str, Any] = {}


def _derived_no_ask(market: dict[str, Any]):
    value = generator._d(market.get("no_ask_dollars"))
    if value is not None:
        return value
    yes_bid = generator._d(market.get("yes_bid_dollars"))
    return generator.ONE - yes_bid if yes_bid is not None else None


def _derived_yes_ask(market: dict[str, Any]):
    value = generator._d(market.get("yes_ask_dollars"))
    if value is not None:
        return value
    no_bid = generator._d(market.get("no_bid_dollars"))
    return generator.ONE - no_bid if no_bid is not None else None


def _robust_leg_from_market(
    market: dict[str, Any], event_ticker: str, side: str, horizon: datetime
) -> Any:
    side = side.lower()
    _DIAG["markets_examined"] += 1
    if side not in {"yes", "no"}:
        _DIAG["reject_bad_side"] += 1
        return None
    status = str(market.get("status") or "").lower()
    if status not in {"active", "open"}:
        _DIAG["reject_status"] += 1
        return None
    _DIAG["status_open"] += 1
    close = generator._parse_dt(
        market.get("close_time")
        or market.get("expected_expiration_time")
        or market.get("expiration_time")
    )
    now = datetime.now(timezone.utc)
    if close is None:
        _DIAG["reject_no_close"] += 1
        return None
    if close <= now:
        _DIAG["reject_closed_time"] += 1
        return None
    if close > horizon:
        _DIAG["reject_beyond_horizon"] += 1
        return None
    _DIAG["within_horizon"] += 1
    ticker = str(market.get("ticker") or "")
    if not ticker:
        _DIAG["reject_no_ticker"] += 1
        return None

    if side == "yes":
        hedge_side = "no"
        price = _derived_no_ask(market)
        depth = generator._d(market.get("yes_bid_size_fp") or market.get("yes_bid_size"))
        if market.get("no_ask_dollars") in (None, "") and price is not None:
            _DIAG["derived_no_ask"] += 1
    else:
        hedge_side = "yes"
        price = _derived_yes_ask(market)
        depth = generator._d(market.get("yes_ask_size_fp") or market.get("yes_ask_size"))
        if market.get("yes_ask_dollars") in (None, "") and price is not None:
            _DIAG["derived_yes_ask"] += 1

    if price is None:
        _DIAG["reject_no_price"] += 1
        return None
    if not (generator.ZERO < price < generator.ONE):
        _DIAG["reject_nonmarketable_price"] += 1
        return None
    _DIAG["marketable_price"] += 1
    if depth is None:
        _DIAG["reject_no_depth"] += 1
        return None
    if depth < generator.ONE:
        _DIAG["reject_depth_lt_q1"] += 1
        return None
    _DIAG["eligible_legs"] += 1
    return generator.Leg(
        event_ticker,
        ticker,
        side,
        hedge_side,
        price,
        generator._fee(price),
        depth,
        close,
    )


def _robust_hedge_quote(market: dict[str, Any], selected: str) -> dict[str, Any]:
    selected = selected.lower()
    hedge = "no" if selected == "yes" else "yes" if selected == "no" else ""
    if hedge == "yes":
        price = live_base._d(market.get("yes_ask_dollars"))
        if price is None:
            no_bid = live_base._d(market.get("no_bid_dollars"))
            price = live_base.ONE - no_bid if no_bid is not None else None
        depth = live_base._d(market.get("yes_ask_size_fp") or market.get("yes_ask_size"))
    elif hedge == "no":
        price = live_base._d(market.get("no_ask_dollars"))
        if price is None:
            yes_bid = live_base._d(market.get("yes_bid_dollars"))
            price = live_base.ONE - yes_bid if yes_bid is not None else None
        depth = live_base._d(market.get("yes_bid_size_fp") or market.get("yes_bid_size"))
    else:
        price = depth = None
    ok = bool(
        price is not None
        and live_base.ZERO < price < live_base.ONE
        and depth is not None
        and depth >= live_base.ONE
        and str(market.get("status") or "").lower() in {"active", "open"}
    )
    return {
        "hedge_side": hedge,
        "price": price,
        "depth": depth,
        "fee": live_base._fee(price) if price is not None else None,
        "ok": ok,
    }


def _robust_make_hedge(market: dict[str, Any], selected: str) -> Any:
    ticker = str(market.get("ticker") or "")
    selected = selected.lower()
    hedge = "no" if selected == "yes" else "yes" if selected == "no" else ""
    if hedge == "yes":
        price = live_base._d(market.get("yes_ask_dollars"))
        if price is None:
            no_bid = live_base._d(market.get("no_bid_dollars"))
            price = live_base.ONE - no_bid if no_bid is not None else None
        depth = live_base._d(market.get("yes_ask_size_fp") or market.get("yes_ask_size"))
        yes_limit = price
        native = "YES"
    elif hedge == "no":
        price = live_base._d(market.get("no_ask_dollars"))
        if price is None:
            yes_bid = live_base._d(market.get("yes_bid_dollars"))
            price = live_base.ONE - yes_bid if yes_bid is not None else None
        depth = live_base._d(market.get("yes_bid_size_fp") or market.get("yes_bid_size"))
        yes_limit = live_base.ONE - price if price is not None else None
        native = "NO"
    else:
        return None
    if (
        not ticker
        or price is None
        or yes_limit is None
        or depth is None
        or not (live_base.ZERO < price < live_base.ONE)
        or depth < live_base.ONE
        or str(market.get("status") or "").lower() not in {"active", "open"}
    ):
        return None
    leg = live_base.ExecutionLeg(
        venue="kalshi",
        market_id=ticker,
        normalized_outcome=native,
        native_outcome=native,
        raw_outcome_price=price,
        yes_book_limit=yes_limit,
        source_side="CONTRACT",
    )
    return live_base.HedgePlan(
        ticker, selected, hedge, price, depth, live_base._fee(price), leg
    )


async def _diagnostic_event_markets(client: Any, event_ticker: str) -> list[dict[str, Any]]:
    global _SAMPLE
    rows = await _ORIGINAL_EVENT_MARKETS(client, event_ticker)
    _DIAG["event_market_calls"] += 1
    _DIAG["market_rows_returned"] += len(rows)
    if rows and not _SAMPLE:
        first = rows[0]
        _SAMPLE = {
            "event_ticker": event_ticker,
            "market_ticker": first.get("ticker"),
            "status": first.get("status"),
            "close_time": first.get("close_time") or first.get("expected_expiration_time") or first.get("expiration_time"),
            "yes_bid_dollars": first.get("yes_bid_dollars"),
            "yes_bid_size_fp": first.get("yes_bid_size_fp"),
            "yes_ask_dollars": first.get("yes_ask_dollars"),
            "yes_ask_size_fp": first.get("yes_ask_size_fp"),
            "no_ask_dollars": first.get("no_ask_dollars"),
        }
    return rows


async def _diagnostic_generate() -> dict[str, Any]:
    global _SAMPLE
    _DIAG.clear()
    _SAMPLE = {}
    result = await _ORIGINAL_GENERATE()
    logger.warning(
        "SPORTS_MVE_LEG_DIAGNOSTIC %s",
        json.dumps(
            {
                "counts": dict(_DIAG),
                "sample_market": _SAMPLE,
                "candidate_baskets_considered": result.get("candidate_baskets_considered") if isinstance(result, dict) else None,
                "markets_created_or_resolved": result.get("markets_created_or_resolved") if isinstance(result, dict) else None,
                "order_entry": False,
            },
            separators=(",", ":"),
            default=str,
        ),
    )
    return result


generator._leg_from_market = _robust_leg_from_market
generator._event_markets = _diagnostic_event_markets
existing_scan._hedge_quote = _robust_hedge_quote
live_base._make_hedge = _robust_make_hedge
base.generate_mves = _diagnostic_generate

run = base.run
main = base.main


if __name__ == "__main__":
    main()
