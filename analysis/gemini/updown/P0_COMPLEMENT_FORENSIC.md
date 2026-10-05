# P0 -- COMPLEMENT REACH FORENSIC

Forensic only. No orders, no new policy, no threshold fitting, no per-window
path chosen. The lead/lag job is left queued and the long recorder untouched.

## 0. The discrepancy is substantially DEFINITIONAL, and that comes first

The two numbers do not measure the same quantity:

* **11/17 hindsight, median best $0.93** counts **MARKETABLE** availability
  -- could we have *crossed* to get the complement.
* **2.04% `complement_reach_rate`** counts **THEORETICAL PASSIVE QUOTE
  FILLS**. That artifact's own labels say the arms "change only a COMPLETE
  price or how long a quote rests" and "every fill is THEORETICAL". It is a
  maker-fill rate.

A maker-fill rate and an availability rate are different things, and most of
the apparent 2% vs 65% gap is that. The forensic below therefore measures
what an inventory engine would actually do: hold a real first leg and look
for a **marketable** complement, at 1 Hz, with measured latency.

## 1. Method

* **series**: the 1 Hz `p_snapshot` book, ~300 observations per window. The
  8-point checkpoint grid cannot resolve a survival curve at 1-120s.
* **first leg**: the already-frozen underdog rule, causally -- first instant
  at or after T+15 where both sides fill q1 and the cheaper side is at or
  below 0.60. **Never moved to make a pair reachable.**
* **latency**: 0.197s, this programme's own measured p90 ack across the
  underdog cohort's five live fills. An opportunity must still be executable
  after it.
* 17 independent windows, each contributing one first leg. No checkpoint is
  counted as an independent sample.

## 2. Decomposition -- and three of the six categories are EMPTY

| category | windows | share |
|---|---|---|
| **A NEVER_REACHED** | **9** | 52.9% |
| B REACHED_AFTER_EXIT | 0 | -- *see note* |
| **C REACHED_TOO_BRIEFLY** | **0** | 0% |
| **D REACHED_NO_DEPTH** | **0** | 0% |
| **E REACHED_EXECUTABLE_MISSED** | **8** | 47.1% |
| F FIRST_LEG_WRONG | 0 | 0% |

**C is empty: all 11 episodes survived the reaction budget.** Episode
durations: min **3.0s**, median **12.0s**, max **179.1s** -- 11 of 11 above
0.197s. Reaction latency is not the constraint.

**D is empty, and this is the surprise.** The sub-$1 complement
availability is **identical at q1, q5, q10 and q25**: 8 of 17 windows,
532.1 open seconds, at every one of those sizes. When the complement is
economically available it is available **at q25**. That contradicts the
earlier capacity-replay note that the complement "stops qualifying at
q>=10" -- those were different corpora and a different (policy-ceiling)
definition, and on this tape depth is simply not the binding thing.

**B is structurally unmeasurable in this design, and I will not report a
zero as a finding.** This forensic holds the first leg to the end of the
series, so by construction it never exits early. B is precisely where the
2.04% came from: the live policy stopped working the inventory, and the
September artifact measured 77.6% of complement notional arriving **after we
stopped quoting**. B is the live-policy failure mode; it cannot be
re-measured from a design that never quits.

**F is empty**: no window had a hindsight sub-$1 pair that the causal first
leg made inaccessible. The frozen first-leg rule did not misposition us.

## 3. Survival curve -- P(sub-$1 complement marketable within X s of fill)

| X | windows | of 17 | P |
|---|---|---|---|
| 1s | 0 | 17 | **0.0%** |
| 5s | 0 | 17 | **0.0%** |
| 10s | 0 | 17 | **0.0%** |
| 30s | 0 | 17 | **0.0%** |
| 60s | 0 | 17 | **0.0%** |
| 120s | 2 | 17 | 11.8% |
| through settlement | 8 | 17 | **47.1%** |

**First-lock time distribution: min 71.0s, median 174.6s, max 249.0s.**

This is the operational heart of it. The complement is **never** available
quickly. After a T+15 first leg the median wait is ~175s, i.e. about T+190
in a 300-second window. Any engine that works this inventory must be
willing to hold a naked directional leg for ~3 minutes with no complement in
sight, and about half the time one never comes.

## 4. The counterfactual, without hindsight optimisation

Engine = take the lock where one was marketable while we held the leg; hold
to settlement otherwise. Not path-optimised: every window gets the same
rule.

| | total | per window |
|---|---|---|
| **engine** (lock 8, hold 9) | **-$2.39** | **-$0.1406** |
| hold-only baseline | -$5.34 | -$0.3338 |

So the inventory engine recovers **$2.95 of the $5.34 loss -- 55%** -- and is
**still negative**. Mean gain per completed lock **+$0.2038**; mean loss per
uncompleted leg **-$0.5025**.

**How much of the 2.04% -> 65% gap is mechanically recoverable?** The
honest answer: the gap was mostly definitional, and of what remains,
**47.1% marketable reach is achievable by a hold-to-settlement engine** --
against a 65% hindsight bound and a 2.04% passive-quote rate. But recovering
it does not make the strategy positive, because the losing half costs 2.5x
what the winning half earns.

## 5. Minimum causal completion rate for positive EV

With mean lock gain +$0.2038 and mean uncompleted-leg loss -$0.5025:

> **required completion rate = 71.2%**

| size | sub-$1 availability | required rate |
|---|---|---|
| q1 | 47.1% | **71.2%** |
| q5 | 47.1% | 71.2% |
| q10 | 47.1% | 71.2% |
| q25 | 47.1% | 71.2% |

The required rate is **size-invariant at these depths** because both the
margin and the loss scale with size. **Observed 47.1% against a required
71.2%: the shortfall is 24 percentage points, and no amount of depth closes
it.**

## 6. L5 comparison -- do we abandon inventory earlier than L5?

**Yes, and the September artifact already quantified it**: 77.6% of
complement notional arrived after our cycles stopped. Combined with this
forensic's median 174.6s first-lock time, the picture is consistent -- the
live policy stopped working inventory well before the complement typically
becomes available.

What I **cannot** yet report is a like-for-like L5 behavioural comparison on
time-held, side-switch counts, same-side accumulation or residual
compression. Gemini has **one** matched-inventory case in the whole corpus;
L5's corpus is built from completions that *reached*. Assembling a six-row
comparison table from n=1 would be the base-rate error I have already made
once in this programme. The one structural difference that **is** evidenced:
L5's sequences complete; ours reach a marketable complement 47% of the time
and only after ~3 minutes of naked exposure.

## 7. What is binding, revised

Not latency (C empty), not depth (D empty), not first-leg positioning (F
empty), and **not** mostly "reach" either -- the reach number was a passive
maker rate. What binds is:

1. **TIMING**: the complement arrives at a median 174.6s, never inside 60s,
   so ~3 minutes of unhedged directional risk is mandatory.
2. **THE LOSING HALF**: 52.9% of first legs never see a marketable
   complement and cost -$0.5025 each, against +$0.2038 when one arrives.
   **71.2% completion is required; 47.1% is available.**

A first leg that is cheaper to be wrong about, or a complement that arrives
sooner, would change this. More depth would not.
