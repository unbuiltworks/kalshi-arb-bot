# What L5 actually does at COMPLETE — and what that says about our 1.05

**Analysis only. No controller, envelope, cohort or hash changed.** Verified
after the fact: `executable 3194e691…`, `envelope b3bff654…`,
`bundle 48f7f1f4…`, cohort `COHORT-2026-09-18-C5` 6/6 with predecessor intact.

Source: `analysis/l5/l5_canonical_sequence.csv.gz`, 71,984 fills, 2,216
windows, **33,765 COMPLETE actions**, 2026-03-19 → 2026-08-31.

`engine_avg` pair cost = completing price + average cost of the surplus side
before the fill. **This is the definition our live controller's own state
produces**, which is why it is the one that decides whether 1.05 is normal.

> **Cross-check:** this was computed independently and reproduces
> `l5_completion_ceiling.json` exactly — n 33,765, p10 0.7668, p25 0.8624,
> p50 0.9697, p75 1.1004, p99 1.4347, max 1.8324. Two implementations agree.

## 1. Empirical COMPLETE pair-cost distribution

| p10 | p25 | p50 | p75 | p90 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| 0.7668 | 0.8624 | **0.9697** | 1.1004 | 1.2205 | 1.2931 | 1.4347 | 1.8324 |

| bucket | share of completes |
|---|---|
| ≤ 0.96 | **47.74%** |
| ≤ 0.98 | 52.58% |
| ≤ 1.00 | 57.25% |
| 1.00–1.02 | 3.83% |
| 1.02–1.05 | 5.33% |
| **> 1.05** | **33.60%** |

**42.75% of L5's COMPLETE fills are above $1.00.** A third are above 1.05.

Chronologically stable, not a full-sample artifact:

| month | n | p50 | >1.05 |
|---|---|---|---|
| 2026-03 | 819 | 0.9720 | 25.4% |
| 2026-04 | 1,714 | 0.9700 | 32.9% |
| 2026-08 | 31,232 | 0.9694 | 33.9% |

**BTC-5m, our exact venue analogue:** n 2,385, p50 0.9875, p75 1.1728,
p95 1.4596, **>1.05 = 40.46%**, only 51.7% at or below 1.00.

Conditionals: tolerance *rises* near settlement (tts<30s p50 1.0166) and with
large residual (res≥200 p50 1.0071, >1.05 42.2%); imbalance barely moves it
(p50 0.963–0.972); after a 4+ same-side run the median complete is **1.057**.

## 2. Conditional behaviour around >$1 pairs

**The strongest signal is conditioning on a CHEAP held leg** — our exact shape:

| held-side avg cost | n | pair p50 | completing price p50 | >1.05 |
|---|---|---|---|---|
| < 0.10 | 76 | 0.9856 | 0.93 | 9.2% |
| **0.10–0.25** | **2,263** | **1.0939** | **0.90** | **66.1%** |
| 0.25–0.50 | 10,896 | 1.0565 | 0.67 | 51.4% |
| 0.50–0.75 | 17,458 | 0.9276 | 0.29 | 22.2% |
| ≥ 0.75 | 3,072 | 0.9033 | 0.10 | 11.9% |

When L5 holds a leg at 0.10–0.25 it completes at a **median price of 0.90** for
a **median pair of 1.0939**. That is our state almost exactly.

Narrowing to held 0.08–0.22 **and** completing price 0.82–0.97 (n=1,125):
p10 **1.0256**, p25 1.0572, **p50 1.0976**, p75 1.1299, p95 1.1633.
**78.76% above 1.05, and only 4.0% at or below 1.00 — the tenth percentile is
already above $1.**

**Are expensive completes worked down?** Yes. Of 11,344 completes above 1.05,
**97.21%** are followed by more fills in the same window and **96.78%** sit in
a window whose final matched-pair cost ends *better* than that complete.

Windows *containing* a >1.05 complete end no worse than those without:

| | windows | final pair p50 | mean | ≤1.00 |
|---|---|---|---|---|
| with a >1.05 complete | 182 | **0.9659** | 0.972 | **69.8%** |
| without one | 1,428 | 0.9714 | 0.994 | 60.1% |

Association, not causation — windows with expensive completes are also larger
and more active, with more oscillation available to work them down (residual
qty p50 106.8 vs 63.0). Matched-pair economics only; residual is reported as
quantity and never summed with it; neither is REALIZED.

**Does L5 knowingly complete above $1 while already holding the opposite
side?** Yes. The engine_avg definition *is* "price paid now + average cost of
the side already held", computed per fill on the state before it. The >$1 mass
is not a window-accounting artifact.

## 3. Exact prediction for the CONTINUOUS-4 replay state

State: **NO 0.01 @ avg 0.14, YES executable 0.91, completion cost 1.05,
residual −0.01, imbalance 0.01.**

Given a cheap held leg, what L5 actually did next (actions taken):

| lane | extend | complete | **complete share** |
|---|---|---|---|
| ALL | 2,113 | 1,540 | 42.16% |
| **BTC-5m** | 189 | 240 | **55.94%** |
| BTC-15m | 1,878 | 1,264 | 40.23% |

**Prediction: COMPLETE.** On BTC-5m it is the majority action (55.94%), and
when L5 completes from this state the pair costs p25 1.0211 / **p50 1.0809** /
p75 1.1291 — **only 34.55% of those completes were as cheap as our 1.05.**

The alternative L5 takes is **not** "wait for the complement to improve". When
it extends a cheap held leg it extends *the cheap side*, at median price
**0.06** (p90 0.16) — it buys more NO at 0.06, it does not wait for YES to fall
from 0.91. That is the oscillator: deepen cheap, or pay up to close.

## 4. Is the current controller behaviourally faithful here?

**Yes — and it is conservative.** It emits YES `COMPLETE` at 0.91 (pair 1.05)
and NO `EXTEND` at 0.07. Both match L5:

- L5's completing price in this band has p50 **0.90**; ours is 0.91.
- L5's extend price in this band has p50 **0.06**; ours is 0.07.
- L5's pair in this band has p50 **1.0809–1.0976**; ours is **1.05**, below its
  p25 of 1.0211–1.0572.

**Our 1.05 is cheaper than roughly 65% of what L5 did in this exact
configuration.**

## 5. Smallest evidence-derived controller change

**None. No change is required for fidelity**, and I recommend none.

A `<= 0.96` pair gate would be a **fidelity regression**: it would forbid
**78.76%** of L5's completes in this configuration, 40.46% of all BTC-5m
completes, and 42.75% of every COMPLETE L5 made. It would convert the
controller from "completes when holding a cheap leg" to "almost never
completes when holding a cheap leg", which is the opposite of the measured
behaviour.

**A tension in the governing docs that you should see plainly:**
`CLAUDE.md` states spring discipline "prefers materially cheaper pair
completion (roughly `<= 0.96`)". The evidence says L5's own median complete is
0.9697 overall but **1.0939 when the held leg is cheap**, with 42.75% of all
completes above $1.00. So `<= 0.96` is **a risk choice, not a description of
L5**. Both can be legitimate — but they should not be conflated, and the
controller cannot be simultaneously faithful to L5 and capped at 0.96.

## 6. Classification, if you impose a cap anyway

A pair-cost cap would be a **RISK OVERLAY** — deliberate deviation from the
replicated behaviour. It is *not* strategy fidelity (the evidence points the
other way) and *not* venue adaptation (Gemini BTC-5m is the lane where L5 is
**most** tolerant, 40.46% above 1.05).

If you want one, it should be labelled a risk overlay, set as a bound rather
than a behavioural claim, and its cost stated: at 0.96 it suppresses roughly
four completes in five in this configuration, leaving inventory one-sided and
carrying directional residual — which the operating model says has no
demonstrated predictive skill and cost **−$12,971 out-of-sample**.

## Limits

- **Trade feed only. A fill is not a quote.** "L5 waited" cannot be
  distinguished from "L5 quoted and went unfilled", so the complete/extend
  split is over *actions taken*.
- `states_like_ours` conditions on L5 having completed; the choice table in §3
  is the unconditioned complement to it.
- Scale differs by orders of magnitude: L5 trades hundreds of shares, our
  window trades 0.01. The *structure* transfers; the size does not.
- BTC-5m in-band completes are n=240 — informative, not large.
- `maker_taker` is 55,647 maker / 11,068 taker / 5,269 unknown; unknown is
  never treated as maker.
