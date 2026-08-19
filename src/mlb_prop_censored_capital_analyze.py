"""Censoring-aware capital-hour analysis for read-only MLB prop shadow episodes.

The episode tracker records CLOSE times as interval-censored under polling: an
opportunity is known present through ``last_seen_ms`` and absent by ``observed_ms``.
Using only the lower bound understates capital-time and can overstate profit per
capital-hour. This module reports both bounds and uses the *upper* bound for the
conservative capital-efficiency metric.

It consumes OPEN/MOVE/CLOSE JSONL only. It has no venue client, credentials, order
entry, sizing, or live-arm path.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any, Iterable

D = Decimal


def _d(value: Any) -> Decimal | None:
    try:
        return D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _valid(row: dict[str, Any]) -> bool:
    return bool(
        row.get("event") in {"OPEN", "MOVE", "CLOSE"}
        and row.get("settlement_key")
        and row.get("direction")
        and isinstance(row.get("opened_ms"), int)
        and isinstance(row.get("observed_ms"), int)
        and row.get("read_only") is True
        and row.get("order_entry") is False
    )


def _episode_id(row: dict[str, Any]) -> str:
    return "|".join(str(row.get(k) or "") for k in ("settlement_key", "direction", "opened_ms"))


def analyze_censored_capital(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    invalid_rows = 0
    for raw in events:
        if not isinstance(raw, dict) or not _valid(raw):
            invalid_rows += 1
            continue
        grouped[_episode_id(raw)].append(dict(raw))

    episodes: list[dict[str, Any]] = []
    for episode_id, rows in grouped.items():
        rows.sort(key=lambda r: (int(r["observed_ms"]), {"OPEN": 0, "MOVE": 1, "CLOSE": 2}[r["event"]]))
        opens = [r for r in rows if r.get("event") == "OPEN"]
        closes = [r for r in rows if r.get("event") == "CLOSE"]
        if len(opens) != 1 or len(closes) > 1:
            invalid_rows += 1
            continue

        opened = opens[0]
        priced = [r for r in rows if r.get("event") in {"OPEN", "MOVE"}]
        if not priced:
            invalid_rows += 1
            continue

        close = closes[0] if closes else None
        start_ms = int(opened["observed_ms"])
        lower_end_ms = int(close.get("last_seen_ms", close["observed_ms"])) if close else int(priced[-1]["observed_ms"])
        upper_end_ms = int(close["observed_ms"]) if close else int(priced[-1]["observed_ms"])
        lower_end_ms = max(start_ms, lower_end_ms)
        upper_end_ms = max(lower_end_ms, upper_end_ms)

        lower_capital_hours = D("0")
        upper_capital_hours = D("0")
        for i, row in enumerate(priced):
            seg_start = int(row["observed_ms"])
            next_change = int(priced[i + 1]["observed_ms"]) if i + 1 < len(priced) else None
            lower_seg_end = min(lower_end_ms, next_change) if next_change is not None else lower_end_ms
            upper_seg_end = min(upper_end_ms, next_change) if next_change is not None else upper_end_ms
            capital = _d(row.get("capital")) or D("0")
            if lower_seg_end > seg_start:
                lower_capital_hours += capital * D(lower_seg_end - seg_start) / D("3600000")
            if upper_seg_end > seg_start:
                upper_capital_hours += capital * D(upper_seg_end - seg_start) / D("3600000")

        first_profit = _d(opened.get("modeled_profit")) or D("0")
        censoring_gap_ms = max(0, upper_end_ms - lower_end_ms)
        episodes.append({
            "episode_id": episode_id,
            "family": opened.get("family"),
            "direction": opened.get("direction"),
            "closed": close is not None,
            "first_seen_profit": first_profit,
            "duration_lower_bound_ms": lower_end_ms - start_ms,
            "duration_upper_bound_ms": upper_end_ms - start_ms,
            "close_censoring_gap_ms": censoring_gap_ms,
            "capital_hours_lower_bound": lower_capital_hours,
            "capital_hours_upper_bound": upper_capital_hours,
            "optimistic_profit_per_capital_hour_lower_bound": (
                first_profit / lower_capital_hours if lower_capital_hours > 0 else None
            ),
            "conservative_profit_per_capital_hour_upper_bound": (
                first_profit / upper_capital_hours if upper_capital_hours > 0 else None
            ),
            "duration_censoring": (
                "INTERVAL_CENSORED_POLLING" if close is not None else "RIGHT_CENSORED_ACTIVE_OR_LOG_ENDED"
            ),
        })

    total_profit = sum((r["first_seen_profit"] for r in episodes), D("0"))
    lower_hours = sum((r["capital_hours_lower_bound"] for r in episodes), D("0"))
    upper_hours = sum((r["capital_hours_upper_bound"] for r in episodes), D("0"))
    closed = sum(bool(r["closed"]) for r in episodes)
    censored_closed = sum(bool(r["closed"] and r["close_censoring_gap_ms"] > 0) for r in episodes)

    return {
        "episodes": len(episodes),
        "closed_episodes": closed,
        "interval_censored_closed_episodes": censored_closed,
        "first_seen_executable_shadow_profit": str(total_profit),
        "capital_hours_lower_bound": str(lower_hours),
        "capital_hours_upper_bound": str(upper_hours),
        "optimistic_profit_per_capital_hour_lower_bound": (
            str(total_profit / lower_hours) if lower_hours > 0 else None
        ),
        "conservative_profit_per_capital_hour_upper_bound": (
            str(total_profit / upper_hours) if upper_hours > 0 else None
        ),
        "capital_efficiency_semantics": (
            "LOWER_BOUND_CAPITAL_HOURS_IS_OPTIMISTIC; UPPER_BOUND_CAPITAL_HOURS_IS_CONSERVATIVE_FOR_POLLED_CLOSES"
        ),
        "warning": (
            "Polling interval-censors opportunity disappearance. Do not use lower-bound capital-hours as an exact "
            "capital-efficiency denominator. Open/right-censored episodes have no finite post-log upper close bound."
        ),
        "episode_rows": [
            {
                **r,
                "first_seen_profit": str(r["first_seen_profit"]),
                "capital_hours_lower_bound": str(r["capital_hours_lower_bound"]),
                "capital_hours_upper_bound": str(r["capital_hours_upper_bound"]),
                "optimistic_profit_per_capital_hour_lower_bound": (
                    str(r["optimistic_profit_per_capital_hour_lower_bound"])
                    if r["optimistic_profit_per_capital_hour_lower_bound"] is not None else None
                ),
                "conservative_profit_per_capital_hour_upper_bound": (
                    str(r["conservative_profit_per_capital_hour_upper_bound"])
                    if r["conservative_profit_per_capital_hour_upper_bound"] is not None else None
                ),
            }
            for r in episodes
        ],
        "invalid_rows": invalid_rows,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                out.append(value)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl", help="OPEN/MOVE/CLOSE JSONL from mlb_prop_episode_tracker")
    args = parser.parse_args()
    print(json.dumps(analyze_censored_capital(load_jsonl(args.jsonl)), sort_keys=True))


if __name__ == "__main__":
    main()
