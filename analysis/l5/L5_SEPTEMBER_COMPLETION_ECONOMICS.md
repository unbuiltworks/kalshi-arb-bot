# What makes 15-minute crypto worth trading — L5, Sep 1–10

Completes the September analysis: the metrics not covered by the cashflow
report — settlement-observed trading net, completion rate, pair cost,
complement gap, residual exposure.

Source: the same 144,394-row harvest, `data/l5/sep2026/`.

## Both halves pay, and the trading half is real

| | 10 days | per day |
|---|---:|---:|
| **trading net** (settlement-observed markets only) | **+$7,125.74** | **+$713** |
| **incentives** (MAKER + TAKER + REWARD) | **+$20,490.13** | **+$2,049** |
| total | +$27,615.87 | +$2,762 |

Trading net is measured only on the **3,510 of 4,236** markets where a
redemption was actually observed: $1,552,370.34 out, $1,559,496.07 back.
**+0.459% of spend, +$2.03 per market.**

So the answer to "why is he still trading it" is **both**: the strategy makes
money on its own — thinly, at 46 basis points of turnover — and incentives
pay **2.9x more than the trading edge**. Neither alone explains the operation;
incentives are the larger half but the trading half is not negative.

## L5 completes ABOVE par routinely — our engine forbids this

Two-sided markets, n=2,479:

| percentile | pair cost |
|---|---:|
| p10 | 0.7753 |
| p25 | 0.8986 |
| **median** | **0.9690** |
| p75 | **1.0435** |
| p90 | **1.2388** |

| bucket | count | share |
|---|---:|---:|
| **< 0.98** | 1,367 | **55.1%** |
| 0.98 – <1.00 | 224 | 9.0% |
| **= 1.00** | 0 | **0.0%** |
| **> 1.00** | **888** | **35.8%** |

**More than a third of L5's completed pairs cost more than $1.00, and his p90
is 1.2388** — the same shape as the MIL −2.5 position that triggered our
safety event and produced the ≤1.00 whole-position ceiling.

He absorbs it because the cheap completions are genuinely cheap (p10 0.7753)
and because incentives run at 2.9x the trading edge. The distribution is
wide and the mean is carried by the left tail, not by avoiding the right one.

**This is a real divergence between L5 and our clone, and it is deliberate on
our side.** Our engine hard-refuses any action that would push average matched
cost above 1.00. That rule is why the MIL shape cannot recur, and the owner
set it after seeing what the alternative cost us. But it means:

- we are **not** replicating L5's completion policy, we are replicating a
  strictly safer subset of it;
- roughly a third of his two-sided volume is unavailable to us by
  construction;
- any expectancy comparison against L5 must account for that, rather than
  treating our numbers as a like-for-like clone result.

Nothing here is a recommendation to relax the ceiling. It is the measurement
of what the ceiling costs in reach, which the owner asked to keep visible.

## He is one-sided far more often than we are

- **58.5%** of markets touched are two-sided
- **41.1%** are **one-sided only** — a directional position taken and held to
  settlement, never completed
- residual share of quantity: **median 17.62%, p90 61.50%**

Two in five markets are pure directional exposure. Our operating model treats
residual as risk to be minimised and holds it only when completion is
uneconomic; L5 appears to accept it as a routine outcome.

## Complement timing

- complement gap (first fill on side A to first fill on side B): **median
  51 s, p90 179 s**

Essentially unchanged from August (median 53–54 s). The 3x longer 15-minute
settlement cycle did **not** slow how fast he puts the second side on. He
moved horizon without changing completion tempo.

## Our own sports completions — the bucket counter the owner asked for

The same buckets, on our live probe's completed pairs:

| bucket | count |
|---|---:|
| < 0.98 | **1** (`aec-itfme-dannun-andsou`, 0.94936) |
| 0.98 – <1.00 | 0 |
| = 1.00 | **1** (`aec-atp-enrval-stetra`, 1.0000) |
| > 1.00 | **0** (refused by construction) |

**n = 2. Far too small to characterise anything** — reported because the
instrument now exists, not because the split means something yet. The metric
to watch as the sample grows is the share landing at exactly 1.00, since
those earn nothing from the trade itself and depend entirely on incentives.

Note the structural difference already visible: L5 has **0.0%** at exactly
1.00 and 35.8% above it; we have 50% at exactly 1.00 and 0% above, because
1.00 is our binding constraint and merely a point his distribution passes
through.

## Deferred, not forgotten

`per_market_cap` / `$ per market` is a misleading label — it bounds
**directional acquisition**, not total spend. Rename to
`directional_acquisition_cap` **at the next safe deployment**. Not now: the
probe is gathering and stays untouched.
