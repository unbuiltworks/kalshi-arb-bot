"""The eight quantitative questions, answered from the reconstructed windows.

Every result carries its own sample size and a conservative verdict label.
Nothing here corrects for the number of hypotheses tested, and the findings
document says so -- these are exploratory comparisons on one trader's public
fills, not confirmatory tests.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Callable, Iterable, Sequence

from src.l5 import config, stats
from src.l5.normalize import Trade
from src.l5.windows import Window, WindowSummary, price_band

MIN_N = config.MIN_SAMPLE_FOR_CLAIM


def _clean(values: Iterable[Any]) -> list[float]:
    return [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]


def _compare(
    label_a: str, values_a: Sequence[float], label_b: str, values_b: Sequence[float]
) -> dict:
    test = stats.mann_whitney_u(values_a, values_b)
    n = min(len(values_a), len(values_b))
    return {
        "group_a": label_a,
        "group_b": label_b,
        "a": stats.describe(values_a),
        "b": stats.describe(values_b),
        "mann_whitney": test,
        "effect_rank_biserial": test["rank_biserial"],
        "verdict": stats.verdict(test["p"], n, MIN_N),
    }


def _rate_compare(label_a: str, k1: int, n1: int, label_b: str, k2: int, n2: int) -> dict:
    test = stats.two_proportion_z(k1, n1, k2, n2)
    return {
        "group_a": label_a,
        "group_b": label_b,
        "two_proportion_z": test,
        "verdict": stats.verdict(test["p"], min(n1, n2), MIN_N),
    }


# --- per-window feature metrics ---------------------------------------------

WINDOW_METRICS: dict[str, Callable[[WindowSummary], Any]] = {
    "total_trades": lambda s: s.total_trades,
    "total_dollars_bought": lambda s: s.total_dollars_bought,
    "total_shares_bought": lambda s: s.total_shares_bought,
    "max_price_bought": lambda s: s.max_price_bought,
    "first_price": lambda s: s.first_price,
    "matched_qty": lambda s: s.matched_qty,
    "matched_pair_cost_per_share": lambda s: s.matched_pair_cost_per_share,
    "residual_frac_of_larger_leg": lambda s: s.residual_frac_of_larger_leg,
    "seconds_to_opposite_side": lambda s: s.seconds_to_opposite_side,
    "first_seconds_from_window_start": lambda s: s.first_seconds_from_window_start,
}

WINDOW_RATES: dict[str, Callable[[WindowSummary], bool]] = {
    "bought_at_70c_plus": lambda s: s.bought_at_70c_plus,
    "bought_at_80c_plus": lambda s: s.bought_at_80c_plus,
    "bought_at_90c_plus": lambda s: s.bought_at_90c_plus,
    "finished_balanced": lambda s: s.finished_balanced,
    "traded_both_sides": lambda s: s.traded_both_sides,
    "first_price_below_half": lambda s: s.first_price_below_half,
}


def complete_windows(windows: Sequence[Window]) -> list[Window]:
    """Windows the retrieved data spans end to end.

    Every window-level statistic uses these. A partially captured window has a
    truncated fill list, so its final inventory and balance are artefacts of
    where the capture stopped, not of how the trader finished.
    """
    return [w for w in windows if w.summary and w.summary.capture_complete]


def _metric_values(summaries: Sequence[WindowSummary], key: str) -> list[float]:
    return _clean(WINDOW_METRICS[key](s) for s in summaries)


def _group_compare(
    label_a: str, group_a: Sequence[WindowSummary], label_b: str, group_b: Sequence[WindowSummary]
) -> dict:
    out: dict[str, Any] = {
        "n_windows_a": len(group_a),
        "n_windows_b": len(group_b),
        "metrics": {},
        "rates": {},
    }
    for key in WINDOW_METRICS:
        out["metrics"][key] = _compare(
            label_a, _metric_values(group_a, key), label_b, _metric_values(group_b, key)
        )
    for key, predicate in WINDOW_RATES.items():
        out["rates"][key] = _rate_compare(
            label_a,
            sum(1 for s in group_a if predicate(s)),
            len(group_a),
            label_b,
            sum(1 for s in group_b if predicate(s)),
            len(group_b),
        )
    return out


# --- coverage ----------------------------------------------------------------


def coverage(trades: Sequence[Trade], windows: Sequence[Window]) -> dict:
    summaries = [w.summary for w in windows if w.summary]
    timestamps = [t.timestamp for t in trades]
    by_cell: Counter = Counter((s.asset, s.window_minutes) for s in summaries)
    return {
        "n_trades": len(trades),
        "n_windows": len(summaries),
        "first_trade_ts": min(timestamps) if timestamps else None,
        "last_trade_ts": max(timestamps) if timestamps else None,
        "windows_by_cell": {f"{a}_{m}m": n for (a, m), n in sorted(by_cell.items())},
        "trades_by_cell": dict(
            Counter(f"{t.asset}_{t.window_minutes}m" for t in trades)
        ),
        "windows_with_realized_pnl": sum(1 for s in summaries if s.realized_pnl is not None),
        "windows_window_source_metadata": sum(
            1 for s in summaries if s.window_source == "metadata"
        ),
        "windows_window_source_text_fallback": sum(
            1 for s in summaries if s.window_source == "text_fallback"
        ),
        "windows_with_window_conflict": sum(1 for s in summaries if s.window_conflict),
        "windows_with_reconstructed_bounds": sum(1 for s in summaries if s.bounds_reconstructed),
        "windows_with_oversold_flag": sum(1 for s in summaries if s.oversold),
        "windows_fully_captured": sum(1 for s in summaries if s.capture_complete),
        "windows_partially_captured": sum(1 for s in summaries if not s.capture_complete),
        "note_on_partial_windows": (
            "Window-level statistics (questions 3-9) use fully captured windows only. "
            "Per-trade statistics (question 2) use every fill."
        ),
    }


# --- Q1: does size escalate with price? --------------------------------------


def q1_size_vs_price(trades: Sequence[Trade], windows: Sequence[Window]) -> dict:
    buys = [t for t in trades if t.side == "BUY"]
    prices = [t.price for t in buys]
    shares = [t.shares for t in buys]
    dollars = [t.dollars for t in buys]

    bands: dict[str, dict] = {}
    for label in config.PRICE_BAND_LABELS:
        in_band = [t for t in buys if price_band(t.price) == label]
        bands[label] = {
            "n_trades": len(in_band),
            "shares": stats.describe([t.shares for t in in_band]),
            "dollars": stats.describe([t.dollars for t in in_band]),
        }

    thresholds: dict[str, dict] = {}
    for threshold in config.ESCALATION_THRESHOLDS:
        below = [t.shares for t in buys if t.price < threshold]
        at_or_above = [t.shares for t in buys if t.price >= threshold]
        below_d = [t.dollars for t in buys if t.price < threshold]
        above_d = [t.dollars for t in buys if t.price >= threshold]
        thresholds[f"{int(threshold * 100)}c"] = {
            "shares": _compare(f"price<{threshold}", below, f"price>={threshold}", at_or_above),
            "dollars": _compare(f"price<{threshold}", below_d, f"price>={threshold}", above_d),
        }

    # Pooling every window together confounds "bigger at high prices" with
    # "bigger in busy windows". The within-window test removes that: it asks
    # whether size rises with price *inside* each window, then counts how many
    # windows agree.
    per_window_rhos: list[float] = []
    for window in windows:
        window_buys = [t for t in window.trades if t.side == "BUY"]
        if len(window_buys) < 4:
            continue
        if len({t.price for t in window_buys}) < 3:
            continue
        result = stats.spearman([t.price for t in window_buys], [t.shares for t in window_buys])
        if result["rho"] is not None:
            per_window_rhos.append(result["rho"])

    positive = sum(1 for r in per_window_rhos if r > 0)

    return {
        "question": "Does position size increase at 70c, 80c or 90c?",
        "n_buy_trades": len(buys),
        "pooled_spearman_price_vs_shares": stats.spearman(prices, shares),
        "pooled_spearman_price_vs_dollars": stats.spearman(prices, dollars),
        "by_price_band": bands,
        "threshold_splits": thresholds,
        "within_window": {
            "n_windows_tested": len(per_window_rhos),
            "rho": stats.describe(per_window_rhos),
            "sign_test_positive_rho": stats.binomial_sign_test(positive, len(per_window_rhos)),
            "bootstrap_median_rho": stats.bootstrap_ci(
                per_window_rhos, stats.median, config.BOOTSTRAP_N, config.BOOTSTRAP_SEED
            ),
            "verdict": stats.verdict(
                stats.binomial_sign_test(positive, len(per_window_rhos))["p"],
                len(per_window_rhos),
                MIN_N,
            ),
        },
    }


# --- Q2 / Q3: 5m vs 15m, BTC vs ETH ------------------------------------------


def q2_duration_split(windows: Sequence[Window]) -> dict:
    summaries = [w.summary for w in complete_windows(windows)]
    five = [s for s in summaries if s.window_minutes == 5]
    fifteen = [s for s in summaries if s.window_minutes == 15]
    return {
        "question": "Does behaviour differ between 5m and 15m windows?",
        **_group_compare("5m", five, "15m", fifteen),
    }


def q3_asset_split(windows: Sequence[Window]) -> dict:
    summaries = [w.summary for w in complete_windows(windows)]
    btc = [s for s in summaries if s.asset == "BTC"]
    eth = [s for s in summaries if s.asset == "ETH"]
    return {
        "question": "Does behaviour differ between BTC and ETH?",
        **_group_compare("BTC", btc, "ETH", eth),
    }


# --- Q4: does he buy the cheaper side? ---------------------------------------


def q4_cheaper_side(trades: Sequence[Trade], windows: Sequence[Window]) -> dict:
    summaries = [w.summary for w in windows if w.summary]
    opening_below_half = sum(1 for s in summaries if s.first_price < 0.5)

    # Cross-check that does not lean on binary complementarity: compare each
    # buy against the nearest buy on the opposite side of the same window
    # within CONTEMPORANEOUS_WINDOW_S seconds. Both prices are observed, so no
    # order-book state is assumed.
    cheaper = 0
    comparable = 0
    margins: list[float] = []
    for window in windows:
        buys = [t for t in window.trades if t.side == "BUY"]
        for trade in buys:
            others = [
                o
                for o in buys
                if o.outcome != trade.outcome
                and abs(o.timestamp - trade.timestamp) <= config.CONTEMPORANEOUS_WINDOW_S
            ]
            if not others:
                continue
            nearest = min(others, key=lambda o: (abs(o.timestamp - trade.timestamp), o.timestamp))
            comparable += 1
            margins.append(nearest.price - trade.price)
            if trade.price < nearest.price:
                cheaper += 1

    all_buys = [t for t in trades if t.side == "BUY"]
    return {
        "question": "Does he systematically buy the cheaper side?",
        "opening_trade_below_50c": {
            **stats.binomial_sign_test(opening_below_half, len(summaries)),
            "verdict": stats.verdict(
                stats.binomial_sign_test(opening_below_half, len(summaries))["p"],
                len(summaries),
                MIN_N,
            ),
            "assumption": (
                "Treats 50c as the neutral point of a two-outcome market. It does not "
                "observe the opposite side's contemporaneous price."
            ),
        },
        "vs_contemporaneous_opposite_side": {
            **stats.binomial_sign_test(cheaper, comparable),
            "n_comparable_buys": comparable,
            "n_buys_total": len(all_buys),
            "price_margin_vs_opposite": stats.describe(margins),
            "verdict": stats.verdict(
                stats.binomial_sign_test(cheaper, comparable)["p"], comparable, MIN_N
            ),
            "assumption": (
                f"Both prices are this trader's own executed fills within "
                f"{config.CONTEMPORANEOUS_WINDOW_S}s of each other. No resting book is inferred."
            ),
        },
        "all_buys_below_50c": stats.binomial_sign_test(
            sum(1 for t in all_buys if t.price < 0.5), len(all_buys)
        ),
    }


# --- Q5: how fast does the opposite side follow? -----------------------------


def q5_time_to_opposite(windows: Sequence[Window]) -> dict:
    summaries = [w.summary for w in complete_windows(windows)]
    both = [s for s in summaries if s.traded_both_sides and s.seconds_to_opposite_side is not None]
    seconds = [float(s.seconds_to_opposite_side) for s in both]
    frac_of_window = [
        s.seconds_to_opposite_side / (s.window_minutes * 60)
        for s in both
        if s.window_minutes
    ]
    by_duration = {}
    for minutes in config.WINDOW_MINUTES:
        subset = [s for s in both if s.window_minutes == minutes]
        by_duration[f"{minutes}m"] = {
            "n": len(subset),
            "seconds": stats.describe([float(s.seconds_to_opposite_side) for s in subset]),
            "trades_before_opposite": stats.describe(
                _clean(s.trades_before_opposite_side for s in subset)
            ),
        }
    return {
        "question": "After the first side, how quickly does the opposite side follow?",
        "n_windows_total": len(summaries),
        "n_windows_both_sides": len(both),
        "share_of_windows_both_sides": (len(both) / len(summaries)) if summaries else None,
        "seconds_to_opposite": stats.describe(seconds),
        "bootstrap_median_seconds": stats.bootstrap_ci(
            seconds, stats.median, config.BOOTSTRAP_N, config.BOOTSTRAP_SEED
        ),
        "fraction_of_window_elapsed": stats.describe(frac_of_window),
        "trades_before_opposite": stats.describe(
            _clean(s.trades_before_opposite_side for s in both)
        ),
        "by_window_length": by_duration,
    }


# --- Q6: is there a combined acquisition-cost target? ------------------------


def q6_pair_cost_target(windows: Sequence[Window]) -> dict:
    complete = complete_windows(windows)
    summaries = [w.summary for w in complete]
    paired = [
        s
        for s in summaries
        if s.matched_qty > 0 and s.matched_pair_cost_per_share is not None
    ]
    costs = [s.matched_pair_cost_per_share for s in paired]

    concentration = {}
    centre = stats.median(costs)
    if centre is not None:
        for tolerance in (0.005, 0.01, 0.02, 0.05):
            within = sum(1 for c in costs if abs(c - centre) <= tolerance)
            concentration[f"within_{tolerance:g}_of_median"] = {
                "n": within,
                "rate": within / len(costs) if costs else None,
            }

    # "Stops once the pair is cheap enough" would show up as the final pair
    # cost also being the running minimum for that window.
    final_is_min = 0
    considered = 0
    for window in complete:
        series = [
            s.matched_pair_cost_per_share
            for s in window.snapshots
            if s.matched_pair_cost_per_share is not None and s.matched_qty > 0
        ]
        if len(series) < 2:
            continue
        considered += 1
        if series[-1] <= min(series) + 1e-9:
            final_is_min += 1

    under_par = sum(1 for c in costs if c < 1.0)
    return {
        "question": "Does he target a combined Up+Down acquisition-cost threshold?",
        "n_paired_windows": len(paired),
        "pair_cost_per_share": stats.describe(costs),
        "bootstrap_median_pair_cost": stats.bootstrap_ci(
            costs, stats.median, config.BOOTSTRAP_N, config.BOOTSTRAP_SEED
        ),
        "concentration_around_median": concentration,
        "pair_cost_below_1_00": {
            **stats.binomial_sign_test(under_par, len(costs)),
            "meaning": (
                "A matched Up+Down pair costing under $1.00 settles at $1.00 and is "
                "locked profit before fees, rebates and gas."
            ),
        },
        "final_pair_cost_is_running_minimum": {
            "n_windows_considered": considered,
            "n_final_is_minimum": final_is_min,
            "rate": (final_is_min / considered) if considered else None,
            "note": (
                "A high rate is consistent with stopping once the pair is cheap enough, "
                "but is also what mechanically happens when the cheap side is bought last."
            ),
        },
        "by_cell": {
            f"{asset}_{minutes}m": stats.describe(
                [
                    s.matched_pair_cost_per_share
                    for s in paired
                    if s.asset == asset and s.window_minutes == minutes
                ]
            )
            for asset in config.ASSETS
            for minutes in config.WINDOW_MINUTES
        },
    }


# --- Q7: balanced vs directional finish --------------------------------------


def q7_balanced_finish(windows: Sequence[Window]) -> dict:
    summaries = [w.summary for w in complete_windows(windows)]
    balanced = sum(1 for s in summaries if s.finished_balanced)
    by_cell = {}
    for asset in config.ASSETS:
        for minutes in config.WINDOW_MINUTES:
            subset = [s for s in summaries if s.asset == asset and s.window_minutes == minutes]
            by_cell[f"{asset}_{minutes}m"] = {
                "n": len(subset),
                "n_balanced": sum(1 for s in subset if s.finished_balanced),
                "rate": (
                    sum(1 for s in subset if s.finished_balanced) / len(subset)
                    if subset
                    else None
                ),
            }
    return {
        "question": "How often does he finish balanced rather than directional?",
        "definition": (
            f"Balanced = residual |Up-Down| <= {config.BALANCED_TOLERANCE_SHARES} shares "
            f"or <= {config.BALANCED_TOLERANCE_FRAC:.0%} of the larger leg."
        ),
        "n_windows": len(summaries),
        "n_balanced": balanced,
        "rate": (balanced / len(summaries)) if summaries else None,
        "residual_fraction": stats.describe(
            _clean(s.residual_frac_of_larger_leg for s in summaries)
        ),
        "residual_side_counts": dict(Counter(s.residual_side for s in summaries)),
        "by_cell": by_cell,
        "one_sided_windows": sum(1 for s in summaries if not s.traded_both_sides),
    }


# --- Q8: profitable vs losing windows ----------------------------------------


def q8_pnl_split(windows: Sequence[Window]) -> dict:
    summaries = [
        w.summary for w in complete_windows(windows) if w.summary.realized_pnl is not None
    ]
    winners = [s for s in summaries if s.realized_pnl > 0]
    losers = [s for s in summaries if s.realized_pnl < 0]
    flat = [s for s in summaries if s.realized_pnl == 0]

    result: dict[str, Any] = {
        "question": "How do profitable and losing windows differ?",
        "n_windows_with_pnl": len(summaries),
        "n_winners": len(winners),
        "n_losers": len(losers),
        "n_flat": len(flat),
        "pnl": stats.describe([s.realized_pnl for s in summaries]),
        "total_realized_pnl": sum(s.realized_pnl for s in summaries) if summaries else None,
        "caveat": (
            "Realized P&L is Polymarket's own position accounting. It excludes liquidity "
            "rebates and maker incentives, which for a two-sided quoting strategy may be a "
            "large share of true economics. A negative trading P&L is therefore not proof "
            "the strategy loses money."
        ),
    }
    if winners and losers:
        result.update(_group_compare("profitable", winners, "losing", losers))
    else:
        result["metrics"] = {}
        result["rates"] = {}
    return result


# --- entry point -------------------------------------------------------------


def analyze(trades: Sequence[Trade], windows: Sequence[Window]) -> dict:
    return {
        "config": {
            "min_sample_for_claim": MIN_N,
            "balanced_tolerance_frac": config.BALANCED_TOLERANCE_FRAC,
            "balanced_tolerance_shares": config.BALANCED_TOLERANCE_SHARES,
            "contemporaneous_window_s": config.CONTEMPORANEOUS_WINDOW_S,
            "bootstrap_n": config.BOOTSTRAP_N,
            "bootstrap_seed": config.BOOTSTRAP_SEED,
            "price_band_edges": list(config.PRICE_BAND_EDGES),
        },
        "coverage": coverage(trades, windows),
        "q1_size_vs_price": q1_size_vs_price(trades, windows),
        "q2_duration_split": q2_duration_split(windows),
        "q3_asset_split": q3_asset_split(windows),
        "q4_cheaper_side": q4_cheaper_side(trades, windows),
        "q5_time_to_opposite": q5_time_to_opposite(windows),
        "q6_pair_cost_target": q6_pair_cost_target(windows),
        "q7_balanced_finish": q7_balanced_finish(windows),
        "q8_pnl_split": q8_pnl_split(windows),
    }
