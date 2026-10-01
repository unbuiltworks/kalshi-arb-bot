# V1 on fresh OOS at q1/q2/q5/q10: no credible path to $3/window

**FRESH OOS.** 35 complete BTC15m windows, 2026-09-30 **1000z–1845z**, which
appear in no prior result — not Batch 1, not Batch 2, not the V1–V4 OOS, not the
Case-2 forensics (37 seen, 35 complete). `CONSERVATIVE_CASE1=True`.

**Replay on recorded tape. NOT realized profit.** Modelled maker rebates excluded
from the settled total per CLAUDE.md.

## Net $/window

| arm | q | net $/win | t | matched/win | fill rate | capital | fees | residual |
|---|---|---|---|---|---|---|---|---|
| CONTROL_ONE_LEG | 1 | +0.0055 | 5.05 | 0.7 | 0.230 | 0.88 | 0.012 | 0.9 |
| CONTROL_ONE_LEG | 10 | +0.0442 | 4.46 | 4.0 | 0.253 | 5.96 | 0.053 | 7.1 |
| L5_REPLICATION | 1 | +0.0089 | 0.69 | 3.1 | 0.386 | 0.82 | 0.052 | 0.9 |
| L5_REPLICATION | 10 | +0.1688 | 2.74 | 18.2 | 0.379 | 7.14 | 0.267 | 8.1 |
| **V1_EXTEND_ONLY** | **1** | **+0.0123** | **0.55** | 3.5 | 0.377 | 1.06 | 0.056 | 1.2 |
| **V1_EXTEND_ONLY** | **10** | **+0.1688** | **2.74** | 18.2 | 0.379 | 7.14 | 0.267 | 8.1 |

**V1 at q1 is +$0.0123/window with t = 0.55 — statistically indistinguishable
from zero.** The best figure anywhere in the table is +$0.1688/window at q10,
which is **1/18th of the $3 target**.

Reaching $3/window would need roughly **178–244 contracts per window**, implying
~$125–170 of committed capital and ~145 contracts of residual directional
exposure — far outside every risk bound in CLAUDE.md, and assuming the venue
could absorb ~18x the size at the same fill rate, which nothing here supports.

## Why V1 collapses into REPLICATION at every clip above 1

Identical on every metric at q2, q5 and q10. The cause is arithmetic, not a bug:
`residual_cap = 2` contracts and the extend test is
`abs(residual) + clip > residual_cap`. At clip 2 one fill already leaves residual
2, so `2 + 2 > 2` blocks **every** extend. **V1's distinguishing behaviour can
only ever engage at clip 1**, where its edge over REPLICATION is +$0.0034/window.

So the one surviving L5-derived behaviour exists only at q1 under a cap of 2, and
at q1 it is not distinguishable from zero.

## THE DECISIVE FINDING: the old economics rested on quotes the market had left

Instrumenting one hour of V1 q1 (4 windows), classifying every would-be fill by
whether the contemporaneous book had crossed our resting price:

| | count | our price minus own-terms ask |
|---|---|---|
| CROSSED (refused as unobserved) | **2,530** | p10 **+$0.06**, p50 **+$0.33**, p90 **+$0.52**, max **+$0.63** |
| not crossed (fill allowed) | **40** | p50 **−$0.01** |

**98.4% of modelled fill events were on quotes sitting through the market**, at a
median of **33 cents** past the best offer. A resting bid 33–63 cents through the
offer cannot persist at a real venue collecting maker fills: it executes on
arrival or on the first tick, at the better price.

The mechanism is deliberate and documented. `_still_valid` returns **`True`
unconditionally** on the non-deficient side:

> "A better touch is NOT a reason to move -- that is the finding the q1 arm was
> built on. It goes only when it stops being economically valid."

The patience rule never repositions a leg because the market moved. That is
defensible **only** if the fill model refuses fills on quotes the market has left
behind. The pre-repair model did not, so patience was being paid for fills that
cannot occur.

(`requotes` is initialised and reported but never incremented anywhere — a dead
counter. Its zero is not evidence and is not used as any; the evidence is the
staleness distribution and `_still_valid` itself.)

## What this reframes

Case 1 was never a subtle queue-semantics question. The engine's dependence on it
was a symptom of never repositioning a quote. Fixing the Case-1 *instrument*
would not have changed this conclusion, which is why no retry is justified.

## Verdict

**There is no credible path from this mechanism to $3 net per 15-minute window.**
Best honest figure +$0.1688/window at q10 with residual 8.1 contracts; the q1
behaviour that distinguishes V1 is indistinguishable from zero; the gap is ~18x
in size with risk bounds and venue capacity both against it.

Recommendation: **stop polishing this implementation** and return to the L5
behavioural model. The specific question to take back there: *what does L5 do
when the market moves away from its resting quote?* Our engine holds the stale
price by design and we have now shown that cannot pay. Either L5 repositions, or
it earns from something this model does not represent at all.
