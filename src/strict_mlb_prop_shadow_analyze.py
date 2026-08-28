"""Analyze read-only strict MLB prop shadow samples without double-counting resting depth.

Expected JSONL/sample fields are intentionally small and venue-agnostic:
``ts_ms``, ``pair_key``, ``family``, ``direction``, ``quantity``,
``locked_profit_dollars``, ``capital_dollars``, ``poly_quote_age_ms``,
``kalshi_quote_age_ms``, ``quote_skew_ms``, and ``price_fingerprint``.

A repeated sample at the same pair/direction/price fingerprint can consume only the
increment above previously observed displayed quantity. A changed fingerprint starts
a new consumable level. Survival is deliberately fail-closed: a threshold is only
PASSED when positive observations span the threshold with sampling gaps no larger
than that threshold. Sparse polling therefore cannot claim sub-second persistence.
This is research-only accounting; it does not infer fills.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Iterable

D = Decimal
SURVIVAL_THRESHOLDS_MS = (50, 100, 250, 500)


@dataclass
class _Level:
    fingerprint: str | None = None
    max_seen_qty: Decimal = D("0")

    def consume(self, fingerprint: str, displayed_qty: Decimal) -> Decimal:
        if displayed_qty <= 0:
            return D("0")
        if fingerprint != self.fingerprint:
            self.fingerprint = fingerprint
            self.max_seen_qty = displayed_qty
            return displayed_qty
        fresh = max(D("0"), displayed_qty - self.max_seen_qty)
        if displayed_qty > self.max_seen_qty:
            self.max_seen_qty = displayed_qty
        return fresh


def _d(value: Any) -> Decimal:
    return D(str(value))


def load_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with Path(path).open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def _survival_status(ep: dict[str, Any], threshold_ms: int) -> str:
    """Return PASSED/FAILED/UNRESOLVED without interpolating through sparse samples."""
    timestamps = [int(ts) for ts in ep.get("positive_ts_ms", [])]
    if not timestamps:
        return "UNRESOLVED"
    open_ts = timestamps[0]
    target = open_ts + threshold_ms

    # A sampled non-positive close before the target is direct evidence the episode
    # did not survive the requested horizon.
    close_ts = ep.get("close_ts_ms")
    if close_ts is not None and int(close_ts) < target:
        return "FAILED"

    upto_target = [ts for ts in timestamps if ts <= target]
    after_target = [ts for ts in timestamps if ts >= target]
    if not after_target:
        return "UNRESOLVED"

    first_after = after_target[0]
    support = upto_target + ([first_after] if not upto_target or upto_target[-1] != first_after else [])
    if len(support) < 2:
        return "UNRESOLVED"
    gaps = [b - a for a, b in zip(support, support[1:])]
    return "PASSED" if gaps and max(gaps) <= threshold_ms else "UNRESOLVED"


def analyze(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda r: (int(r["ts_ms"]), str(r["pair_key"]), str(r["direction"])))
    levels: dict[tuple[str, str], _Level] = defaultdict(_Level)
    episodes: dict[tuple[str, str], dict[str, Any]] = {}
    closed: list[dict[str, Any]] = []
    executable_profit = D("0")
    executable_capital = D("0")
    theoretical_profit = D("0")
    family_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "samples": 0, "episodes": 0, "theoretical_profit_dollars": D("0"),
        "consumable_profit_dollars": D("0"), "consumable_capital_dollars": D("0"),
        "capital_hours": D("0"),
    })

    for row in ordered:
        pair = str(row["pair_key"])
        direction = str(row["direction"])
        family = str(row["family"])
        key = (pair, direction)
        ts = int(row["ts_ms"])
        qty = _d(row["quantity"])
        profit = _d(row["locked_profit_dollars"])
        capital = _d(row["capital_dollars"])
        fp = str(row.get("price_fingerprint") or "")
        positive = qty > 0 and profit > 0 and capital > 0
        bucket = family_stats[family]
        bucket["samples"] += 1

        if positive:
            theoretical_profit += profit
            bucket["theoretical_profit_dollars"] += profit
            ep = episodes.get(key)
            if ep is None:
                ep = {
                    "pair_key": pair, "family": family, "direction": direction,
                    "open_ts_ms": ts, "last_positive_ts_ms": ts, "samples": 0,
                    "positive_ts_ms": [], "max_quote_age_ms": 0, "max_quote_skew_ms": 0,
                    "max_displayed_quantity": D("0"), "peak_capital_dollars": D("0"),
                    "consumable_profit_dollars": D("0"), "consumable_capital_dollars": D("0"),
                }
                episodes[key] = ep
                bucket["episodes"] += 1
            ep["last_positive_ts_ms"] = ts
            ep["samples"] += 1
            ep["positive_ts_ms"].append(ts)
            ep["max_displayed_quantity"] = max(ep["max_displayed_quantity"], qty)
            ep["peak_capital_dollars"] = max(ep["peak_capital_dollars"], capital)
            ep["max_quote_age_ms"] = max(
                ep["max_quote_age_ms"], int(row.get("poly_quote_age_ms", 0)), int(row.get("kalshi_quote_age_ms", 0))
            )
            ep["max_quote_skew_ms"] = max(ep["max_quote_skew_ms"], int(row.get("quote_skew_ms", 0)))

            fresh_qty = levels[key].consume(fp, qty)
            if fresh_qty > 0:
                per_contract_profit = profit / qty
                per_contract_capital = capital / qty
                fresh_profit = fresh_qty * per_contract_profit
                fresh_capital = fresh_qty * per_contract_capital
                executable_profit += fresh_profit
                executable_capital += fresh_capital
                bucket["consumable_profit_dollars"] += fresh_profit
                bucket["consumable_capital_dollars"] += fresh_capital
                ep["consumable_profit_dollars"] += fresh_profit
                ep["consumable_capital_dollars"] += fresh_capital
        else:
            ep = episodes.pop(key, None)
            if ep is not None:
                ep["close_ts_ms"] = ts
                ep["duration_ms"] = max(0, int(ep["last_positive_ts_ms"]) - int(ep["open_ts_ms"]))
                closed.append(ep)
            levels.pop(key, None)

    for key, ep in list(episodes.items()):
        ep["close_ts_ms"] = None
        ep["duration_ms"] = max(0, int(ep["last_positive_ts_ms"]) - int(ep["open_ts_ms"]))
        closed.append(ep)

    total_capital_hours = D("0")
    for ep in closed:
        duration_hours = D(str(ep["duration_ms"])) / D("3600000")
        capital_hours = ep["peak_capital_dollars"] * duration_hours
        ep["capital_hours"] = capital_hours
        total_capital_hours += capital_hours
        family_stats[ep["family"]]["capital_hours"] += capital_hours
        timestamps = [int(ts) for ts in ep.get("positive_ts_ms", [])]
        ep["max_positive_sample_gap_ms"] = max(
            (b - a for a, b in zip(timestamps, timestamps[1:])), default=None
        )
        ep["survival"] = {
            f"{threshold}ms": _survival_status(ep, threshold)
            for threshold in SURVIVAL_THRESHOLDS_MS
        }

    def clean_decimal(v: Any) -> Any:
        return str(v) if isinstance(v, Decimal) else v

    cleaned_episodes = []
    for ep in sorted(closed, key=lambda e: (e["open_ts_ms"], e["pair_key"])):
        cleaned = {k: clean_decimal(v) for k, v in ep.items() if k != "positive_ts_ms"}
        cleaned_episodes.append(cleaned)
    cleaned_family: dict[str, dict[str, Any]] = {}
    for family, b in sorted(family_stats.items()):
        cleaned_family[family] = {k: clean_decimal(v) for k, v in b.items()}

    survival_summary = {
        f"{threshold}ms": {
            status: sum(ep["survival"][f"{threshold}ms"] == status for ep in closed)
            for status in ("PASSED", "FAILED", "UNRESOLVED")
        }
        for threshold in SURVIVAL_THRESHOLDS_MS
    }

    span_ms = (ordered[-1]["ts_ms"] - ordered[0]["ts_ms"]) if len(ordered) >= 2 else 0
    projected_day = None
    if span_ms > 0:
        projected_day = executable_profit * D(86_400_000) / D(span_ms)

    return {
        "samples": len(ordered),
        "unique_episodes": len(closed),
        "theoretical_sample_sum_profit_dollars": str(theoretical_profit),
        "consumable_shadow_profit_dollars": str(executable_profit),
        "consumable_shadow_capital_dollars": str(executable_capital),
        "capital_hours": str(total_capital_hours),
        "survival_summary": survival_summary,
        "survival_warning": (
            "PASSED requires positive samples to span the threshold with no sampling gap larger than that threshold; "
            "sparse polling remains UNRESOLVED and must not be interpreted as sub-second persistence."
        ),
        "observed_span_ms": int(span_ms),
        "naive_projected_consumable_dollars_per_day": str(projected_day) if projected_day is not None else None,
        "projection_warning": "Research capacity only; assumes observed opportunity process repeats and does not model fills/latency/settlement risk.",
        "episodes": cleaned_episodes,
        "by_family": cleaned_family,
        "read_only": True,
        "order_entry": False,
    }
