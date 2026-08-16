"""Capital-efficiency analysis for read-only BTC Gemini/Kalshi WS shadow logs.

Builds on the conservative event-driven/consumable-liquidity analyzer and adds
capital lock time, profit per capital-hour, and a clearly-labelled naive daily
projection. Settlement/index-basis risk remains a separate gate.

Optionally consumes the JSON summary emitted by ``gemini_kalshi_settlement_latency``
so capital-hours can include empirical Kalshi close->settlement p50/p95 delay rather
than assuming capital is released at the 15-minute measurement-window endpoint.
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


def _capital_scenario(
    eps: list[Episode],
    threshold_ms: int | None,
    *,
    post_close_settlement_s: Decimal = D("0"),
) -> dict[str, Any]:
    if post_close_settlement_s < 0:
        raise ValueError("post_close_settlement_s must be non-negative")

    seen: dict[str, Decimal] = {}
    pnl = D("0")
    capital = D("0")
    capital_hours = D("0")
    contracts = D("0")
    consumable_episodes = 0
    unresolved_lock_time = 0
    post_close_ms = post_close_settlement_s * D("1000")

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
        lock_ms = D(max(0, end_ms - ts)) + post_close_ms
        capital_hours += fresh_capital * lock_ms / MS_PER_HOUR

    return {
        "consumable_episodes": consumable_episodes,
        "consumable_contracts": str(contracts),
        "consumable_shadow_pnl_dollars": str(pnl),
        "consumable_capital_deployed_dollars": str(capital),
        "consumable_capital_hours": str(capital_hours),
        "consumable_profit_per_capital_hour": str(pnl / capital_hours) if capital_hours > 0 else None,
        "unresolved_lock_time_episodes": unresolved_lock_time,
        "post_close_settlement_s_assumed": str(post_close_settlement_s),
    }


def _project_day(scenario: dict[str, Any], observation_ms: int) -> None:
    pnl = D(scenario["consumable_shadow_pnl_dollars"])
    scenario["naive_projected_shadow_pnl_per_day"] = (
        str(pnl * MS_PER_DAY / D(observation_ms)) if observation_ms > 0 else None
    )


def _scenario_set(
    eps: list[Episode],
    observation_ms: int,
    *,
    post_close_settlement_s: Decimal,
) -> dict[str, Any]:
    entry = _capital_scenario(
        eps,
        None,
        post_close_settlement_s=post_close_settlement_s,
    )
    _project_day(entry, observation_ms)

    survival: dict[str, Any] = {}
    for threshold in THRESHOLDS_MS:
        scenario = _capital_scenario(
            eps,
            threshold,
            post_close_settlement_s=post_close_settlement_s,
        )
        _project_day(scenario, observation_ms)
        survival[str(threshold)] = scenario
    return {"entry": entry, "survival": survival}


def settlement_delay_scenarios_from_summary(payload: dict[str, Any]) -> dict[str, Decimal]:
    """Extract BTC empirical close->settlement p50/p95 from latency-study JSON."""
    try:
        summary = payload["by_asset"]["BTC"]["close_to_settlement"]
    except (KeyError, TypeError):
        raise ValueError("settlement latency summary is missing BTC close_to_settlement")

    out: dict[str, Decimal] = {}
    for label, field in (("p50", "median_s"), ("p95", "p95_s")):
        value = summary.get(field)
        if value is None:
            raise ValueError(f"settlement latency summary is missing BTC {field}")
        delay = D(str(value))
        if delay < 0:
            raise ValueError(f"settlement latency summary has negative BTC {field}")
        out[label] = delay
    return out


def capital_economics(
    rows: list[dict[str, Any]],
    settlement_delays_s: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    base = analyze(rows)
    eps = episodes(rows)
    timestamps = [int(r["receive_ts_ms"]) for r in rows]
    observation_ms = (timestamps[-1] - timestamps[0]) if len(timestamps) >= 2 else 0

    window_end_only = _scenario_set(
        eps,
        observation_ms,
        post_close_settlement_s=D("0"),
    )

    adjusted: dict[str, Any] = {}
    for label, delay_s in sorted((settlement_delays_s or {}).items()):
        delay = D(str(delay_s))
        if delay < 0:
            raise ValueError("settlement delay scenarios must be non-negative")
        adjusted[label] = _scenario_set(
            eps,
            observation_ms,
            post_close_settlement_s=delay,
        )

    return {
        "base": base,
        "entry": window_end_only["entry"],
        "survival": window_end_only["survival"],
        "settlement_delay_adjusted": adjusted,
        "observation_ms": observation_ms,
        "projection_warning": "NAIVE_SHORT_SAMPLE_NOT_A_FORECAST",
        "capital_hour_method": "SUM_FRESH_CONSUMABLE_NET_COST_TIMES_TIME_TO_MATCHED_WINDOW_END_PLUS_OPTIONAL_EMPIRICAL_POST_CLOSE_SETTLEMENT_DELAY",
        "window_end_only_warning": "LOWER_BOUND_UNLESS_SETTLEMENT_DELAY_ADJUSTED_IS_SUPPLIED",
        "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument(
        "--settlement-latency-summary",
        type=Path,
        help="JSON output from src.gemini_kalshi_settlement_latency; adds BTC p50/p95 capital-hour scenarios",
    )
    args = parser.parse_args()

    delays = None
    if args.settlement_latency_summary is not None:
        payload = json.loads(args.settlement_latency_summary.read_text(encoding="utf-8"))
        delays = settlement_delay_scenarios_from_summary(payload)

    print(json.dumps(capital_economics(load_rows(args.paths), delays), separators=(",", ":")))


if __name__ == "__main__":
    main()
