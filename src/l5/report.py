"""Render L5_STRATEGY_FINDINGS.md from the analysis output.

The template is deliberately hedged. Every rule is stated with its sample
size, its counterexamples, and a confidence label that tops out at
"evidence" -- this is one trader's public fills, not a controlled experiment.
"""

from __future__ import annotations

from typing import Any, Sequence

from src.l5 import config
from src.l5.windows import Window


def num(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int,)):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return str(value)


def pct(value: Any) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _describe_row(label: str, d: dict, digits: int = 3) -> str:
    return (
        f"| {label} | {d.get('n', 0):,} | {num(d.get('median'), digits)} | "
        f"{num(d.get('p25'), digits)} | {num(d.get('p75'), digits)} | "
        f"{num(d.get('mean'), digits)} | {num(d.get('min'), digits)} | "
        f"{num(d.get('max'), digits)} |"
    )


_DESCRIBE_HEADER = (
    "| group | n | median | p25 | p75 | mean | min | max |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- |"
)


def _compare_block(title: str, block: dict, digits: int = 3) -> str:
    test = block.get("mann_whitney", {})
    lines = [
        f"**{title}** — {block.get('verdict', 'n/a')} "
        f"(Mann-Whitney p={num(test.get('p'), 4)}, "
        f"effect r={num(block.get('effect_rank_biserial'), 3)})",
        "",
        _DESCRIBE_HEADER,
        _describe_row(block.get("group_a", "a"), block.get("a", {}), digits),
        _describe_row(block.get("group_b", "b"), block.get("b", {}), digits),
        "",
        "",  # blank line so the next block is not absorbed into the table
    ]
    return "\n".join(lines)


def _counterexamples(windows: Sequence[Window], predicate, describe, limit: int = 8) -> list[str]:
    out: list[str] = []
    for window in windows:
        summary = window.summary
        if summary is None or not predicate(summary):
            continue
        out.append(describe(summary))
        if len(out) >= limit:
            break
    return out


def _bullets(items: Sequence[str], empty: str = "_None found in the sample._") -> str:
    if not items:
        return empty
    return "\n".join(f"- {item}" for item in items)


def render(analysis: dict, windows: Sequence[Window], manifest: dict | None = None) -> str:
    cov = analysis["coverage"]
    q1 = analysis["q1_size_vs_price"]
    q2 = analysis["q2_duration_split"]
    q3 = analysis["q3_asset_split"]
    q4 = analysis["q4_cheaper_side"]
    q5 = analysis["q5_time_to_opposite"]
    q6 = analysis["q6_pair_cost_target"]
    q7 = analysis["q7_balanced_finish"]
    q8 = analysis["q8_pnl_split"]

    summaries = [w.summary for w in windows if w.summary]
    pair_cost_median = q6["pair_cost_per_share"].get("median")
    opposite_median = q5["seconds_to_opposite"].get("median")
    opposite_p75 = q5["seconds_to_opposite"].get("p75")

    parts: list[str] = []
    parts.append(
        f"""# L5 Strategy Findings

Behavioural reconstruction of Polymarket wallet `{config.TARGET_WALLET}`
(profile `{config.TARGET_PROFILE}`) across BTC and ETH 5-minute and 15-minute
Up/Down markets, built from public API data only.

> **Read this first.** Everything below is inferred from *executed fills*. Polymarket's
> public endpoints do not expose resting orders, cancellations, or historical book state,
> so nothing here can distinguish "he chose not to buy" from "his order did not fill".
> No order-book state is reconstructed anywhere in this analysis. Realized P&L, where
> shown, is Polymarket's own position accounting and **excludes liquidity rebates and
> maker incentives** — for a two-sided quoting strategy those may dominate the economics,
> so a negative trading P&L here is not evidence the strategy loses money.
>
> These are exploratory comparisons across many hypotheses with no multiple-comparison
> correction. Treat p-values as ranking signals, not as confirmatory tests.

## 1. Coverage

| | |
| --- | --- |
| Trades reconstructed | {cov['n_trades']:,} |
| Windows reconstructed | {cov['n_windows']:,} |
| Windows classified from market timestamps | {cov['windows_window_source_metadata']:,} |
| Windows classified from title text only (weaker) | {cov['windows_window_source_text_fallback']:,} |
| Windows where timestamps and title disagree | {cov['windows_with_window_conflict']:,} |
| Windows with reconstructed start bounds | {cov['windows_with_reconstructed_bounds']:,} |
| Windows with realized P&L available | {cov['windows_with_realized_pnl']:,} |
| Windows flagged oversold (a sell exceeded visible inventory) | {cov['windows_with_oversold_flag']:,} |

Windows per cell: {cov['windows_by_cell'] or 'none'}

"""
    )

    if manifest:
        truncated = [
            e for e in manifest.get("endpoints", []) if e.get("truncated")
        ]
        if truncated:
            parts.append("**Retrieval was truncated on:** " + ", ".join(
                f"`{e['endpoint']}` ({e.get('truncation_reason', 'unknown')})" for e in truncated
            ) + "\n\nHistory older than the truncation point is missing from every number below.\n\n")

    # --- Q1
    within = q1["within_window"]
    parts.append(
        f"""## 2. Does position size increase at 70c / 80c / 90c?

Pooled across all {q1['n_buy_trades']:,} buys, Spearman(price, shares) =
{num(q1['pooled_spearman_price_vs_shares'].get('rho'))}
(p={num(q1['pooled_spearman_price_vs_shares'].get('p'), 4)}); Spearman(price, dollars) =
{num(q1['pooled_spearman_price_vs_dollars'].get('rho'))}
(p={num(q1['pooled_spearman_price_vs_dollars'].get('p'), 4)}).

Pooling confounds "bigger at high prices" with "bigger in busy windows", so the
controlled reading is the within-window one: in each window separately, does size rise
with price? Of {within['n_windows_tested']:,} windows with enough distinct prices to test,
{within['sign_test_positive_rho'].get('successes', 0):,} had a positive rank correlation
(rate {pct(within['sign_test_positive_rho'].get('rate'))},
sign-test p={num(within['sign_test_positive_rho'].get('p'), 4)}) — **{within['verdict']}**.
Median within-window rho {num(within['rho'].get('median'))}
[95% CI {num(within['bootstrap_median_rho'].get('lo'))},
{num(within['bootstrap_median_rho'].get('hi'))}].

Size by price band:

| band | buys | median shares | median $ |
| --- | --- | --- | --- |
"""
    )
    for label in config.PRICE_BAND_LABELS:
        band = q1["by_price_band"][label]
        parts.append(
            f"| {label} | {band['n_trades']:,} | {num(band['shares'].get('median'), 1)} | "
            f"{num(band['dollars'].get('median'), 2)} |\n"
        )
    parts.append("\nThreshold splits (shares bought below vs at/above each level):\n\n")
    for name, block in q1["threshold_splits"].items():
        parts.append(_compare_block(f"{name} threshold — shares", block["shares"], 1))

    # --- Q2 / Q3
    for heading, block in (
        ("3. 5-minute vs 15-minute windows", q2),
        ("4. BTC vs ETH", q3),
    ):
        parts.append(f"## {heading}\n\n")
        parts.append(
            f"{block['n_windows_a']:,} vs {block['n_windows_b']:,} windows.\n\n"
        )
        for key in ("total_trades", "total_dollars_bought", "matched_pair_cost_per_share",
                    "seconds_to_opposite_side", "max_price_bought"):
            if key in block.get("metrics", {}):
                parts.append(_compare_block(key, block["metrics"][key]))
        for key in ("finished_balanced", "bought_at_90c_plus"):
            rate = block.get("rates", {}).get(key)
            if rate:
                test = rate["two_proportion_z"]
                parts.append(
                    f"**{key} rate** — {rate['group_a']} {pct(test.get('p1'))} vs "
                    f"{rate['group_b']} {pct(test.get('p2'))} "
                    f"(p={num(test.get('p'), 4)}) — {rate['verdict']}\n\n"
                )

    # --- Q4
    opening = q4["opening_trade_below_50c"]
    contemp = q4["vs_contemporaneous_opposite_side"]
    parts.append(
        f"""## 5. Does he systematically buy the cheaper side?

Two independent readings, because the naive one leans on an assumption.

**Opening trade below 50c:** {opening.get('successes', 0):,} of {opening.get('trials', 0):,}
windows ({pct(opening.get('rate'))}, p={num(opening.get('p'), 4)}) — **{opening['verdict']}**.
_{opening['assumption']}_

**Against his own contemporaneous opposite-side fill:** of {contemp['n_comparable_buys']:,}
buys with an opposite-side fill within {config.CONTEMPORANEOUS_WINDOW_S}s,
{contemp.get('successes', 0):,} were on the cheaper side ({pct(contemp.get('rate'))},
p={num(contemp.get('p'), 4)}) — **{contemp['verdict']}**. Median price gap to the opposite
side: {num(contemp['price_margin_vs_opposite'].get('median'))}.
_{contemp['assumption']}_

## 6. How quickly does the opposite side follow?

{q5['n_windows_both_sides']:,} of {q5['n_windows_total']:,} windows
({pct(q5['share_of_windows_both_sides'])}) saw both sides traded.

Median delay from the first fill to the first opposite-side fill:
**{num(opposite_median, 1)}s** [95% CI {num(q5['bootstrap_median_seconds'].get('lo'), 1)},
{num(q5['bootstrap_median_seconds'].get('hi'), 1)}], p25 {num(q5['seconds_to_opposite'].get('p25'), 1)}s,
p75 {num(opposite_p75, 1)}s. That is
{pct(q5['fraction_of_window_elapsed'].get('median'))} of the window length at the median.
Median trades on the opening side before switching:
{num(q5['trades_before_opposite'].get('median'), 1)}.

| window | n | median seconds | p75 seconds |
| --- | --- | --- | --- |
"""
    )
    for key, cell in q5["by_window_length"].items():
        parts.append(
            f"| {key} | {cell['n']:,} | {num(cell['seconds'].get('median'), 1)} | "
            f"{num(cell['seconds'].get('p75'), 1)} |\n"
        )

    # --- Q6
    under = q6["pair_cost_below_1_00"]
    parts.append(
        f"""
## 7. Is there a combined Up+Down acquisition-cost target?

Across {q6['n_paired_windows']:,} windows that ended holding both sides, the matched-pair
acquisition cost (weighted-average Up price + weighted-average Down price) had median
**${num(pair_cost_median)}** [95% CI {num(q6['bootstrap_median_pair_cost'].get('lo'))},
{num(q6['bootstrap_median_pair_cost'].get('hi'))}], IQR
{num(q6['pair_cost_per_share'].get('p25'))}–{num(q6['pair_cost_per_share'].get('p75'))},
sd {num(q6['pair_cost_per_share'].get('stdev'))}.

{under.get('successes', 0):,} of {under.get('trials', 0):,} paired windows
({pct(under.get('rate'))}) finished with a pair cost under $1.00 — a locked settlement
profit before fees, rebates and gas.

Concentration around the median:

| tolerance | windows | share |
| --- | --- | --- |
"""
    )
    for key, cell in q6["concentration_around_median"].items():
        parts.append(f"| {key.replace('_', ' ')} | {cell['n']:,} | {pct(cell['rate'])} |\n")
    parts.append(
        f"\nFinal pair cost was the window's running minimum in "
        f"{q6['final_pair_cost_is_running_minimum']['n_final_is_minimum']:,} of "
        f"{q6['final_pair_cost_is_running_minimum']['n_windows_considered']:,} windows "
        f"({pct(q6['final_pair_cost_is_running_minimum']['rate'])}). "
        f"_{q6['final_pair_cost_is_running_minimum']['note']}_\n\n"
    )

    # --- Q7
    parts.append(
        f"""## 8. Balanced or directional at the close?

{q7['definition']}

**{q7['n_balanced']:,} of {q7['n_windows']:,} windows ({pct(q7['rate'])}) finished balanced.**
Median residual as a fraction of the larger leg:
{pct(q7['residual_fraction'].get('median'))}. Residual side counts:
{q7['residual_side_counts']}. Windows that only ever traded one side:
{q7['one_sided_windows']:,}.

| cell | windows | balanced | rate |
| --- | --- | --- | --- |
"""
    )
    for key, cell in q7["by_cell"].items():
        parts.append(f"| {key} | {cell['n']:,} | {cell['n_balanced']:,} | {pct(cell['rate'])} |\n")

    # --- Q8
    parts.append(
        f"""
## 9. Profitable vs losing windows

Realized P&L was available for {q8['n_windows_with_pnl']:,} windows:
{q8['n_winners']:,} profitable, {q8['n_losers']:,} losing, {q8['n_flat']:,} flat.
Total realized P&L across those windows: {num(q8.get('total_realized_pnl'), 2)}.
Median per window: {num(q8['pnl'].get('median'), 2)}.

> {q8['caveat']}

"""
    )
    for key in ("matched_pair_cost_per_share", "total_dollars_bought", "max_price_bought",
                "seconds_to_opposite_side", "residual_frac_of_larger_leg"):
        block = q8.get("metrics", {}).get(key)
        if block:
            parts.append(_compare_block(key, block))
    balanced_rate = q8.get("rates", {}).get("finished_balanced")
    if balanced_rate:
        test = balanced_rate["two_proportion_z"]
        parts.append(
            f"**finished_balanced rate** — profitable {pct(test.get('p1'))} vs losing "
            f"{pct(test.get('p2'))} (p={num(test.get('p'), 4)}) — {balanced_rate['verdict']}\n\n"
        )

    # --- candidate rules + counterexamples
    ce_open = _counterexamples(
        windows,
        lambda s: s.first_price >= 0.5,
        lambda s: f"`{s.market_slug or s.condition_id}` — opened {s.first_side} at "
                  f"{num(s.first_price)} ({s.window_start_iso})",
    )
    ce_onesided = _counterexamples(
        windows,
        lambda s: not s.traded_both_sides,
        lambda s: f"`{s.market_slug or s.condition_id}` — only ever traded {s.first_side}, "
                  f"{s.total_trades} trades, {num(s.total_dollars_bought, 2)} bought",
    )
    ce_unbalanced = _counterexamples(
        windows,
        lambda s: not s.finished_balanced,
        lambda s: f"`{s.market_slug or s.condition_id}` — residual {num(s.residual_qty, 1)} "
                  f"shares {s.residual_side} ({pct(s.residual_frac_of_larger_leg)} of larger leg)",
    )
    ce_expensive = _counterexamples(
        windows,
        lambda s: s.matched_pair_cost_per_share is not None
        and s.matched_pair_cost_per_share >= 1.0,
        lambda s: f"`{s.market_slug or s.condition_id}` — pair cost "
                  f"${num(s.matched_pair_cost_per_share)}, matched {num(s.matched_qty, 1)} shares",
    )

    slow_threshold = opposite_p75 if opposite_p75 is not None else 0
    ce_slow = _counterexamples(
        windows,
        lambda s: s.seconds_to_opposite_side is not None
        and s.seconds_to_opposite_side > max(slow_threshold, 1),
        lambda s: f"`{s.market_slug or s.condition_id}` — {s.seconds_to_opposite_side}s "
                  f"before the opposite side",
    )

    target = pair_cost_median if pair_cost_median is not None else 0.99
    delay = opposite_median if opposite_median is not None else 0

    parts.append(
        f"""## 10. Candidate behavioural rules

Each rule states its support, its counterexamples, and a confidence level. The confidence
column is capped at "moderate" throughout: this is a single trader, observed through fills
only, over one sample period.

### R1 — Opens on the side priced below 50c

- **Support:** {opening.get('successes', 0):,}/{opening.get('trials', 0):,} windows ({pct(opening.get('rate'))}), p={num(opening.get('p'), 4)}
- **Confidence:** {opening['verdict']}
- **Counterexamples ({len([w for w in summaries if w.first_price >= 0.5]):,} total):**

{_bullets(ce_open)}

### R2 — Takes the opposite side shortly after the first

- **Support:** both sides traded in {q5['n_windows_both_sides']:,}/{q5['n_windows_total']:,} windows ({pct(q5['share_of_windows_both_sides'])}); median delay {num(opposite_median, 1)}s
- **Confidence:** descriptive — no null model is being tested here
- **Counterexamples — never traded the second side ({q7['one_sided_windows']:,} total):**

{_bullets(ce_onesided)}

- **Counterexamples — unusually slow to cross (> p75 = {num(slow_threshold, 1)}s):**

{_bullets(ce_slow)}

### R3 — Finishes with a matched two-sided book

- **Support:** {q7['n_balanced']:,}/{q7['n_windows']:,} windows ({pct(q7['rate'])}) finished balanced
- **Confidence:** descriptive
- **Counterexamples ({q7['n_windows'] - q7['n_balanced']:,} total):**

{_bullets(ce_unbalanced)}

### R4 — Accumulates toward a combined pair cost near ${num(target)}

- **Support:** {q6['n_paired_windows']:,} paired windows, median pair cost ${num(target)}, IQR {num(q6['pair_cost_per_share'].get('p25'))}–{num(q6['pair_cost_per_share'].get('p75'))}; {pct(under.get('rate'))} finished under $1.00
- **Confidence:** {'moderate' if q6['n_paired_windows'] >= config.MIN_SAMPLE_FOR_CLAIM else 'insufficient data'} — a tight distribution is *consistent with* a target but does not prove one; the same shape arises if he simply stops when the second leg gets expensive
- **Counterexamples — pair cost at or above $1.00:**

{_bullets(ce_expensive)}

### R5 — Buys larger as price rises

- **Support:** within-window sign test {within['sign_test_positive_rho'].get('successes', 0):,}/{within['n_windows_tested']:,} positive, p={num(within['sign_test_positive_rho'].get('p'), 4)}
- **Confidence:** {within['verdict']}
- **Counterexample rate:** {within['n_windows_tested'] - within['sign_test_positive_rho'].get('successes', 0):,} windows showed the opposite sign

## 11. Implementable pseudocode

This is a *description of observed behaviour*, not a recommendation, and not a strategy
that has been validated for profitability. It reproduces the shape of the fills only. The
parameters are medians from this sample and will drift.

```python
# Observed-behaviour sketch. Read-only research artifact.
#
# Fitted parameters (medians from this sample -- refit before any use):
PAIR_COST_TARGET      = {num(target)}      # combined Up+Down acquisition cost
OPPOSITE_SIDE_DELAY_S = {num(delay, 1)}    # median seconds before crossing to side 2
BALANCED_TOLERANCE    = {config.BALANCED_TOLERANCE_FRAC}

def on_window_open(window):
    # R1: open on whichever side is quoted below the midpoint.
    side = cheaper_side(window)          # observed: opened <50c in {pct(opening.get('rate'))} of windows
    buy(side, size=base_size(window))

def on_fill(window, state):
    # R5: size scales with price -- larger clips as the leg gets expensive.
    #     Direction of the effect: {within['verdict']}.
    #
    # R2/R4: cross to the other side, then keep adding while the *combined*
    #        acquisition cost still leaves the matched pair under target.
    other = opposite(state.first_side)
    projected = state.avg_price(state.first_side) + current_price(other)
    if projected <= PAIR_COST_TARGET:
        buy(other, size=size_for_price(current_price(other)))

def on_window_close(window, state):
    # R3: finish matched. Observed balanced finish rate: {pct(q7['rate'])}.
    residual = state.qty(UP) - state.qty(DOWN)
    if abs(residual) > BALANCED_TOLERANCE * max(state.qty(UP), state.qty(DOWN)):
        pass  # observed: he did NOT always flatten -- {q7['n_windows'] - q7['n_balanced']:,} windows ended directional
```

## 12. What this analysis cannot tell you

- **Order intent.** Only fills are public. A window with one side traded may be a
  directional bet, or a two-sided quote where the other side never filled. These are
  indistinguishable in the data and are never assumed apart.
- **Book context.** No historical order book is available, so "he bought the cheaper
  side" is measured against his own fills, not against the market's quotes.
- **True economics.** Rebates, maker incentives, and gas are outside the position
  accounting. Section 9's P&L is trading P&L only.
- **Causality.** Section 2's price/size relationship is observational. Prices rise late in
  a window and sizes may rise late for unrelated reasons; the within-window test reduces
  but does not remove that confound.
- **Sample period.** Only history the public endpoints still serve is included; see the
  coverage table and any truncation note above.
"""
    )
    return "".join(parts)
