"""Analyze read-only BTC proxy-calibration settlement logs.

Input may be raw JSONL payloads or Railway-style lines containing
``PROXY_SETTLEMENT {json}``.  Cohorts answer a narrow empirical question: when a
free public-price composite was already on the same side of both venue thresholds
at a given seconds-to-expiry checkpoint, how often did the eventual Gemini and
Kalshi outcomes still disagree or the proxy predict either venue incorrectly?

This is basis-risk research, never risk-free-arbitrage certification.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
import math
from pathlib import Path
from typing import Any, Iterable

D = Decimal
CHECKPOINTS_S = (60, 30, 10, 5, 3, 2, 1)
MARGIN_BPS = (D("0"), D("1"), D("2"), D("5"), D("10"), D("20"), D("50"))
MIN_LIVE_RESEARCH_SAMPLE = 1000
MAX_WILSON95_OUTCOME_DISAGREEMENT = 0.005


def wilson_upper_95(errors: int, n: int) -> float | None:
    if n <= 0:
        return None
    if errors < 0 or errors > n:
        raise ValueError("invalid binomial counts")
    z = 1.959963984540054
    p = errors / n
    z2 = z * z
    center = p + z2 / (2 * n)
    radius = z * math.sqrt((p * (1 - p) + z2 / (4 * n)) / n)
    return (center + radius) / (1 + z2 / n)


def _d(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return D(str(value))
    except Exception:
        return None


def parse_settlement_lines(lines: Iterable[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    marker = "PROXY_SETTLEMENT "
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if marker in line:
            line = line.split(marker, 1)[1].strip()
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("classification") == "INDEX_MISMATCH_CALIBRATION_ONLY":
            rows.append(payload)
    return rows


def _checkpoint_rows(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    g_strike = _d(payload.get("gemini_strike"))
    k_target = _d(payload.get("kalshi_target"))
    g_actual = str(payload.get("gemini_outcome") or "").lower()
    k_actual = str(payload.get("kalshi_result") or "").lower()
    if g_strike is None or k_target is None or g_actual not in {"yes", "no"} or k_actual not in {"yes", "no"}:
        return []

    rows: list[dict[str, Any]] = []
    for cp in payload.get("checkpoints") or []:
        if not isinstance(cp, dict):
            continue
        price = _d(cp.get("composite_price"))
        checkpoint_s = cp.get("checkpoint_s")
        if price is None or checkpoint_s is None:
            continue
        g_pred = "yes" if price > g_strike else "no"
        k_pred = "yes" if price > k_target else "no"
        g_margin = abs(price - g_strike) / g_strike * D("10000") if g_strike else None
        k_margin = abs(price - k_target) / k_target * D("10000") if k_target else None
        if g_margin is None or k_margin is None:
            continue
        rows.append({
            "window": str(payload.get("window_end_utc") or ""),
            "checkpoint_s": int(checkpoint_s),
            "same_side": g_pred == k_pred,
            "min_boundary_margin_bps": min(g_margin, k_margin),
            "gemini_proxy_correct": g_pred == g_actual,
            "kalshi_proxy_correct": k_pred == k_actual,
            "outcomes_agree": g_actual == k_actual,
            "both_proxy_correct": g_pred == g_actual and k_pred == k_actual,
        })
    return rows


def _percentile(values: list[Decimal], q: float) -> str | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return str(ordered[idx])


def summarize(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    flat = [row for payload in payloads for row in _checkpoint_rows(payload)]
    result: dict[str, Any] = {}
    for checkpoint_s in CHECKPOINTS_S:
        checkpoint_rows = [r for r in flat if r["checkpoint_s"] == checkpoint_s]
        cohorts: dict[str, Any] = {}
        for margin in MARGIN_BPS:
            eligible = [
                r for r in checkpoint_rows
                if r["same_side"] and r["min_boundary_margin_bps"] >= margin
            ]
            n = len(eligible)
            disagreements = sum(not r["outcomes_agree"] for r in eligible)
            proxy_errors = sum(not r["both_proxy_correct"] for r in eligible)
            upper = wilson_upper_95(disagreements, n)
            research_gate = (
                n >= MIN_LIVE_RESEARCH_SAMPLE
                and disagreements == 0
                and upper is not None
                and upper <= MAX_WILSON95_OUTCOME_DISAGREEMENT
            )
            cohorts[str(margin.normalize()) if margin else "0"] = {
                "min_abs_margin_bps_from_both_boundaries": str(margin),
                "eligible_windows": n,
                "venue_outcome_disagreements": disagreements,
                "venue_outcome_disagreement_rate": disagreements / n if n else None,
                "venue_outcome_disagreement_wilson_upper_95": upper,
                "proxy_wrong_for_either_venue": proxy_errors,
                "proxy_wrong_rate": proxy_errors / n if n else None,
                "research_gate_passes": research_gate,
            }
        margins = [r["min_boundary_margin_bps"] for r in checkpoint_rows if r["same_side"]]
        result[str(checkpoint_s)] = {
            "checkpoint_s": checkpoint_s,
            "windows_with_checkpoint": len(checkpoint_rows),
            "same_side_windows": sum(r["same_side"] for r in checkpoint_rows),
            "same_side_margin_bps_p50": _percentile(margins, 0.50),
            "same_side_margin_bps_p95": _percentile(margins, 0.95),
            "cohorts": cohorts,
        }

    baseline_n = len(payloads)
    baseline_disagreements = sum(
        str(p.get("gemini_outcome") or "").lower() in {"yes", "no"}
        and str(p.get("kalshi_result") or "").lower() in {"yes", "no"}
        and str(p.get("gemini_outcome")).lower() != str(p.get("kalshi_result")).lower()
        for p in payloads
    )
    return {
        "settled_windows": baseline_n,
        "baseline_outcome_disagreements": baseline_disagreements,
        "baseline_outcome_disagreement_rate": baseline_disagreements / baseline_n if baseline_n else None,
        "checkpoints": result,
        "research_gate_minimum_samples": MIN_LIVE_RESEARCH_SAMPLE,
        "research_gate_max_wilson95_outcome_disagreement": MAX_WILSON95_OUTCOME_DISAGREEMENT,
        "research_gate_interpretation": "STATISTICAL_BASIS_RISK_FILTER_NOT_RISK_FREE_ARBITRAGE_CERTIFICATION",
        "classification": "INDEX_MISMATCH_CALIBRATION_ONLY",
        "read_only": True,
        "order_entry": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    lines: list[str] = []
    for path in args.paths:
        lines.extend(path.read_text(encoding="utf-8").splitlines())
    print(json.dumps(summarize(parse_settlement_lines(lines)), separators=(",", ":")))


if __name__ == "__main__":
    main()
