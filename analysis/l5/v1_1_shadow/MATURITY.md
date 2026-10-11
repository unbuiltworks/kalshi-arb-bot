# Prospective sample maturity — preregistered 2026-09-16, before the sample

Written **before** the numbers exist so that "mature" cannot be chosen to suit
whatever the data turns out to say. Nothing here may be relaxed to reach a
conclusion; failing a criterion is a reportable result, not a reason to edit
this file.

No tuning follows from this sample. The criterion decides **when to stop
measuring**, never what the controller does.

## What the sample is for

One operational number drives the rest of the package: the **V1.1 participation
rate** — the fraction of observed market-states on which the controller would
propose. It feeds C (which candidate envelope) and D (concurrency, expected
time-to-cap in the bounded probe). Everything else is diagnostic.

So maturity is defined on the participation rate, and on the V1 control rate
beside it, because the *comparison* is the deliverable.

## Materiality, derived from the decision rather than picked

| | |
|---|---|
| **Material** | **5.0 pp.** The concurrency recommendation in D moves by roughly one market per 5 pp of participation at the observed market count; below that, no cap value, no envelope choice and no stop condition changes. |
| **Precision target** | **2.0 pp**, a 2.5× margin inside material. |

A 2 pp shift moves expected proposals per market-hour by ~3.6 at the shadow's
180 evaluations/hour, and moves time-to-cap by ~3% relative. Neither changes any
authorized number in D.

## The criterion — all six must hold

### M0 VALIDITY — the sample is the thing we think it is
- zero service restarts inside the measured window (a restart resets the
  theoretical inventory walk, so rates before and after are not one sample);
- `act_artifact_missing` count **0** (without Stage 1 every row WAITs and the
  comparison is vacuous);
- `no_status` events < 5% of cycles (a dead handoff is not a quiet market).

### M1 COVERAGE — floors, so precision cannot be reached on a degenerate sample
- observations ≥ **25,000**
- distinct markets ≥ **100**
- elapsed wall time ≥ **3.0 h**
- complete 15-minute blocks ≥ **12**

### M2 BASELINE STEADY STATE — the artifact that would otherwise flatter us
Every V1 WAIT is a risk cap binding. While the shadow book is near flat no cap
binds, V1 sits at **1.00**, and the measured reduction is overstated. So:

- cumulative V1 proposal rate < **0.99**, AND
- the V1 rate itself satisfies M3 and M4.

Until the baseline is in its own steady state there is no comparison to report.

### M3 PRECISION — 95% CI half-width ≤ 2.0 pp, for **both** rates
Rows are heavily autocorrelated — the same market re-observed every 20 s is not
a new draw — so a binomial CI would be far too narrow. Uses **non-overlapping
15-minute batch means** with a Student-t quantile at B−1 df. 15 minutes is long
relative to the decorrelation timescale (L5's next-action gap p90 is 70 s).

### M4 STABILITY UNDER DOUBLING — for **both** rates
`|p̂(N) − p̂(N/2)| ≤ 2.0 pp` on the most recent doubling of observations. If
doubling the sample moved the estimate less than the precision target, another
doubling is unlikely to move the operational conclusion.

### M5 NO TREND — for **both** rates
The last four non-overlapping 30-minute windows each lie within **±3.0 pp** of
the cumulative rate. Catches slow drift that M3 and M4 can both pass.

## Checkpoints

Geometric in observations — **1k, 2.5k, 5k, 10k, 25k, 50k, 100k** — plus hourly,
so convergence is *watched* rather than a cutoff being chosen. Every checkpoint
reports cumulative and rolling side by side.

## Stopping guard

If M1 is satisfied at **N = 100,000** and M3 still fails, the conclusion is
that the rate is **not converging**, and the report says so and why. The sample
is not extended indefinitely in search of a stable number.

## Tracked at every checkpoint

observations · distinct markets · elapsed wall time · V1 proposal rate · V1.1
proposal rate · Stage-1 suppression rate · full/base/marginal lookup rates for
both controllers · ACT probability distribution (deciles + quantiles) · Stage-1
decision reasons · MODEL / RISK_CAP / VENUE final divergence · desired inventory
and residual distributions · exposure distribution · fraction hitting the
residual and exposure caps.

## Not in scope of this criterion

Whether L5 would have acted. Not observable on this tape — L5 trades
Polymarket.com, this is Polymarket US sports. No amount of sample fixes that,
and it is answered by the chronological OOS in `../act_wait/FINDINGS.md`.
