"""Read-only Gemini-maker/Kalshi-taker feasibility telemetry for basis-risk q1.

This module deliberately cannot place, cancel, or modify orders.  It observes the
same synchronized books and threshold-dominance context as the isolated basis-risk
process, but evaluates a passive Gemini quote at the *existing valid best bid* before
the normal taker/taker >=3c candidate gate.

Why best bid rather than inventing a price increment: the visible bid is known to be
a valid instrument price.  If joining that bid cannot preserve the existing $0.03
after-fee floor against the current Kalshi taker hedge, no more-aggressive Gemini
maker quote can preserve it either.  If it can, the live maker->hedge transport still
requires a separate reviewed implementation and fill-time hedge revalidation.
"""
from __future__ import annotations

from decimal import Decimal
import json
import logging
import time
from typing import Any

from src import gemini_kalshi_basis_risk_q1_child as child
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee

D = Decimal
MIN_LOCKED_PROFIT = D("0.03")
MAX_QUOTE_SKEW_MS = 50
MAX_BOOK_AGE_MS = 250
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_maker_shadow")
_INSTALLED = False
_LAST_LOG: dict[tuple[str, str], tuple[tuple[Any, ...], float]] = {}


def _book_geometry(direction: str, g: Any, k: Any) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Return Gemini maker bid/ask and Kalshi taker hedge ask/depth in outcome space."""
    if direction == "GEMINI_YES_KALSHI_NO":
        return D(g.yes_bid), D(g.yes_ask), D(k.no_ask), D(k.no_ask_size)
    if direction == "KALSHI_YES_GEMINI_NO":
        return D(g.no_bid), D(g.no_ask), D(k.yes_ask), D(k.yes_ask_size)
    raise ValueError(direction)


def _should_log(key: tuple[str, str], state: tuple[Any, ...], *, economically_feasible: bool) -> bool:
    now = time.monotonic()
    prior = _LAST_LOG.get(key)
    interval = 2.0 if economically_feasible else 10.0
    if prior is not None and prior[0] == state and now - prior[1] < interval:
        return False
    _LAST_LOG[key] = (state, now)
    return True


def _observe(*, feed: Any, event: Any, kalshi_fee: Any, min_gap_bps: Decimal) -> None:
    event_ms = int(event[1])
    if event_ms < int(feed.boundary_ms):
        return
    g = getattr(feed, "eval_gemini", None)
    k = getattr(feed, "eval_kalshi", None)
    if g is None or k is None:
        return

    now_ms = int(time.time() * 1000)
    gemini_age_ms = max(0, now_ms - int(g.ts_ms))
    kalshi_age_ms = max(0, now_ms - int(k.ts_ms))
    quote_skew_ms = abs(int(g.ts_ms) - int(k.ts_ms))
    post_activation = bool(int(g.ts_ms) >= int(feed.boundary_ms) and int(k.ts_ms) >= int(feed.boundary_ms))
    fresh = bool(
        post_activation
        and max(gemini_age_ms, kalshi_age_ms) <= MAX_BOOK_AGE_MS
        and quote_skew_ms <= MAX_QUOTE_SKEW_MS
    )
    if not fresh:
        return

    readiness = child.v2.readiness
    basis = readiness.basis
    snapshot = basis.basis_snapshot(feed.asset, now_ms)
    if snapshot is None:
        return
    target_info = readiness._KALSHI_TARGETS.get(feed.asset)
    if target_info is None:
        return
    target_window, target_value = target_info
    if target_window != snapshot.get("basis_window_end_utc"):
        return
    gemini_strike = child._dec(snapshot.get("kaiko_strike"))
    if gemini_strike is None:
        return

    for direction in ("GEMINI_YES_KALSHI_NO", "KALSHI_YES_GEMINI_NO"):
        auth = child.dominance_authorization(
            direction=direction,
            gemini_strike=gemini_strike,
            kalshi_target=target_value,
            min_gap_bps=min_gap_bps,
        )
        maker_bid, maker_ask, hedge_ask, hedge_depth = _book_geometry(direction, g, k)
        if not (D("0") < maker_bid < D("1") and D("0") < maker_ask < D("1") and D("0") < hedge_ask < D("1")):
            continue
        maker_fee = gemini_predictions_maker_fee(maker_bid, D("1"))
        hedge_fee = D(kalshi_fee(hedge_ask, D("1")))
        total_cost = maker_bid + maker_fee + hedge_ask + hedge_fee
        locked_profit = D("1") - total_cost
        economically_feasible = bool(
            maker_bid < maker_ask
            and hedge_depth >= D("1")
            and locked_profit >= MIN_LOCKED_PROFIT
        )
        dominance_safe = bool(auth.get("authorized"))
        live_candidate_shadow = bool(dominance_safe and economically_feasible)
        taker_to_bid_improvement = maker_ask - maker_bid
        floor_headroom = locked_profit - MIN_LOCKED_PROFIT
        payload = {
            "asset": feed.asset,
            "direction": direction,
            "gemini_symbol": feed.pair.gemini_symbol,
            "kalshi_ticker": feed.pair.kalshi_ticker,
            "basis_window_end_utc": snapshot.get("basis_window_end_utc"),
            "threshold_gap_bps": auth.get("threshold_gap_bps"),
            "threshold_gap_abs_bps": auth.get("threshold_gap_abs_bps"),
            "dominance_safe": dominance_safe,
            "dominance_blocker": auth.get("blocker"),
            "configured_min_gap_bps": str(min_gap_bps),
            "gemini_passive_quote_source": "CURRENT_BEST_BID_VALID_PRICE",
            "gemini_maker_bid": str(maker_bid),
            "gemini_maker_ask": str(maker_ask),
            "gemini_maker_fee": str(maker_fee),
            "kalshi_taker_hedge_ask": str(hedge_ask),
            "kalshi_taker_hedge_depth": str(hedge_depth),
            "kalshi_taker_fee": str(hedge_fee),
            "hypothetical_total_cost": str(total_cost),
            "hypothetical_locked_profit": str(locked_profit),
            "minimum_locked_profit": str(MIN_LOCKED_PROFIT),
            "floor_headroom": str(floor_headroom),
            "taker_ask_to_passive_bid_improvement": str(taker_to_bid_improvement),
            "quote_skew_ms": quote_skew_ms,
            "gemini_age_ms": gemini_age_ms,
            "kalshi_age_ms": kalshi_age_ms,
            "post_activation_books": post_activation,
            "fresh_250ms_and_50ms_skew": fresh,
            "maker_join_bid_preserves_3c": economically_feasible,
            "dominance_and_maker_feasible": live_candidate_shadow,
            "classification": "LIVE_BASIS_RISK_MAKER_FEASIBILITY_SHADOW",
            "guaranteed_arb": False,
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }
        state = (
            str(maker_bid), str(maker_ask), str(hedge_ask), str(hedge_depth),
            str(locked_profit), str(auth.get("threshold_gap_abs_bps")),
            dominance_safe, economically_feasible, snapshot.get("basis_window_end_utc"),
        )
        key = (str(feed.asset), direction)
        if _should_log(key, state, economically_feasible=economically_feasible):
            logger.warning("CRYPTO_BASIS_MAKER_FEASIBILITY %s", json.dumps(payload, separators=(",", ":"), default=str))


def install(*, min_gap_bps: Decimal) -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    exact = child.v2.readiness.exact
    base = exact.base
    original_evaluate = base._evaluate_event

    def evaluate_with_maker_shadow(*args: Any, **kwargs: Any):
        result = original_evaluate(*args, **kwargs)
        try:
            feed = kwargs.get("feed")
            event = kwargs.get("event")
            kalshi_fee = kwargs.get("kalshi_fee")
            if feed is not None and event is not None and kalshi_fee is not None:
                _observe(
                    feed=feed,
                    event=event,
                    kalshi_fee=kalshi_fee,
                    min_gap_bps=D(min_gap_bps),
                )
        except Exception as exc:
            # Diagnostics must never alter live authorization or execution behavior.
            logger.warning("CRYPTO_BASIS_MAKER_FEASIBILITY_ERROR type=%s", type(exc).__name__)
        return result

    base._evaluate_event = evaluate_with_maker_shadow
    _INSTALLED = True
    logger.warning(
        "CRYPTO_BASIS_MAKER_FEASIBILITY_INSTALLED passive_quote=current_best_bid "
        "gemini_fee=maker kalshi_hedge=taker q=1 min_profit=0.03 "
        "execution_gates_changed=false trade_authorized=false order_entry=false"
    )
