"""Analyze aligned Gemini/Kalshi watcher JSONL with both venue taker fees.

This is read-only post-processing. It deliberately treats one continuous positive
run as one consumable opportunity so repeated one-second observations of the same
resting top-of-book are not multiplied into fictitious P&L.

For legacy watcher logs that predate embedded Kalshi fee metadata, callers must
explicitly provide ``--kalshi-fee-multiplier``. Current KXBTC15M/KXETH15M/
KXSOL15M/KXXRP15M were independently verified as quadratic multiplier=1, but this
analyzer fails closed rather than silently assuming that value forever.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from src.gemini_kalshi_execution_shadow import (
    TopOfBook,
    evaluate_pair,
    gemini_predictions_taker_fee,
)
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee

D = Decimal
SURVIVAL_MS = (50, 100, 250, 500)


def _book(row: dict[str, Any], venue: str) -> TopOfBook:
    prefix = "gemini" if venue == "gemini" else "kalshi"
    return TopOfBook(
        venue=venue,
        market_id=str(row[f"{prefix}_symbol"] if prefix == "gemini" else row["kalshi_ticker"]),
        ts_ms=int(row["ts_ms"]),
        yes_bid=D(str(row[f"{prefix}_yes_bid"])),
        yes_bid_size=D(str(row[f"{prefix}_yes_bid_size"])),
        yes_ask=D(str(row[f"{prefix}_yes_ask"])),
        yes_ask_size=D(str(row[f"{prefix}_yes_ask_size"])),
    )


def _window_end_ms(value: str) -> int:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def _kalshi_fee(multiplier: Decimal):
    def fee(price: Decimal, quantity: Decimal) -> Decimal:
        return kalshi_quadratic_taker_fee(price, quantity, fee_multiplier=multiplier)
    return fee


@dataclass
class Episode:
    asset: str
    window_end_utc: str
    direction: str
    start_ms: int
    last_positive_ms: int
    max_quantity: Decimal
    best_locked_profit: Decimal
    best_net_cost: Decimal
    best_roi: Decimal
    survival: dict[int, bool]

    @property
    def duration_ms(self) -> int:
        return max(0, self.last_positive_ms - self.start_ms)

    @property
    def capital_hours(self) -> Decimal:
        end_ms = _window_end_ms(self.window_end_utc)
        hold_ms = max(0, end_ms - self.start_ms)
        return self.best_net_cost * D(hold_ms) / D("3600000")


def analyze_rows(rows: list[dict[str, Any]], *, fee_multiplier: Decimal, max_quote_skew_ms: int = 500) -> dict[str, Any]:
    rows = sorted(rows, key=lambda r: (str(r.get("asset")), str(r.get("window_end_utc")), int(r.get("ts_ms", 0))))
    kalshi_fee = _kalshi_fee(fee_multiplier)
    active: dict[tuple[str, str, str], Episode] = {}
    episodes: list[Episode] = []
    sample_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def close(key: tuple[str, str, str]) -> None:
        episode = active.pop(key, None)
        if episode is not None:
            episodes.append(episode)

    grouped_keys: set[tuple[str, str]] = set()
    previous_group: tuple[str, str] | None = None
    for row in rows:
        if not row.get("fresh", True):
            continue
        asset = str(row["asset"])
        window = str(row["window_end_utc"])
        group = (asset, window)
        if previous_group is not None and group != previous_group:
            for key in [k for k in active if k[:2] == previous_group]:
                close(key)
        previous_group = group
        grouped_keys.add(group)
        sample_counts[asset]["fresh_samples"] += 1

        g = _book(row, "gemini")
        k = _book(row, "kalshi")
        candidates = evaluate_pair(
            g,
            k,
            gemini_fee=gemini_predictions_taker_fee,
            kalshi_fee=kalshi_fee,
            max_quote_skew_ms=max_quote_skew_ms,
        )
        positive_by_direction = {c.direction: c for c in candidates}
        if candidates:
            sample_counts[asset]["both_fee_positive_samples"] += 1

        known_directions = {k_[2] for k_ in active if k_[:2] == group} | set(positive_by_direction)
        ts_ms = int(row["ts_ms"])
        for direction in known_directions:
            key = (asset, window, direction)
            candidate = positive_by_direction.get(direction)
            if candidate is None:
                close(key)
                continue
            ep = active.get(key)
            if ep is None:
                ep = Episode(
                    asset=asset,
                    window_end_utc=window,
                    direction=direction,
                    start_ms=ts_ms,
                    last_positive_ms=ts_ms,
                    max_quantity=candidate.quantity,
                    best_locked_profit=candidate.locked_profit,
                    best_net_cost=candidate.net_cost,
                    best_roi=candidate.roi_on_cost,
                    survival={ms: False for ms in SURVIVAL_MS},
                )
                active[key] = ep
            else:
                ep.last_positive_ms = ts_ms
                ep.max_quantity = max(ep.max_quantity, candidate.quantity)
                if candidate.locked_profit > ep.best_locked_profit:
                    ep.best_locked_profit = candidate.locked_profit
                    ep.best_net_cost = candidate.net_cost
                    ep.best_roi = candidate.roi_on_cost
            elapsed = ts_ms - ep.start_ms
            for ms in SURVIVAL_MS:
                if elapsed >= ms:
                    ep.survival[ms] = True

    for key in list(active):
        close(key)

    by_asset: dict[str, dict[str, Any]] = {}
    assets = sorted({str(r.get("asset")) for r in rows if r.get("asset")})
    for asset in assets:
        eps = [e for e in episodes if e.asset == asset]
        total_profit = sum((e.best_locked_profit for e in eps), D("0"))
        capital_hours = sum((e.capital_hours for e in eps), D("0"))
        by_asset[asset] = {
            "fresh_samples": sample_counts[asset]["fresh_samples"],
            "both_fee_positive_samples": sample_counts[asset]["both_fee_positive_samples"],
            "unique_positive_episodes": len(eps),
            "best_episode_profit_dollars": str(max((e.best_locked_profit for e in eps), default=D("0"))),
            "shadow_profit_dollars_one_execution_per_episode": str(total_profit),
            "capital_hours": str(capital_hours),
            "profit_per_capital_hour": str(total_profit / capital_hours if capital_hours > 0 else D("0")),
            "max_displayed_quantity": str(max((e.max_quantity for e in eps), default=D("0"))),
            "survival_episode_counts": {str(ms): sum(1 for e in eps if e.survival[ms]) for ms in SURVIVAL_MS},
        }

    observation_ms = 0
    if rows:
        timestamps = [int(r["ts_ms"]) for r in rows if "ts_ms" in r]
        if timestamps:
            observation_ms = max(timestamps) - min(timestamps)
    total_profit = sum((e.best_locked_profit for e in episodes), D("0"))
    projected_day = total_profit * D("86400000") / D(observation_ms) if observation_ms > 0 else D("0")
    return {
        "by_asset": by_asset,
        "unique_positive_episodes": len(episodes),
        "shadow_profit_dollars_one_execution_per_episode": str(total_profit),
        "observation_ms": observation_ms,
        "naive_projected_shadow_profit_per_day": str(projected_day),
        "projection_warning": "NAIVE_SHORT_SAMPLE_NOT_A_FORECAST",
        "kalshi_fee_type": "quadratic",
        "kalshi_fee_multiplier": str(fee_multiplier),
        "gemini_fee_type": "predictions_taker",
        "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
        "repeated_depth_counted_as_pnl": False,
        "read_only": True,
        "order_entry": False,
    }


def load_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if isinstance(row, dict) and "ts_ms" in row and "asset" in row:
                    rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--kalshi-fee-multiplier", type=Decimal, required=True)
    parser.add_argument("--max-quote-skew-ms", type=int, default=500)
    args = parser.parse_args()
    if args.kalshi_fee_multiplier < 0:
        raise SystemExit("--kalshi-fee-multiplier must be non-negative")
    result = analyze_rows(
        load_rows(args.paths),
        fee_multiplier=args.kalshi_fee_multiplier,
        max_quote_skew_ms=args.max_quote_skew_ms,
    )
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
