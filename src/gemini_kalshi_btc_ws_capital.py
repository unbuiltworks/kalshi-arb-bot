"""Capital-efficiency analysis for read-only BTC Gemini/Kalshi WS shadow logs.

Builds on the conservative event-driven/consumable-liquidity analyzer and adds
capital lock time, profit per capital-hour, and a clearly-labelled naive daily
projection. Settlement/index-basis risk remains a separate gate.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from src.gemini_kalshi_btc_ws_analyze import (
    D,
    THRESHOLDS_MS,
    Episode,
    _consume,
    analyze,
    episodes,
    load_rows,
)

MS_PER_HOUR = D("3600000")
MS_PER_DAY = D("86400000")


def _window_end_ms(ep: Episode) -> int | None:
    try:
        window = ep.market_key.rsplit("|", 1)[-1]
        dt = datetime.fromisoformat(window.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError, OverflowError):
        return None


def _candidate_at(ep: Episode, threshold_ms: int | None) -> tuple[int, dict[str, Any]] | None:
    if threshold_ms is None:
        return ep.start_ms, ep.entry
    target = ep.start_ms + threshold_ms
    for ts, candidate in ep.observations:
        if ts >= target:
            return ts, candidate
    return None


def _capital_scenario(eps: list[Episode], threshold_ms: int | None) -> dict[str, Any]:
    seen: dict[str, Decimal] = {}
    pnl = D("0")
    capital = D("0")
    capital_hours = D("0")
    contracts = D("0")
    consumable_episodes = 0
    unresolved_lock_time = 0

    for ep in eps:
        picked = _candidate_at(ep, threshold_ms)
        if picked is None:
            continue
        ts, candidate = picked
        fresh_qty, fresh_pnl, fresh_capital = _consume(candidate, seen)
        if fresh_qty <= 0:
            continue
        consumable_episodes += 1
        contracts += fresh_qty
        pnl += fresh_pnl
        capital += fresh_capital

        end_ms = _window_end_ms(ep)
        if end_ms is None:
            unresolved_lock_time += 1
            continue
        lock_ms = max(0, end_ms - ts)
        capital_hours += fresh_capital * D(lock_ms) / MS_PER_HOUR

    return {
        "consumable_episodes": consumable_episodes,
        "consumable_contracts": str(contracts),
        "consumable_shadow_pnl_dollars": str(pnl),
        "consumable_capital_deployed_dollars": str(capital),
        "consumable_capital_hours": str(capital_hours),
        "consumable_profit_per_capital_hour": str(pnl / capital_hours) if capital_hours > 0 else None,
        "unresolved_lock_time_episodes": unresolved_lock_time,
    }


def capital_economics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    base = analyze(rows)
    eps = episodes(rows)
    timestamps = [int(r["receive_ts_ms"]) for r in rows]
    observation_ms = (timestamps[-1] - timestamps[0]) if len(timestamps) >= 2 else 0

    entry = _capital_scenario(eps, None)
    survival: dict[str, Any] = {}
    for threshold in THRESHOLDS_MS:
        scenario = _capital_scenario(eps, threshold)
        pnl = D(scenario["consumable_shadow_pnl_dollars"])
        scenario["naive_projected_shadow_pnl_per_day"] = (
            str(pnl * MS_PER_DAY / D(observation_ms)) if observation_ms > 0 else None
        )
        survival[str(threshold)] = scenario

    entry_pnl = D(entry["consumable_shadow_pnl_dollars"])
    entry["naive_projected_shadow_pnl_per_day"] = (
        str(entry_pnl * MS_PER_DAY / D(observation_ms)) if observation_ms > 0 else None
    )

    return {
        "base": base,
        "entry": entry,
        "survival": survival,
        "observation_ms": observation_ms,
        "projection_warning": "NAIVE_SHORT_SAMPLE_NOT_A_FORECAST",
        "capital_hour_method": "SUM_FRESH_CONSUMABLE_NET_COST_TIMES_TIME_TO_MATCHED_WINDOW_END",
        "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps(capital_economics(load_rows(args.paths)), separators=(",", ":")))


if __name__ == "__main__":
    main()
