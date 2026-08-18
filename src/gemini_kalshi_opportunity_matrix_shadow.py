"""Read-only multi-asset opportunity matrix for Gemini/Kalshi 15-minute crypto.

This module expands the exact-limit revalidation research lane without weakening or
modifying the existing strict 20 bp executor. It measures BTC/ETH/SOL/XRP opportunities
across:

* apparent locked-profit entry floors: 2c and 3c;
* double-boundary safety margins: 5/10/15/20 bp from BOTH Gemini and Kalshi thresholds;
* exact original-limit revalidation delays: 25/50/100 ms.

Every matrix result still requires the original quote-lock episode, exact original
limits, displayed depth, post-activation books, <=250 ms book age, and <=50 ms
cross-venue receive skew. Gemini public spot remains a proxy rather than the official
Kaiko settlement index, so this module is research-only and contains no order transport.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json
import logging
import time
from typing import Any

from src import gemini_kalshi_basis_execution_revalidation_safe as guarded
from src import gemini_kalshi_micro_live_readiness_20bp as targeting

exact = guarded.exact
basis = guarded.basis
hot = exact.hot
safe = exact.safe
D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_opportunity_matrix_shadow")

BOUNDARY_BPS = (D("5"), D("10"), D("15"), D("20"))
PROFIT_FLOORS = (D("0.02"), D("0.03"))
DELAYS_MS = (25, 50, 100)
MAX_BOOK_AGE_MS = 250
MAX_BOOK_SKEW_MS = 50

_ORIGINAL_BASIS_SNAPSHOT = basis.basis_snapshot
_ORIGINAL_OBSERVE = basis.BasisEpisodeState.observe
_ORIGINAL_COLLECT_WITH_BASIS = basis._collect_all_with_basis
_ORIGINAL_PLAN_FROM_BOOKS = exact._plan_from_books
_PLAN_ENTRY_LADDER: dict[tuple[str, str, int], dict[str, bool]] = {}
_COUNTERS: defaultdict[str, int] = defaultdict(int)
_INSTALLED = False


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def _bp_label(value: Decimal) -> str:
    return f"{int(value)}bp"


def _profit_label(value: Decimal) -> str:
    return f"{int(value * 100)}c"


def augment_double_boundary_ladder(
    snapshot: dict[str, Any] | None,
    *,
    kalshi_threshold: Decimal | None,
) -> dict[str, Any] | None:
    """Add 5/10/15/20 bp same-side gates to one historical basis snapshot."""
    if snapshot is None:
        return None
    out = dict(snapshot)
    spot = _dec(out.get("spot_mid"))
    gemini_strike = _dec(out.get("kaiko_strike"))
    ladder = {_bp_label(bp): False for bp in BOUNDARY_BPS}
    if (
        spot is None
        or gemini_strike is None
        or gemini_strike == 0
        or kalshi_threshold is None
        or kalshi_threshold == 0
    ):
        out.update({
            "kalshi_target": str(kalshi_threshold) if kalshi_threshold is not None else None,
            "same_side_of_both_boundaries": False,
            "gemini_boundary_margin_bps": None,
            "kalshi_boundary_margin_bps": None,
            "min_abs_double_boundary_margin_bps": None,
            "double_boundary_ladder": ladder,
        })
        return out

    gemini_margin = (spot - gemini_strike) / gemini_strike * D("10000")
    kalshi_margin = (spot - kalshi_threshold) / kalshi_threshold * D("10000")
    same_side = (spot > gemini_strike) == (spot >= kalshi_threshold)
    min_margin = min(abs(gemini_margin), abs(kalshi_margin))
    fresh = bool(out.get("proxy_fresh_1000ms"))
    ladder = {
        _bp_label(bp): bool(fresh and same_side and min_margin >= bp)
        for bp in BOUNDARY_BPS
    }
    out.update({
        "kalshi_target": str(kalshi_threshold),
        "gemini_boundary_margin_bps": str(gemini_margin),
        "kalshi_boundary_margin_bps": str(kalshi_margin),
        "same_side_of_both_boundaries": same_side,
        "min_abs_double_boundary_margin_bps": str(min_margin),
        "double_boundary_ladder": ladder,
    })
    return out


def _matrix_snapshot(asset: str, candidate_ms: int) -> dict[str, Any] | None:
    snapshot = _ORIGINAL_BASIS_SNAPSHOT(asset, candidate_ms)
    if snapshot is None:
        return None
    target_info = targeting._KALSHI_TARGETS.get(asset)
    target = None
    if target_info is not None and target_info[0] == snapshot.get("basis_window_end_utc"):
        target = target_info[1]
    return augment_double_boundary_ladder(snapshot, kalshi_threshold=target)


def _observe_matrix(self, snapshot: dict[str, Any] | None) -> None:
    _ORIGINAL_OBSERVE(self, snapshot)
    ladder = (snapshot or {}).get("double_boundary_ladder") or {}
    margin = _dec((snapshot or {}).get("min_abs_double_boundary_margin_bps"))
    first = self.observations == 1
    for bp in BOUNDARY_BPS:
        label = _bp_label(bp)
        gate = bool(ladder.get(label, False))
        entry_attr = f"entry_double_boundary_{label}"
        whole_attr = f"all_events_double_boundary_{label}"
        if first:
            setattr(self, entry_attr, gate)
            setattr(self, whole_attr, gate)
        else:
            setattr(self, whole_attr, bool(getattr(self, whole_attr, True) and gate))
    if first:
        self.min_abs_double_boundary_margin_bps = margin
    elif margin is not None:
        prior = getattr(self, "min_abs_double_boundary_margin_bps", None)
        self.min_abs_double_boundary_margin_bps = margin if prior is None else min(prior, margin)


def _plan_from_books_matrix(**kwargs):
    plan = _ORIGINAL_PLAN_FROM_BOOKS(**kwargs)
    snapshot = kwargs.get("snapshot") or {}
    ladder = snapshot.get("double_boundary_ladder") or {}
    _PLAN_ENTRY_LADDER[(plan.asset, plan.direction, int(plan.opened_ms))] = {
        _bp_label(bp): bool(ladder.get(_bp_label(bp), False)) for bp in BOUNDARY_BPS
    }
    return plan


def build_matrix(
    *,
    entry_profit: Decimal,
    base_ready: bool,
    entry_ladder: dict[str, bool],
    whole_ladder: dict[str, bool],
    now_ladder: dict[str, bool],
) -> dict[str, dict[str, bool]]:
    """Return profit-floor x boundary-ladder pass/fail decisions."""
    return {
        _profit_label(profit): {
            _bp_label(bp): bool(
                entry_profit >= profit
                and base_ready
                and entry_ladder.get(_bp_label(bp), False)
                and whole_ladder.get(_bp_label(bp), False)
                and now_ladder.get(_bp_label(bp), False)
            )
            for bp in BOUNDARY_BPS
        }
        for profit in PROFIT_FLOORS
    }


async def _collect_all_with_matrix(**kwargs):
    requested_assets = tuple(
        dict.fromkeys(
            str(asset).upper()
            for asset in kwargs.get("assets", ("BTC", "ETH", "SOL", "XRP"))
            if str(asset).upper() in basis.ASSET_SYMBOL
        )
    )
    target_task = asyncio.create_task(
        targeting._kalshi_target_loop(requested_assets),
        name="opportunity-matrix-kalshi-targets",
    )
    try:
        return await _ORIGINAL_COLLECT_WITH_BASIS(**kwargs)
    finally:
        target_task.cancel()
        await asyncio.gather(target_task, return_exceptions=True)


async def _probe_matrix(*, feed, plan, active_episodes, delays_ms=DELAYS_MS) -> None:
    previous = 0
    key = (plan.asset, plan.direction, int(plan.opened_ms))
    entry_ladder = dict(_PLAN_ENTRY_LADDER.get(key, {}))
    try:
        for delay_ms in delays_ms:
            await asyncio.sleep(max(0, int(delay_ms) - previous) / 1000)
            previous = int(delay_ms)
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
                    and max(gemini_age_ms, kalshi_age_ms) <= MAX_BOOK_AGE_MS
                    and book_skew_ms <= MAX_BOOK_SKEW_MS
                )

            state = basis._BASIS_EPISODES.get((plan.asset, plan.direction))
            same_episode = guarded.same_original_episode(plan, active_episodes, state)
            snapshot = basis.basis_snapshot(plan.asset, now_ms) if same_window else None
            now_ladder = dict((snapshot or {}).get("double_boundary_ladder") or {})
            whole_ladder = {
                _bp_label(bp): bool(
                    same_episode
                    and state is not None
                    and getattr(state, f"all_events_double_boundary_{_bp_label(bp)}", False)
                )
                for bp in BOUNDARY_BPS
            }
            base_ready = bool(
                same_episode
                and strict_fresh
                and checks["both_marketable"]
                and D(plan.quantity) == D("1")
            )
            matrix = build_matrix(
                entry_profit=D(plan.entry_locked_profit),
                base_ready=base_ready,
                entry_ladder=entry_ladder,
                whole_ladder=whole_ladder,
                now_ladder=now_ladder,
            )
            any_pass = any(value for row in matrix.values() for value in row.values())
            _COUNTERS[f"checks_{delay_ms}ms"] += 1
            for profit_label, row in matrix.items():
                for bp_label, passed in row.items():
                    if passed:
                        _COUNTERS[f"pass_{profit_label}_{bp_label}_{delay_ms}ms"] += 1

            logger.info("CRYPTO_OPPORTUNITY_MATRIX_CHECK %s", json.dumps({
                "asset": plan.asset,
                "direction": plan.direction,
                "opened_ms": int(plan.opened_ms),
                "delay_ms": int(delay_ms),
                "quantity": str(plan.quantity),
                "gemini_symbol": plan.gemini_symbol,
                "kalshi_ticker": plan.kalshi_ticker,
                "original_gemini_limit": str(plan.gemini_limit),
                "original_kalshi_limit": str(plan.kalshi_limit),
                "entry_locked_quote_profit": str(plan.entry_locked_profit),
                "entry_capital": str(plan.entry_capital),
                "same_window": same_window,
                "same_original_episode": same_episode,
                **checks,
                "post_activation_books": post_activation_books,
                "gemini_age_ms": gemini_age_ms,
                "kalshi_age_ms": kalshi_age_ms,
                "book_skew_ms": book_skew_ms,
                "strict_fresh_250ms_and_50ms_skew": strict_fresh,
                "entry_double_boundary_ladder": entry_ladder,
                "double_boundary_ladder_now": now_ladder,
                "double_boundary_ladder_held_through_episode": whole_ladder,
                "min_abs_double_boundary_margin_bps_now": (
                    snapshot.get("min_abs_double_boundary_margin_bps") if snapshot else None
                ),
                "matrix": matrix,
                "any_matrix_pass": any_pass,
                "counters": dict(_COUNTERS),
                "profit_interpretation": "ENTRY_APPARENT_QUOTE_LOCK_NOT_REALIZED_PNL",
                "official_live_kaiko": False,
                "settlement_status": hot.SETTLEMENT_STATUS,
                "trade_authorized": False,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str))
    finally:
        _PLAN_ENTRY_LADDER.pop(key, None)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    # Install the already-tested exact revalidation evaluator first, then enrich only
    # its research snapshot/probe path. This intentionally never imports order transport.
    exact.install()
    basis.basis_snapshot = _matrix_snapshot
    basis.BasisEpisodeState.observe = _observe_matrix
    basis._collect_all_with_basis = _collect_all_with_matrix
    # basis.install() has already bound hot.collect_all to the old function object.
    # Rebind explicitly so the target-cache task is actually part of this service.
    hot.collect_all = _collect_all_with_matrix
    exact._plan_from_books = _plan_from_books_matrix
    exact._probe = _probe_matrix
    _INSTALLED = True


def main() -> None:
    install()
    logger.info(
        "CRYPTO_OPPORTUNITY_MATRIX_ENABLED assets=BTC,ETH,SOL,XRP "
        "profit_floors=0.02,0.03 boundary_bps=5,10,15,20 delays_ms=25,50,100 "
        "max_book_age_ms=250 max_book_skew_ms=50 trade_authorized=false "
        "read_only=true order_entry=false"
    )
    safe.main()


if __name__ == "__main__":
    main()
