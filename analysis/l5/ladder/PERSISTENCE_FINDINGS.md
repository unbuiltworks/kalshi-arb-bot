# Persistent resting vs dynamic reposting — results

Frozen protocol **5816406e**. 678 treated episodes, 727 matched control
episodes, 12 BTC15m windows, maker fills, **bids only**. S3 untouched and
frozen at `8b20a259…87df`. No live orders.

# VERDICT: **CONSISTENT_WITH_REPOSTING**

**A price L5 previously used is not meaningfully more likely to produce a
return fill than a price L5 never used.** Repeated near-touch exposure is
the simpler description.

## P1 — the primary endpoint, and the control is the whole test

| | rate | k / n |
| --- | --- | --- |
| **treated** (prior L5 fill at `p`) | **0.2675** | 180 / 673 |
| **control** (no prior L5 fill at `p`) | **0.2521** | 183 / 726 |
| **ratio** | **1.061** | — |
| windows with treated > control | **6 / 12** | chance |
| frozen bar | ratio ≥ 2.0, n ≥ 30, ≥9/12 windows | **FAIL** |

The two class distributions are near-identical:

| class | treated | control |
| --- | --- | --- |
| SAME_EXACT_PRICE_RETURN | 180 | 183 |
| ADJACENT_PRICE_RETURN | 216 | 277 |
| OPPOSITE_OUTCOME_FILL | 275 | 261 |
| NO_L5_FILL | 2 | 5 |
| INSUFFICIENT_SUBSEQUENT_OPPORTUNITY | 5 | 1 |

Per-window scatter is symmetric, not one-sided — in window …274000 the
**control** rate (0.476) is nearly double the treated rate (0.259).

As recorded in §0 of the protocol before any result: because L5 places **at
the touch**, a return fill is expected under *both* hypotheses, so the
return rate alone could never discriminate. The control does the work, and
it says prior use confers **~6 %** relative advantage — noise at this n.

**Primary measurement, time from market return to first fill at the old
level:** median **9 s**, IQR 3–24 s, p90 44 s (n = 180). Fast, and equally
fast at control prices.

## P2 — the especially important prediction: direction right, size wrong

> If a latent 80-share order was only partially consumed before the
> excursion, the old price should be more likely to fill upon return.

| | rate | n |
| --- | --- | --- |
| prior consumption **PARTIAL** | **0.2776** | 544 |
| prior consumption **FULL** | **0.2248** | 129 |
| difference | **+0.0528** | — |
| windows with partial > full | **6 / 12** | — |
| frozen bar | diff ≥ 0.10, n ≥ 30 each, ≥8 windows | **FAIL** |

The sign is as predicted but the effect is **half the bar** and splits 6/12
across windows.

**And the mechanism check runs the wrong way.** If partial clips fill on
return because unconsumed capacity is still sitting there, then *more*
remaining latent quantity should mean *more* return fills. It does not:
`remaining_latent_INFERRED` has Cliff's delta **−0.079** (hits median 58.9
shares remaining vs 63.0 for non-hits) — negligible, and pointing the
opposite way. The modest partial/full gap is therefore **not** tracking
remaining capacity.

## Covariate dependence — all negligible

Return-fill vs all other eligible outcomes, treated arm (n = 180 vs 493):

| covariate | Cliff's delta | magnitude |
| --- | --- | --- |
| excursion peak distance | −0.051 | negligible |
| excursion duration | −0.044 | negligible |
| TTE | +0.055 | negligible |
| inventory at return | +0.008 | negligible |
| consumed before excursion | +0.015 | negligible |
| remaining latent (inferred) | **−0.079** | negligible |

Nothing about the excursion, the inventory state, the clock or the inferred
remaining quantity predicts whether the old level fills on return.

**Reporting deviation, disclosed:** the protocol said covariates would be
compared for "return-fill probability" without naming the contrast group. I
first implemented it as `SAME_EXACT_PRICE_RETURN` vs `NO_L5_FILL`, which is
**unevaluable** — `NO_L5_FILL` has only n = 2 treated episodes, because the
frozen classification routes nearly every non-exact outcome into
`ADJACENT_PRICE_RETURN` or `OPPOSITE_OUTCOME_FILL` first. The table above
uses hit vs **all other eligible** episodes, which is the faithful reading
of "return-fill probability". The frozen covariate list and statistic are
unchanged, and no covariate or threshold was searched.

## One bug fixed mid-run, and it mattered

My first control implementation excluded any price L5 filled **anywhere in
the window**, instead of the protocol's "no prior L5 fill **before that
excursion**". That made a same-price return fill **impossible by
construction** and forced the control rate to exactly **0.0000** — which
would have been read as infinite advantage for treated levels, i.e. a
spurious and spectacular confirmation of persistence.

Corrected to pre-excursion eligibility (a control price may be filled
later; that is precisely the counterfactual), the control rate is 0.2521
and the effect vanishes. The exact `0.0` was the tell: a rate of precisely
zero across 409 episodes is a definition, not a measurement.

## What this does and does not establish

### CONSISTENT_WITH_REPOSTING
- Prior use of a price confers **~6 %** relative advantage on return
  (1.061×), against a 2.0× bar, with 6/12 window agreement.
- The partial/full gap is +5.3 pp against a 10 pp bar, and the
  remaining-quantity covariate contradicts the capacity mechanism.
- Together with the prior frozen finding that L5 places at the touch
  (median offset 0 ticks at every rank), **the simplest description is
  repeated near-touch exposure**: when the touch arrives somewhere, L5
  trades there; where it has been before does not matter much.

### NOT ESTABLISHED — and cannot be
**A returning fill is never proof the original order remained resting.**
There is no order id, no placement event and no cancel in this data, so
**order identity is UNIDENTIFIABLE**. This result does not show that L5
cancels; it shows that *persistence leaves no measurable footprint* in
return-fill rates. A trader who left orders resting and a trader who
re-posted at the touch would both produce these numbers — which is exactly
why the verdict is "consistent with reposting", not "reposting proven".

### Residual limitations
- `NO_L5_FILL` is rare (2 treated, 5 control) because L5 is almost always
  filling *something*; the discriminating contrast is exact-price vs
  adjacent/opposite, not vs silence.
- Adjacent returns (±2 ticks) sit inside the frozen near-touch band and are
  near-guaranteed under reposting, so they are reported separately and
  never pooled with exact returns, as preregistered.
- Episode construction is blind to subsequent L5 fills by design, but the
  **anchor** requires a prior fill, so treated levels are necessarily ones
  L5 was willing to trade at least once — a selection the control cannot
  fully neutralise.

## Bottom line

The leave-behind-liquidity hypothesis predicted that previously consumed
levels would be privileged on return. **They are not.** Combined with the
earlier coordinate-system result, the picture is consistent and simple:
**L5's exposure tracks the touch, and the apparent ladder is where the
touch has been — not liquidity left behind to be found again.**
