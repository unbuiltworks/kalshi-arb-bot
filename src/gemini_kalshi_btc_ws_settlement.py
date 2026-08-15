"""Settlement-adjusted economics for read-only BTC Gemini/Kalshi WS shadow logs.

Takes the conservative event-driven, consumable-liquidity BTC episodes and joins
those matched contracts to eventual Gemini/Kalshi outcomes. This converts the
synthetic complementary-leg "locked" P&L into actual 0x/1x/2x settlement payout
for entry and 5/10/20/30/40/50/100/250/500ms survival scenarios.

Large WebSocket captures are streamed line-by-line. Only fee-positive episode
observations are retained in memory; hundreds of thousands of ordinary market-data
rows are discarded immediately. This keeps the analysis usable on the small AWS
instance that runs the collector.

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
    _candidates,
    _consume,
    _market_key,
    episodes,
)
from src.gemini_kalshi_settlement_agreement import AgreementRow, _gemini_settled
from src.gemini_kalshi_settlement_probe import _kalshi_settled_paced, match_settled_true_windows

BASIS_MARGIN_THRESHOLDS_BPS = (
    D("0"), D("0.25"), D("0.5"), D("1"), D("2"), D("5"), D("10")
)


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


def load_episodes_stream(paths: list[Path]) -> list[Episode]:
    """Build BTC positive episodes without retaining the full JSONL capture.

    Collector JSONL is append-only and timestamp ordered. The common production
    path passes one capture file; multiple paths are consumed in caller-supplied
    chronological order. Only candidate observations are retained.
    """
    active: dict[tuple[str, str], Episode] = {}
    done: list[Episode] = []
    last_ts: int | None = None

    for path in paths:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("asset") != "BTC" or row.get("read_only") is not True:
                    continue
                ts = int(row["receive_ts_ms"])
                if last_ts is not None and ts < last_ts:
                    raise ValueError("BTC capture rows must be supplied in chronological order")
                last_ts = ts

                market = _market_key(row)
                current = _candidates(row)

                # Contract rollover closes all episodes from the prior market.
                for key in list(active):
                    if key[0] != market:
                        done.append(active.pop(key))

                # A new event with no candidate in an active direction closes it.
                for key in list(active):
                    if key[0] == market and key[1] not in current:
                        done.append(active.pop(key))

                for direction, candidate in current.items():
                    key = (market, direction)
                    ep = active.get(key)
                    if ep is None:
                        ep = Episode(
                            market_key=market,
                            direction=direction,
                            start_ms=ts,
                            last_positive_ms=ts,
                            entry=candidate,
                            observations=[],
                        )
                        active[key] = ep
                    ep.last_positive_ms = ts
                    ep.observations.append((ts, candidate))

    done.extend(active.values())
    done.sort(key=lambda e: (e.start_ms, e.market_key, e.direction))
    return done


def _scenario(
    eps: list[Episode],
    by_ticker: dict[str, AgreementRow],
    threshold_ms: int | None,
    *,
    min_abs_gemini_margin_bps: Decimal | None = None,
) -> dict[str, Any]:
    seen: dict[str, Decimal] = {}
    matched = 0
    unresolved = 0
    excluded_unknown_margin = 0
    excluded_inside_margin = 0
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

        if min_abs_gemini_margin_bps is not None:
            if agreement.gemini_margin_bps is None:
                excluded_unknown_margin += 1
                continue
            if abs(D(str(agreement.gemini_margin_bps))) < min_abs_gemini_margin_bps:
                excluded_inside_margin += 1
                continue

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
        "excluded_unknown_gemini_margin_candidates": excluded_unknown_margin,
        "excluded_inside_margin_candidates": excluded_inside_margin,
        "consumable_episodes": consumable,
        "consumable_contracts": str(contracts),
        "apparent_locked_profit_dollars": str(apparent),
        "settlement_adjusted_pnl_dollars": str(actual),
        "basis_effect_dollars": str(actual - apparent),
        "adverse_mismatch_consumable_episodes": adverse_mismatches,
        "favorable_mismatch_consumable_episodes": favorable_mismatches,
    }


def _basis_margin_sensitivity(
    eps: list[Episode], by_ticker: dict[str, AgreementRow]
) -> dict[str, Any]:
    """Retrospectively gate sub-second economics by Gemini close-boundary margin.

    Unknown margins fail closed in every gated cohort. This is sensitivity
    analysis only, not a live entry rule.
    """
    out: dict[str, Any] = {}
    for threshold in BASIS_MARGIN_THRESHOLDS_BPS:
        key = str(threshold.normalize()) if threshold != 0 else "0"
        out[key] = {
            "min_abs_gemini_margin_bps": str(threshold),
            "entry": _scenario(
                eps,
                by_ticker,
                None,
                min_abs_gemini_margin_bps=threshold,
            ),
            "survival": {
                str(ms): _scenario(
                    eps,
                    by_ticker,
                    ms,
                    min_abs_gemini_margin_bps=threshold,
                )
                for ms in THRESHOLDS_MS
            },
        }
    return out


def settlement_adjusted_episodes(eps: list[Episode], agreements: list[AgreementRow]) -> dict[str, Any]:
    by_ticker = {row.kalshi_ticker: row for row in agreements if row.asset == "BTC"}
    entry = _scenario(eps, by_ticker, None)
    survival = {str(ms): _scenario(eps, by_ticker, ms) for ms in THRESHOLDS_MS}
    return {
        "unique_directional_positive_episodes": len(eps),
        "settled_btc_contracts_available": len(by_ticker),
        "entry": entry,
        "survival": survival,
        "basis_margin_sensitivity": _basis_margin_sensitivity(eps, by_ticker),
        "accounting_method": "CONSUMABLE_LIQUIDITY_WITH_ACTUAL_0X_1X_2X_SETTLEMENT_PAYOUT",
        "basis_margin_policy": "RETROSPECTIVE_ONLY_UNKNOWN_MARGIN_EXCLUDED_FROM_GATED_COHORTS",
        "input_memory_method": "STREAM_JSONL_KEEP_POSITIVE_EPISODES_ONLY",
        "unresolved_policy": "UNMATCHED_OR_UNSETTLED_EPISODES_EXCLUDED_NOT_ASSUMED_SAFE",
        "classification": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
    }


def settlement_adjusted(rows: list[dict[str, Any]], agreements: list[AgreementRow]) -> dict[str, Any]:
    """In-memory helper retained for unit tests and small synthetic inputs."""
    return settlement_adjusted_episodes(episodes(rows), agreements)


async def run(paths: list[Path], *, hours: int, gemini_limit: int, max_expiry_skew_s: float) -> dict[str, Any]:
    eps = load_episodes_stream(paths)
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, kalshi = await asyncio.gather(
            _gemini_settled(http, gemini_limit),
            _kalshi_settled_paced(http, hours),
        )
    agreements = match_settled_true_windows(gemini, kalshi, max_expiry_skew_s)
    return settlement_adjusted_episodes(eps, agreements)


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
