# Near-Touch Flow: pre-registration

**Frozen before the cut.** Fourth pre-registration in this programme.

Owner, 2026-09-11:

> In sufficiently active markets, maker orders posted ≤2 ticks from the
> relevant touch have materially higher two-sided completion probability than
> farther-away orders, while retaining positive after-fee/rebate pair
> economics and reducing stranded residual incidence toward ≤20%.

And the gate, which is the part that matters:

> **Does the ≤2-tick/high-flow cohort produce positive expected realized
> economics after assigning actual settlement outcomes to every residual?**

---

## 0. A correction to the number that motivated this

I reported the overall shadow completion rate as **3.24%**, implying ~97%
one-sided incidence, and set the 2-tick cohort's **76.2%** beside it. Those are
**different horizons** and the comparison flattered the 2-tick cohort:

| | 300 s (frozen verdict) | 1800 s (research) |
|---|---:|---:|
| all proposals | 3.24% | **25.23%** |
| 2-tick band | 47.6% | **76.2%** |

Like for like at 30 minutes, the 2-tick edge is **76.2% against 25.23% — about
3×, not 23×**. Implied one-sided incidence **23.8% against 74.8%**.

The owner's reading survives the correction: the 2-tick cohort is still the
first cohort measured anywhere near the viability range, and 23.8% is still
outside the ≤20% target. But the margin over baseline is 3×, and everything
below is graded against the corrected baseline.

**Prior exposure, declared.** I have already seen the predictor split, the
distance bands and the strata — they are what generated this hypothesis. This
is not a blind test and will not be presented as one. What the pre-registration
buys here is that the **gate** and the **falsifiers** are fixed before the cut,
not that the features are novel.

## 1. The rule, and what may define it

> A proposal is **NEAR-TOUCH** iff it is posted within **2 ticks** of the
> relevant touch. It is **HIGH-FLOW** iff the market's contemporaneous
> `flow_usd_per_min` is at or above a threshold fixed on the existing data
> before scoring.

Two parameters: the tick distance (**fixed at 2 by the measured cliff**, not
fitted) and the flow threshold. **Settlement outcomes, resolution sides and
realized P&L may never define either.** Both inputs are decision-time
quantities available before an order is posted.

Settlement enters **only as the scorer**, per the gate. That separation is the
same one the residual and entry pre-registrations used, and it is what stops
"assign actual settlement outcomes" from becoming hindsight fitting.

## 2. What is computable from what we have — established before promising it

The shadow service emits **service-computed aggregates only**. Per-proposal
rows do not reach the logs: `execution` carries counts
(`advanced`/`research_updated`/`open`/`total`), `markets[]` carries per-market
window counts, and neither carries a proposal's distance, flow, price,
residual size and outcome together on one row.

**Computable today, offline, no deployment:**

- completion rate by distance band at 300 s and 1800 s, with n per band;
- flow, spread, movement and band-position separation between filled and
  unfilled proposals (medians by outcome);
- sport and market-type strata, with each stratum's usable/not-usable flag;
- time-to-fill distribution; opportunity loss of the flow ranking;
- per-market residual size, from `two_arm[].decide_market_only.residual_final`.

**NOT computable today, and the gate depends on it:**

- the **loss distribution of the 23.8% that do not complete**. Scoring a
  residual needs its size, its side, its cost basis and the market's
  resolution, joined per proposal. The logs carry residual size per market and
  nothing else on that list.
- residual incidence and pair economics cross-cut by distance × flow × band
  simultaneously;
- after-fee/rebate pair economics per completed pair.

## 3. Therefore, stated plainly before any work is done

**The owner's advancement gate cannot be evaluated on current evidence.** Not
because the data does not exist — the shadow computes it internally — but
because the per-proposal rows are never emitted, and this session cannot read
the service's volume.

Closing that needs **one read-only emission change** to a service that already
has no order path: emit a row per resolved proposal carrying distance, flow,
band, spread, price, residual size and side, status, and time-to-fill. That is
a deployment, and the owner has withheld deployments. **It is his call, and
this document exists so the choice is explicit rather than discovered halfway
through a study that cannot finish.**

Note the shape of the decision: the wide-spread test was declined as not worth
a deployment. The near-touch gate — the hypothesis that replaced it — needs a
different and smaller one to be measurable at all.

## 4. What will be reported without that change

An honest partial: the completion curves and separations above, cut as finely
as the aggregates allow, **with the gate explicitly unmet rather than
approximated**. No modelled residual losses, no imputed settlement outcomes, no
"estimated" expected value standing in for the measured one.

## 5. The sample constraint, recorded now

The 2-tick band holds **n = 21**, essentially all NFL. At 76.2% completion that
is roughly **five residuals**. Five is not a loss distribution. Even with
per-proposal emission, the gate needs the cohort to grow before its answer
means anything, and the honest expected outcome of this study is **"the
direction is real, the gate is not yet answerable"**.

## 6. Falsifiers, fixed now

- **The 2-tick edge is an NFL artifact.** Only NFL is a usable stratum today
  (n = 502 of 506). If the effect does not appear in a second sport when one
  becomes usable, it is a sport finding, not a mechanism finding.
- **Flow and distance are the same variable.** The 2-tick band's median flow is
  399 against 12.6 in the 3–4 band. If near-touch proposals are simply the ones
  made in busy markets, then "post closer" is not an instruction — "select
  busier markets" is, and the tick threshold is decoration.
- **The residuals are catastrophic.** If the non-completing 23.8% lose enough,
  the cohort is negative regardless of the fill rate. This is the owner's own
  stated concern and it is the gate, not a caveat.

## 7. What this does not touch

R5F stays off. The stopped probe is not restarted. Today's
**−$9.3736 venue-confirmed realized / +$0.2500 credited** stands as the failed
baseline. The cricket / `falf-ter` / `kaycro` accounting-completeness gap stays
a separate item and does not block this.

---

**Committed before the cut.** Deviations recorded as deviations.
