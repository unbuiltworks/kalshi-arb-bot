"""Read-only opportunity -> settlement audit for Gemini/Kalshi 15-minute crypto.

This service keeps the existing opportunity matrix intact, but remembers every matrix
candidate that actually passes a 2c/3c x 5/10/15/20bp x 25/50/100ms cell. After the
corresponding Gemini/Kalshi window settles, it joins the exact candidate to both venue
outcomes and computes hypothetical P/L using the entry ``net_cost`` already produced by
the fee-aware shadow accounting core.

Important: this is counterfactual research. No orders are submitted, no account state is
read, and a hypothetical profit is not realized P/L.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src import gemini_kalshi_opportunity_matrix_shadow as matrix
from src.gemini_kalshi_fast_overlap import KALSHI_REST
from src.gemini_kalshi_settlement_agreement import _gemini_settled
from src.gemini_kalshi_settlement_probe import match_settled_true_windows

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_opportunity_settlement_audit_shadow")

SETTLEMENT_POLL_S = 60.0
SETTLEMENT_LOOKBACK_HOURS = 8
KALSHI_15M_SERIES = {
    "BTC": "KXBTC15M",
    "ETH": "KXETH15M",
    "SOL": "KXSOL15M",
    "XRP": "KXXRP15M",
}

_PENDING: dict[tuple[str, str, int], dict[str, Any]] = {}
_SETTLED_KEYS: set[tuple[str, str, int]] = set()
_AUDIT_COUNTERS: defaultdict[str, int] = defaultdict(int)


def _pass_cells(matrix_result: dict[str, dict[str, bool]], delay_ms: int) -> set[str]:
    return {
        f"{profit}_{bp}_{int(delay_ms)}ms"
        for profit, row in matrix_result.items()
        for bp, passed in row.items()
        if passed
    }


def _purchased_sides(direction: str) -> tuple[str, str]:
    """Return (Gemini purchased outcome, Kalshi purchased outcome)."""
    if direction == "GEMINI_YES_KALSHI_NO":
        return "yes", "no"
    if direction == "KALSHI_YES_GEMINI_NO":
        return "no", "yes"
    raise ValueError(f"unsupported direction: {direction}")


def hypothetical_settlement(
    *,
    direction: str,
    quantity: Decimal,
    entry_capital: Decimal,
    gemini_outcome: str,
    kalshi_outcome: str,
) -> dict[str, Any]:
    """Compute binary contract payout and fee-inclusive counterfactual P/L.

    ``entry_capital`` is ``ShadowCandidate.net_cost``: raw cost plus both taker fees.
    Therefore no fee adjustment is added here.
    """
    gemini_side, kalshi_side = _purchased_sides(direction)
    g_outcome = str(gemini_outcome).lower()
    k_outcome = str(kalshi_outcome).lower()
    q = D(quantity)
    wins = int(gemini_side == g_outcome) + int(kalshi_side == k_outcome)
    payout = q * D(wins)
    pnl = payout - D(entry_capital)
    return {
        "purchased_gemini_side": gemini_side,
        "purchased_kalshi_side": kalshi_side,
        "winning_legs": wins,
        "gross_settlement_payout": str(payout),
        "hypothetical_fee_inclusive_pnl": str(pnl),
        "venue_outcomes_agree": g_outcome == k_outcome,
        "settlement_pattern": (
            "ONE_WIN_ONE_LOSS_COMPATIBLE"
            if wins == 1
            else "BOTH_LEGS_WIN_INDEX_MISMATCH"
            if wins == 2
            else "BOTH_LEGS_LOSE_INDEX_MISMATCH"
        ),
    }


def _record_candidate(
    *,
    plan,
    delay_ms: int,
    matrix_result: dict[str, dict[str, bool]],
    entry_ladder: dict[str, bool],
    whole_ladder: dict[str, bool],
    now_ladder: dict[str, bool],
    margin_now: Any,
) -> None:
    cells = _pass_cells(matrix_result, delay_ms)
    if not cells:
        return
    key = (str(plan.kalshi_ticker), str(plan.direction), int(plan.opened_ms))
    row = _PENDING.get(key)
    if row is None:
        row = {
            "asset": str(plan.asset),
            "direction": str(plan.direction),
            "opened_ms": int(plan.opened_ms),
            "quantity": str(plan.quantity),
            "gemini_symbol": str(plan.gemini_symbol),
            "kalshi_ticker": str(plan.kalshi_ticker),
            "original_gemini_limit": str(plan.gemini_limit),
            "original_kalshi_limit": str(plan.kalshi_limit),
            "entry_locked_quote_profit": str(plan.entry_locked_profit),
            "entry_capital": str(plan.entry_capital),
            "entry_double_boundary_ladder": dict(entry_ladder),
            "passing_cells": set(),
            "max_pass_delay_ms": 0,
            "first_pass_observed_ms": int(time.time() * 1000),
            "last_pass_observed_ms": int(time.time() * 1000),
            "last_margin_bps": None,
        }
        _PENDING[key] = row
        _AUDIT_COUNTERS["candidates_opened"] += 1
    row["passing_cells"].update(cells)
    row["max_pass_delay_ms"] = max(int(row["max_pass_delay_ms"]), int(delay_ms))
    row["last_pass_observed_ms"] = int(time.time() * 1000)
    row["last_margin_bps"] = str(margin_now) if margin_now is not None else None
    row["double_boundary_ladder_held_through_episode"] = dict(whole_ladder)
    row["double_boundary_ladder_now"] = dict(now_ladder)
    _AUDIT_COUNTERS[f"candidate_pass_updates_{int(delay_ms)}ms"] += 1

    logger.info(
        "CRYPTO_OPPORTUNITY_AUDIT_PENDING %s",
        json.dumps({
            **{k: v for k, v in row.items() if k != "passing_cells"},
            "passing_cells": sorted(row["passing_cells"]),
            "audit_counters": dict(_AUDIT_COUNTERS),
            "profit_interpretation": "COUNTERFACTUAL_NOT_REALIZED_PNL",
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":"), default=str),
    )


async def _kalshi_settled_series(
    http: httpx.AsyncClient,
    *,
    hours: int,
    series_ticker: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = ""
    min_settled_ts = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
    while True:
        params: dict[str, Any] = {
            "status": "settled",
            "limit": 1000,
            "mve_filter": "exclude",
            "min_settled_ts": min_settled_ts,
            "series_ticker": series_ticker,
        }
        if cursor:
            params["cursor"] = cursor
        response: httpx.Response | None = None
        for attempt in range(5):
            response = await http.get(f"{KALSHI_REST}/markets", params=params)
            if response.status_code != 429:
                break
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else min(6.0, 0.75 * (2 ** attempt))
            except ValueError:
                delay = min(6.0, 0.75 * (2 ** attempt))
            await asyncio.sleep(delay)
        assert response is not None
        response.raise_for_status()
        payload = response.json()
        rows.extend(row for row in payload.get("markets", []) if isinstance(row, dict))
        cursor = str(payload.get("cursor") or "")
        if not cursor:
            return rows
        await asyncio.sleep(0.25)


async def _settlement_rows(http: httpx.AsyncClient) -> list[Any]:
    gemini_task = asyncio.create_task(_gemini_settled(http, 1000))
    kalshi_tasks = [
        asyncio.create_task(
            _kalshi_settled_series(
                http,
                hours=SETTLEMENT_LOOKBACK_HOURS,
                series_ticker=series,
            )
        )
        for series in KALSHI_15M_SERIES.values()
    ]
    gemini = await gemini_task
    kalshi_lists = await asyncio.gather(*kalshi_tasks)
    kalshi = [market for rows in kalshi_lists for market in rows]
    return match_settled_true_windows(gemini, kalshi, 30.0)


def _emit_settled_audits(rows: list[Any]) -> None:
    by_ticker = {str(row.kalshi_ticker): row for row in rows}
    for key, candidate in list(_PENDING.items()):
        if key in _SETTLED_KEYS:
            continue
        settled = by_ticker.get(str(candidate["kalshi_ticker"]))
        if settled is None:
            continue
        settlement = hypothetical_settlement(
            direction=str(candidate["direction"]),
            quantity=D(str(candidate["quantity"])),
            entry_capital=D(str(candidate["entry_capital"])),
            gemini_outcome=str(settled.gemini_outcome),
            kalshi_outcome=str(settled.kalshi_outcome),
        )
        _SETTLED_KEYS.add(key)
        _AUDIT_COUNTERS["candidates_settled"] += 1
        if settled.agrees:
            _AUDIT_COUNTERS["compatible_settlements"] += 1
        else:
            _AUDIT_COUNTERS["venue_disagreements"] += 1
        pnl = D(str(settlement["hypothetical_fee_inclusive_pnl"]))
        if pnl > 0:
            _AUDIT_COUNTERS["hypothetical_positive_pnl"] += 1
        elif pnl < 0:
            _AUDIT_COUNTERS["hypothetical_negative_pnl"] += 1
        else:
            _AUDIT_COUNTERS["hypothetical_zero_pnl"] += 1

        payload = {
            **{k: v for k, v in candidate.items() if k != "passing_cells"},
            "passing_cells": sorted(candidate["passing_cells"]),
            "settlement_expiry_utc": settled.expiry_utc.isoformat(),
            "gemini_outcome": settled.gemini_outcome,
            "kalshi_outcome": settled.kalshi_outcome,
            "gemini_strike": settled.gemini_strike,
            "gemini_settlement": settled.gemini_settlement,
            "gemini_final_margin_bps": settled.gemini_margin_bps,
            **settlement,
            "audit_counters": dict(_AUDIT_COUNTERS),
            "profit_interpretation": "COUNTERFACTUAL_NOT_REALIZED_PNL",
            "official_live_kaiko_at_entry": False,
            "settlement_status": matrix.hot.SETTLEMENT_STATUS,
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }
        logger.info(
            "CRYPTO_OPPORTUNITY_SETTLEMENT_AUDIT %s",
            json.dumps(payload, separators=(",", ":"), default=str),
        )
        if not settled.agrees:
            logger.warning(
                "CRYPTO_OPPORTUNITY_SETTLEMENT_MISMATCH %s",
                json.dumps(payload, separators=(",", ":"), default=str),
            )

        # Once logged, the candidate no longer needs memory in this process.
        _PENDING.pop(key, None)


async def _settlement_loop() -> None:
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        while True:
            try:
                if _PENDING:
                    rows = await _settlement_rows(http)
                    _emit_settled_audits(rows)
                logger.info(
                    "CRYPTO_OPPORTUNITY_AUDIT_HEARTBEAT %s",
                    json.dumps({
                        "pending_candidates": len(_PENDING),
                        "settled_candidates": len(_SETTLED_KEYS),
                        "audit_counters": dict(_AUDIT_COUNTERS),
                        "settlement_poll_s": SETTLEMENT_POLL_S,
                        "profit_interpretation": "COUNTERFACTUAL_NOT_REALIZED_PNL",
                        "trade_authorized": False,
                        "read_only": True,
                        "order_entry": False,
                    }, separators=(",", ":"), default=str),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "CRYPTO_OPPORTUNITY_AUDIT_SETTLEMENT_ERROR trade_authorized=false read_only=true order_entry=false"
                )
            await asyncio.sleep(SETTLEMENT_POLL_S)


async def _probe_with_audit(*, feed, plan, active_episodes, delays_ms=matrix.DELAYS_MS) -> None:
    """Matrix probe plus in-memory capture of cells that actually pass."""
    previous = 0
    key = (plan.asset, plan.direction, int(plan.opened_ms))
    entry_ladder = dict(matrix._PLAN_ENTRY_LADDER.get(key, {}))
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
                checks = matrix.exact.same_limits_marketable(plan, g, k)
                gemini_age_ms = max(0, now_ms - int(g.ts_ms))
                kalshi_age_ms = max(0, now_ms - int(k.ts_ms))
                book_skew_ms = abs(int(g.ts_ms) - int(k.ts_ms))
                post_activation_books = bool(
                    int(g.ts_ms) >= int(feed.boundary_ms)
                    and int(k.ts_ms) >= int(feed.boundary_ms)
                )
                strict_fresh = bool(
                    post_activation_books
                    and max(gemini_age_ms, kalshi_age_ms) <= matrix.MAX_BOOK_AGE_MS
                    and book_skew_ms <= matrix.MAX_BOOK_SKEW_MS
                )

            state = matrix.basis._BASIS_EPISODES.get((plan.asset, plan.direction))
            same_episode = matrix.guarded.same_original_episode(plan, active_episodes, state)
            snapshot = matrix.basis.basis_snapshot(plan.asset, now_ms) if same_window else None
            now_ladder = dict((snapshot or {}).get("double_boundary_ladder") or {})
            whole_ladder = {
                matrix._bp_label(bp): bool(
                    same_episode
                    and state is not None
                    and getattr(state, f"all_events_double_boundary_{matrix._bp_label(bp)}", False)
                )
                for bp in matrix.BOUNDARY_BPS
            }
            base_ready = bool(
                same_episode
                and strict_fresh
                and checks["both_marketable"]
                and D(plan.quantity) == D("1")
            )
            matrix_result = matrix.build_matrix(
                entry_profit=D(plan.entry_locked_profit),
                base_ready=base_ready,
                entry_ladder=entry_ladder,
                whole_ladder=whole_ladder,
                now_ladder=now_ladder,
            )
            any_pass = any(value for row in matrix_result.values() for value in row.values())
            matrix._COUNTERS[f"checks_{delay_ms}ms"] += 1
            for profit_label, row in matrix_result.items():
                for bp_label, passed in row.items():
                    if passed:
                        matrix._COUNTERS[f"pass_{profit_label}_{bp_label}_{delay_ms}ms"] += 1

            margin_now = snapshot.get("min_abs_double_boundary_margin_bps") if snapshot else None
            if any_pass:
                _record_candidate(
                    plan=plan,
                    delay_ms=int(delay_ms),
                    matrix_result=matrix_result,
                    entry_ladder=entry_ladder,
                    whole_ladder=whole_ladder,
                    now_ladder=now_ladder,
                    margin_now=margin_now,
                )

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
                "min_abs_double_boundary_margin_bps_now": margin_now,
                "matrix": matrix_result,
                "any_matrix_pass": any_pass,
                "counters": dict(matrix._COUNTERS),
                "audit_pending_candidates": len(_PENDING),
                "profit_interpretation": "ENTRY_APPARENT_QUOTE_LOCK_NOT_REALIZED_PNL",
                "official_live_kaiko": False,
                "settlement_status": matrix.hot.SETTLEMENT_STATUS,
                "trade_authorized": False,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str))
    finally:
        matrix._PLAN_ENTRY_LADDER.pop(key, None)


async def _collect_all_with_audit(**kwargs):
    settlement_task = asyncio.create_task(
        _settlement_loop(),
        name="crypto-opportunity-settlement-audit",
    )
    try:
        return await matrix._collect_all_with_matrix(**kwargs)
    finally:
        settlement_task.cancel()
        await asyncio.gather(settlement_task, return_exceptions=True)


def install() -> None:
    matrix.install()
    # Replace only the research probe/collector hooks. No order transport is imported.
    matrix.exact._probe = _probe_with_audit
    matrix.hot.collect_all = _collect_all_with_audit


def main() -> None:
    install()
    logger.info(
        "CRYPTO_OPPORTUNITY_SETTLEMENT_AUDIT_ENABLED assets=BTC,ETH,SOL,XRP "
        "profit_floors=0.02,0.03 boundary_bps=5,10,15,20 delays_ms=25,50,100 "
        "settlement_poll_s=60 counterfactual_only=true trade_authorized=false "
        "read_only=true order_entry=false"
    )
    matrix.safe.main()


if __name__ == "__main__":
    main()
