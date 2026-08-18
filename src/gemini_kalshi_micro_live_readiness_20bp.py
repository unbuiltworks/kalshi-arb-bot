"""Read-only 20 bp double-boundary micro-live readiness wrapper.

This module deliberately cannot place orders. It extends the exact-limit 50/100 ms
Gemini/Kalshi revalidation lane with the conservative gate intended for the first
one-contract live probe:

* exactly one contract;
* entry locked quote profit >= $0.03 after the scanner's configured fees;
* the same original quote-lock episode remains open;
* both original limit orders remain simultaneously marketable with displayed depth;
* both books are post-activation, <=250 ms old, and <=100 ms cross-venue skew;
* a fresh Gemini public-spot proxy stays on the same side of BOTH the Gemini Kaiko
  strike and Kalshi floor strike, at least 20 bp from the nearer boundary, for the
  entire positive episode and at the exact revalidation checkpoint.

Gemini public spot is not the official Kaiko settlement index. Therefore even a
``micro_live_ready_20bp`` result remains evidence only: trade_authorized is always
false and this module imports no order transport.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
import json
import logging
import time
from typing import Any

import httpx

from src import gemini_kalshi_basis_execution_revalidation_safe as guarded
from src.gemini_kalshi_predictive_hot import predicted_pair

exact = guarded.exact
basis = guarded.basis
D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_micro_live_readiness_20bp")
KALSHI_REST = "https://external-api.kalshi.com/trade-api/v2"
MIN_DOUBLE_BOUNDARY_BPS = D("20")
MIN_LOCKED_PROFIT = D("0.03")

# asset -> (window_end_iso, Kalshi threshold)
_KALSHI_TARGETS: dict[str, tuple[str, Decimal]] = {}
_PLAN_ENTRY_20BP: dict[tuple[str, str, int], bool] = {}
_COUNTERS = {
    "checks_50ms": 0,
    "checks_100ms": 0,
    "ready_50ms": 0,
    "ready_100ms": 0,
}

_ORIGINAL_COLLECT_WITH_BASIS = basis._collect_all_with_basis
_ORIGINAL_BASIS_SNAPSHOT = basis.basis_snapshot
_ORIGINAL_OBSERVE = basis.BasisEpisodeState.observe
_ORIGINAL_PLAN_FROM_BOOKS = exact._plan_from_books


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def kalshi_target(market: dict[str, Any]) -> Decimal | None:
    """Extract one unambiguous Kalshi settlement threshold; otherwise fail closed."""
    vals = [v for v in (_dec(market.get("floor_strike")), _dec(market.get("cap_strike"))) if v is not None]
    unique = sorted(set(vals))
    functional = _dec(market.get("functional_strike"))
    if len(unique) == 1:
        return unique[0]
    if functional is not None and (not unique or functional in unique):
        return functional
    return None


def augment_double_boundary(snapshot: dict[str, Any] | None, *, kalshi_threshold: Decimal | None) -> dict[str, Any] | None:
    """Add the prospective 20 bp double-boundary gate to a basis snapshot."""
    if snapshot is None:
        return None
    out = dict(snapshot)
    spot = _dec(out.get("spot_mid"))
    gemini_strike = _dec(out.get("kaiko_strike"))
    if spot is None or gemini_strike is None or gemini_strike == 0 or kalshi_threshold in {None, D("0")}:
        out.update({
            "kalshi_target": str(kalshi_threshold) if kalshi_threshold is not None else None,
            "same_side_of_both_boundaries": False,
            "min_abs_double_boundary_margin_bps": None,
            "research_double_boundary_gate_20bp_fresh_1000ms": False,
        })
        return out

    assert kalshi_threshold is not None
    gemini_margin = (spot - gemini_strike) / gemini_strike * D("10000")
    kalshi_margin = (spot - kalshi_threshold) / kalshi_threshold * D("10000")
    same_side = (spot > gemini_strike) == (spot >= kalshi_threshold)
    min_margin = min(abs(gemini_margin), abs(kalshi_margin))
    fresh = bool(out.get("proxy_fresh_1000ms"))
    out.update({
        "kalshi_target": str(kalshi_threshold),
        "gemini_boundary_margin_bps": str(gemini_margin),
        "kalshi_boundary_margin_bps": str(kalshi_margin),
        "same_side_of_both_boundaries": same_side,
        "min_abs_double_boundary_margin_bps": str(min_margin),
        "research_double_boundary_gate_20bp_fresh_1000ms": bool(
            fresh and same_side and min_margin >= MIN_DOUBLE_BOUNDARY_BPS
        ),
    })
    return out


async def _kalshi_target_loop() -> None:
    """Fetch exact Kalshi thresholds once per predicted window, never on the hot path."""
    async with httpx.AsyncClient(timeout=10.0) as http:
        while True:
            window_end = basis._next_window_end()
            found: dict[str, tuple[str, Decimal]] = {}
            for asset in basis.ASSET_SYMBOL:
                pair = predicted_pair(asset)
                if abs((pair.window_end - window_end).total_seconds()) >= 1:
                    continue
                try:
                    response = await http.get(f"{KALSHI_REST}/markets/{pair.kalshi_ticker}")
                    if response.status_code != 200:
                        continue
                    market = response.json().get("market") or {}
                    target = kalshi_target(market)
                    if target is not None:
                        found[asset] = (window_end.isoformat(), target)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "CRYPTO_MICRO_READY_TARGET_ERROR asset=%s type=%s error=%s",
                        asset,
                        type(exc).__name__,
                        str(exc)[:160],
                    )
            _KALSHI_TARGETS.clear()
            _KALSHI_TARGETS.update(found)
            logger.info("CRYPTO_MICRO_READY_TARGET_CONTEXT %s", json.dumps({
                "window_end_utc": window_end.isoformat(),
                "assets_with_kalshi_target": sorted(found),
                "minimum_double_boundary_bps": str(MIN_DOUBLE_BOUNDARY_BPS),
                "trade_authorized": False,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")))
            await asyncio.sleep(max(0.05, window_end.timestamp() + 0.25 - time.time()))


def _basis_snapshot_20bp(asset: str, candidate_ms: int) -> dict[str, Any] | None:
    snapshot = _ORIGINAL_BASIS_SNAPSHOT(asset, candidate_ms)
    if snapshot is None:
        return None
    target_info = _KALSHI_TARGETS.get(asset)
    target = None
    if target_info is not None and target_info[0] == snapshot.get("basis_window_end_utc"):
        target = target_info[1]
    return augment_double_boundary(snapshot, kalshi_threshold=target)


def _observe_20bp(self, snapshot: dict[str, Any] | None) -> None:
    _ORIGINAL_OBSERVE(self, snapshot)
    gate20 = bool(snapshot and snapshot.get("research_double_boundary_gate_20bp_fresh_1000ms"))
    margin = _dec(snapshot.get("min_abs_double_boundary_margin_bps")) if snapshot else None
    if self.observations == 1:
        self.entry_gate_20bp = gate20
        self.all_events_gate_20bp = gate20
        self.min_abs_double_boundary_margin_bps = margin
    else:
        self.all_events_gate_20bp = bool(getattr(self, "all_events_gate_20bp", True) and gate20)
        prior = getattr(self, "min_abs_double_boundary_margin_bps", None)
        if margin is not None:
            self.min_abs_double_boundary_margin_bps = margin if prior is None else min(prior, margin)


def _plan_from_books_20bp(**kwargs):
    plan = _ORIGINAL_PLAN_FROM_BOOKS(**kwargs)
    snapshot = kwargs.get("snapshot")
    _PLAN_ENTRY_20BP[(plan.asset, plan.direction, int(plan.opened_ms))] = bool(
        snapshot and snapshot.get("research_double_boundary_gate_20bp_fresh_1000ms")
    )
    return plan


async def _collect_all_with_20bp(**kwargs):
    target_task = asyncio.create_task(_kalshi_target_loop(), name="micro-live-kalshi-targets")
    try:
        return await _ORIGINAL_COLLECT_WITH_BASIS(**kwargs)
    finally:
        target_task.cancel()
        await asyncio.gather(target_task, return_exceptions=True)


async def _probe_20bp(*, feed, plan, active_episodes, delays_ms=(50, 100)) -> None:
    previous = 0
    key = (plan.asset, plan.direction, int(plan.opened_ms))
    entry20 = bool(_PLAN_ENTRY_20BP.get(key, False))
    try:
        for delay_ms in delays_ms:
            await asyncio.sleep(max(0, delay_ms - previous) / 1000)
            previous = delay_ms
            now_ms = int(time.time() * 1000)
            g, k = feed.gemini, feed.kalshi
            same_window = bool(
                feed.active
                and feed.pair.gemini_symbol == plan.gemini_symbol
                and feed.pair.kalshi_ticker == plan.kalshi_ticker
            )
            checks = {
                "gemini_price_ok": False,
                "gemini_depth_ok": False,
                "kalshi_price_ok": False,
                "kalshi_depth_ok": False,
                "both_marketable": False,
            }
            gemini_age_ms = None
            kalshi_age_ms = None
            book_skew_ms = None
            post_activation_books = False
            strict_fresh = False
            if same_window and g is not None and k is not None:
                checks = exact.same_limits_marketable(plan, g, k)
                gemini_age_ms = max(0, now_ms - int(g.ts_ms))
                kalshi_age_ms = max(0, now_ms - int(k.ts_ms))
                book_skew_ms = abs(int(g.ts_ms) - int(k.ts_ms))
                post_activation_books = bool(
                    int(g.ts_ms) >= int(feed.boundary_ms)
                    and int(k.ts_ms) >= int(feed.boundary_ms)
                )
                strict_fresh = bool(
                    post_activation_books
                    and max(gemini_age_ms, kalshi_age_ms) <= 250
                    and book_skew_ms <= 100
                )

            state = basis._BASIS_EPISODES.get((plan.asset, plan.direction))
            same_episode = guarded.same_original_episode(plan, active_episodes, state)
            snapshot = basis.basis_snapshot(plan.asset, now_ms) if same_window else None
            gate20_now = bool(snapshot and snapshot.get("research_double_boundary_gate_20bp_fresh_1000ms"))
            whole20 = bool(same_episode and state and getattr(state, "all_events_gate_20bp", False))
            one_contract = D(plan.quantity) == D("1")
            profit_floor = D(plan.entry_locked_profit) >= MIN_LOCKED_PROFIT
            ready = bool(
                entry20
                and same_episode
                and whole20
                and gate20_now
                and strict_fresh
                and checks["both_marketable"]
                and one_contract
                and profit_floor
            )
            _COUNTERS[f"checks_{delay_ms}ms"] += 1
            if ready:
                _COUNTERS[f"ready_{delay_ms}ms"] += 1

            logger.info("CRYPTO_HOT_MICRO_LIVE_READINESS %s", json.dumps({
                "asset": plan.asset,
                "direction": plan.direction,
                "opened_ms": int(plan.opened_ms),
                "delay_ms": delay_ms,
                "quantity": str(plan.quantity),
                "gemini_symbol": plan.gemini_symbol,
                "kalshi_ticker": plan.kalshi_ticker,
                "original_gemini_limit": str(plan.gemini_limit),
                "original_kalshi_limit": str(plan.kalshi_limit),
                "entry_locked_quote_profit": str(plan.entry_locked_profit),
                "entry_capital": str(plan.entry_capital),
                "minimum_locked_profit": str(MIN_LOCKED_PROFIT),
                "entry_double_boundary_20bp": entry20,
                "same_window": same_window,
                "same_original_episode": same_episode,
                **checks,
                "post_activation_books": post_activation_books,
                "gemini_age_ms": gemini_age_ms,
                "kalshi_age_ms": kalshi_age_ms,
                "book_skew_ms": book_skew_ms,
                "strict_fresh_250ms": strict_fresh,
                "double_boundary_20bp_now": gate20_now,
                "double_boundary_20bp_held_through_episode": whole20,
                "min_abs_double_boundary_margin_bps_now": (
                    snapshot.get("min_abs_double_boundary_margin_bps") if snapshot else None
                ),
                "one_contract": one_contract,
                "profit_floor_passes": profit_floor,
                "micro_live_ready_20bp": ready,
                "counters": dict(_COUNTERS),
                "official_live_kaiko": False,
                "settlement_status": exact.hot.SETTLEMENT_STATUS,
                "trade_authorized": False,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str))
    finally:
        _PLAN_ENTRY_20BP.pop(key, None)


def install() -> None:
    # Preserve the existing exact-revalidation pipeline; only enrich its basis context
    # and replace the probe with the stricter read-only 20 bp readiness check.
    basis.basis_snapshot = _basis_snapshot_20bp
    basis.BasisEpisodeState.observe = _observe_20bp
    basis._collect_all_with_basis = _collect_all_with_20bp
    exact._plan_from_books = _plan_from_books_20bp
    exact._probe = _probe_20bp


def main() -> None:
    install()
    logger.info(
        "CRYPTO_HOT_MICRO_LIVE_READINESS_ENABLED minimum_profit=0.03 quantity=1 "
        "double_boundary_min_bps=20 delays_ms=50,100 trade_authorized=false "
        "read_only=true order_entry=false"
    )
    guarded.main()


if __name__ == "__main__":
    main()
