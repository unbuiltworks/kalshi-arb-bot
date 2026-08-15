"""Settlement-adjusted economics for read-only BTC Gemini/Kalshi WS shadow logs.

Takes the conservative event-driven, consumable-liquidity BTC episodes and joins
those matched contracts to eventual Gemini/Kalshi outcomes. This converts the
synthetic complementary-leg "locked" P&L into actual 0x/1x/2x settlement payout
for entry and 5/10/20/30/40/50/100/250/500ms survival scenarios.

This remains retrospective research only. It does not make unmatched or unsettled
contracts look safe, and it contains no order-entry path.
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import httpx

from src.gemini_kalshi_btc_ws_analyze import (
    D,
    THRESHOLDS_MS,
    Episode,
    _candidate_at if False else None,
)
from src.gemini_kalshi_btc_ws_analyze import _consume, episodes, load_rows
from src.gemini_kalshi_settlement_agreement import AgreementRow, _gemini_settled
from src.gemini_kalshi_settlement_probe import _kalshi_settled_paced, match_settled_true_windows


def _pick(ep: Episode, threshold_ms: int | None) -> dict[str, Any] | None:
    if threshold_ms is None:
        return ep.entry
    target = ep.start_ms + threshold_ms
    for ts, candidate in ep.observations:
        if ts >= target:
            return candidate
    return None


def _kalshi_ticker(ep: Episode) -> str:
    parts = ep.market_key.split("|", 2)
    return parts[1] if len(parts) >= 2 else ""


def _payout_multiplier(direction: str, gemini_outcome: str, kalshi_outcome: str) -> int:
    g = gemini_outcome.lower()
    k = kalshi_outcome.lower()
    if g not in {"yes", "no"} or k not in {"yes", "no"}:
        raise ValueError("unsupported settlement outcome")
    if direction == "GEMINI_YES_KALSHI_NO":
        return int(g == "yes") + int(k == "no")
    if direction == "KALSHI_YES_GEMINI_NO":
        return int(k == "yes") + int(g == "no")
    raise ValueError(f"unsupported direction={direction!r}")


def _scenario(eps: list[Episode], by_ticker: dict[str, AgreementRow], threshold_ms: int | None) -> dict[str, Any]:
    seen: dict[str, Decimal] = {}
    matched = 0
    unresolved = 0
    consumable = 0
    contracts = D("0")
    apparent = D("0")
    actual = D("0")
    adverse_mismatches = 0
    favorable_mismatches = 0

    for ep in eps:
        candidate = _pick(ep, threshold_ms)
        if candidate is None:
            continue
        agreement = by_ticker.get(_kalshi_ticker(ep))
        if agreement is None:
            unresolved += 1
            continue
        matched += 1
        fresh_qty, fresh_locked, fresh_cost = _consume(candidate, seen)
        if fresh_qty <= 0:
            continue
        consumable += 1
        contracts += fresh_qty
        apparent += fresh_locked
        mult = _payout_multiplier(ep.direction, agreement.gemini_outcome, agreement.kalshi_outcome)
        realized = fresh_qty * D(mult) - fresh_cost
        actual += realized
        if not agreement.agrees:
            if realized < 0:
                adverse_mismatches += 1
            elif realized > fresh_locked:
                favorable_mismatches += 1

    return {
        "settled_episode_candidates_matched": matched,
        "unresolved_episode_candidates": unresolved,
        "consumable_episodes": consumable,
        "consumable_contracts": str(contracts),
        "apparent_locked_profit_dollars": str(apparent),
        "settlement_adjusted_pnl_dollars": str(actual),
        "basis_effect_dollars": str(actual - apparent),
        "adverse_mismatch_consumable_episodes": adverse_mismatches,
        "favorable_mismatch_consumable_episodes": favorable_mismatches,
    }


def settlement_adjusted(rows: list[dict[str, Any]], agreements: list[AgreementRow]) -> dict[str, Any]:
    eps = episodes(rows)
    by_ticker = {row.kalshi_ticker: row for row in agreements if row.asset == "BTC"}
    entry = _scenario(eps, by_ticker, None)
    survival = {str(ms): _scenario(eps, by_ticker, ms) for ms in THRESHOLDS_MS}
    return {
        "unique_directional_positive_episodes": len(eps),
        "settled_btc_contracts_available": len(by_ticker),
        "entry": entry,
        "survival": survival,
        "accounting_method": "CONSUMABLE_LIQUIDITY_WITH_ACTUAL_0X_1X_2X_SETTLEMENT_PAYOUT",
        "unresolved_policy": "UNMATCHED_OR_UNSETTLED_EPISODES_EXCLUDED_NOT_ASSUMED_SAFE",
        "classification": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
    }


async def run(paths: list[Path], *, hours: int, gemini_limit: int, max_expiry_skew_s: float) -> dict[str, Any]:
    rows = load_rows(paths)
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, kalshi = await asyncio.gather(
            _gemini_settled(http, gemini_limit),
            _kalshi_settled_paced(http, hours),
        )
    agreements = match_settled_true_windows(gemini, kalshi, max_expiry_skew_s)
    return settlement_adjusted(rows, agreements)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--gemini-limit", type=int, default=10000)
    parser.add_argument("--max-expiry-skew-s", type=float, default=30.0)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(
        args.paths,
        hours=args.hours,
        gemini_limit=args.gemini_limit,
        max_expiry_skew_s=args.max_expiry_skew_s,
    )), separators=(",", ":")))


if __name__ == "__main__":
    main()
