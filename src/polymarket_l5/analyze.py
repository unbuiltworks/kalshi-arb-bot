"""The eight pre-registered behavioural questions, answered from the CSVs.

Every result carries its own sample size and a conservative confidence label.
Nothing here fills gaps: when a field is unavailable (unresolved market, no
positions data) the affected test reports ``insufficient-data`` rather than
substituting an assumption.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import config, stats

BALANCE_TOLERANCE_ABS = 1.0  # shares
BALANCE_TOLERANCE_REL = 0.01  # 1% of the larger leg


def read_csv(path: Path) -> List[Dict[str, str]]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def fnum(row: Dict[str, str], key: str) -> Optional[float]:
    value = row.get(key, "")
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _subset(rows: Sequence[Dict[str, str]], key: str, value: str) -> List[Dict[str, str]]:
    return [row for row in rows if row.get(key) == value]


def _col(rows: Sequence[Dict[str, str]], key: str) -> List[float]:
    return [value for value in (fnum(row, key) for row in rows) if value is not None]


def _is_balanced(row: Dict[str, str]) -> bool:
    residual = fnum(row, "residual_qty_abs")
    up = fnum(row, "final_up_qty") or 0.0
    down = fnum(row, "final_down_qty") or 0.0
    if residual is None:
        return False
    larger = max(up, down)
    if residual <= BALANCE_TOLERANCE_ABS:
        return True
    return larger > 0 and (residual / larger) <= BALANCE_TOLERANCE_REL


def _window_pnl(row: Dict[str, str]) -> Optional[float]:
    reported = fnum(row, "pnl_polymarket_positions")
    if reported is not None:
        return reported
    return fnum(row, "pnl_derived_resolution")


# --- Q1: does size increase with price? --------------------------------------


def q1_size_vs_price(trades: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    buys = [row for row in trades if (row.get("side") or "").upper() == "BUY"]
    prices = [fnum(row, "price") for row in buys]
    shares = [fnum(row, "shares") for row in buys]
    dollars = [fnum(row, "dollars") for row in buys]

    per_band = {}
    for name, low, high in config.PRICE_BANDS:
        band = [
            row
            for row in buys
            if (fnum(row, "price") is not None and low <= (fnum(row, "price") or 0) < high)
        ]
        per_band[name] = {
            "n_fills": len(band),
            "shares": stats.describe(_col(band, "shares")),
            "dollars": stats.describe(_col(band, "dollars")),
        }

    threshold_tests = {}
    for threshold in config.PRICE_THRESHOLDS:
        below = [
            fnum(row, "shares")
            for row in buys
            if (fnum(row, "price") is not None and (fnum(row, "price") or 0) < threshold)
        ]
        at_or_above = [
            fnum(row, "shares")
            for row in buys
            if (fnum(row, "price") is not None and (fnum(row, "price") or 0) >= threshold)
        ]
        test = stats.mann_whitney(at_or_above, below)
        below_d = [
            fnum(row, "dollars")
            for row in buys
            if (fnum(row, "price") is not None and (fnum(row, "price") or 0) < threshold)
        ]
        above_d = [
            fnum(row, "dollars")
            for row in buys
            if (fnum(row, "price") is not None and (fnum(row, "price") or 0) >= threshold)
        ]
        dollars_test = stats.mann_whitney(above_d, below_d)
        effect = bool(
            test.get("median1") is not None
            and test.get("median2") is not None
            and test["median1"] != test["median2"]
        )
        threshold_tests[f"ge_{int(threshold * 100)}c"] = {
            "shares_rank_sum": test,
            "dollars_rank_sum": dollars_test,
            "confidence": stats.confidence_label(
                min(test.get("n1") or 0, test.get("n2") or 0), test.get("p_value"), effect
            ),
        }

    return {
        "question": "Does position size increase at 70c / 80c / 90c?",
        "n_buy_fills": len(buys),
        "spearman_price_vs_shares": stats.spearman(prices, shares),
        "spearman_price_vs_dollars": stats.spearman(prices, dollars),
        "per_price_band": per_band,
        "threshold_tests": threshold_tests,
    }


# --- Q2/Q3: group comparisons -------------------------------------------------

GROUP_METRICS = (
    "total_trades",
    "total_dollars_bought",
    "max_price_bought",
    "matched_qty",
    "matched_pair_unit_cost",
    "residual_qty_abs",
    "seconds_first_to_opposite_side",
    "first_price",
    "first_trade_seconds_from_start",
)


def _compare_groups(
    group_a: Sequence[Dict[str, str]], group_b: Sequence[Dict[str, str]], label_a: str, label_b: str
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "group_a": label_a,
        "group_b": label_b,
        "n_a": len(group_a),
        "n_b": len(group_b),
        "metrics": {},
        "rates": {},
    }
    for metric in GROUP_METRICS:
        values_a, values_b = _col(group_a, metric), _col(group_b, metric)
        test = stats.mann_whitney(values_a, values_b)
        effect = bool(
            test.get("median1") is not None
            and test.get("median2") is not None
            and test["median1"] != test["median2"]
        )
        out["metrics"][metric] = {
            "a": stats.describe(values_a),
            "b": stats.describe(values_b),
            "rank_sum": test,
            "confidence": stats.confidence_label(
                min(len(values_a), len(values_b)), test.get("p_value"), effect
            ),
        }
    for label, predicate in (
        ("balanced_at_end", _is_balanced),
        ("cheaper_side_first", lambda row: row.get("cheaper_side_first") == "yes"),
        ("any_buy_ge_80c", lambda row: row.get("any_buy_ge_80c") == "yes"),
        ("any_buy_ge_90c", lambda row: row.get("any_buy_ge_90c") == "yes"),
        ("opposite_side_ever_bought", lambda row: row.get("opposite_side_ever_bought") == "yes"),
    ):
        x1 = sum(1 for row in group_a if predicate(row))
        x2 = sum(1 for row in group_b if predicate(row))
        test = stats.two_proportion_test(x1, len(group_a), x2, len(group_b))
        out["rates"][label] = {
            "test": test,
            "confidence": stats.confidence_label(
                min(len(group_a), len(group_b)),
                test.get("p_value"),
                bool(test.get("diff")),
            ),
        }
    return out


def q2_window_length(summary: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    result = _compare_groups(
        _subset(summary, "window_minutes", "5"),
        _subset(summary, "window_minutes", "15"),
        "5m",
        "15m",
    )
    result["question"] = "Does behaviour differ between 5m and 15m windows?"
    return result


def q3_asset(summary: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    result = _compare_groups(
        _subset(summary, "asset", "BTC"), _subset(summary, "asset", "ETH"), "BTC", "ETH"
    )
    result["question"] = "Does behaviour differ between BTC and ETH?"
    return result


# --- Q4: cheaper side first ---------------------------------------------------


def q4_cheaper_side(summary: Sequence[Dict[str, str]], trades: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    decided = [row for row in summary if row.get("cheaper_side_first") in ("yes", "no")]
    cheaper = sum(1 for row in decided if row.get("cheaper_side_first") == "yes")
    buys = [row for row in trades if (row.get("side") or "").upper() == "BUY"]
    sub50 = sum(1 for row in buys if (fnum(row, "price") or 1.0) < 0.5)
    window_test = stats.one_proportion_test(cheaper, len(decided), 0.5)
    fill_test = stats.one_proportion_test(sub50, len(buys), 0.5)
    return {
        "question": "Does he systematically buy the cheaper side (first / overall)?",
        "note": (
            "On a binary Up/Down market the two sides price to ~$1.00, so 'cheaper "
            "side' is equivalent to a fill below $0.50 at that moment."
        ),
        "first_buy_cheaper_side": window_test,
        "first_buy_confidence": stats.confidence_label(
            len(decided), window_test.get("p_value"), cheaper != len(decided) - cheaper
        ),
        "all_buys_below_50c": fill_test,
        "all_buys_confidence": stats.confidence_label(
            len(buys), fill_test.get("p_value"), sub50 != len(buys) - sub50
        ),
        "first_price_distribution": stats.describe(_col(summary, "first_price")),
    }


# --- Q5: latency to the opposite side ----------------------------------------


def q5_opposite_latency(summary: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    with_opposite = [row for row in summary if row.get("opposite_side_ever_bought") == "yes"]
    latencies = _col(with_opposite, "seconds_first_to_opposite_side")
    result: Dict[str, Any] = {
        "question": "After buying the favoured side, how quickly is the opposite side bought?",
        "n_windows": len(summary),
        "n_windows_with_opposite_buy": len(with_opposite),
        "share_with_opposite_buy": (len(with_opposite) / len(summary)) if summary else None,
        "latency_seconds": stats.describe(latencies),
        "latency_median_ci95": stats.bootstrap_ci(latencies),
        "by_window_minutes": {},
        "by_asset": {},
        "latency_as_fraction_of_window": None,
    }
    for minutes in config.WINDOW_BUCKETS_MIN:
        group = _subset(with_opposite, "window_minutes", str(minutes))
        result["by_window_minutes"][f"{minutes}m"] = stats.describe(
            _col(group, "seconds_first_to_opposite_side")
        )
    for asset in config.ASSETS:
        group = _subset(with_opposite, "asset", asset)
        result["by_asset"][asset] = stats.describe(_col(group, "seconds_first_to_opposite_side"))

    fractions = []
    for row in with_opposite:
        latency = fnum(row, "seconds_first_to_opposite_side")
        minutes = fnum(row, "window_minutes")
        if latency is not None and minutes:
            fractions.append(latency / (minutes * 60.0))
    result["latency_as_fraction_of_window"] = stats.describe(fractions)
    return result


# --- Q6: combined acquisition-cost threshold ---------------------------------


def q6_pair_cost_threshold(summary: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    costs = _col(
        [row for row in summary if (fnum(row, "matched_qty") or 0) > 0], "matched_pair_unit_cost"
    )
    description = stats.describe(costs)
    grid = [round(0.90 + 0.01 * step, 2) for step in range(0, 16)]
    cumulative = {
        f"<= {value:.2f}": (sum(1 for cost in costs if cost <= value) / len(costs)) if costs else None
        for value in grid
    }
    median_value = stats.median(costs)
    within_1c = (
        sum(1 for cost in costs if median_value is not None and abs(cost - median_value) <= 0.01)
        / len(costs)
        if costs
        else None
    )
    within_2c = (
        sum(1 for cost in costs if median_value is not None and abs(cost - median_value) <= 0.02)
        / len(costs)
        if costs
        else None
    )
    iqr = (
        description["p75"] - description["p25"]
        if description["p75"] is not None and description["p25"] is not None
        else None
    )
    return {
        "question": "Does he target a combined Up+Down acquisition-cost threshold?",
        "interpretation": (
            "A matched Up+Down pair pays exactly $1.00 at resolution. A unit cost "
            "clustered just below $1.00 is consistent with a pair-cost target; a "
            "wide, unimodal-at-nothing spread is not."
        ),
        "n_windows_with_pairs": len(costs),
        "unit_cost": description,
        "unit_cost_median_ci95": stats.bootstrap_ci(costs),
        "iqr_width": iqr,
        "share_within_1c_of_median": within_1c,
        "share_within_2c_of_median": within_2c,
        "share_below_1_00": (sum(1 for cost in costs if cost < 1.0) / len(costs)) if costs else None,
        "cumulative_share_at_or_below": cumulative,
        "confidence": stats.confidence_label(
            len(costs),
            None if iqr is None else (0.01 if (iqr is not None and iqr <= 0.02) else 1.0),
            iqr is not None,
        ),
    }


# --- Q7: balanced vs directional finish --------------------------------------


def q7_balanced_finish(summary: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    balanced = sum(1 for row in summary if _is_balanced(row))
    test = stats.one_proportion_test(balanced, len(summary), 0.5)
    by_group: Dict[str, Any] = {}
    for key, values in (("window_minutes", ["5", "15"]), ("asset", list(config.ASSETS))):
        for value in values:
            group = _subset(summary, key, value)
            hits = sum(1 for row in group if _is_balanced(row))
            by_group[f"{key}={value}"] = {
                "n": len(group),
                "balanced": hits,
                "share": (hits / len(group)) if group else None,
            }
    residual = _col(summary, "residual_qty_abs")
    residual_share = []
    for row in summary:
        abs_residual = fnum(row, "residual_qty_abs")
        up = fnum(row, "final_up_qty") or 0.0
        down = fnum(row, "final_down_qty") or 0.0
        larger = max(up, down)
        if abs_residual is not None and larger > 0:
            residual_share.append(abs_residual / larger)
    return {
        "question": "How often does he finish balanced vs directional?",
        "definition": (
            f"balanced = residual <= {BALANCE_TOLERANCE_ABS} share or "
            f"<= {BALANCE_TOLERANCE_REL:.0%} of the larger leg"
        ),
        "n_windows": len(summary),
        "balanced_windows": balanced,
        "balanced_test_vs_50pct": test,
        "residual_qty_abs": stats.describe(residual),
        "residual_share_of_larger_leg": stats.describe(residual_share),
        "by_group": by_group,
        "confidence": stats.confidence_label(len(summary), test.get("p_value"), True),
    }


# --- Q8: winners vs losers ----------------------------------------------------


def q8_profit_vs_loss(summary: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    with_pnl = [row for row in summary if _window_pnl(row) is not None]
    winners = [row for row in with_pnl if (_window_pnl(row) or 0) > 0]
    losers = [row for row in with_pnl if (_window_pnl(row) or 0) < 0]
    result = _compare_groups(winners, losers, "profitable", "losing")
    result.update(
        {
            "question": "How do profitable and losing windows differ?",
            "n_windows_with_pnl": len(with_pnl),
            "n_profitable": len(winners),
            "n_losing": len(losers),
            "n_flat": len(with_pnl) - len(winners) - len(losers),
            "pnl_distribution": stats.describe([_window_pnl(row) for row in with_pnl]),
            "total_pnl": sum(_window_pnl(row) or 0.0 for row in with_pnl) if with_pnl else None,
            "pnl_sources": _count_values(with_pnl, "pnl_source"),
            "caveat": (
                "Trading P&L only. Maker rebates, liquidity-incentive payouts and gas "
                "are NOT included and must not be assumed to be."
            ),
        }
    )
    return result


def _count_values(rows: Sequence[Dict[str, str]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row.get(key, "")] = counts.get(row.get(key, ""), 0) + 1
    return counts


# --- Driver -------------------------------------------------------------------


def coverage(summary: Sequence[Dict[str, str]], trades: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    first_ts = min((fnum(row, "timestamp") or 0) for row in trades) if trades else None
    last_ts = max((fnum(row, "timestamp") or 0) for row in trades) if trades else None
    return {
        "n_trades": len(trades),
        "n_windows": len(summary),
        "windows_by_asset": _count_values(summary, "asset"),
        "windows_by_length": _count_values(summary, "window_minutes"),
        "windows_by_window_source": _count_values(summary, "window_source"),
        "windows_with_resolution": sum(1 for row in summary if row.get("winning_outcome")),
        "windows_with_any_pnl": sum(1 for row in summary if _window_pnl(row) is not None),
        "first_trade_ts": first_ts,
        "last_trade_ts": last_ts,
    }


def analyze(output_dir: Optional[Path] = None) -> Dict[str, Any]:
    output_dir = Path(output_dir or config.OUTPUT_DIR)
    trades = read_csv(output_dir / config.TRADES_CSV)
    summary = read_csv(output_dir / config.SUMMARY_CSV)
    results = {
        "coverage": coverage(summary, trades),
        "q1_size_vs_price": q1_size_vs_price(trades),
        "q2_window_length": q2_window_length(summary),
        "q3_asset": q3_asset(summary),
        "q4_cheaper_side": q4_cheaper_side(summary, trades),
        "q5_opposite_latency": q5_opposite_latency(summary),
        "q6_pair_cost_threshold": q6_pair_cost_threshold(summary),
        "q7_balanced_finish": q7_balanced_finish(summary),
        "q8_profit_vs_loss": q8_profit_vs_loss(summary),
    }
    path = output_dir / config.STATS_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return results
