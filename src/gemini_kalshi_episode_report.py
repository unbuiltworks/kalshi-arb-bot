"""Conservative read-only economics report for Gemini/Kalshi aligned-watch JSONL.

The report deliberately treats repeated samples of the same resting displayed depth as
one opportunity episode.  For each episode it uses the single best after-both-fee
candidate observed during that episode rather than summing sample-level locked P/L.

This is research tooling only. Gemini/Kalshi settlement-index equivalence remains
unresolved and every output is explicitly blocked from execution promotion.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

D = Decimal


@dataclass
class Episode:
    asset: str
    window_end_utc: str
    direction: str
    start_ms: int
    end_ms: int
    samples: int
    best_locked_profit: Decimal
    best_quantity: Decimal
    best_net_cost: Decimal
    min_quote_skew_ms: int
    max_quote_skew_ms: int

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    @property
    def capital_dollars(self) -> Decimal:
        return self.best_net_cost * self.best_quantity

    @property
    def capital_hours(self) -> Decimal:
        return self.capital_dollars * D(self.duration_ms) / D(3_600_000)


def _d(value: Any) -> Decimal:
    return D(str(value))


def _best_candidates(row: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = row.get("both_taker_fee_candidates") or []
    if not row.get("fresh", False):
        return []
    if row.get("settlement_status") != "INDEX_MISMATCH_RESEARCH_ONLY":
        return []
    return [c for c in candidates if _d(c.get("locked_profit", "0")) > 0]


def build_episodes(rows: Iterable[dict[str, Any]], *, gap_tolerance_ms: int = 2_500) -> list[Episode]:
    active: dict[tuple[str, str, str], Episode] = {}
    finished: list[Episode] = []

    ordered = sorted(rows, key=lambda r: int(r.get("ts_ms") or 0))
    for row in ordered:
        ts_ms = int(row.get("ts_ms") or 0)
        asset = str(row.get("asset") or "")
        window = str(row.get("window_end_utc") or "")
        skew = int(row.get("quote_skew_ms") or 0)
        candidates = _best_candidates(row)
        seen_keys: set[tuple[str, str, str]] = set()

        for candidate in candidates:
            direction = str(candidate.get("direction") or "")
            if not asset or not window or not direction:
                continue
            key = (asset, window, direction)
            seen_keys.add(key)
            profit = _d(candidate.get("locked_profit", "0"))
            qty = _d(candidate.get("quantity", "0"))
            net_cost = _d(candidate.get("net_cost", "0"))
            ep = active.get(key)
            if ep is None or ts_ms - ep.end_ms > gap_tolerance_ms:
                if ep is not None:
                    finished.append(ep)
                active[key] = Episode(
                    asset=asset,
                    window_end_utc=window,
                    direction=direction,
                    start_ms=ts_ms,
                    end_ms=ts_ms,
                    samples=1,
                    best_locked_profit=profit,
                    best_quantity=qty,
                    best_net_cost=net_cost,
                    min_quote_skew_ms=skew,
                    max_quote_skew_ms=skew,
                )
                continue

            ep.end_ms = ts_ms
            ep.samples += 1
            ep.min_quote_skew_ms = min(ep.min_quote_skew_ms, skew)
            ep.max_quote_skew_ms = max(ep.max_quote_skew_ms, skew)
            if profit > ep.best_locked_profit:
                ep.best_locked_profit = profit
                ep.best_quantity = qty
                ep.best_net_cost = net_cost

        # A sampled non-positive row closes an episode immediately for the same asset/window.
        for key, ep in list(active.items()):
            if key[0] == asset and key[1] == window and key not in seen_keys and ts_ms >= ep.end_ms:
                finished.append(ep)
                del active[key]

    finished.extend(active.values())
    return sorted(finished, key=lambda e: (e.start_ms, e.asset, e.direction))


def summarize(rows: list[dict[str, Any]], *, gap_tolerance_ms: int = 2_500) -> dict[str, Any]:
    episodes = build_episodes(rows, gap_tolerance_ms=gap_tolerance_ms)
    timestamps = [int(r.get("ts_ms") or 0) for r in rows if int(r.get("ts_ms") or 0) > 0]
    observed_hours = D("0")
    if len(timestamps) >= 2:
        observed_hours = D(max(timestamps) - min(timestamps)) / D(3_600_000)

    by_asset: dict[str, dict[str, Any]] = {}
    for ep in episodes:
        bucket = by_asset.setdefault(ep.asset, {
            "episodes": 0,
            "shadow_profit_dollars": D("0"),
            "capital_hours": D("0"),
            "max_quantity": D("0"),
            "max_profit": D("0"),
            "durations_ms": [],
            "max_quote_skew_ms": 0,
        })
        bucket["episodes"] += 1
        bucket["shadow_profit_dollars"] += ep.best_locked_profit
        bucket["capital_hours"] += ep.capital_hours
        bucket["max_quantity"] = max(bucket["max_quantity"], ep.best_quantity)
        bucket["max_profit"] = max(bucket["max_profit"], ep.best_locked_profit)
        bucket["durations_ms"].append(ep.duration_ms)
        bucket["max_quote_skew_ms"] = max(bucket["max_quote_skew_ms"], ep.max_quote_skew_ms)

    serialized_assets: dict[str, Any] = {}
    for asset, bucket in sorted(by_asset.items()):
        durations = sorted(bucket.pop("durations_ms"))
        median_duration = durations[len(durations) // 2] if durations else 0
        profit = bucket["shadow_profit_dollars"]
        run_rate = profit * D("24") / observed_hours if observed_hours > 0 else None
        serialized_assets[asset] = {
            "unique_fee_positive_episodes": bucket["episodes"],
            "episode_best_shadow_profit_dollars": str(profit),
            "observed_shadow_dollars_per_day": str(run_rate) if run_rate is not None else None,
            "capital_hours": str(bucket["capital_hours"]),
            "max_episode_quantity": str(bucket["max_quantity"]),
            "max_episode_profit_dollars": str(bucket["max_profit"]),
            "median_episode_duration_ms": median_duration,
            "max_episode_duration_ms": max(durations) if durations else 0,
            "max_quote_skew_ms": bucket["max_quote_skew_ms"],
        }

    return {
        "assets": serialized_assets,
        "total_unique_fee_positive_episodes": len(episodes),
        "observed_hours": str(observed_hours),
        "accounting": "ONE_BEST_OBSERVATION_PER_EPISODE_NO_RESTING_DEPTH_SUM",
        "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
        "promotion_status": "RESEARCH_ONLY_BLOCKED",
        "read_only": True,
        "order_entry": False,
        "target_note": "$480/day is a research capacity target only, never a forecast.",
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--gap-tolerance-ms", type=int, default=2500)
    args = parser.parse_args()
    print(json.dumps(summarize(load_jsonl(args.jsonl), gap_tolerance_ms=args.gap_tolerance_ms), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
