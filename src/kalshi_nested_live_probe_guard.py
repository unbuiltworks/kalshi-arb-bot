"""Pure guardrails and V2 payload builders for a future nested Kalshi live probe.

This module contains NO network calls.  It exists to freeze the first live probe's
risk envelope before any transport is allowed to use it:
- exactly one contract;
- same-event, proven nested greater/less relationship only;
- mapped quadratic fee schedule with no pending fee change;
- event closes within six hours;
- non-boundary 1c..99c outcome costs only;
- at least three cents modeled guaranteed profit after the immediate hedge fee;
- maker leg is post-only and short-lived; hedge leg is IOC.

Kalshi V2 order prices are in unified YES-price space.  A long-NO order is an
``ask`` and therefore converts an outcome cost ``c`` to V2 price ``1-c``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path
import time
import uuid
from zoneinfo import ZoneInfo

from src.kalshi_nested_maker_shadow import D, maker_then_taker_profit

NY = ZoneInfo("America/New_York")
MAX_QUANTITY = D("1")
MIN_TARGET_PROFIT = D("0.03")
MIN_OUTCOME_COST = D("0.01")
MAX_OUTCOME_COST = D("0.99")
MAX_TIME_TO_CLOSE_SECONDS = D(str(6 * 3600))
MAX_MAKER_REST_SECONDS = 60


@dataclass(frozen=True)
class NestedLiveProbeSpec:
    event_ticker: str
    event_close: datetime
    strike_type: str
    lower_ticker: str
    lower_strike: Decimal
    higher_ticker: str
    higher_strike: Decimal
    maker_ticker: str
    maker_side: str
    maker_cost: Decimal
    hedge_ticker: str
    hedge_side: str
    hedge_cost: Decimal
    fee_type: str
    fee_multiplier: Decimal
    scheduled_fee_changes: tuple[dict, ...] = ()
    target_profit: Decimal = MIN_TARGET_PROFIT
    quantity: Decimal = MAX_QUANTITY
    maker_rest_seconds: int = 10


def outcome_to_v2_book_side(outcome_side: str) -> str:
    side = outcome_side.lower().strip()
    if side == "yes":
        return "bid"
    if side == "no":
        return "ask"
    raise ValueError("outcome side must be yes or no")


def outcome_cost_to_v2_price(outcome_side: str, outcome_cost: Decimal) -> Decimal:
    cost = D(outcome_cost)
    if not D("0") <= cost <= D("1"):
        raise ValueError("outcome cost must be in [0,1]")
    return cost if outcome_side.lower().strip() == "yes" else D("1") - cost


def v2_price_to_outcome_cost(outcome_side: str, v2_price: Decimal) -> Decimal:
    price = D(v2_price)
    if not D("0") <= price <= D("1"):
        raise ValueError("V2 price must be in [0,1]")
    return price if outcome_side.lower().strip() == "yes" else D("1") - price


def _expected_nested_legs(spec: NestedLiveProbeSpec) -> set[tuple[str, str]]:
    if not spec.lower_strike < spec.higher_strike:
        raise ValueError("nested strikes require lower < higher")
    st = spec.strike_type.lower().strip()
    if st == "greater":
        return {(spec.lower_ticker, "yes"), (spec.higher_ticker, "no")}
    if st == "less":
        return {(spec.lower_ticker, "no"), (spec.higher_ticker, "yes")}
    raise ValueError("live probe supports only greater/less strike types")


def validate_spec(spec: NestedLiveProbeSpec, *, now: datetime | None = None) -> tuple[Decimal, Decimal]:
    """Return (locked_profit, hedge_fee) after all first-probe gates pass."""
    if D(spec.quantity) != MAX_QUANTITY:
        raise ValueError("first nested live probe is exactly one contract")
    if spec.fee_type != "quadratic":
        raise ValueError("live probe requires mapped quadratic fee type")
    if spec.scheduled_fee_changes:
        raise ValueError("scheduled fee change present; fail closed")
    if D(spec.fee_multiplier) < 0:
        raise ValueError("fee multiplier cannot be negative")
    if not (1 <= int(spec.maker_rest_seconds) <= MAX_MAKER_REST_SECONDS):
        raise ValueError("maker rest window must be 1..60 seconds")
    if not spec.event_ticker or not spec.lower_ticker.startswith(spec.event_ticker + "-") or not spec.higher_ticker.startswith(spec.event_ticker + "-"):
        raise ValueError("both legs must belong to the exact same event")

    actual = {(spec.maker_ticker, spec.maker_side.lower()), (spec.hedge_ticker, spec.hedge_side.lower())}
    if actual != _expected_nested_legs(spec):
        raise ValueError("maker/hedge legs do not match the proven nested payoff orientation")
    if spec.maker_ticker == spec.hedge_ticker:
        raise ValueError("maker and hedge markets must differ")

    maker_cost = D(spec.maker_cost)
    hedge_cost = D(spec.hedge_cost)
    for label, cost in (("maker", maker_cost), ("hedge", hedge_cost)):
        if not MIN_OUTCOME_COST <= cost <= MAX_OUTCOME_COST:
            raise ValueError(f"{label} outcome cost must stay inside 1c..99c")

    current = now or datetime.now(NY)
    close = spec.event_close
    if close.tzinfo is None:
        raise ValueError("event_close must be timezone-aware")
    seconds_to_close = D(str((close.astimezone(NY) - current.astimezone(NY)).total_seconds()))
    if seconds_to_close <= 0:
        raise ValueError("event is already closed")
    if seconds_to_close > MAX_TIME_TO_CLOSE_SECONDS:
        raise ValueError("capital lock exceeds six-hour first-probe ceiling")

    locked, fee = maker_then_taker_profit(
        maker_price=maker_cost,
        hedge_price=hedge_cost,
        fee_multiplier=D(spec.fee_multiplier),
        quantity=MAX_QUANTITY,
    )
    if D(spec.target_profit) < MIN_TARGET_PROFIT:
        raise ValueError("first live probe target cannot be below three cents")
    if locked < D(spec.target_profit):
        raise ValueError("exact payload economics no longer preserve target profit")
    return locked, fee


def live_probe_authorized(cli_live: bool, *, consume_token: bool = False) -> bool:
    """Require CLI opt-in, environment gate, and a one-use arm-token file."""
    if not cli_live or os.getenv("ENABLE_KALSHI_NESTED_LIVE_PROBES", "NO") != "YES":
        return False
    token_path = os.getenv("KALSHI_NESTED_LIVE_ARM_TOKEN_FILE", "").strip()
    if not token_path:
        return False
    token = Path(token_path)
    if not token.is_file():
        return False
    if consume_token:
        token.unlink(missing_ok=True)
    return True


def build_v2_payloads(spec: NestedLiveProbeSpec, *, now_epoch: int | None = None) -> tuple[dict, dict]:
    """Build the exact short-lived post-only maker and IOC hedge payloads."""
    validate_spec(spec)
    now_s = int(time.time()) if now_epoch is None else int(now_epoch)
    root = f"nested-probe-{uuid.uuid4().hex[:18]}"
    maker = {
        "ticker": spec.maker_ticker,
        "client_order_id": root + "-m",
        "side": outcome_to_v2_book_side(spec.maker_side),
        "count": "1.00",
        "price": str(outcome_cost_to_v2_price(spec.maker_side, D(spec.maker_cost))),
        "time_in_force": "good_till_canceled",
        "self_trade_prevention_type": "taker_at_cross",
        "expiration_time": now_s + int(spec.maker_rest_seconds),
        "post_only": True,
        "cancel_order_on_pause": True,
        "reduce_only": False,
        "subaccount": 0,
        "exchange_index": 0,
    }
    hedge = {
        "ticker": spec.hedge_ticker,
        "client_order_id": root + "-h",
        "side": outcome_to_v2_book_side(spec.hedge_side),
        "count": "1.00",
        "price": str(outcome_cost_to_v2_price(spec.hedge_side, D(spec.hedge_cost))),
        "time_in_force": "immediate_or_cancel",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        "cancel_order_on_pause": True,
        "reduce_only": False,
        "subaccount": 0,
        "exchange_index": 0,
    }
    return maker, hedge
