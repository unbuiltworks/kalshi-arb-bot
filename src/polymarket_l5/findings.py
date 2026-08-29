"""Render L5_STRATEGY_FINDINGS.md from the computed statistics.

The report is generated, never hand-written, so its claims cannot drift from
the numbers. Candidate rules are stated with their support rate, an explicit
counterexample list, and a conservative confidence label; a rule with thin
support is printed as thin, not quietly dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import config, stats
from .analyze import _is_balanced, _window_pnl, fnum, read_csv
from .timeutil import now_iso

Row = Dict[str, str]


@dataclass
class Rule:
    key: str
    statement: str
    applies: Callable[[Row], bool]
    holds: Callable[[Row], bool]
    pseudocode: str


def _median_pair_cost(summary: Sequence[Row]) -> Optional[float]:
    return stats.median(
        [
            fnum(row, "matched_pair_unit_cost")
            for row in summary
            if (fnum(row, "matched_qty") or 0) > 0
        ]
    )


def _latency_p75(summary: Sequence[Row]) -> Optional[float]:
    return stats.quantile(
        [
            fnum(row, "seconds_first_to_opposite_side")
            for row in summary
            if row.get("opposite_side_ever_bought") == "yes"
        ],
        0.75,
    )


def build_rules(summary: Sequence[Row]) -> List[Rule]:
    median_cost = _median_pair_cost(summary)
    cost_target = None if median_cost is None else round(median_cost + 0.01, 4)
    latency_cut = _latency_p75(summary)

    rules = [
        Rule(
            key="R1_opens_cheaper_side",
            statement="The first buy in a window is on the side priced below $0.50.",
            applies=lambda row: row.get("cheaper_side_first") in ("yes", "no"),
            holds=lambda row: row.get("cheaper_side_first") == "yes",
            pseudocode="if best_price(side) < 0.50: open_here(side)",
        ),
        Rule(
            key="R2_always_buys_both_sides",
            statement="Both Up and Down are bought before the window closes.",
            applies=lambda row: True,
            holds=lambda row: row.get("opposite_side_ever_bought") == "yes",
            pseudocode="after first leg fills: require second leg on opposite outcome",
        ),
        Rule(
            key="R3_pair_cost_target",
            statement=(
                "Matched Up+Down acquisition cost finishes at or below "
                + (f"${cost_target:.4f}" if cost_target is not None else "the median target")
                + " (median + $0.01)."
            ),
            applies=lambda row: (fnum(row, "matched_qty") or 0) > 0
            and fnum(row, "matched_pair_unit_cost") is not None,
            holds=lambda row: cost_target is not None
            and (fnum(row, "matched_pair_unit_cost") or 9.9) <= cost_target,
            pseudocode=(
                "if wavg_up + candidate_down_price <= PAIR_COST_TARGET: buy_down()"
            ),
        ),
        Rule(
            key="R4_finishes_balanced",
            statement="The window ends with matched Up/Down inventory (no material residual).",
            applies=lambda row: True,
            holds=_is_balanced,
            pseudocode="before expiry: flatten |up_qty - down_qty| to ~0",
        ),
        Rule(
            key="R5_pays_up_to_high_prices",
            statement="At least one buy occurs at $0.80 or above.",
            applies=lambda row: True,
            holds=lambda row: row.get("any_buy_ge_80c") == "yes",
            pseudocode="allow chasing the second leg up to 0.80+ when pair cost still clears",
        ),
        Rule(
            key="R6_second_leg_latency",
            statement=(
                "The opposite side is bought within "
                + (f"{latency_cut:.0f}s" if latency_cut is not None else "the observed p75")
                + " of the first buy (observed p75)."
            ),
            applies=lambda row: row.get("opposite_side_ever_bought") == "yes"
            and fnum(row, "seconds_first_to_opposite_side") is not None,
            holds=lambda row: latency_cut is not None
            and (fnum(row, "seconds_first_to_opposite_side") or 9e9) <= latency_cut,
            pseudocode="second_leg_deadline = first_fill_ts + SECOND_LEG_MAX_SECONDS",
        ),
    ]
    return rules


def evaluate_rule(rule: Rule, summary: Sequence[Row]) -> Dict[str, Any]:
    applicable = [row for row in summary if rule.applies(row)]
    holds = [row for row in applicable if rule.holds(row)]
    fails = [row for row in applicable if not rule.holds(row)]
    test = stats.one_proportion_test(len(holds), len(applicable), 0.5)
    share = (len(holds) / len(applicable)) if applicable else None
    return {
        "key": rule.key,
        "statement": rule.statement,
        "n_applicable": len(applicable),
        "n_holds": len(holds),
        "share": share,
        "ci95": test.get("ci95"),
        "p_vs_coinflip": test.get("p_value"),
        "confidence": stats.confidence_label(
            len(applicable), test.get("p_value"), share is not None and share != 0.5
        ),
        "counterexamples": [
            {
                "window_id": row.get("window_id", ""),
                "market_slug": row.get("market_slug", ""),
                "asset": row.get("asset", ""),
                "window_minutes": row.get("window_minutes", ""),
                "first_price": row.get("first_price", ""),
                "matched_pair_unit_cost": row.get("matched_pair_unit_cost", ""),
                "residual_qty_signed": row.get("residual_qty_signed", ""),
            }
            for row in fails[:5]
        ],
        "n_counterexamples": len(fails),
        "pseudocode": rule.pseudocode,
    }


def _fmt(value: Any, places: int = 4) -> str:
    if value is None or value == "":
        return "n/a"
    if isinstance(value, (int,)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{places}f}"
    return str(value)


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _describe_line(label: str, description: Dict[str, Any]) -> str:
    return (
        f"| {label} | {description.get('n', 0)} | {_fmt(description.get('median'))} | "
        f"{_fmt(description.get('p25'))} | {_fmt(description.get('p75'))} | "
        f"{_fmt(description.get('mean'))} |"
    )


def render(results: Dict[str, Any], summary: Sequence[Row], manifest: Dict[str, Any]) -> str:
    coverage = results.get("coverage", {})
    n_windows = coverage.get("n_windows", 0)
    n_trades = coverage.get("n_trades", 0)
    lines: List[str] = []
    add = lines.append

    add("# L5 Strategy Findings")
    add("")
    add(f"- Subject wallet: `{config.TARGET_ADDRESS}` (profile `{config.TARGET_PROFILE_SLUG}`)")
    add("- Scope: BTC and ETH 5-minute and 15-minute Up/Down markets only")
    add(f"- Generated: {now_iso()}")
    add(f"- Data source: public Polymarket APIs, read-only; raw responses cached under `{config.RAW_DIR.relative_to(config.REPO_ROOT)}`")
    add(f"- Fetch status: **{manifest.get('fetch_status', 'unknown')}**")
    add("")

    if n_trades == 0 or n_windows == 0:
        add("## STATUS: NO DATA — no conclusions drawn")
        add("")
        add(
            "The exporter produced zero in-scope fills, so **every question below is "
            "unanswered**. Nothing in this file should be read as a finding about the "
            "trader. Re-run `python -m src.polymarket_l5.cli all` from an environment "
            "with network access to Polymarket's public APIs and this document will "
            "regenerate with real numbers."
        )
        add("")
        note = manifest.get("fetch_note")
        if note:
            add(f"Reason recorded by the last fetch attempt: {note}")
            add("")
        add("The pre-registered analysis plan (what will be computed, and how) is in")
        add("`README_FOR_ANALYSIS.md`. The rule templates below are hypotheses only.")
        add("")

    add("## 1. Coverage")
    add("")
    add(f"- Fills in scope: **{n_trades}**")
    add(f"- Windows in scope: **{n_windows}**")
    add(f"- By asset: {coverage.get('windows_by_asset', {})}")
    add(f"- By window length: {coverage.get('windows_by_length', {})}")
    add(f"- Window-classification provenance: {coverage.get('windows_by_window_source', {})}")
    add(f"- Windows with a known resolution: {coverage.get('windows_with_resolution', 0)}")
    add(f"- Windows with any P&L figure: {coverage.get('windows_with_any_pnl', 0)}")
    add("")
    if 0 < n_windows < 20:
        add(
            f"> **Small sample.** {n_windows} windows is enough to describe behaviour but "
            "not to establish it. Treat every rate below as provisional."
        )
        add("")

    add("## 2. Candidate behavioural rules")
    add("")
    add("| Rule | Holds | n | Share | 95% CI | Counterexamples | Confidence |")
    add("|---|---|---|---|---|---|---|")
    rule_results = [evaluate_rule(rule, summary) for rule in build_rules(summary)]
    for item in rule_results:
        ci = item.get("ci95")
        ci_text = "n/a" if not ci else f"{ci[0] * 100:.0f}–{ci[1] * 100:.0f}%"
        add(
            f"| `{item['key']}` | {item['statement']} | {item['n_applicable']} | "
            f"{_pct(item['share'])} | {ci_text} | {item['n_counterexamples']} | "
            f"{item['confidence']} |"
        )
    add("")
    for item in rule_results:
        if not item["counterexamples"]:
            continue
        add(f"**Counterexamples for `{item['key']}`** ({item['n_counterexamples']} total, first 5):")
        add("")
        add("| window_id | slug | asset | min | first_price | pair_cost | residual |")
        add("|---|---|---|---|---|---|---|")
        for example in item["counterexamples"]:
            add(
                f"| `{example['window_id'][:18]}` | {example['market_slug']} | {example['asset']} | "
                f"{example['window_minutes']} | {example['first_price']} | "
                f"{example['matched_pair_unit_cost']} | {example['residual_qty_signed']} |"
            )
        add("")

    add("## 3. Question-by-question results")
    add("")

    q1 = results.get("q1_size_vs_price", {})
    add("### Q1 — Does position size increase at 70c / 80c / 90c?")
    add("")
    add(f"Buy fills analysed: {q1.get('n_buy_fills', 0)}")
    spearman_shares = q1.get("spearman_price_vs_shares", {})
    add(
        f"Spearman(price, shares): rho={_fmt(spearman_shares.get('rho'))}, "
        f"p={_fmt(spearman_shares.get('p_value'), 5)}, n={spearman_shares.get('n', 0)}"
    )
    add("")
    add("| Price band | fills | median shares | median $ |")
    add("|---|---|---|---|")
    bands = q1.get("per_price_band") or {}
    for name, _low, _high in config.PRICE_BANDS:  # keep the natural price order
        band = bands.get(name) or {}
        add(
            f"| {name} | {band.get('n_fills', 0)} | "
            f"{_fmt((band.get('shares') or {}).get('median'))} | "
            f"{_fmt((band.get('dollars') or {}).get('median'))} |"
        )
    add("")
    for label, test in (q1.get("threshold_tests") or {}).items():
        rank_sum = test.get("shares_rank_sum", {})
        add(
            f"- **{label}**: median shares {_fmt(rank_sum.get('median1'))} at/above vs "
            f"{_fmt(rank_sum.get('median2'))} below (n={rank_sum.get('n1', 0)}/"
            f"{rank_sum.get('n2', 0)}), p={_fmt(rank_sum.get('p_value'), 5)} — "
            f"{test.get('confidence')}"
        )
    add("")

    for key, heading in (
        ("q2_window_length", "Q2 — 5-minute vs 15-minute windows"),
        ("q3_asset", "Q3 — BTC vs ETH"),
        ("q8_profit_vs_loss", "Q8 — Profitable vs losing windows"),
    ):
        block = results.get(key, {})
        add(f"### {heading}")
        add("")
        add(
            f"Groups: {block.get('group_a')} (n={block.get('n_a', 0)}) vs "
            f"{block.get('group_b')} (n={block.get('n_b', 0)})"
        )
        if key == "q8_profit_vs_loss":
            add("")
            add(
                f"Windows with P&L: {block.get('n_windows_with_pnl', 0)} "
                f"(profitable {block.get('n_profitable', 0)}, losing {block.get('n_losing', 0)}, "
                f"flat {block.get('n_flat', 0)}); P&L sources: {block.get('pnl_sources', {})}"
            )
            add("")
            add(f"> {block.get('caveat', '')}")
        add("")
        add("| Metric | median A | median B | p | confidence |")
        add("|---|---|---|---|---|")
        for metric, payload in (block.get("metrics") or {}).items():
            rank_sum = payload.get("rank_sum", {})
            add(
                f"| {metric} | {_fmt(rank_sum.get('median1'))} | {_fmt(rank_sum.get('median2'))} | "
                f"{_fmt(rank_sum.get('p_value'), 5)} | {payload.get('confidence')} |"
            )
        add("")
        add("| Rate | A | B | p | confidence |")
        add("|---|---|---|---|---|")
        for label, payload in (block.get("rates") or {}).items():
            test = payload.get("test", {})
            add(
                f"| {label} | {_pct(test.get('p1'))} | {_pct(test.get('p2'))} | "
                f"{_fmt(test.get('p_value'), 5)} | {payload.get('confidence')} |"
            )
        add("")

    q4 = results.get("q4_cheaper_side", {})
    first_buy = q4.get("first_buy_cheaper_side", {})
    all_buys = q4.get("all_buys_below_50c", {})
    add("### Q4 — Does he systematically buy the cheaper side?")
    add("")
    add(f"_{q4.get('note', '')}_")
    add("")
    add(
        f"- First buy on the sub-$0.50 side: {first_buy.get('successes', 0)}/"
        f"{first_buy.get('n', 0)} = {_pct(first_buy.get('p_hat'))}, "
        f"p vs coin flip={_fmt(first_buy.get('p_value'), 5)} — {q4.get('first_buy_confidence')}"
    )
    add(
        f"- All buy fills below $0.50: {all_buys.get('successes', 0)}/{all_buys.get('n', 0)} = "
        f"{_pct(all_buys.get('p_hat'))}, p={_fmt(all_buys.get('p_value'), 5)} — "
        f"{q4.get('all_buys_confidence')}"
    )
    add("")

    q5 = results.get("q5_opposite_latency", {})
    add("### Q5 — Latency from first leg to opposite side")
    add("")
    add(
        f"- Windows where the opposite side is ever bought: "
        f"{q5.get('n_windows_with_opposite_buy', 0)}/{q5.get('n_windows', 0)} "
        f"({_pct(q5.get('share_with_opposite_buy'))})"
    )
    add("")
    add("| Group | n | median | p25 | p75 | mean |")
    add("|---|---|---|---|---|---|")
    add(_describe_line("all (seconds)", q5.get("latency_seconds", {})))
    for label, description in (q5.get("by_window_minutes") or {}).items():
        add(_describe_line(f"{label} (seconds)", description))
    for label, description in (q5.get("by_asset") or {}).items():
        add(_describe_line(f"{label} (seconds)", description))
    add(_describe_line("fraction of window", q5.get("latency_as_fraction_of_window", {})))
    add("")

    q6 = results.get("q6_pair_cost_threshold", {})
    add("### Q6 — Combined Up+Down acquisition-cost threshold")
    add("")
    add(f"_{q6.get('interpretation', '')}_")
    add("")
    unit = q6.get("unit_cost", {})
    add(f"- Windows holding matched pairs: {q6.get('n_windows_with_pairs', 0)}")
    add(
        f"- Pair unit cost: median {_fmt(unit.get('median'))}, IQR width "
        f"{_fmt(q6.get('iqr_width'))}, range {_fmt(unit.get('min'))}–{_fmt(unit.get('max'))}"
    )
    add(f"- Share within $0.01 of median: {_pct(q6.get('share_within_1c_of_median'))}")
    add(f"- Share within $0.02 of median: {_pct(q6.get('share_within_2c_of_median'))}")
    add(f"- Share below $1.00 (locked profit before fees/rebates): {_pct(q6.get('share_below_1_00'))}")
    add("")

    q7 = results.get("q7_balanced_finish", {})
    add("### Q7 — Balanced vs directional finishes")
    add("")
    add(f"_{q7.get('definition', '')}_")
    add(
        f"- Balanced at expiry: {q7.get('balanced_windows', 0)}/{q7.get('n_windows', 0)} "
        f"({_pct((q7.get('balanced_test_vs_50pct') or {}).get('p_hat'))}) — "
        f"{q7.get('confidence')}"
    )
    add(f"- Residual (shares): {q7.get('residual_qty_abs', {})}")
    add(f"- By group: {q7.get('by_group', {})}")
    add("")

    add("## 4. Implementable pseudocode")
    add("")
    add("Reconstruction of the behaviour the rules above describe. It is a *description of")
    add("observed fills*, not a validated strategy, and it deliberately encodes only what")
    add("the data can support.")
    add("")
    add("```python")
    median_cost = _median_pair_cost(summary)
    latency_cut = _latency_p75(summary)
    add(f"PAIR_COST_TARGET      = {_fmt(median_cost) if median_cost is not None else 'None  # not measurable'}")
    add(f"SECOND_LEG_MAX_SECONDS = {_fmt(latency_cut, 0) if latency_cut is not None else 'None  # not measurable'}")
    add("MAX_SECOND_LEG_PRICE   = PAIR_COST_TARGET - wavg_first_leg_price")
    add("")
    add("def on_window_open(window):")
    add("    # R1: enter on whichever side is trading below $0.50.")
    add("    side = 'Up' if best_ask('Up') < best_ask('Down') else 'Down'")
    add("    fill = buy(side, size=BASE_SIZE)")
    add("    state.first_leg = fill")
    add("")
    add("def on_book_update(window):")
    add("    # R3 + R6: complete the pair while the combined cost still clears the target.")
    add("    other = opposite(state.first_leg.side)")
    add("    budget = PAIR_COST_TARGET - state.wavg_price(state.first_leg.side)")
    add("    if best_ask(other) <= budget:")
    add("        buy(other, size=state.qty(state.first_leg.side) - state.qty(other))")
    add("    elif now() - state.first_leg.ts > SECOND_LEG_MAX_SECONDS:")
    add("        # R5: observed behaviour pays up rather than staying legged.")
    add("        buy(other, size=..., limit=best_ask(other))")
    add("")
    add("def before_expiry(window):")
    add("    # R4: finish matched; residual is directional risk this trader mostly avoids.")
    add("    flatten(abs(state.qty('Up') - state.qty('Down')))")
    add("```")
    add("")

    add("## 5. What this analysis cannot tell you")
    add("")
    add("- Public activity shows **executed fills only**. Resting orders, cancels, quote")
    add("  updates and queue position are invisible; a maker strategy will look like a")
    add("  sequence of unexplained fills.")
    add("- Historical order-book state at fill time is not reconstructed. Statements like")
    add("  \"he crossed the spread\" are not derivable from this data and are not made.")
    add("- P&L here is **trading** P&L. Maker rebates and liquidity-incentive payouts do")
    add("  not appear in fill or position data; a window that looks flat or negative may")
    add("  still be profitable after incentives, and vice versa.")
    add("- 'Cheaper side' is inferred from the fill price against the $1.00 binary")
    add("  complement, not from a snapshot of both books.")
    add("- Window classification uses market duration metadata first and text second;")
    add("  markets that could only be classified from text are excluded and listed in")
    add(f"  `{config.EXCLUSIONS_CSV}`.")
    add("- Correlation across windows is not independence: consecutive windows in the")
    add("  same session share regime, so p-values are optimistic.")
    add("")
    return "\n".join(lines) + "\n"


def write_findings(
    results: Dict[str, Any],
    manifest: Dict[str, Any],
    output_dir: Optional[Path] = None,
    findings_path: Optional[Path] = None,
) -> Path:
    output_dir = Path(output_dir or config.OUTPUT_DIR)
    summary = read_csv(output_dir / config.SUMMARY_CSV)
    target = Path(findings_path or (config.REPO_ROOT / "L5_STRATEGY_FINDINGS.md"))
    target.write_text(render(results, summary, manifest), encoding="utf-8")
    return target
