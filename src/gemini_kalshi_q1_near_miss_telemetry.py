"""Read-only q=1 near-miss telemetry for the Gemini/Kalshi HOT scanner.

The live scanner only returns after-fee-positive candidates. This wrapper measures the
same q=1 top-of-book snapshots even when net cost is above $1, so a zero-candidate
window tells us whether books are one cent or twenty cents away. Candidate generation
and every execution/risk gate are unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import time
from typing import Any

from src import gemini_kalshi_multi_ws_hot as hot

D = Decimal
ONE = D("1")
Q = D("1")
HEARTBEAT_S = 10.0
logger = logging.getLogger("kalshi_arb.gemini_kalshi_q1_near_miss_telemetry")
_INSTALLED = False
_ORIGINAL_SMALL_Q = hot._small_q_candidates


@dataclass
class Best:
    observations: int = 0
    synchronized_q1_observations: int = 0
    capital_ok_observations: int = 0
    positive_after_fee_observations: int = 0
    floor_3c_observations: int = 0
    best_after_fee_profit: Decimal | None = None
    best_gross_profit: Decimal | None = None
    raw_cost: Decimal | None = None
    fees: Decimal | None = None
    net_cost: Decimal | None = None
    leg1_price: Decimal | None = None
    leg2_price: Decimal | None = None
    leg1_depth: Decimal | None = None
    leg2_depth: Decimal | None = None
    quote_skew_ms: int | None = None
    capital_ok_at_best: bool | None = None
    next_log_mono: float = 0.0


_STATE: dict[tuple[str, str, str], Best] = {}


def _asset(g_market: str) -> str:
    text = str(g_market or "").upper()
    for asset in ("BTC", "ETH", "SOL", "XRP"):
        if f"-{asset}15M" in text:
            return asset
    return "UNKNOWN"


def _observe(*, g: Any, k: Any, kalshi_fee: Any, max_quote_skew_ms: int, capital_limit: Decimal, direction: str) -> None:
    skew = abs(int(g.ts_ms) - int(k.ts_ms))
    if direction == "GEMINI_YES_KALSHI_NO":
        p1, d1 = D(g.yes_ask), D(g.yes_ask_size)
        p2, d2 = D(k.no_ask), D(k.no_ask_size)
        f1 = hot.gemini_predictions_taker_fee(p1, Q)
        f2 = kalshi_fee(p2, Q)
    else:
        p1, d1 = D(k.yes_ask), D(k.yes_ask_size)
        p2, d2 = D(g.no_ask), D(g.no_ask_size)
        f1 = kalshi_fee(p1, Q)
        f2 = hot.gemini_predictions_taker_fee(p2, Q)

    key = (str(g.market_id), str(k.market_id), direction)
    state = _STATE.setdefault(key, Best())
    state.observations += 1
    now_mono = time.monotonic()

    depth_ok = d1 >= Q and d2 >= Q
    skew_ok = skew <= int(max_quote_skew_ms)
    raw = p1 + p2
    fees = D(f1) + D(f2)
    net = raw + fees
    gross_profit = ONE - raw
    after_fee_profit = ONE - net
    capital_ok = net <= D(capital_limit)
    synchronized_q1 = depth_ok and skew_ok

    if synchronized_q1:
        state.synchronized_q1_observations += 1
        if capital_ok:
            state.capital_ok_observations += 1
        if after_fee_profit > 0:
            state.positive_after_fee_observations += 1
        if after_fee_profit >= D("0.03"):
            state.floor_3c_observations += 1
        if state.best_after_fee_profit is None or after_fee_profit > state.best_after_fee_profit:
            state.best_after_fee_profit = after_fee_profit
            state.best_gross_profit = gross_profit
            state.raw_cost = raw
            state.fees = fees
            state.net_cost = net
            state.leg1_price = p1
            state.leg2_price = p2
            state.leg1_depth = d1
            state.leg2_depth = d2
            state.quote_skew_ms = skew
            state.capital_ok_at_best = capital_ok

    if now_mono >= state.next_log_mono:
        state.next_log_mono = now_mono + HEARTBEAT_S
        best = state.best_after_fee_profit
        logger.warning(
            "CRYPTO_Q1_NEAR_MISS %s",
            json.dumps({
                "asset": _asset(g.market_id),
                "direction": direction,
                "gemini_symbol": str(g.market_id),
                "kalshi_ticker": str(k.market_id),
                "observations": state.observations,
                "synchronized_q1_observations": state.synchronized_q1_observations,
                "capital_ok_observations": state.capital_ok_observations,
                "positive_after_fee_observations": state.positive_after_fee_observations,
                "floor_3c_observations": state.floor_3c_observations,
                "best_gross_profit": str(state.best_gross_profit) if state.best_gross_profit is not None else None,
                "best_after_fee_profit": str(best) if best is not None else None,
                "distance_to_positive_after_fee": str(max(D("0"), -best)) if best is not None else None,
                "distance_to_3c_floor": str(max(D("0"), D("0.03") - best)) if best is not None else None,
                "best_raw_cost": str(state.raw_cost) if state.raw_cost is not None else None,
                "best_fees": str(state.fees) if state.fees is not None else None,
                "best_net_cost": str(state.net_cost) if state.net_cost is not None else None,
                "best_leg1_price": str(state.leg1_price) if state.leg1_price is not None else None,
                "best_leg2_price": str(state.leg2_price) if state.leg2_price is not None else None,
                "best_leg1_depth": str(state.leg1_depth) if state.leg1_depth is not None else None,
                "best_leg2_depth": str(state.leg2_depth) if state.leg2_depth is not None else None,
                "best_quote_skew_ms": state.quote_skew_ms,
                "capital_ok_at_best": state.capital_ok_at_best,
                "configured_max_quote_skew_ms": int(max_quote_skew_ms),
                "configured_capital_limit": str(capital_limit),
                "quantity": "1",
                "economics_changed": False,
                "trade_authorized": False,
                "order_entry": False,
            }, separators=(",", ":")),
        )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    def wrapped(g, k, *, kalshi_fee, max_quote_skew_ms, capital_limit):
        for direction in ("GEMINI_YES_KALSHI_NO", "KALSHI_YES_GEMINI_NO"):
            try:
                _observe(g=g, k=k, kalshi_fee=kalshi_fee, max_quote_skew_ms=max_quote_skew_ms, capital_limit=D(capital_limit), direction=direction)
            except Exception as exc:
                logger.warning("CRYPTO_Q1_NEAR_MISS_TELEMETRY_ERROR type=%s error=%s", type(exc).__name__, str(exc)[:180])
        return _ORIGINAL_SMALL_Q(g, k, kalshi_fee=kalshi_fee, max_quote_skew_ms=max_quote_skew_ms, capital_limit=capital_limit)

    hot._small_q_candidates = wrapped
    _INSTALLED = True
    logger.warning("CRYPTO_Q1_NEAR_MISS_TELEMETRY_INSTALLED quantity=1 heartbeat_s=10 synchronized_before_capital=true economics_changed=false order_entry=false")
