# The price-acceptance rule — tested, and the static-cap clone refuted

Script: `../scripts/83_price_acceptance.py`. Raw output:
`PRICE_ACCEPTANCE_RESULT.json`. All 760,817 fills.

Grok's round-4 position was a *"static, inventory-blind, time-blind
reservation-price rule that simply refuses to buy either leg above roughly
0.48–0.50"*, with a clone needing only that plus sizes, timings and the
0.28 hazard. Five sub-claims were asserted rather than measured. Two hold,
three do not — and one of the three fails arithmetically.

| claim | verdict |
|---|---|
| C1 static cap near 0.48–0.50 | **REFUTED — arithmetically impossible** |
| C2 symmetric across legs | SUPPORTED (UP/DOWN CIs overlap) |
| C3 no time-in-window dependence | **REFUTED as stated** (0.085 spread) |
| C4 inventory-blind | **REFUTED as stated** (0.52 → 0.37 with residual) |
| C5 stable across the corpus | SUPPORTED (0.029 spread across months) |

## C1 — there is no cap, and there cannot be one

Accepted-price distribution over all 760,817 fills:

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 |
|---|---|---|---|---|---|---|---|---|
| 0.02 | 0.04 | 0.07 | 0.19 | **0.46** | 0.71 | 0.87 | 0.93 | 0.97 |

* **45.8%** of fills are above 0.50
* **16.2%** are above 0.80
* p99 is **0.97**

A cap at 0.48–0.50 would have to reject nearly half the corpus. The
sub-0.50 **mean** (0.46142) is real and stable; a **cap** is not what
produces it. The distribution is wide and near-symmetric about 0.46, not
truncated.

The arithmetic cross-check is conclusive. A static per-leg cap at 0.48
forces *every* pair to cost ≤ 0.96. In fact **58.5%** of windows end with
a pair cost above 0.96 and **41.4%** at or above **1.00**. No static
per-leg cap can generate the observed pair-cost distribution.

## C3 and C4 — refuted as stated, but the gradient is NOT evidence of a rule

Mean accepted price declines steadily through a window, and declines with
residual size:

| position in window | mean price | | residual at previous fill | mean price |
|---|---|---|---|---|
| decile 0 | 0.5087 | | flat (0) | 0.5207 |
| decile 2 | 0.4833 | | 1–50 | 0.4715 |
| decile 5 | 0.4572 | | 50–200 | 0.4628 |
| decile 7 | 0.4604 | | 200–1,000 | 0.4396 |
| decile 9 | **0.4240** | | >1,000 | **0.3706** |

Also: the **first fill of a window averages 0.534** — *above* fair — while
all later fills average 0.460.

So Grok's "time-blind" and "inventory-blind" claims are false as
descriptions. **But attributing these gradients to a decision rule would
repeat a mistake this project has now made twice.**

In a binary window, prices polarise toward 0 and 1 as resolution
approaches. If the account simply buys whichever leg is currently cheap,
then the mean accepted price **must** fall as the window progresses, and
**must** fall as the residual accumulates on the side that is losing —
with no change in policy whatsoever. Both gradients are exactly what a
fixed, price-blind-in-time policy produces against a polarising market.

Separating "the rule tightened" from "the market moved" requires the
market's own price path or the order book. **Neither is in this corpus**
(`../LIMITS_OF_PUBLIC_FILLS.md` §2). So:

* **Robust:** the mean accepted price is 0.4614 against a side-blind
  baseline of 0.5000, stable month to month (0.457–0.486), symmetric
  across legs, and it is *not* produced by a cap.
* **Descriptive only, confounded:** the within-window and
  within-residual gradients. They are real in the data and must not be
  read as a reservation-price schedule.

This is the third time on this dataset that a conditional metric has
carried a confound — per-window switch rate, per-window completion rate,
and now per-decile accepted price. The pattern is consistent: **any metric
conditioned on a variable that itself moves with the window's own clock
needs a null model before it is interpreted.**

## Where that leaves the clone

Three successive clone specifications have now been tested and all three
fail:

1. **Opportunity-driven switching** (round 1) — refuted: P(switch) flat
   across residual-VWAP deciles.
2. **Memoryless hazard + cadence + size mix** (round 3) — refuted:
   reproduces run length to four decimals, earns $1,770 against $70,917.
3. **Static per-leg price cap** (round 4) — refuted: arithmetically
   incompatible with the observed pair-cost distribution.

What survives is narrow and honest: the account pays a mean of **0.4614**
per contract where side-blind selection pays 0.5000, it does so
symmetrically on both legs, stably across six months, with a wide accepted
price distribution, and it locks **+1.4 cents per matched pair** as a
result.

**The mechanism that produces that 3.9-cent-per-contract advantage is not
identifiable from fills alone.** The two candidates — passive resting that
gets crossed into on the cheap side, versus active selection of mispriced
moments — predict the same fill record. Distinguishing them needs the
maker/taker flag or the book, and that is now the binding constraint on
the whole question.

---

## Bearing on our prior analysis (HOLD BACK — not for Grok)

1. **The 0.4614 figure is the cleanest clone target we have, and it is not
   a rebate.** It is a 3.9-cent-per-contract advantage over side-blind
   selection, independent of the incentive programme, stable across every
   month including the pre-REWARD period. Any clone spec should be stated
   as "achieve a mean accepted price near 0.46 on both legs", which is
   directly measurable on any candidate venue's tape.

2. **It supersedes `L5_BAND` as a parameter.** The canonical band
   [0.25, 0.79] (p10–p90) was fitted as an acceptance gate. The measured
   distribution is p10 = 0.07, p90 = 0.87 — far wider than the band — and
   the refutation of C1 shows a band is the wrong functional form
   entirely. The band gates OPEN and EXTEND in the deployed shadow code,
   so it is suppressing fills L5 would have taken. That is the same class
   of error as the `>=5c average improvement` gate already flagged as our
   invention.

3. **The venue re-gate from the null-model test now has a concrete
   threshold.** Instead of "does the simultaneous pair cost under $1.00",
   the screen becomes: **on Gemini/Kalshi tape we already hold, what mean
   accepted price is achievable per leg within one window, buying whichever
   side is cheap?** If that mean is at or above 0.50, the mechanism cannot
   be reproduced there and the venue really is dead. If it is materially
   below 0.50, the earlier rejection was measuring the wrong thing. This is
   a desk-side computation on existing data.

4. **Methodological warning for the canonical model, now three for three.**
   Per-window switch rate, per-window completion rate, and per-decile
   accepted price have all carried clock confounds. Every conditional
   metric in `CANON_CONSTANTS.md` and `CANON_REGIMES.md` that is
   conditioned on time-in-window, residual size, or fills-per-window
   should be re-derived against a null model before any of it informs a
   parameter.
