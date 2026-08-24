"""Read-only Gemini-maker/Kalshi-taker feasibility telemetry for basis-risk q1.

This module deliberately cannot place, cancel, or modify orders. It observes the
same synchronized books and threshold-dominance context as the isolated basis-risk
process, but evaluates a passive Gemini quote at the existing valid best bid before
the normal taker/taker >=3c candidate gate.

The hook wraps ``basis_execution_revalidation._SAFE_EVALUATE`` because that function
is invoked on every hot evaluation *before* ``_small_q_candidates`` applies the
normal taker/taker economics gate. This lets us measure whether maker pricing could
create the missing price improvement without changing live authorization.

For every live Gemini instrument observed, public event metadata is also fetched and
logged once.  A future q=1 maker promotion must refuse mutation unless quantity=1 is
aligned to the exact contract quantityMinimum/quantityIncrement and the price is
aligned to priceMinimum/priceIncrement.  The metadata fetch itself is public/read-only.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src import gemini_kalshi_basis_risk_q1_child as child
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_kalshi_fast_overlap import _gemini_active_crypto

D = Decimal
MIN_LOCKED_PROFIT = D("0.03")
MAX_QUOTE_SKEW_MS = 50
MAX_BOOK_AGE_MS = 250
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_maker_shadow")
_INSTALLED = False
_LAST_LOG: dict[tuple[str, str], tuple[tuple[Any, ...], float]] = {}
_META: dict[str, dict[str, Any] | None] = {}
_META_TASKS: set[str] = set()


def _book_geometry(direction: str, g: Any, k: Any) -> tuple[Decimal, Decimal, Decimal, Decimal]:
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


def _aligned(value: Decimal, *, minimum: Decimal, increment: Decimal) -> bool:
    if increment <= 0 or value < minimum:
        return False
    return (value - minimum) % increment == 0


def _contract_meta(contract: dict[str, Any]) -> dict[str, Any]:
    q_inc = D(str(contract.get("quantityIncrement") or "0"))
    q_min = D(str(contract.get("quantityMinimum") or "0"))
    p_inc = D(str(contract.get("priceIncrement") or "0"))
    p_min = D(str(contract.get("priceMinimum") or "0"))
    quantity_one_aligned = _aligned(D("1"), minimum=q_min, increment=q_inc)
    # q=1 is indivisible only when the permitted quantity grid cannot express a
    # positive fill smaller than one contract.  This first maker canary refuses
    # fractional-fill instruments even if quantity=1 itself is a valid order size.
    quantity_one_indivisible = bool(quantity_one_aligned and q_inc >= D("1"))
    return {
        "instrumentSymbol": str(contract.get("instrumentSymbol") or contract.get("instrument_symbol") or ""),
        "status": contract.get("status"),
        "marketState": contract.get("marketState"),
        "quantityIncrement": str(q_inc),
        "quantityMinimum": str(q_min),
        "priceIncrement": str(p_inc),
        "priceMinimum": str(p_min),
        "quantity_one_aligned": quantity_one_aligned,
        "quantity_one_indivisible": quantity_one_indivisible,
        "maker_live_q1_metadata_gate": bool(
            quantity_one_indivisible
            and p_inc > 0
            and p_min > 0
            and str(contract.get("marketState") or "").lower() == "open"
        ),
    }


async def _fetch_metadata(symbol: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            events = await _gemini_active_crypto(http)
        found = None
        for event in events:
            for contract in event.get("contracts") or []:
                if not isinstance(contract, dict):
                    continue
                candidate = str(contract.get("instrumentSymbol") or contract.get("instrument_symbol") or "")
                if candidate == symbol:
                    found = _contract_meta(contract)
                    break
            if found is not None:
                break
        _META[symbol] = found
        logger.warning(
            "CRYPTO_BASIS_MAKER_CONTRACT_METADATA %s",
            json.dumps({
                "symbol": symbol,
                "found": found is not None,
                "metadata": found,
                "trade_authorized": False,
                "order_entry": False,
            }, separators=(",", ":"), default=str),
        )
    except Exception as exc:
        _META[symbol] = None
        logger.warning(
            "CRYPTO_BASIS_MAKER_CONTRACT_METADATA_ERROR symbol=%s type=%s",
            symbol, type(exc).__name__,
        )
    finally:
        _META_TASKS.discard(symbol)


def _ensure_metadata(symbol: str) -> None:
    if not symbol or symbol in _META or symbol in _META_TASKS:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _META_TASKS.add(symbol)
    loop.create_task(_fetch_metadata(symbol))


def contract_metadata(symbol: str) -> dict[str, Any] | None:
    """Expose the verified public contract grid to the future live maker module."""
    value = _META.get(str(symbol))
    return dict(value) if isinstance(value, dict) else None


def _observe(*, feed: Any, event: Any, kalshi_fee: Any, min_gap_bps: Decimal) -> None:
    event_ms = int(event[1])
    if event_ms < int(feed.boundary_ms):
        return
    _ensure_metadata(str(feed.pair.gemini_symbol))
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
    snapshot = readiness.basis.basis_snapshot(feed.asset, now_ms)
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
        metadata = contract_metadata(str(feed.pair.gemini_symbol))
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
            "floor_headroom": str(locked_profit - MIN_LOCKED_PROFIT),
            "taker_ask_to_passive_bid_improvement": str(maker_ask - maker_bid),
            "quote_skew_ms": quote_skew_ms,
            "gemini_age_ms": gemini_age_ms,
            "kalshi_age_ms": kalshi_age_ms,
            "post_activation_books": post_activation,
            "fresh_250ms_and_50ms_skew": fresh,
            "maker_join_bid_preserves_3c": economically_feasible,
            "dominance_and_maker_feasible": live_candidate_shadow,
            "contract_metadata_loaded": metadata is not None,
            "maker_live_q1_metadata_gate": bool(metadata and metadata.get("maker_live_q1_metadata_gate")),
            "classification": "LIVE_BASIS_RISK_MAKER_FEASIBILITY_SHADOW",
            "guaranteed_arb": False,
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }
        state = (
            str(maker_bid), str(maker_ask), str(hedge_ask), str(hedge_depth),
            str(locked_profit), str(auth.get("threshold_gap_abs_bps")),
            dominance_safe, economically_feasible,
            bool(metadata and metadata.get("maker_live_q1_metadata_gate")),
            snapshot.get("basis_window_end_utc"),
        )
        key = (str(feed.asset), direction)
        if _should_log(key, state, economically_feasible=economically_feasible):
            logger.warning("CRYPTO_BASIS_MAKER_FEASIBILITY %s", json.dumps(payload, separators=(",", ":"), default=str))


def install(*, min_gap_bps: Decimal) -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    exact = child.v2.readiness.exact
    original_safe_evaluate = exact._SAFE_EVALUATE

    def safe_evaluate_with_maker_shadow(*args: Any, **kwargs: Any):
        result = original_safe_evaluate(*args, **kwargs)
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
            logger.warning("CRYPTO_BASIS_MAKER_FEASIBILITY_ERROR type=%s", type(exc).__name__)
        return result

    exact._SAFE_EVALUATE = safe_evaluate_with_maker_shadow
    _INSTALLED = True
    logger.warning(
        "CRYPTO_BASIS_MAKER_FEASIBILITY_INSTALLED hook=exact_safe_evaluate_pre_taker_gate "
        "passive_quote=current_best_bid gemini_fee=maker kalshi_hedge=taker q=1 min_profit=0.03 "
        "metadata_grid_check=public_event_contract execution_gates_changed=false "
        "trade_authorized=false order_entry=false"
    )
