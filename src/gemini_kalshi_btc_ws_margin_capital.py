"""Basis-margin-filtered capital-hours for read-only BTC Gemini/Kalshi WS shadow logs.

Recomputes fresh consumable BTC capital usage on the exact retrospective Gemini
close-margin cohorts used by ``gemini_kalshi_btc_ws_settlement``. This avoids
joining stressed P&L to an unfiltered capital denominator. Optional empirical
BTC close->settlement p50/p95 delay from ``gemini_kalshi_settlement_latency`` is
added to window-end lock time.

Research only. No authentication or order-entry path.
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import httpx

from src.gemini_kalshi_btc_ws_analyze import D, THRESHOLDS_MS, Episode, _consume
from src.gemini_kalshi_btc_ws_capital import (
    MS_PER_HOUR,
    _candidate_at,
    _window_end_ms,
    settlement_delay_scenarios_from_summary,
)
from src.gemini_kalshi_btc_ws_settlement import (
    BASIS_MARGIN_THRESHOLDS_BPS,
    _kalshi_ticker,
    load_episodes_stream,
)
from src.gemini_kalshi_settlement_agreement import AgreementRow, _gemini_settled
from src.gemini_kalshi_settlement_probe import _kalshi_settled_paced, match_settled_true_windows


def _scenario(
    eps: list[Episode],
    by_ticker: dict[str, AgreementRow],
    threshold_ms: int | None,
    *,
    min_abs_gemini_margin_bps: Decimal,
    post_close_settlement_s: Decimal = D("0"),
) -> dict[str, Any]:
    if min_abs_gemini_margin_bps < 0:
        raise ValueError("min_abs_gemini_margin_bps must be non-negative")
    if post_close_settlement_s < 0:
        raise ValueError("post_close_settlement_s must be non-negative")

    seen: dict[str, Decimal] = {}
    contracts = D("0")
    capital = D("0")
    capital_hours = D("0")
    consumable = 0
    excluded_unknown_margin = 0
    excluded_inside_margin = 0
    unresolved = 0
    unresolved_lock_time = 0
    post_close_ms = post_close_settlement_s * D("1000")

    for ep in eps:
        picked = _candidate_at(ep, threshold_ms)
        if picked is None:
            continue
        ts, candidate = picked
        agreement = by_ticker.get(_kalshi_ticker(ep))
        if agreement is None:
            unresolved += 1
            continue
        if agreement.gemini_margin_bps is None:
            excluded_unknown_margin += 1
            continue
        if abs(D(str(agreement.gemini_margin_bps))) < min_abs_gemini_margin_bps:
            excluded_inside_margin += 1
            continue

        fresh_qty, _, fresh_capital = _consume(candidate, seen)
        if fresh_qty <= 0:
            continue
        consumable += 1
        contracts += fresh_qty
        capital += fresh_capital

        end_ms = _window_end_ms(ep)
        if end_ms is None:
            unresolved_lock_time += 1
            continue
        lock_ms = D(max(0, end_ms - ts)) + post_close_ms
        capital_hours += fresh_capital * lock_ms / MS_PER_HOUR

    return {
        "consumable_episodes": consumable,
        "consumable_contracts": str(contracts),
        "consumable_capital_deployed_dollars": str(capital),
        "consumable_capital_hours": str(capital_hours),
        "excluded_unknown_gemini_margin_candidates": excluded_unknown_margin,
        "excluded_inside_margin_candidates": excluded_inside_margin,
        "unresolved_episode_candidates": unresolved,
        "unresolved_lock_time_episodes": unresolved_lock_time,
        "min_abs_gemini_margin_bps": str(min_abs_gemini_margin_bps),
        "post_close_settlement_s_assumed": str(post_close_settlement_s),
    }


def _cohort_set(
    eps: list[Episode],
    by_ticker: dict[str, AgreementRow],
    *,
    post_close_settlement_s: Decimal,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for margin in BASIS_MARGIN_THRESHOLDS_BPS:
        key = str(margin.normalize()) if margin != 0 else "0"
        out[key] = {
            "min_abs_gemini_margin_bps": str(margin),
            "entry": _scenario(
                eps,
                by_ticker,
                None,
                min_abs_gemini_margin_bps=margin,
                post_close_settlement_s=post_close_settlement_s,
            ),
            "survival": {
                str(ms): _scenario(
                    eps,
                    by_ticker,
                    ms,
                    min_abs_gemini_margin_bps=margin,
                    post_close_settlement_s=post_close_settlement_s,
                )
                for ms in THRESHOLDS_MS
            },
        }
    return out


def margin_capital_economics(
    eps: list[Episode],
    agreements: list[AgreementRow],
    settlement_delays_s: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    by_ticker = {row.kalshi_ticker: row for row in agreements if row.asset == "BTC"}
    adjusted: dict[str, Any] = {}
    for label, delay in sorted((settlement_delays_s or {}).items()):
        delay_d = D(str(delay))
        if delay_d < 0:
            raise ValueError("settlement delay scenarios must be non-negative")
        adjusted[str(label)] = _cohort_set(
            eps,
            by_ticker,
            post_close_settlement_s=delay_d,
        )

    return {
        "basis_margin_capital": _cohort_set(
            eps,
            by_ticker,
            post_close_settlement_s=D("0"),
        ),
        "settlement_delay_adjusted_basis_margin_capital": adjusted,
        "capital_hour_method": "FRESH_CONSUMABLE_NET_COST_X_TIME_TO_WINDOW_END_PLUS_OPTIONAL_EMPIRICAL_POST_CLOSE_SETTLEMENT_DELAY_ON_EXACT_MARGIN_COHORT",
        "margin_policy": "MATCHES_BTC_WS_SETTLEMENT_RETROSPECTIVE_GEMINI_CLOSE_MARGIN_THRESHOLDS_UNKNOWN_MARGIN_FAILS_CLOSED",
        "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
    }


async def run(
    paths: list[Path],
    *,
    hours: int,
    gemini_limit: int,
    max_expiry_skew_s: float,
    settlement_delays_s: dict[str, Decimal] | None,
) -> dict[str, Any]:
    eps = load_episodes_stream(paths)
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, kalshi = await asyncio.gather(
            _gemini_settled(http, gemini_limit),
            _kalshi_settled_paced(http, hours),
        )
    agreements = match_settled_true_windows(gemini, kalshi, max_expiry_skew_s)
    return margin_capital_economics(eps, agreements, settlement_delays_s)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--gemini-limit", type=int, default=10000)
    parser.add_argument("--max-expiry-skew-s", type=float, default=30.0)
    parser.add_argument(
        "--settlement-latency-summary",
        type=Path,
        help="Optional JSON from gemini_kalshi_settlement_latency; adds BTC p50/p95 post-close delay",
    )
    args = parser.parse_args()

    delays = None
    if args.settlement_latency_summary is not None:
        payload = json.loads(args.settlement_latency_summary.read_text(encoding="utf-8"))
        delays = settlement_delay_scenarios_from_summary(payload)

    result = asyncio.run(run(
        args.paths,
        hours=args.hours,
        gemini_limit=args.gemini_limit,
        max_expiry_skew_s=args.max_expiry_skew_s,
        settlement_delays_s=delays,
    ))
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
