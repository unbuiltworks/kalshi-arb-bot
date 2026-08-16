"""Fail-closed guards and payload builders for a first nested Kalshi live probe.

This module has NO network calls and cannot place an order.  It exists so the
first real-money experiment can be mechanically constrained before transport is
wired up.

The only structure authorized here is a same-event ``greater`` ladder:
    buy YES(lower strike) + buy NO(higher strike)
which has a minimum settlement payout of $1.00 across all possible outcomes.

Execution risk remains: the maker leg can fill while the hedge is no longer
available.  These helpers therefore validate the exact one-contract maker and
FOK hedge payloads and recompute the post-fee minimum locked profit from those
exact outbound prices.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import os
import re
import uuid

from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee

D = Decimal
ALLOWED_SERIES = frozenset({"KXETHD", "KXSOLD"})
MAX_PROBE_QUANTITY = D("1")
MAX_TIME_TO_CLOSE_SECONDS = D("21600")  # 6 hours
DEFAULT_MIN_LOCKED_PROFIT = D("0.03")
LIVE_ENV = "ENABLE_KALSHI_NESTED_LIVE_PROBE"
MARKET_RE = re.compile(r"^(?P<event>.+)-T(?P<strike>-?\d+(?:\.\d+)?)$")


@dataclass(frozen=True)
class ProbeLeg:
    ticker: str
    outcome: str  # yes | no, economically purchased outcome
    cost: Decimal


@dataclass(frozen=True)
class NestedProbePlan:
    series: str
    event_ticker: str
    event_close: datetime
    maker: ProbeLeg
    hedge: ProbeLeg
    fee_type: str
    fee_multiplier: Decimal
    target_profit: Decimal = DEFAULT_MIN_LOCKED_PROFIT
    quantity: Decimal = MAX_PROBE_QUANTITY


def live_probe_authorized(cli_live: bool) -> bool:
    """Two independent opt-ins; disabled unless both are explicit."""
    return bool(cli_live) and os.getenv(LIVE_ENV, "NO") == "YES"


def market_event_and_strike(ticker: str) -> tuple[str, Decimal]:
    match = MARKET_RE.fullmatch(str(ticker).upper())
    if not match:
        raise ValueError(f"unsupported nested market ticker: {ticker}")
    try:
        strike = D(match.group("strike"))
    except InvalidOperation as exc:
        raise ValueError(f"invalid strike in ticker: {ticker}") from exc
    return match.group("event"), strike


def _validate_cost(cost: Decimal) -> Decimal:
    value = D(cost)
    if not (D("0.01") <= value <= D("0.99")):
        raise ValueError("probe leg cost must be between $0.01 and $0.99")
    return value


def _validate_structure(plan: NestedProbePlan, *, now: datetime) -> None:
    if plan.series not in ALLOWED_SERIES:
        raise ValueError("first nested live probe is restricted to KXETHD/KXSOLD")
    if plan.quantity != MAX_PROBE_QUANTITY:
        raise ValueError("first nested live probe is hard-capped at exactly 1 contract")
    if plan.target_profit < DEFAULT_MIN_LOCKED_PROFIT:
        raise ValueError("first nested live probe requires at least 3 cents modeled locked profit")
    if plan.fee_type != "quadratic":
        raise ValueError("unknown or maker-fee-bearing fee type; fail closed")
    if plan.fee_multiplier <= 0:
        raise ValueError("fee multiplier must be positive")
    if plan.event_close.tzinfo is None or now.tzinfo is None:
        raise ValueError("event_close and now must be timezone-aware")
    if plan.event_close <= now:
        raise ValueError("event already closed")
    if plan.event_close - now > timedelta(seconds=float(MAX_TIME_TO_CLOSE_SECONDS)):
        raise ValueError("event exceeds 6-hour capital-lock ceiling")

    maker_event, maker_strike = market_event_and_strike(plan.maker.ticker)
    hedge_event, hedge_strike = market_event_and_strike(plan.hedge.ticker)
    if maker_event != plan.event_ticker or hedge_event != plan.event_ticker:
        raise ValueError("both legs must belong to the exact same event")
    if not plan.event_ticker.startswith(plan.series + "-"):
        raise ValueError("event ticker does not belong to allowed series")
    if maker_strike == hedge_strike:
        raise ValueError("nested legs require distinct strikes")

    _validate_cost(plan.maker.cost)
    _validate_cost(plan.hedge.cost)

    lower = (plan.maker if maker_strike < hedge_strike else plan.hedge)
    higher = (plan.hedge if maker_strike < hedge_strike else plan.maker)
    if lower.outcome.lower() != "yes" or higher.outcome.lower() != "no":
        raise ValueError("greater-ladder guarantee requires YES(lower) + NO(higher)")


def outcome_cost_from_v2_payload(payload: dict) -> tuple[str, Decimal]:
    """Return economically purchased outcome and cost from a V2 YES-book order."""
    side = str(payload.get("side") or "").lower()
    price = D(str(payload.get("price")))
    if side == "bid":
        return "yes", price
    if side == "ask":
        return "no", D("1") - price
    raise ValueError("V2 side must be bid or ask")


def build_v2_buy_payload(
    *,
    leg: ProbeLeg,
    maker: bool,
    client_order_id: str | None = None,
) -> dict:
    """Encode buy-YES/buy-NO economics into Kalshi V2 single YES-book form."""
    cost = _validate_cost(leg.cost)
    outcome = leg.outcome.lower()
    if outcome == "yes":
        side = "bid"
        yes_book_price = cost
    elif outcome == "no":
        side = "ask"
        yes_book_price = D("1") - cost
    else:
        raise ValueError("outcome must be yes or no")

    payload = {
        "ticker": leg.ticker,
        "client_order_id": client_order_id or f"nested-probe-{uuid.uuid4().hex[:24]}",
        "side": side,
        "count": "1.00",
        "price": f"{yes_book_price:.4f}",
        "time_in_force": "good_till_canceled" if maker else "fill_or_kill",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": bool(maker),
        "cancel_order_on_pause": True,
        "reduce_only": False,
        "subaccount": 0,
        "exchange_index": 0,
    }
    return payload


def recompute_exact_locked_profit(
    *,
    maker_payload: dict,
    hedge_payload: dict,
    fee_multiplier: Decimal,
) -> Decimal:
    """Minimum settlement P&L for exact 1-lot payload prices after taker hedge fee.

    The first probe permits only fee_type=quadratic, where the post-only maker
    leg is not charged the taker fee.  Hedge fee is recomputed from the exact
    outbound V2 price rather than trusting a caller's cached model value.
    """
    if str(maker_payload.get("count")) != "1.00" or str(hedge_payload.get("count")) != "1.00":
        raise ValueError("exact payloads must each be one contract")
    if not maker_payload.get("post_only"):
        raise ValueError("maker payload must be post_only")
    if str(maker_payload.get("time_in_force")) != "good_till_canceled":
        raise ValueError("maker payload must be good_till_canceled")
    if hedge_payload.get("post_only"):
        raise ValueError("hedge payload must not be post_only")
    if str(hedge_payload.get("time_in_force")) != "fill_or_kill":
        raise ValueError("hedge payload must be fill_or_kill")

    maker_outcome, maker_cost = outcome_cost_from_v2_payload(maker_payload)
    hedge_outcome, hedge_cost = outcome_cost_from_v2_payload(hedge_payload)
    if maker_outcome == hedge_outcome:
        raise ValueError("nested guarantee requires complementary outcomes")

    hedge_yes_price = D(str(hedge_payload["price"]))
    hedge_fee = kalshi_quadratic_taker_fee(
        hedge_yes_price,
        D("1"),
        fee_multiplier=D(fee_multiplier),
    )
    return D("1") - maker_cost - hedge_cost - hedge_fee


def validate_and_build_payloads(
    plan: NestedProbePlan,
    *,
    now: datetime,
) -> tuple[dict, dict, Decimal]:
    """Validate plan, build exact maker/FOK hedge payloads, then revalidate P&L."""
    _validate_structure(plan, now=now)
    base_id = f"nested-probe-{uuid.uuid4().hex[:20]}"
    maker_payload = build_v2_buy_payload(
        leg=plan.maker,
        maker=True,
        client_order_id=base_id + "-m",
    )
    hedge_payload = build_v2_buy_payload(
        leg=plan.hedge,
        maker=False,
        client_order_id=base_id + "-h",
    )
    if maker_payload["ticker"] == hedge_payload["ticker"]:
        raise ValueError("nested probe must use distinct markets")

    locked = recompute_exact_locked_profit(
        maker_payload=maker_payload,
        hedge_payload=hedge_payload,
        fee_multiplier=plan.fee_multiplier,
    )
    if locked < plan.target_profit:
        raise ValueError("exact outbound payload economics fall below target profit")
    return maker_payload, hedge_payload, locked
