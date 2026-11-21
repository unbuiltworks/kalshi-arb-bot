# Bounded adverse-move rescue: does a >$1 ceiling pay, before incentives?

OWNER P0: backtest 1.005 / 1.01 / 1.015 / 1.02 / 1.025 / 1.03 against L5
actual (A) and our current ≤1.00 continuation policy (B), on complete
resolved-market accounting including zero-redemption losers, and find the
smallest ceiling that materially improves trading P&L **before incentives**.

## Answer: none of them, on this sample. The best is +16.5 bp and t = 1.31.

| ceiling | trading P&L | vs B(1.00) | mean/day | sd/day | **t** | days>0 |
|---|---:|---:|---:|---:|---:|---:|
| **B = 1.000** | −6,667.81 | — | — | — | — | — |
| 1.005 | −5,536.47 | +1,131 | 113 | 420 | 0.85 | 6/10 |
| **1.010** | **−4,504.64** | **+2,163** | 216 | 524 | **1.31** | 7/10 |
| 1.015 | −4,975.95 | +1,692 | 169 | 671 | 0.80 | 7/10 |
| 1.020 | −6,893.92 | **−226** | −23 | 891 | −0.08 | 7/10 |
| 1.025 | −6,477.35 | +190 | 19 | 1,078 | 0.06 | 7/10 |
| 1.030 | −6,112.28 | +556 | 56 | 1,168 | 0.15 | 7/10 |
| 1.050 | −5,017.76 | +1,650 | — | — | — | — |
| 1.100 | −4,480.90 | +2,187 | — | — | — | — |
| **A = ∞ (L5)** | **−4,208.75** | **+2,459** | — | — | — | — |

t critical (df=9, two-tailed, 5%) = **2.262**. **Nothing comes close.**

### Two features that matter more than the point estimates

**The curve is non-monotonic.** 1.020 is *worse than doing nothing*
(−$226) while 1.015 either side of it is +$1,692 and +$2,163. A real
mechanism with a boundary produces a monotone or single-peaked curve. This
looks like noise around zero.

**Variance rises monotonically with the ceiling: sd/day 420 → 524 → 671 →
891 → 1,078 → 1,168.** That part *is* clean and monotone. A higher rescue
ceiling reliably buys more day-to-day variance, and unreliably buys return.

**Per-day deltas vs B at 1.010:** +1004, +162, +668, −51, +847, +24, +237,
−343, +304, −688. Two of ten days (Sep 8, Sep 10) are negative for every
ceiling and get *more* negative as the ceiling rises — Sep 8 runs −497 at
1.005 to −1,885 at 1.030.

## What rescue does reliably deliver: less directional exposure

| ceiling | residual qty at settlement | vs B |
|---|---:|---:|
| B = 1.000 | 699,688 | — |
| 1.010 | 654,754 | −6.4% |
| 1.030 | 592,218 | −15.4% |
| A = ∞ | 462,223 | **−33.9%** |

Monotone and large. **Rescue buys risk reduction, not expectancy.** If the
objective is lower directional variance, it works and the effect is not
subtle. If the objective is trading profit, this sample cannot show it.

## Method, and its limits

Per market, fills are replayed **in timestamp order**. A fill that would push
the average matched pair cost above the ceiling is **refused**, and the
refusal changes the state every later fill is evaluated against — this is a
sequential re-simulation, not a re-bucketing of the final numbers.

Winner is taken from the venue: `redeem_qty` matches the winning side's
quantity; a market with no redemption means the only side held lost.
Membership is by **resolution**, never redemption (see the survivorship note
below), so zero-redemption losers are in.

**Policy B is an approximation and the direction of the error is knowable.**
Our real engine, on refusing a completion, keeps working the market and may
fill later at a better price. The replay can only *remove* fills L5 actually
made; it cannot invent the ones our engine would have gone on to get. So B as
simulated is **pessimistic** — the true B is somewhere between this and A,
which if anything *narrows* the already-insignificant gap.

**Simulator fidelity: −$4,208.75 simulated at ceiling ∞ vs −$5,033.94
actual, a gap of $825.** Five markets of 4,217 differ by more than $0.50 and
**one — `btc-updown-15m-1788448500` — accounts for $691 of the $825**.
Unexplained; likely a partial or multi-row redemption the reconstruction
flattens. It does not move the policy *comparison* (every arm carries the
same market) but it is a real 16% discrepancy against actual and is disclosed
rather than absorbed.

Ten days, one venue, one lane, BTC direction autocorrelated across concurrent
windows — so the 4,217 markets are nothing like 4,217 independent trials.

## Recommendation

**Do not move the live ceiling.** The owner's bar was "materially reduces
directional residual losses **and** improves trading P&L before incentives".
The first half is met decisively; the second is not met at any tested
ceiling. On a venue without L5-scale rebate, buying variance for a
statistically indistinguishable return is the wrong trade.

If the risk-reduction half is wanted on its own terms — a smaller residual
book, accepted as costing a little — that is a different decision, it is
supportable from this data, and 1.01 is where the cost is lowest.

**What would change the answer:** out-of-sample days. The point estimate at
1.01 (+$2,163, +16.5 bp) is not nothing; it is simply not separable from zero
on ten days. Re-running this on Sep 11–30 as the harvest extends is cheap and
is the natural next test.
