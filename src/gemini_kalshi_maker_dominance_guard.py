"""Threshold-dominance guard for the isolated Gemini/Kalshi maker lane.

Gemini and Kalshi 15-minute crypto contracts can use different thresholds and
different settlement indexes.  Maker economics alone are therefore insufficient
for order authorization.  This module fetches the exact matched-window Gemini
strike and exact Kalshi market target, then permits candidate discovery only in
the threshold-dominance-safe orientation used by the bounded basis-risk lane.

This is still INDEX_MISMATCH_RESEARCH_ONLY, never guaranteed arbitrage.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import logging
from typing import Any

import httpx

from src import gemini_kalshi_maker_live_probe_q1 as base
from src.gemini_kalshi_fast_overlap import (
    GEMINI_REST,
    KALSHI_REST,
    _gemini_active_crypto,
    _gemini_meta,
    _gemini_symbol,
)
from src.gemini_kalshi_settlement_agreement import _gemini_up_contract

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_maker_dominance_guard")
MIN_GAP_BPS = D("0.5")
CLASSIFICATION = "INDEX_MISMATCH_RESEARCH_ONLY"


def _dec(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def gemini_strike(event: dict[str, Any]) -> Decimal | None:
    contract = _gemini_up_contract(event)
    if contract is None:
        return None
    strike = contract.get("strike") or {}
    if not isinstance(strike, dict):
        return None
    value = _dec(strike.get("value"))
    return value if value is not None and value > 0 else None


def kalshi_target(market: dict[str, Any]) -> Decimal | None:
    strikes = [
        value
        for value in (_dec(market.get("floor_strike")), _dec(market.get("cap_strike")))
        if value is not None
    ]
    unique = sorted(set(strikes))
    functional = _dec(market.get("functional_strike"))
    if len(unique) == 1:
        return unique[0]
    if functional is not None and (not unique or functional in unique):
        return functional
    return None


def authorize_geometry(*, direction: str, gemini_strike_value: Any, kalshi_target_value: Any,
                       min_gap_bps: Decimal = MIN_GAP_BPS) -> dict[str, Any]:
    g = _dec(gemini_strike_value)
    k = _dec(kalshi_target_value)
    floor = D(min_gap_bps)
    out: dict[str, Any] = {
        "authorized": False,
        "direction": str(direction),
        "gemini_strike": str(g) if g is not None else None,
        "kalshi_target": str(k) if k is not None else None,
        "threshold_gap_bps": None,
        "threshold_gap_abs_bps": None,
        "dominance_safe_direction": None,
        "min_gap_bps": str(floor),
        "blocker": None,
        "classification": CLASSIFICATION,
        "guaranteed_arb": False,
    }
    if floor < MIN_GAP_BPS:
        out["blocker"] = "MIN_GAP_BELOW_APPROVED_MAKER_FLOOR"
        return out
    if g is None or k is None or g <= 0:
        out["blocker"] = "MISSING_OR_INVALID_THRESHOLD"
        return out
    gap = k - g
    gap_bps = gap / g * D("10000")
    if gap > 0:
        safe = "GEMINI_YES_KALSHI_NO"
    elif gap < 0:
        safe = "KALSHI_YES_GEMINI_NO"
    else:
        safe = "SYMMETRIC_EQUAL_THRESHOLD"
    out.update({
        "threshold_gap_bps": str(gap_bps),
        "threshold_gap_abs_bps": str(abs(gap_bps)),
        "dominance_safe_direction": safe,
    })
    if safe == "SYMMETRIC_EQUAL_THRESHOLD":
        out["blocker"] = "EQUAL_THRESHOLD_NO_PROTECTIVE_GAP"
        return out
    if abs(gap_bps) < floor:
        out["blocker"] = "PROTECTIVE_GAP_BELOW_MINIMUM"
        return out
    if str(direction) != safe:
        out["blocker"] = "DANGEROUS_THRESHOLD_ORIENTATION"
        return out
    out["authorized"] = True
    out["blocker"] = "NONE"
    return out


async def fetch_pair_geometry(pair: Any, asset: str) -> dict[str, Any]:
    """Fetch exact same-window thresholds from authoritative public metadata."""
    asset = str(asset).upper()
    async with httpx.AsyncClient(timeout=5.0) as http:
        try:
            events = await _gemini_active_crypto(http)
        except Exception as exc:
            return {
                "authorized": False,
                "blocker": "GEMINI_METADATA_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "classification": CLASSIFICATION,
            }

        event = None
        for row in events:
            meta = _gemini_meta(row)
            if meta is None:
                continue
            row_asset, duration, end = meta
            if row_asset != asset or duration != 15:
                continue
            if abs((end - pair.window_end).total_seconds()) >= 1:
                continue
            if _gemini_symbol(row) != pair.gemini_symbol:
                continue
            event = row
            break
        g = gemini_strike(event) if event is not None else None
        if g is None:
            return {
                "authorized": False,
                "blocker": "EXACT_GEMINI_STRIKE_UNAVAILABLE",
                "gemini_symbol": pair.gemini_symbol,
                "classification": CLASSIFICATION,
            }

        try:
            response = await http.get(f"{KALSHI_REST}/markets/{pair.kalshi_ticker}")
        except Exception as exc:
            return {
                "authorized": False,
                "blocker": "KALSHI_METADATA_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "classification": CLASSIFICATION,
            }
        if response.status_code != 200:
            return {
                "authorized": False,
                "blocker": f"KALSHI_METADATA_HTTP_{response.status_code}",
                "classification": CLASSIFICATION,
            }
        market = response.json().get("market") or {}
        if str(market.get("ticker") or "") != str(pair.kalshi_ticker):
            return {
                "authorized": False,
                "blocker": "KALSHI_TICKER_IDENTITY_MISMATCH",
                "classification": CLASSIFICATION,
            }
        k = kalshi_target(market)
        if k is None:
            return {
                "authorized": False,
                "blocker": "EXACT_KALSHI_TARGET_UNAVAILABLE",
                "classification": CLASSIFICATION,
            }

    # Compute safe orientation once; the candidate-specific function below still
    # verifies that exact direction rather than trusting a string from discovery.
    probe = authorize_geometry(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_strike_value=g,
        kalshi_target_value=k,
    )
    safe = probe.get("dominance_safe_direction")
    if safe in (None, "SYMMETRIC_EQUAL_THRESHOLD"):
        return probe
    auth = authorize_geometry(
        direction=str(safe),
        gemini_strike_value=g,
        kalshi_target_value=k,
    )
    auth.update({
        "asset": asset,
        "window_end_utc": pair.window_end.isoformat(),
        "gemini_symbol": pair.gemini_symbol,
        "kalshi_ticker": pair.kalshi_ticker,
    })
    return auth


def candidate_for_authorized_direction(*, gemini: Any, kalshi: Any, fee_meta: Any,
                                       now_ms: int, direction: str):
    """Apply the existing maker economics to one already-authorized orientation."""
    if direction == "GEMINI_YES_KALSHI_NO":
        maker_outcome, hedge_outcome = "NO", "YES"
    elif direction == "KALSHI_YES_GEMINI_NO":
        maker_outcome, hedge_outcome = "YES", "NO"
    else:
        return None
    best_bid, bid_size, best_ask = base.maker_side_book(kalshi, maker_outcome)
    economic_max = base.max_safe_maker_price(
        direction=direction,
        gemini=gemini,
        fee_meta=fee_meta,
        target_profit=base.TARGET_PROFIT,
    )
    if economic_max is None:
        return None
    maker_price = base.desired_maker_price(
        policy=base.POLICY,
        best_bid=best_bid,
        best_ask=best_ask,
        economic_max=economic_max,
    )
    if maker_price is None or maker_price < best_bid:
        return None
    econ = base.quote_economics(
        direction=direction,
        maker_price=maker_price,
        gemini=gemini,
        fee_meta=fee_meta,
    )
    if econ is None or econ.locked_profit < base.TARGET_PROFIT:
        return None
    queue_ahead = bid_size if maker_price == best_bid else D("0")
    return base.Candidate(
        direction=direction,
        maker_outcome=maker_outcome,
        hedge_outcome=hedge_outcome,
        maker_price=maker_price,
        hedge_price=econ.hedge_price,
        locked_profit=econ.locked_profit,
        best_bid=best_bid,
        best_ask=best_ask,
        queue_ahead=queue_ahead,
        observed_ms=now_ms,
    )


def install_wait_guard() -> None:
    """Patch only this process's candidate wait; all live mutation stays in safe.run_once."""
    original_wait = base._wait_candidate

    async def guarded_wait(*, pair, fee_meta, timeout_s: float = 120.0):
        geometry = await fetch_pair_geometry(pair, base.ASSET)
        logger.warning(
            "MAKER_THRESHOLD_DOMINANCE_GATE %s",
            json.dumps({**geometry, "order_entry": False}, separators=(",", ":"), default=str),
        )
        if not geometry.get("authorized"):
            return None, None, None, None, []
        allowed = str(geometry["dominance_safe_direction"])
        prior_selector = base.candidate_from_books

        def selector(*, gemini, kalshi, fee_meta, now_ms: int):
            candidate = candidate_for_authorized_direction(
                gemini=gemini,
                kalshi=kalshi,
                fee_meta=fee_meta,
                now_ms=now_ms,
                direction=allowed,
            )
            if candidate is None:
                return None
            auth = authorize_geometry(
                direction=candidate.direction,
                gemini_strike_value=geometry.get("gemini_strike"),
                kalshi_target_value=geometry.get("kalshi_target"),
            )
            return candidate if auth.get("authorized") else None

        base.candidate_from_books = selector
        try:
            return await original_wait(pair=pair, fee_meta=fee_meta, timeout_s=timeout_s)
        finally:
            base.candidate_from_books = prior_selector

    base._wait_candidate = guarded_wait
    logger.warning(
        "MAKER_THRESHOLD_DOMINANCE_GUARD_INSTALLED min_gap_bps=%s btc_quarantined=true "
        "classification=%s guaranteed_arb=false",
        MIN_GAP_BPS,
        CLASSIFICATION,
    )
