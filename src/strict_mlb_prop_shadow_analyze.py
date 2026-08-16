"""Analyze read-only strict MLB prop shadow samples without double-counting resting depth.

Expected JSONL/sample fields are intentionally small and venue-agnostic:
``ts_ms``, ``pair_key``, ``family``, ``direction``, ``quantity``,
``locked_profit_dollars``, ``capital_dollars``, ``poly_quote_age_ms``,
``kalshi_quote_age_ms``, ``quote_skew_ms``, and ``price_fingerprint``.

A repeated sample at the same pair/direction/price fingerprint can consume only the
increment above previously observed displayed quantity. A changed fingerprint starts
a new consumable level. This is research-only accounting; it does not infer fills.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Iterable

D = Decimal


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
                    "max_quote_age_ms": 0, "max_quote_skew_ms": 0,
                    "consumable_profit_dollars": D("0"), "consumable_capital_dollars": D("0"),
                }
                episodes[key] = ep
                bucket["episodes"] += 1
            ep["last_positive_ts_ms"] = ts
            ep["samples"] += 1
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

    def clean_decimal(v: Any) -> Any:
        return str(v) if isinstance(v, Decimal) else v

    cleaned_episodes = [{k: clean_decimal(v) for k, v in ep.items()} for ep in sorted(closed, key=lambda e: (e["open_ts_ms"], e["pair_key"]))]
    cleaned_family: dict[str, dict[str, Any]] = {}
    for family, b in sorted(family_stats.items()):
        cleaned_family[family] = {k: clean_decimal(v) for k, v in b.items()}

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
        "observed_span_ms": int(span_ms),
        "naive_projected_consumable_dollars_per_day": str(projected_day) if projected_day is not None else None,
        "projection_warning": "Research capacity only; assumes observed opportunity process repeats and does not model fills/latency/settlement risk.",
        "episodes": cleaned_episodes,
        "by_family": cleaned_family,
        "read_only": True,
        "order_entry": False,
    }
