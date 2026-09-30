# Two-sided OOS batch 1 — 15 settled BTC15m windows: NULL ARM RESULT

Run commit: `41171d66`. Controller frozen at `a61bd5e0`. Raw output:
`analysis/TWO_SIDED_OOS_BATCH1_RAW.txt`.

## Headline

**The comparison did not test anything.** All three arms — CONTROL_ONE_LEG,
L5_REPLICATION, L5_ADAPTED_ECONOMIC — produced BIT-IDENTICAL values on all 23
preregistered metrics across all 15 windows. Total settled net
`-12.67338223566038367463107255` for each. Zero metrics differ in any window.

The arms collapse to one policy for two independent, proven reasons.

### Defect A — CONTROL is not a control

`cycles_started` is initialised to 0 in `src/l5_two_sided.py:324` and **never
incremented anywhere in the repository**. The gate

    src/l5_two_sided.py:429   if self.cycles_started >= self.max_cycles:

is therefore permanently false for any `max_cycles >= 1`. CONTROL was built with
`max_cycles=1` and completed **14 cycles** in the 1500z window. The runner's
other CONTROL marker, `one_leg_only`, is by its own comment
("marker for the report") behaviourally inert. Nothing in the harness makes
CONTROL one-leg, so CONTROL === L5_REPLICATION.

### Defect B — ADAPTED is mathematically identical to REPLICATION

In `best_action`, the ADAPTED-only branch

    if exit_v is not None and comp_v is not None and exit_v > comp_v and \
            residual_tolerance > 0 and -exit_v > residual_tolerance:
        return EXIT_NOW

is fully subsumed by the branch immediately following it, which returns
EXIT_NOW on `residual_tolerance > 0 and -exit_v > residual_tolerance` alone.
Swept over 10,443 (comp_v, exit_v, tolerance) combinations including None:
**0 inputs where the two arms return different actions.** This is provable by
inspection and needed no data.

## Accounting identity — holds exactly, 15/15

    matched_gross_edge + flatten_pnl_shadow + residual_settlement_pnl
        - taker_fees_cashflow + maker_rebates_modeled_cashflow == settled_net

Zero failures, residual < 1e-10 in every window.

| term | 15-window total |
|---|---|
| matched_gross_edge | +5.6801 |
| residual_settlement_pnl | -0.4800 |
| taker_fees_cashflow | -2.2500 |
| maker_rebates_modeled_cashflow | 0.0000 |
| **flatten_pnl_shadow** | **-15.6235** |
| = settled_net | -12.6734 |

**The reported "settled net" is dominated by a SHADOW term.** Settlement-backed
components alone are `5.6801 - 0.4800 - 2.2500 = +2.9501`. The -12.67 headline
is reached only by including `flatten_pnl_shadow`, a modelled flatten of
one-sided inventory at executable prices — which under the strict labels in
CLAUDE.md is shadow, not settled and not realized. Both numbers must be carried
separately; neither is realized P&L.

## What the run does show (identically for all arms)

- Residual discipline is real: `max_directional_residual` = 1 contract in every
  window; `residual_now` 0 in 13 of 15.
- Matched throughput: 143.52 matched contracts over 15 windows from 240 maker
  fills and 127 taker fills, with 322 cancels and peak capital $13.15 total.
- `maker_rebates_modeled_cashflow` is exactly 0.00 in all 15 windows despite 240
  maker fills — consistent with the q1 live finding that per-order cent rounding
  (`cents=True`) floors the credit to zero at this size. Expected, but it means
  no rebate economics exist at q1.
- The large negative `flatten_pnl_shadow` against a small `matched_gross_edge`
  says the acquired one-sided inventory marks badly against the executable
  opposite side straight after acquisition. That is an adverse-selection signal
  and is the substantive question this batch raises.

## Status

This is NOT an out-of-sample result for the controller, positive or negative,
because no arm contrast was measured. The 15 windows remain unspent: they were
replayed by one policy, and the data is untouched by any tuning.
