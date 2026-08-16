"""Read-only manual ticket for a bounded long-horizon nested execution test.

This module intentionally contains NO HTTP/WebSocket transport and NO live arm gate.
It exists only to turn a currently observed research candidate into exact V2-shaped
payloads an operator can inspect/manual-enter.  It does not weaken the production
live guard (3c target / <=6h close).

Research-only envelope:
- exactly 1 contract on each leg;
- same-event ``greater`` nested pair only for this first ticket;
- cheap seed (buy NO on the higher strike) exactly 1c;
- maker (buy YES on the lower strike) <=97c and <=2c improvement over live bid;
- mapped quadratic fee, no scheduled fee changes;
- event no more than 7 days away;
- modeled pair floor >=1c after the seed taker fee;
- seed is IOC; maker is post-only, short-lived.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import time
import uuid
from zoneinfo import ZoneInfo

from src.kalshi_nested_live_probe_guard import (
    D,
    outcome_cost_to_v2_price,
    outcome_to_v2_book_side,
)
from src.kalshi_nested_maker_shadow import maker_then_taker_profit

NY = ZoneInfo("America/New_York")
MAX_TEST_TIME_TO_CLOSE_SECONDS = D(str(7 * 24 * 3600))
MAX_TEST_MAKER_IMPROVEMENT = D("0.02")
MAX_TEST_SEED_COST = D("0.01")
MIN_TEST_LOCKED_PROFIT = D("0.01")


@dataclass(frozen=True)
class ManualNestedTestTicket:
    event_ticker: str
    event_close: datetime
    lower_ticker: str
    lower_strike: Decimal
    higher_ticker: str
    higher_strike: Decimal
    maker_bid: Decimal
    maker_cost: Decimal
    seed_cost: Decimal
    fee_type: str = "quadratic"
    fee_multiplier: Decimal = D("1")
    scheduled_fee_changes: tuple[dict, ...] = ()
    maker_rest_seconds: int = 10


def validate_manual_ticket(ticket: ManualNestedTestTicket, *, now: datetime | None = None) -> tuple[Decimal, Decimal]:
    if not ticket.event_ticker:
        raise ValueError("event ticker required")
    if not ticket.lower_ticker.startswith(ticket.event_ticker + "-") or not ticket.higher_ticker.startswith(ticket.event_ticker + "-"):
        raise ValueError("both legs must belong to the same event")
    if not D(ticket.lower_strike) < D(ticket.higher_strike):
        raise ValueError("nested strikes require lower < higher")
    if ticket.fee_type != "quadratic":
        raise ValueError("manual test requires mapped quadratic fees")
    if ticket.scheduled_fee_changes:
        raise ValueError("scheduled fee change present")
    if D(ticket.fee_multiplier) < 0:
        raise ValueError("invalid fee multiplier")
    if not 1 <= int(ticket.maker_rest_seconds) <= 60:
        raise ValueError("maker rest must be 1..60 seconds")

    maker_bid = D(ticket.maker_bid)
    maker_cost = D(ticket.maker_cost)
    seed_cost = D(ticket.seed_cost)
    if seed_cost != MAX_TEST_SEED_COST:
        raise ValueError("manual execution test requires exactly a 1c seed")
    if not D("0.01") <= maker_cost <= D("0.97"):
        raise ValueError("maker cost outside manual test envelope")
    improvement = maker_cost - maker_bid
    if improvement <= 0 or improvement > MAX_TEST_MAKER_IMPROVEMENT:
        raise ValueError("maker improvement outside manual test envelope")

    current = now or datetime.now(NY)
    if ticket.event_close.tzinfo is None:
        raise ValueError("event close must be timezone-aware")
    seconds_to_close = D(str((ticket.event_close.astimezone(NY) - current.astimezone(NY)).total_seconds()))
    if seconds_to_close <= 0 or seconds_to_close > MAX_TEST_TIME_TO_CLOSE_SECONDS:
        raise ValueError("manual test event must close within 7 days")

    locked, seed_fee = maker_then_taker_profit(
        maker_price=maker_cost,
        hedge_price=seed_cost,
        fee_multiplier=D(ticket.fee_multiplier),
        quantity=D("1"),
    )
    if locked < MIN_TEST_LOCKED_PROFIT:
        raise ValueError("manual test no longer preserves 1c pair floor")
    return locked, seed_fee


def build_manual_payloads(ticket: ManualNestedTestTicket, *, now_epoch: int | None = None) -> tuple[dict, dict]:
    """Return (seed IOC first, post-only maker second); no network calls."""
    locked, seed_fee = validate_manual_ticket(ticket)
    now_s = int(time.time()) if now_epoch is None else int(now_epoch)
    root = f"nested-manual-{uuid.uuid4().hex[:16]}"

    # Proven greater-strike relationship: YES(lower) + NO(higher).
    seed = {
        "ticker": ticket.higher_ticker,
        "client_order_id": root + "-seed",
        "side": outcome_to_v2_book_side("no"),
        "count": "1.00",
        "price": str(outcome_cost_to_v2_price("no", D(ticket.seed_cost))),
        "time_in_force": "immediate_or_cancel",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        "cancel_order_on_pause": True,
        "reduce_only": False,
        "subaccount": 0,
        "exchange_index": 0,
    }
    maker = {
        "ticker": ticket.lower_ticker,
        "client_order_id": root + "-maker",
        "side": outcome_to_v2_book_side("yes"),
        "count": "1.00",
        "price": str(outcome_cost_to_v2_price("yes", D(ticket.maker_cost))),
        "time_in_force": "good_till_canceled",
        "self_trade_prevention_type": "taker_at_cross",
        "expiration_time": now_s + int(ticket.maker_rest_seconds),
        "post_only": True,
        "cancel_order_on_pause": True,
        "reduce_only": False,
        "subaccount": 0,
        "exchange_index": 0,
    }
    return seed, maker


def describe_ticket(ticket: ManualNestedTestTicket) -> dict:
    locked, seed_fee = validate_manual_ticket(ticket)
    seed, maker = build_manual_payloads(ticket)
    return {
        "classification": "READ_ONLY_MANUAL_EXECUTION_TEST_TICKET",
        "event_ticker": ticket.event_ticker,
        "event_close": ticket.event_close.isoformat(),
        "lower_ticker": ticket.lower_ticker,
        "lower_strike": str(ticket.lower_strike),
        "higher_ticker": ticket.higher_ticker,
        "higher_strike": str(ticket.higher_strike),
        "maker_bid": str(ticket.maker_bid),
        "maker_cost": str(ticket.maker_cost),
        "maker_improvement": str(D(ticket.maker_cost) - D(ticket.maker_bid)),
        "seed_cost": str(ticket.seed_cost),
        "modeled_seed_taker_fee": str(seed_fee),
        "modeled_seed_only_cash_outlay": str(D(ticket.seed_cost) + seed_fee),
        "modeled_min_pair_profit": str(locked),
        "execution_order": "SEED_IOC_FIRST_THEN_POST_ONLY_MAKER",
        "seed_payload": seed,
        "maker_payload": maker,
        "actual_order_placed": False,
        "network_calls": False,
    }
