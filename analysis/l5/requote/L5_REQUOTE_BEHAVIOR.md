# What L5 does when the market moves away from his previous execution level

**117,683 L5 actions across 1,008 BTC15m markets**, built from EXISTING evidence.
No new collection, no recorder, no live trading, no strategy change.

## Inventory first: what exists, and what does not

| needed | status |
|---|---|
| L5 side / price / size | **available** — `data/l5/sep2026/*.json.gz`, 145,050 BTC15m fill rows |
| inventory before / after, matched, residual | **available** — replayed in action order |
| elapsed time since prior L5 action | **available** — venue `timestamp`, 1 s resolution |
| same-side vs opposite-side follow-up, subsequent action | **available** |
| L5 event time vs our receipt time | **available** — `data/l5/ops/activity_20260901.jsonl` |
| contemporaneous BBO / depth | **UNAVAILABLE** — no Polymarket L2 tape aligned to L5's action times |
| distance from current touch | **UNAVAILABLE** — same reason |

Fragment grouping reuses `scripts.l5_behavior_census.actions_by_market` unchanged:
rows sharing (slug, timestamp, outcome, price) are one order, not many.

**Nothing new was built except the tape itself.** No recorder was added, because the
only missing fields are book fields no existing tape carries for L5's venue.

### Two measurement corrections made before interpreting anything

1. **21.4% of activity prices arrive off the 0.01 grid** (`0.8099999757` for
   `0.81`). A raw equality test therefore compares floating-point noise, not price
   levels. Snapping to the grid moved "same price" from 9.4% to **23.7%** and
   "exactly the old price" from 13.9% to **35.3%** — the fix changed the answer.
2. **The collection stamp is not a realtime receipt.** Measured lag (our
   `_collected_utc` minus venue `timestamp`, n = 4,371): min 6 s, p10 1,369 s,
   **median 7,993 s**, p90 13,609 s, max 16,419 s. A median of 2.2 hours means
   this is a BATCH pull, not a realtime feed. So L5's ordering comes from the
   venue timestamp alone and **his reaction latency is not measurable from this
   evidence**. A lag of hours is certainly not L5 acting hours later.

## The answer

**L5 is imbalanced essentially always: 116,649 of 117,683 actions (99.98%).**
Imbalance is not a state he resolves; it is his operating condition. (The flat
comparison group is n = 26, so there is effectively no control.)

### What he does next, while imbalanced

| | share |
|---|---|
| **A/C — same side, NEW price** | **43.4%** |
| **B — same side, SAME price** | **23.7%** |
| **D — opposite side (complement)** | **32.9%** |

### It is NOT state-dependent on imbalance size

| \|residual\| | n | A new px | B same px | D opposite |
|---|---|---|---|---|
| [0, 5) | 2,376 | 44.6% | 24.3% | 31.1% |
| [5, 20) | 6,631 | 42.9% | 25.6% | 31.5% |
| [20, 100) | 33,040 | 43.2% | 24.1% | 32.7% |
| [100, 500) | 69,175 | 43.5% | 23.2% | 33.3% |
| >= 500 | 5,453 | 43.7% | 25.1% | 31.3% |

Flat to within ~2 points across two orders of magnitude of residual. **The mixture
does not depend on how imbalanced he is.** A cap-like or imbalance-triggered rule
is not what he is running.

> **CORRECTION.** This document went on to call the split a "state-independent
> mixture". That was too strong: it generalised from ONE variable. See
> `L5_NEXT_ACTION_PREDICTORS.md` -- conditioning on action size, distance from
> his own last level and recent price movement moves the probabilities to
> P(A) 0.28-0.60, P(B) 0.03-0.50, P(D) 0.15-0.69. The correct narrow claim is
> that RESIDUAL does not explain the mixture; other state does.

### How far he moves, and how fast

Same-side re-price distance while imbalanced (n = 78,296):
**p50 $0.01**, p75 $0.02, p90 $0.05, p99 $0.17; 35.3% at exactly the old price.

Seconds to his next action while imbalanced: **p50 2 s**, p90 17 s, p99 58 s.

## The smallest rule that explains it

*(Superseded in part: the proportions are conditional, not fixed. See the
correction above and `L5_NEXT_ACTION_PREDICTORS.md`.)*

> **Act again within a couple of seconds. Stay imbalanced. Each time, either
> re-execute the same side within about one tick of your last level (~43%), repeat
> that exact level (~24%), or take the complement (~33%) — in fixed proportions,
> regardless of how large the residual is.**

## Why this kills V1's quoting rule

Our engine's `_still_valid` returns `True` unconditionally on the non-deficient
side, documented as *"a better touch is NOT a reason to move"*. Under the old fill
model, 98.4% of its fills sat a **median 33 cents** through the market.

**L5's consecutive same-side executions are a median 1 cent apart, p90 5 cents,
p99 17 cents.** Whatever mechanism he uses, he never executes 33 cents away from
his own recent level. The two behaviours are not variants of one thesis; ours was
an artifact of a fill model that paid for impossible fills, and his is a tight,
fast, continuously re-levelled process.

The patience thesis survives only in a weak form: patience in *staying imbalanced*
(he always is), not patience in *leaving a quote parked* (he does not, beyond a
tick).

## What this evidence cannot establish

* **Fill-only.** These are executions, not orders. A new fill price is consistent
  with re-anchoring, but does **not** prove cancel-and-replace: he may hold several
  resting levels simultaneously and have different ones fill. Resting orders,
  cancels, queue position and book state are **not inferred here**.
* **No book.** BBO, depth and distance-from-touch are unavailable, so "the market
  moved" is proxied by his own execution path. That is an observable, not a touch,
  and is labelled as a proxy everywhere.
* **No reaction latency.** The collection lag is hours; his response time to a
  market move is unmeasurable from this evidence.

The next question these open, in priority order: whether the ~24% exact-level
repeats are a distinct behaviour or the same process re-hitting a level, and
whether the fixed 43/24/33 mixture is stable across regimes and across days.
