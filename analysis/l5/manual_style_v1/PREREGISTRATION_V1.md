# MANUAL_STYLE_FAVORITE_ACCUMULATOR_V1 — preregistration

Committed **before** any V1 number was computed. Shadow/replay only, the
same 28 untouched tape windows, canonical fees from `src/pmus_fees.py`.
D0–D4 execution optimization is **closed** and none of them is touched
again. No live orders, no scaling.

## The rule, complete

1. At the start of an eligible BTC15m window, **seed both sides** with a
   fixed dollar amount of UP and the same of DOWN, taker, at the
   contemporaneous executable touch, capped by displayed depth.
2. Once per 60 s thereafter: read authenticated PMUS state, take the
   **executable mid** `(bid+ask)/2`, call UP favored above 0.50 and DOWN
   below it (an exact 0.50 is no action), and make **one fixed-dollar market
   purchase of the favored outcome**.
3. On a leadership flip, later purchases go to the new favorite.
4. **All inventory is retained.** No trail, no sell, no unwind, no recycling
   at any point inside the window.
5. The whole portfolio settles at expiration.

There is no intrawindow exit, so there is nothing to tune about one. Cadence
(60 s), favorite threshold (0.50), seed and purchase size are **given**, not
fitted, and none is varied.

## Sizing — the manual experiment's own ratio, normalized

The manual sequence was ≈$0.50 UP + ≈$0.50 DOWN, then ≈$0.09–$0.10 per
minute: a **5:1 seed-to-extension ratio per side**. The ratio is the only
real parameter here, because every dollar quantity scales linearly.

So at this lane's standing $1 normalization: **extension = $1 per minute,
seed = $5 UP + $5 DOWN.** That preserves what was actually traded rather
than inventing a 1:1 seed.

## The counterfactual — decomposition, not optimization

**V1** = two-sided seed + minute favorite accumulation.
**V1-no-seed** = the minute favorite accumulation alone, rule otherwise
identical.

Stated in advance so it cannot be presented as a discovery: **V1-no-seed is
rule-identical to Variant A** from the original FAVORITE_DCA study (taker,
60 s, mid > 0.50, $1, hold to settlement, no exit). It must therefore
reproduce A's published −$1.178/window exactly. If it does not, the harness
is wrong and the lane is void. That is a consistency check, and it also
means the seed's contribution is the *only* new quantity V1 measures.

## Reported

Seed economics and favorite-extension economics **separately**, fees,
settlement payout, net/window, gross-before-fee/window, **gross ÷ fees**,
median, positive-window fraction, worst window, leave-best-1, leave-best-3,
capital required, maximum directional residual, and number of leadership
flips.

The headline comparison is **gross per dollar of fees against frozen D0's
0.956**, for both V1 and V1-no-seed.

## Gate

No scaling and no $200 envelope unless normalized expectancy is positive
**and** robust — positive mean, positive median, positive leave-best-1 and
leave-best-3 — the same bar every variant in this programme has been held
to.
