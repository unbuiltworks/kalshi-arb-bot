# The decisive test — Grok's memoryless-clone claim is REJECTED

Script: `../scripts/82_null_model_test.py`. Raw output:
`NULL_MODEL_TEST_RESULT.json`. All 760,817 fills / 17,046 windows,
20 simulated replicates per window.

## The claim under test

> "A clone that simply posts (or takes) both legs with that fixed switch
> probability, at the observed cadence, with the observed size mix, and
> never actively closes residual would reproduce the statistical texture
> of the entire 760k-fill trace. The remaining variance is therefore
> explained by the venue's arrival process, not by further trader
> decisions visible in the public fills."

## Method

Every window keeps its **actual** fills — count, timestamps, sizes, and
the window's own price level. Only the **leg sequence** is regenerated,
from the measured per-month switch hazard (0.421 → 0.272). Prices are
complemented (`p → 1−p`) wherever the simulated side differs from the
real one, because the two outcomes are complements: if UP really traded at
0.40 at that instant, DOWN was available at 0.60. That keeps the simulated
book coherent with the venue's actual prices and makes the counterfactual
honest — "what if you had taken the other side at that same moment".

Holding everything else fixed is what isolates the claim: leg choice as a
coin flip, versus the venue's contribution.

## Result — rejected on both outcomes

| | REAL | MEMORYLESS SIM |
|---|---|---|
| mean same-leg run length | **3.4046** | **3.4041** |
| matched qty, mean per window | 399.9 | 378.5 |
| residual qty, mean per window | 122.1 | 164.8 |
| pair cost median | **0.98000** | **0.99895** |
| pair cost p25 / p75 | 0.9057 / 1.0540 | 0.9144 / 1.0786 |
| share of pairs below $1.00 | **0.5860** | **0.5040** |
| mean locked profit per pair | **+0.01434** | **−0.00082** |
| share of windows P&L-positive | **0.5807** | 0.5295 |
| **total P&L** | **+$70,917** | **+$1,770** |

Paired within-window, real minus the mean of its own replicates:

* pair cost **−0.01605** [−0.01832, −0.01368] — the real sequence builds a
  matched book **1.6 cents per pair cheaper** than a coin flip can.
* P&L **+$4.156 per window** [+$3.170, +$5.092].

**The memoryless model reproduces the texture almost exactly and produces
essentially none of the money.** Run length matches to four decimals.
Matched and residual quantities are close. And the profit is **40× smaller
— $1,770 against $70,917** — with mean locked profit per pair at
**−0.0008, i.e. zero**, exactly as the arithmetic of a coin flip demands
(p and 1−p sum to 1).

## Why the earlier tests missed this

They conditioned on the wrong variable. The round-1 and round-2 findings
were all about **when** a switch happens:

* P(switch) flat at 0.25–0.32 across every residual-VWAP decile — **true**.
* Δpair-cost on a switch fill indistinguishable from a same-leg fill —
  **true**.
* Per-fill switch hazard near-constant at 0.27–0.29 across five months —
  **true**.

All of that remains correct. **Switch *timing* is price-blind.** But the
money is not in timing — it is in the **price accepted on each leg**:

| | share of fills at price < 0.50 | mean fill price |
|---|---|---|
| whole corpus | 0.5315 | **0.4614** |

A side-blind selector pays **0.5000** by construction. L5 pays **0.4614** —
**3.9 cents per contract** below the coin-flip baseline, and the share
below 0.50 is only 0.53, so the edge is not "picks the cheap side slightly
more often" but "when it buys, it buys materially cheaper". Monthly it is
stable: 0.512–0.568.

So the decision that matters is a **price-acceptance / patience policy on
each leg independently**, not a leg-sequencing policy. The two are
orthogonal, which is exactly why a battery of switch-timing tests could
all pass while the underlying claim was false.

## What this means for a clone

Grok's conclusion — "a clone needs almost nothing beyond the constant 0.28
hazard, the observed cadence distribution, and the observed size
distribution" — is **false**. A clone built to that specification earns
$1,770 where the original earned $70,917 on identical fills.

What a clone actually needs is the thing the hazard/cadence/size triple
leaves out: **the rule that determines which prices get accepted.** On this
data that rule is visible only through its output — an accepted-price
distribution with mean 0.4614 — because orders, cancellations, the book
and the maker/taker flag are all invisible (see
`../LIMITS_OF_PUBLIC_FILLS.md`).

Two readings remain open and this data cannot separate them:

1. **Patience.** Resting passively on both legs and being filled only when
   someone crosses to the cheap side. The edge is then a property of the
   venue's flow, and the trader's contribution is presence plus price
   discipline.
2. **Selection.** Actively choosing moments when a leg is mispriced
   relative to its complement. The edge is then the trader's.

Distinguishing them needs the order book or the maker/taker flag. Neither
is in this corpus.

## One caveat that cuts against the positive reading

Being filled cheap on each leg and still ending with a residual that
settles badly are consistent: the matched book locks **+1.4 cents per
pair**, while the residual's value depends entirely on the outcome. The
real sequence wins on pair cost **and** on P&L here, but the P&L figure
excludes fees, rebates and anything off-venue, and residual settlement is
the high-variance term. The 1.6-cent pair-cost advantage is the robust
result; the $4.16-per-window P&L advantage carries the residual's variance
with it.
