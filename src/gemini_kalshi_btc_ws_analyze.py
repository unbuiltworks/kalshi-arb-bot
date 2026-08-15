"""Analyze read-only BTC Gemini/Kalshi WebSocket shadow logs.

Unlike the legacy ~1s REST watcher, this analyzer is intended for the event-driven
collector in ``gemini_kalshi_btc_ws_shadow``.  It treats each fee-positive
candidate direction as a separate episode and asks whether a *new positive event*
is still observed at/after 50/100/250/500ms.  That intentionally fails more
conservatively than assuming an unchanged quote survived through an event gap.

P&L is never multiplied by every event.  For each episode/threshold we count at
most one execution: the first still-positive candidate observed at or after the
threshold.  Settlement remains a separate INDEX_MISMATCH_RESEARCH_ONLY gate.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from decimal import Decimal
import json
from pathlib import Path
from statistics import median
from typing import Any

D = Decimal
THRESHOLDS_MS = (50, 100, 250, 500)


@dataclass
class Episode:
    direction: str
    start_ms: int
    last_positive_ms: int
    entry: dict[str, Any]
    observations: list[tuple[int, dict[str, Any]]] = field(default_factory=list)


def load_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("asset") == "BTC" and row.get("read_only") is True:
                    rows.append(row)
    rows.sort(key=lambda r: int(r["receive_ts_ms"]))
    return rows


def _candidates(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for raw in row.get("both_taker_fee_candidates") or []:
        if not isinstance(raw, dict):
            continue
        direction = str(raw.get("direction") or "")
        if not direction:
            continue
        try:
            if D(str(raw["locked_profit"])) <= 0 or D(str(raw["quantity"])) <= 0:
                continue
        except (KeyError, ValueError):
            continue
        out[direction] = raw
    return out


def episodes(rows: list[dict[str, Any]]) -> list[Episode]:
    active: dict[str, Episode] = {}
    done: list[Episode] = []
    for row in rows:
        ts = int(row["receive_ts_ms"])
        current = _candidates(row)
        for direction in list(active):
            if direction not in current:
                done.append(active.pop(direction))
        for direction, candidate in current.items():
            ep = active.get(direction)
            if ep is None:
                ep = Episode(direction, ts, ts, candidate, [])
                active[direction] = ep
            ep.last_positive_ms = ts
            ep.observations.append((ts, candidate))
    done.extend(active.values())
    done.sort(key=lambda e: (e.start_ms, e.direction))
    return done


def _first_at_or_after(ep: Episode, threshold_ms: int) -> dict[str, Any] | None:
    target = ep.start_ms + threshold_ms
    for ts, candidate in ep.observations:
        if ts >= target:
            return candidate
    return None


def analyze(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eps = episodes(rows)
    timestamps = [int(r["receive_ts_ms"]) for r in rows]
    deltas = [b - a for a, b in zip(timestamps, timestamps[1:]) if b > a]
    quote_skews = [
        abs(int(r["gemini_receive_ts_ms"]) - int(r["kalshi_receive_ts_ms"]))
        for r in rows
        if r.get("gemini_receive_ts_ms") is not None and r.get("kalshi_receive_ts_ms") is not None
    ]
    k_latencies = [
        int(r["kalshi_exchange_to_receive_ms"])
        for r in rows
        if r.get("kalshi_exchange_to_receive_ms") is not None
        and int(r["kalshi_exchange_to_receive_ms"]) >= 0
    ]

    threshold_results: dict[str, Any] = {}
    for threshold in THRESHOLDS_MS:
        survivors = []
        pnl = D("0")
        capital = D("0")
        quantity = D("0")
        for ep in eps:
            candidate = _first_at_or_after(ep, threshold)
            if candidate is None:
                continue
            survivors.append(ep)
            pnl += D(str(candidate["locked_profit"]))
            capital += D(str(candidate["net_cost"]))
            quantity += D(str(candidate["quantity"]))
        threshold_results[str(threshold)] = {
            "surviving_episodes": len(survivors),
            "survival_rate": (len(survivors) / len(eps)) if eps else None,
            "one_execution_per_surviving_episode_shadow_pnl_dollars": str(pnl),
            "capital_deployed_dollars": str(capital),
            "contracts": str(quantity),
            "pnl_per_dollar_deployed": str(pnl / capital) if capital > 0 else None,
        }

    by_direction: dict[str, int] = {}
    for ep in eps:
        by_direction[ep.direction] = by_direction.get(ep.direction, 0) + 1

    entry_pnl = sum((D(str(ep.entry["locked_profit"])) for ep in eps), D("0"))
    entry_capital = sum((D(str(ep.entry["net_cost"])) for ep in eps), D("0"))
    observation_ms = (timestamps[-1] - timestamps[0]) if len(timestamps) >= 2 else 0

    return {
        "rows": len(rows),
        "unique_directional_positive_episodes": len(eps),
        "episodes_by_direction": dict(sorted(by_direction.items())),
        "entry_one_execution_per_episode_shadow_pnl_dollars": str(entry_pnl),
        "entry_capital_deployed_dollars": str(entry_capital),
        "entry_pnl_per_dollar_deployed": str(entry_pnl / entry_capital) if entry_capital > 0 else None,
        "survival": threshold_results,
        "observation_ms": observation_ms,
        "event_interval_median_ms": median(deltas) if deltas else None,
        "quote_skew_median_ms": median(quote_skews) if quote_skews else None,
        "quote_skew_p95_ms": _percentile(quote_skews, 0.95),
        "kalshi_exchange_to_receive_median_ms": median(k_latencies) if k_latencies else None,
        "kalshi_exchange_to_receive_p95_ms": _percentile(k_latencies, 0.95),
        "survival_method": "REQUIRES_NEW_POSITIVE_EVENT_AT_OR_AFTER_THRESHOLD",
        "pnl_method": "FIRST_STILL_POSITIVE_EVENT_ONE_EXECUTION_PER_DIRECTIONAL_EPISODE",
        "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
    }


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = int((len(ordered) - 1) * q)
    return ordered[idx]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(load_rows(args.paths)), separators=(",", ":")))


if __name__ == "__main__":
    main()
