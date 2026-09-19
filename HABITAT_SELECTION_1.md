# Pre-entry habitat selection — can a window be judged before money is at risk?

**Maker-price optimisation is falsified and that conclusion is frozen.** No
pricing arms were added. Model A, the TTE bounds and the live authorization
are unchanged. `GEMINI_RECYCLE` is kept as the adapted execution control.

**The question here:** is a Gemini window that will support two-sided
recycling distinguishable from one that will not, using only information
available **before the first order exists**?

**Two samples, and they answer differently.**

| | independent windows | positives (par-constrained pair) |
|---|---|---|
| Gemini BTC5m | **31** | **1** |
| L5 Polymarket (3 slices) | **2,184** | 1,527 |

Code: `habitat_selection.py` (27 tests), `l5_postfill_crossvenue.py`.
Reproduce: `python3 -m src.gemini_execution.habitat_selection_report`.
Output: `analysis/gemini/habitat_selection.json`,
`analysis/l5/l5_preentry_gradient.json`.

*(I first wrote this module as `habitat.py` and silently destroyed the
collector of that name — `public_flow`, `ladder_shape`, `cycle_sample`,
`window_summary`, which the soak and the pre-entry reader both import. The
full suite caught it, the original is restored from git, this module is
renamed, and a test pins the separation. Blast radius: zero soak runs hit it,
the habitat path never imports the collector, and every number below is
byte-identical before and after the restore.)*

---

## 0. The three results

1. **The Gemini sample cannot answer the question.** 31 windows, **1**
   par-constrained positive. No feature separates anything, nothing survives
   a leave-one-out, and the single positive **contradicts** the strongest
   cross-venue candidate.
2. **The L5 gradient is measured but CONFOUNDED WITH REGIME** — see §6. Only
   BTC15m is a maker operation (95.5% maker); BTC5m and ETH5m are
   predominantly taker, so the gradient orders strategies as much as habitats.
   L5 BTC15m's **75.7% par-constrained recycling** stands as the maker
   benchmark; the "five monotone habitat features" claim does not.
3. **The oracle bound is high: 90.0% of gross exposure avoidable** with
   perfect foresight while keeping the one good window. Habitat selection
   *could* be worth enough to justify collecting a larger sample — if the
   separation exists at all.

---

## 1. The leakage boundary, enforced rather than asserted

**One row per window. The independent unit is the window** — not the seed, not
the arm, not the residual state. An earlier pass counted 72 observations where
there were 3 because every arm replayed the same moments; that cannot recur
here by construction.

**The boundary is the first recorded cycle.** In the sequential replay the
first quote goes live at that instant, so its book is observed *before* any
order exists and is admissible. Everything at or after it — the contract's own
tape from that instant, every later book, every fill — is **outcome** and may
never appear in a feature.

Each row carries the boundary and the latest timestamp any feature drew on.
`check_no_leakage()` compares them and a test pins the comparison.

> **Rows failing the leakage check: 0 of 31.**

The predecessor contract is the cleanest admissible source: it has already
expired when our window opens, its whole tape is public, and none of it can
contain anything we did.

## 2. Gemini: the outcome base rates

| outcome | full tape | no ≥100-contract bursts |
|---|---|---|
| ≥1 initial maker fill | 17 / 31 | 15 / 31 |
| ≥1 opposite-side reachable print after first fill | **3** | **1** |
| ≥1 COMPLETE fill | 3 | 1 |
| ≥1 matched pair | 3 | 1 |
| **≥1 par-constrained matched pair (primary)** | **1** | **0** |

**The primary outcome fires once in 31 windows, and not at all without the
bursts.** That is the sample the rest of this section has to work with.

## 3. Gemini: nothing separates, and the honest reason

| feature | ρ (Spearman) | median contrast | drops that destroy the contrast | survives every drop |
|---|---|---|---|---|
| prior_prints | −0.298 | −4.0 | 1 | **no** |
| prior_notional_usd | −0.296 | −11.70 | 1 | **no** |
| prior_prints_per_min | — | — | **31** | no |
| prior_two_sided | — | — | **31** | no |
| prior_side_balance | — | — | **31** | no |
| entry_levels | 0.271 | 2.0 | 1 | no |
| own_price_move_before_entry | 0.265 | 0.06 | 1 | no |
| entry_yes_ask | 0.204 | 0.12 | 1 | no |
| entry_tte_s | −0.204 | −4.93 | 1 | no |
| entry_spread | 0.103 | 0.01 | 1 | no |
| entry_touch_depth | −0.062 | −72.0 | 1 | no |
| touch_turnover_before_entry | 0.080 | 0.0 | 1 | no |

**`any_meaningful: false`.** With **1 positive and 30 negatives**, the smaller
cell is 1 against a floor of 30. Every coefficient above describes these 31
windows and estimates nothing.

**Leave-one-out corrects a trap I had left in.** The first version counted only
*sign flips*, so dropping the single positive — which empties a cell and leaves
no contrast at all — registered as "no flip" and every feature read
`survives_every_drop: true`. That made one observation look robust. Drops that
**destroy** the contrast are now counted, and the answer is:

> **No feature survives every drop. Not one.**

**The six predecessor-rate features cannot form a contrast at all** (destroy =
31 of 31), because the single positive window has no value for them — and the
reason is the most interesting fact in this section.

## 4. The one positive window contradicts the leading hypothesis

`GEMI-BTC05M2609191315-UP`, pair basis **0.93**:

| | |
|---|---|
| predecessor | `GEMI-BTC05M2609191310-UP` |
| **predecessor prints** | **0** |
| predecessor prints/min, two-sided, side balance | undefined — nothing traded |
| entry TTE | 264.3 s |
| entry spread | 0.33 |
| entry touch depth | 312.0, 5 levels |
| own prints before entry | 2 |
| touch turnover before entry | 0.0 (no change across the two reads) |

**The only Gemini window that recycled at an acceptable basis followed a
completely dead predecessor.** On the L5 gradient, predecessor activity and
two-sidedness are the strongest candidates — and the one Gemini success is the
exact opposite of what they predict.

One window proves nothing in either direction. What it does establish is that
**the Gemini sample provides no support for the predecessor-activity
hypothesis**, and that it is too small to provide support for anything.

## 5. The counterfactual oracle bound

With perfect foresight, entering only the windows that produced a
par-constrained pair:

| | full tape |
|---|---|
| independent windows | 31 |
| windows with the outcome | 1 (base rate 3.23%) |
| gross contracts, all / retained | 0.20 / 0.02 |
| **gross exposure avoided** | **90.0%** |
| capital at risk, all / retained | $0.0903 / $0.0093 |
| **capital avoided** | **89.7%** |
| residual, all / retained | $0.0545 / $0 |
| **residual avoided** | **100%** |
| outcome retained | 100% (by construction) |

**This is not deployable and cannot be run.** The retained share is 1.0 by
definition and means nothing on its own. The number that matters is the
exposure avoided: **a perfect habitat selector would cut gross exposure by 90%
and residual to zero while keeping every good window.**

**The no-burst oracle reads 100% and is degenerate** — with zero positives,
"avoid everything" is trivially optimal and is not a finding. It is reported
that way rather than as a better number.

So the ceiling is high enough to justify collecting a larger sample. It says
nothing yet about whether a real selector can approach it.

## 6. The cross-venue gradient — CORRECTED: confounded with regime

> **CORRECTION (2026-09-19, same day).** This section originally read the
> gradient below as a *habitat* gradient. It is at least partly a
> **maker-versus-taker regime** difference, and I should have caught it before
> publishing: `src/l5_shadow/cohorts.py` records it, and its own docstring
> warns that averaging these cohorts "produced every misleading cross-cohort
> figure this programme has published."

Chain-attributed roles, cross-checked against the venue's own maker flag at
**1,270 of 1,270**, and pinned by `tests/test_l5_cohorts.py`:

| cohort | windows | fills | **maker %** | fills/window |
|---|---|---|---|---|
| **BTC15m** | 527 | 63,569 | **95.5%** | 121 |
| BTC5m | 957 | 6,227 | **0.05%** | 6.5 |
| ETH5m | 703 | 2,069 | **0.24%** | 2.9 |

**Only BTC15m is a maker operation.** BTC5m and ETH5m are a different
strategy, not a thinner version of the same one. A taker's round-trip and a
maker's recycling are not the same quantity.

The measured rates, which stand as measurements:

| slice | windows | **par-constrained rate** | complement rate | regime |
|---|---|---|---|---|
| L5 BTC **15m** | 526 | **75.7%** | 96.2% | **MAKER** |
| L5 BTC **5m** | 956 | 50.2% | 83.2% | predominantly taker |
| L5 ETH **5m** | 702 | 20.7% | 42.0% | predominantly taker |
| Gemini BTC5m (ours) | 31 | 3.2% | 9.7% | maker |

And the five features that move monotonically along that order:

| feature | BTC15m | BTC5m | ETH5m | monotonic |
|---|---|---|---|---|
| prior fills/min | 10.42 | 2.687 | 2.675 | yes |
| prior notional/min | 91.72 | 52.26 | 27.27 | yes |
| prior two-sided (median) | 1.0 | 1.0 | 0.0 | yes |
| prior side balance | 0.727 | 0.500 | 0.000 | yes |
| entry TTE (s) | 885 | 243 | 183 | yes |
| prior seconds since last fill | 147 | 145 | 290 | no |

**What survives the correction.** L5 BTC15m at **75.7% par-constrained
recycling** is the maker benchmark, and it is real, large (526 windows) and
the right thing to compare our Gemini quoting against.

**What does not survive.** The claim that these five features describe a
recyclable *habitat*. Ordered by regime as much as by habitat, the "gradient"
has a maker operation at one end and two taker operations at the other. Worse,
`prior_two_sided` and `prior_side_balance` are precisely what a maker produces
**by definition** — so on the L5 side they may be measuring "was L5 making
here", not "was this market recyclable". They are the two features I called
the strongest, and they are the two most exposed to the confound.

**What this means for the next step.** The comparison must be **maker against
maker**, and it must use each venue's **external tape** rather than L5's own
fills — which is exactly what `L5_HABITAT_INVENTORY.md` establishes is
available today for Polymarket BTC15m, Polymarket BTC5m and Gemini BTC15m.

### The observability caveat, which still applies

**L5's rows are built from L5's OWN fills; Gemini's from the whole market
tape.** Polymarket's extract carries no counterparty tape, so on the L5 side
`prior_fills_per_min` is *how busy L5 was*, not how busy the market was. That
was already the weakest part of this comparison and the regime confound
compounds it. `entry_tte_s` is the one feature measured identically on both
sides.

## 7. What this licenses, and the decision it sets up

**Licensed:** the L5 gradient is real, it is monotone in five pre-entry
features across 2,184 windows, and the two strongest are direct measures of
two-sidedness. The oracle ceiling on Gemini is 90% of gross exposure.

**Not licensed:** any selector, threshold, admission rule or live change. The
Gemini sample has **one positive**. No model is fitted and none could be.

**The decision this sets up, in the owner's own terms:** if pre-entry state
contains no meaningful separation between recyclable and leakage windows as N
grows, demote Gemini BTC5m rather than invent another execution trick. As of
31 windows there is **no separation at all**, and the one positive points away
from the leading candidate. That is not yet the demotion threshold — it is one
observation — but nothing here argues against demotion either.

**The cheapest informative next step is not more Gemini BTC5m windows.** The
gradient says the feature that matters is predecessor two-sidedness, and
Gemini BTC5m sits at the bottom of the gradient. Running this exact diagnostic
— complement rate, par-constrained recycling, matched/gross, residual,
activity and runway — against a higher-activity habitat would test the
hypothesis far faster than waiting for Gemini BTC5m to produce a second
positive.

## 8. Collection status

Pre-entry pair collection continues on public reads. **The fail-closed
credential/rollover gate is not weakened**: Gemini's read-only credentials are
absent from this container, the soak correctly refuses to treat an unreadable
open-order list as an empty one, and single-window invocations re-enter that
gate per window rather than bypassing it. Live pre-entry snapshots: **18 of the
20-pair floor**, and 18 of the 31 habitat rows carry one (which is why
`touch_turnover_before_entry` has 18/31 coverage).

The sequential replay is re-run as new independent windows arrive, with results
reported both including and excluding the ≥100-contract bursts.

---

**Accounting labels.** Every outcome above comes from a **SHADOW/THEORETICAL**
replay: no order existed, nothing was submitted, filled, settled or realized.
Matched-pair economics and directional-residual exposure are separate lines and
are never summed. L5's Polymarket P&L is not reconstructed and not claimed —
only the sequence, timing, side, price and TTE of its own fills. Nothing is
live, no authorization is requested, and no threshold, selector or admission
rule exists anywhere in this work.
