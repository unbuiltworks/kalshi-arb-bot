# Reconciling the L5 figures, and what actually limits our throughput

Reproduce everything here with
`python3 -I analysis/l5/issue206/l5_reconciliation.py`.

## 1. The waterfall

| cut | count | why it differs |
|---|---|---|
| RAW trade rows | 75,864 | the collector pages with limit/offset; overlapping pages repeat rows |
| UNIQUE trades | **70,435** | deduped on (txhash, asset, timestamp, size, side) — **7.2% were duplicates** |
| UNIQUE btc15m | **62,020** | 88.1% of unique; the rest is 5m and ETH |
| btc15m windows | 528 | 1 has redemptions only |
| … with trades and a redemption, two-sided | 501 | 13 no-redeem, 13 one-sided |
| … **capture-consistent** | **408** | see §2 — this is the set every figure below uses |
| … outcome-determinable | 186 | redemption equals one side's quantity exactly |

## 2. A data defect that was inflating everything, including my own report

A redemption pays **$1 per winning contract**, and only one outcome pays, so
**the payout can never exceed the larger side's holding.**

| cut | windows | redeem > larger side |
|---|---|---|
| outcome-determinable | 186 | **0 (0.0%)** |
| redeem-mismatch | 315 | **93 (29.5%)** |

Those 93 windows are missing trades — the collector lost rows in the busiest
windows. Missing trades mean **missing cost**, so spend is understated and
net overstated. They were carrying most of the reported edge:

| | n | net per window |
|---|---|---|
| capture-consistent | 408 | **+$37.76** |
| redeem exceeds holding | 93 | **+$230.44** |

**Corrections to my previous report.** L5's net per window is **+$37.76**,
not +$71.80. The throughput gradient is real but ~40% smaller:

| trades in window | n | net/window (clean) | previously reported |
|---|---|---|---|
| 0–10 | 32 | +$0.51 | +$0.51 |
| 10–50 | 138 | −$2.45 | −$1.98 |
| 50–150 | 94 | **+$30.12** | +$61.06 |
| 150+ | 144 | **+$89.57** | +$146.22 |

The *direction* survives decisively — a 175× spread from the quietest to the
busiest bucket — but the magnitude was overstated and I should have run the
completeness check before quoting it.

## 3. Independent verification of the residual result

Two methods, deliberately different:

- **attribution** — needs the winning outcome, so only the 186 determinable
  windows: `+$0.0367/contract`.
- **residue** — `residual P&L = observed net − matched P&L`. A matched pair
  redeems $1 whichever way the window resolves, so its P&L is
  `m × (1 − basis)` with no attribution at all.

| method | windows | residual P&L / contract |
|---|---|---|
| attribution | determinable (186) | **+$0.0367** |
| residue | determinable (186) | **+$0.0263** |
| residue | mismatch (315) | +$0.2118 ← **contaminated** |
| residue | all with redeem (501) | +$0.1762 ← **contaminated** |

The two methods **agree to within 1¢ on the same windows**. The 8× larger
figures come from the windows failing §2's completeness check, where the
missing cost lands entirely in the residue. **They are artefacts and are
discarded.** My earlier +$17.95¢ figure was the same artefact in another
form.

**So the defensible range for an L5 residual contract is +$0.026 to
+$0.037.** It rests on 20,069 contracts in 186 windows that pass every
consistency check available — and those windows are the **quiet** ones
(median 40.5 trades against 152 for the mismatches), which is a selection
limit that cannot be removed from this data. Pair basis (0.9710 vs 0.9619)
and residual fraction (0.173 vs 0.174) are comparable across the cuts, so
the subset is representative on those dimensions but **not on activity**.

**Fees and rebates:** the whole file holds 4 MAKER_REBATE, 3 TAKER_REBATE
and 3 REWARD events. None is included in any figure above.

**Maker or taker:** the pair basis is below 1.00 in **78.6%** of windows
(median 0.9619). A taker lifting both sides pays the spread and lands above
1.00, so L5 is resting maker liquidity — which is why its economics are
relevant to ours at all.

## 4. The zero residual-expectancy prior is a neutral hypothesis

Pinned by `test_the_zero_residual_expectancy_is_a_NEUTRAL_HYPOTHESIS`.

`REOPEN_SINGLE_OUTCOME_USD = 0` is **not** a claim that our maker fills are
profitable. It is the EV identity's own answer for a leg bought at a fair
price (`EV(hold) = EV(complete)` exactly), and it is **strictly below** the
+$0.026…+$0.037 L5 realised, so nothing of L5's edge is banked. It is also
**above** the superseded −$0.2440 PROVEN artefact, which measured a sampling
rule rather than a market.

Our own five settled residual legs lost four of five. **n=5 cannot refute
20,069 contracts, and 20,069 contracts of someone else's fills cannot
establish ours.** Zero sits between the two and commits to neither. The
**worst case is unchanged** and still reserves the whole leg.

## 5. Why only two live orders — and why that is not the constraint

`SafetyLimits.max_live_orders` (default 1, deployed 2 via
`L5_MAX_LIVE_ORDERS`), plus a `pair_second_leg_ok` exemption that raises the
ceiling by exactly one so a pair's second leg always has a slot.

**At our authorized scale the slot count is not what binds. Cash is, and it
does not recycle inside a window.** Measured against the real executor with
a $1.00 envelope:

```
tick 0   UP  sent
tick 1   DOWN sent
tick 2   refused  PAIR_UP:CASH_COMMITMENT:1.3766>1.00
(both legs then fill -- a completed pair)
tick 3   refused  PAIR_UP:CASH_COMMITMENT:2.1897>1.00
```

A completed pair does **not** free its outlay: `CASH_COMMITMENT` *rises*
with fills and is released only at settlement, at window end. So under $1
the engine gets **one pair per window**, and the reopening gate is never
reached — cash refuses first. `test_a_completed_pair_does_not_free_its_cash_
mid_window` pins this, and `test_a_larger_envelope_does_let_further_rounds_
through` shows the same code reaching further rounds on a larger envelope.

**Consequences, quantified.** One pair per window × 96 windows/day × the
~5¢ pair edge ≈ **$4.80/day maximum** under the current authorization, and
that assumes every window produces a completing pair. $480/day is 100× that.
Three levers exist, and only two are real:

1. **Capital per window** — linear, and the dominant term. L5 spends ~$1,065
   a window.
2. **Concurrent markets** — linear in markets. `CLAUDE.md` holds live
   replication to one market at a time.
3. **Rounds per window at fixed capital** — **blocked** by cumulative cash
   commitment. No controller change reaches it.

Queue management and replenishment therefore cannot raise throughput at $1;
they determine whether the *one* available pair completes. That is what the
patience floor and PRESERVE are for, and they remain the right work.

## 6. The 0.96 ceiling against L5's distribution

L5's capture-consistent pair basis: p05 0.8533, p25 0.9299, **p50 0.9578**,
p75 0.9955, p95 1.0947.

| ceiling | L5 windows accepted | matched-volume share | edge/pair | **expected per window at one pair** |
|---|---|---|---|---|
| 0.94 | 32.8% | 25.7% | +$0.0843 | 2.76¢ |
| 0.95 | 41.7% | 36.1% | +$0.0761 | 3.17¢ |
| **0.96** ⬅ deployed | **51.0%** | **49.7%** | **+$0.0679** | **3.46¢** |
| 0.97 | 59.3% | 61.2% | +$0.0620 | 3.68¢ |
| 0.98 | 65.9% | 70.2% | +$0.0572 | 3.77¢ |
| 1.00 | 77.5% | 86.0% | +$0.0487 | 3.77¢ |

The last column is what matters at our scale, where we take **one** pair per
window: participation rate × edge. **The curve is flat from 0.96 to 1.00 —
3.46¢ against 3.77¢, about +9%.**

**The evidence does not support loosening.** A 9% expected gain is inside
the noise of estimates drawn from 408 windows of someone else's fills, it
buys a materially larger share of near-zero-edge pairs (the edge/pair falls
28%), and `CLAUDE.md` locks near-$1 tolerance until the economics are
source-confirmed **for our account**. The ceiling stays at 0.96. It is worth
revisiting only if capital rises enough that we take several pairs per
window, when participation rate starts to matter more than per-pair edge.

## 7. Three kinds of number, kept apart

- **L5's historical realised profit:** +$15,407 over 408 capture-consistent
  btc15m windows, +$37.76/window, 1,065 of turnover per window. *Someone
  else's money, from public data.*
- **Our simulated results:** the A/B/C replay, −0.0554 to +0.1265 per window
  depending on which end of the fill bracket is believed. *Not money.*
- **Our confirmed settled realised net: −$15.6109 all-time.* That is the
  only figure that is ours, and it has not moved.

## 8. Remaining limitations

1. **The residual result's selection limit cannot be removed from this
   data.** It is measured on quiet windows; busy windows fail the
   completeness check. Re-collecting L5's activity with complete paging
   would fix it and needs no authorization — it is a read-only collector.
2. **Whether we reproduce L5's residual outcome is unknown**, and n=5
   against 20,069 settles nothing either way.
3. **Why the cheap side beats its price** (38.6% on a 34.9% basis) is
   untested. Intra-window mean reversion is the obvious candidate.
   `ACQUIRE_UNFAVORED` still refuses for want of an independent estimate.
4. **Our fill rate at frequency is unknown.** The 72.7% pair rate comes from
   a handful of orders per window, not 135.
