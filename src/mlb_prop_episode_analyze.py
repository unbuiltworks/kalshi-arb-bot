"""Analyze read-only MLB prop opportunity lifecycle JSONL into capacity economics.

Consumes OPEN/MOVE/CLOSE events from ``mlb_prop_episode_tracker``. Repeated resting
quotes are not events, so they cannot be double-counted as fresh capacity here.
The $1 and $5 probe views are alternative sizing views of the same underlying
liquidity, not independent opportunities; portfolio totals therefore count at most
one probe plan per underlying OPEN episode. This module is research-only and
contains no venue clients or order-entry path.
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


def _episode_id(event: dict[str, Any]) -> str:
    """Economic episode identity independent of alternative paper sizing tier.

    The tracker deliberately permits a <=$1 <-> <=$5 tier switch to be a MOVE while
    preserving ``opened_ms``. Keying by probe_field would split that MOVE away from
    its OPEN and corrupt duration/capital accounting. A direction flip is already a
    CLOSE+OPEN with a fresh opened_ms and therefore remains a separate episode.
    """
    return "|".join(
        str(event.get(k) or "")
        for k in ("settlement_key", "direction", "opened_ms")
    )


def _liquidity_episode_id(row: dict[str, Any]) -> str:
    """Identity shared by alternative $1/$5 sizing views of one OPEN episode."""
    return "|".join(
        str(row.get(k) or "")
        for k in ("settlement_key", "direction", "opened_ms")
    )


def _valid_event(event: dict[str, Any]) -> bool:
    return bool(
        event.get("event") in {"OPEN", "MOVE", "CLOSE"}
        and event.get("settlement_key")
        and event.get("probe_field") in {"probe_1usd", "probe_5usd"}
        and event.get("direction")
        and isinstance(event.get("opened_ms"), int)
        and isinstance(event.get("observed_ms"), int)
        and event.get("read_only") is True
        and event.get("order_entry") is False
    )


def _normalize_groups(
    grouped: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """Keep current tier-switch episodes whole while tolerating legacy dual-view logs.

    Current tracker output has one OPEN per economic identity and may later MOVE to a
    different probe_field. Older logs could contain simultaneous <=$1 and <=$5 OPENs
    for the same economic identity. Those legacy rows must remain separate views so
    downstream portfolio de-duplication can select one without blending overlapping
    capital. Ambiguous groups with multiple OPENs in the same probe tier fail closed.
    """
    normalized: dict[str, list[dict[str, Any]]] = {}
    invalid = 0
    for episode_id, rows in grouped.items():
        opens = [r for r in rows if r.get("event") == "OPEN"]
        if len(opens) <= 1:
            normalized[episode_id] = rows
            continue

        by_probe: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_probe[str(row.get("probe_field") or "")].append(row)

        legacy_ok = True
        for probe_rows in by_probe.values():
            if sum(r.get("event") == "OPEN" for r in probe_rows) != 1:
                legacy_ok = False
                break
        if not legacy_ok:
            invalid += 1
            continue

        for probe_field, probe_rows in by_probe.items():
            normalized[f"{episode_id}|legacy_view|{probe_field}"] = probe_rows
    return normalized, invalid


def _dedupe_probe_views(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Choose one sizing view per underlying liquidity episode.

    A <=$1 plan and <=$5 plan generated from the same books consume the same resting
    opportunity. Summing them would count the smaller plan twice. For portfolio/capacity
    totals, retain the view with the greater first-seen modeled profit; ties prefer the
    lower-capital view. Per-probe summaries remain available separately.
    """
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_liquidity_episode_id(row)].append(row)

    selected: list[dict[str, Any]] = []
    for candidates in grouped.values():
        selected.append(
            max(
                candidates,
                key=lambda r: (
                    r["first_seen_profit"],
                    -r["first_seen_capital"],
                    1 if r["probe_field"] == "probe_5usd" else 0,
                ),
            )
        )
    return selected


def analyze_events(
    events: Iterable[dict[str, Any]],
    *,
    observation_start_ms: int | None = None,
    observation_end_ms: int | None = None,
) -> dict[str, Any]:
    """Return de-duplicated theoretical vs first-seen executable-shadow economics.

    ``first_seen_profit`` counts each unique underlying OPEN once in portfolio totals:
    what a simultaneous synthetic paper fill would have locked at the first observed
    qualifying quote. ``peak_profit`` is an optimistic within-episode ceiling and is
    reported separately. Capital-hours integrate the active plan across MOVE segments
    through the last positive observation. No repeated unchanged resting quote, and no
    alternate $1/$5 sizing view of the same quote, can add another unit of portfolio
    profit or capacity.

    A normalized USD/day capacity figure is emitted only when callers provide the
    complete scanner observation window. Inferring the denominator from positive
    OPEN/CLOSE events would omit quiet runtime and systematically inflate $/day.
    The supplied observation window must also cover every retained positive episode;
    otherwise the projection is withheld rather than trusting a truncated denominator.
    """
    raw_grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    invalid = 0
    for event in events:
        if not _valid_event(event):
            invalid += 1
            continue
        raw_grouped[_episode_id(event)].append(dict(event))

    grouped, grouping_invalid = _normalize_groups(raw_grouped)
    invalid += grouping_invalid

    episode_rows: list[dict[str, Any]] = []
    positive_start: int | None = None
    positive_end: int | None = None
    budget_tier_switch_episodes = 0

    for episode_id, rows in grouped.items():
        rows.sort(key=lambda r: (int(r["observed_ms"]), {"OPEN": 0, "MOVE": 1, "CLOSE": 2}[r["event"]]))
        opens = [r for r in rows if r["event"] == "OPEN"]
        closes = [r for r in rows if r["event"] == "CLOSE"]
        if len(opens) != 1:
            invalid += 1
            continue

        opened = opens[0]
        start_ms = int(opened["observed_ms"])
        close = closes[-1] if closes else None
        end_ms = int(close.get("last_seen_ms", close["observed_ms"])) if close else int(rows[-1]["observed_ms"])
        end_ms = max(start_ms, end_ms)
        positive_start = start_ms if positive_start is None else min(positive_start, start_ms)
        positive_end = end_ms if positive_end is None else max(positive_end, end_ms)

        priced = [r for r in rows if r["event"] in {"OPEN", "MOVE"}]
        probe_fields_seen = tuple(sorted({str(r.get("probe_field")) for r in priced if r.get("probe_field")}))
        tier_switches = sum(
            priced[i].get("probe_field") != priced[i - 1].get("probe_field")
            for i in range(1, len(priced))
        )
        if tier_switches:
            budget_tier_switch_episodes += 1

        first_profit = _d(opened.get("modeled_profit")) or D("0")
        first_capital = _d(opened.get("capital")) or D("0")
        peak_profit = max((_d(r.get("modeled_profit")) or D("0") for r in priced), default=D("0"))
        peak_capital = max((_d(r.get("capital")) or D("0") for r in priced), default=D("0"))

        capital_hours = D("0")
        for i, row in enumerate(priced):
            seg_start = int(row["observed_ms"])
            if i + 1 < len(priced):
                seg_end = min(end_ms, int(priced[i + 1]["observed_ms"]))
            else:
                seg_end = end_ms
            if seg_end <= seg_start:
                continue
            capital = _d(row.get("capital")) or D("0")
            capital_hours += capital * D(seg_end - seg_start) / D("3600000")

        episode_rows.append(
            {
                "episode_id": episode_id,
                "settlement_key": opened.get("settlement_key"),
                "probe_field": opened.get("probe_field"),
                "probe_fields_seen": probe_fields_seen,
                "budget_tier_switches": tier_switches,
                "family": opened.get("family"),
                "direction": opened.get("direction"),
                "opened_ms": int(opened["opened_ms"]),
                "closed": close is not None,
                "duration_ms": end_ms - start_ms,
                "first_seen_profit": first_profit,
                "peak_profit": peak_profit,
                "first_seen_capital": first_capital,
                "peak_capital": peak_capital,
                "capital_hours": capital_hours,
            }
        )

    def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
        unique = len(rows)
        first_profit = sum((r["first_seen_profit"] for r in rows), D("0"))
        peak_profit = sum((r["peak_profit"] for r in rows), D("0"))
        capital_hours = sum((r["capital_hours"] for r in rows), D("0"))
        closed = sum(bool(r["closed"]) for r in rows)
        return {
            "unique_episodes": unique,
            "closed_episodes": closed,
            "first_seen_executable_shadow_profit": str(first_profit),
            "theoretical_peak_profit": str(peak_profit),
            "capital_hours": str(capital_hours),
            "profit_per_capital_hour": str(first_profit / capital_hours) if capital_hours > 0 else None,
            "max_episode_duration_ms": max((r["duration_ms"] for r in rows), default=0),
            "budget_tier_switch_episodes": sum(bool(r.get("budget_tier_switches")) for r in rows),
        }

    by_probe: dict[str, dict[str, Any]] = {}
    for probe_field in ("probe_1usd", "probe_5usd"):
        by_probe[probe_field] = aggregate([r for r in episode_rows if r["probe_field"] == probe_field])

    portfolio_rows = _dedupe_probe_views(episode_rows)

    by_family: dict[str, dict[str, Any]] = {}
    for family in sorted({str(r.get("family") or "unknown") for r in portfolio_rows}):
        by_family[family] = aggregate([r for r in portfolio_rows if str(r.get("family") or "unknown") == family])

    overall = aggregate(portfolio_rows)

    positive_span_hours = D("0")
    if positive_start is not None and positive_end is not None and positive_end > positive_start:
        positive_span_hours = D(positive_end - positive_start) / D("3600000")

    observation_bounds_valid = bool(
        isinstance(observation_start_ms, int)
        and isinstance(observation_end_ms, int)
        and observation_end_ms > observation_start_ms
    )
    observation_covers_positive_episodes = bool(
        observation_bounds_valid
        and (
            positive_start is None
            or (
                observation_start_ms <= positive_start
                and positive_end is not None
                and observation_end_ms >= positive_end
            )
        )
    )
    observation_window_valid = observation_bounds_valid and observation_covers_positive_episodes

    observation_window_invalid_reason: str | None = None
    if not observation_bounds_valid:
        observation_window_invalid_reason = "missing_or_invalid_bounds"
    elif not observation_covers_positive_episodes:
        observation_window_invalid_reason = "window_does_not_cover_all_positive_episodes"

    observation_span_hours: Decimal | None = None
    if observation_window_valid:
        observation_span_hours = D(observation_end_ms - observation_start_ms) / D("3600000")

    first_total = D(overall["first_seen_executable_shadow_profit"])
    projected = (
        first_total * D("24") / observation_span_hours
        if observation_span_hours is not None and observation_span_hours > 0
        else None
    )

    return {
        "overall": overall,
        "by_probe": by_probe,
        "by_family": by_family,
        "probe_view_episodes": len(episode_rows),
        "unique_liquidity_episodes": len(portfolio_rows),
        "budget_tier_switch_episodes": budget_tier_switch_episodes,
        "episode_identity_rule": "settlement_key + direction + opened_ms; probe_field changes remain MOVE within one episode",
        "portfolio_dedup_rule": (
            "<= $1 and <= $5 plans are alternative sizing views of the same resting liquidity; "
            "legacy dual-view logs are kept separate before portfolio totals select one view."
        ),
        "positive_episode_span_hours": str(positive_span_hours),
        "observation_window_provided": observation_window_valid,
        "observation_window_bounds_valid": observation_bounds_valid,
        "observation_window_covers_positive_episodes": observation_covers_positive_episodes,
        "observation_window_invalid_reason": observation_window_invalid_reason,
        "observation_start_ms": observation_start_ms if observation_bounds_valid else None,
        "observation_end_ms": observation_end_ms if observation_bounds_valid else None,
        "observation_span_hours": str(observation_span_hours) if observation_span_hours is not None else None,
        "capacity_projection_usd_per_day": str(projected) if projected is not None else None,
        "projection_warning": (
            "Capacity normalization only: de-duplicated first-seen simultaneous synthetic fills divided by the explicit "
            "full scanner observation window. It is not a forecast and excludes settlement-rule, fill, latency, and "
            "basis-risk uncertainty. Projection is withheld when the complete observation window is not supplied or "
            "does not cover every retained positive episode, so quiet runtime or positive runtime cannot be omitted "
            "from the denominator."
        ),
        "invalid_events": invalid,
        "read_only": True,
        "order_entry": False,
    }


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl", help="OPEN/MOVE/CLOSE JSONL from mlb_prop_episode_tracker")
    parser.add_argument("--observation-start-ms", type=int, default=None, help="scanner observation window start (epoch ms)")
    parser.add_argument("--observation-end-ms", type=int, default=None, help="scanner observation window end (epoch ms)")
    args = parser.parse_args()
    print(
        json.dumps(
            analyze_events(
                load_jsonl(args.jsonl),
                observation_start_ms=args.observation_start_ms,
                observation_end_ms=args.observation_end_ms,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
