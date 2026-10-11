# EXECUTABLE WINDOW ENGINE -- one decision report

Research/shadow only. **No new live authorization**, no live orders, no
strategy or collector changed, no OOS partition unsealed. Everything below
reuses existing machinery; nothing new was built for it except the
sequential pair-cost computation in section C, which is an analysis over
already-committed tapes.

## 0. Current state, established first

**Running jobs** (none touched):

| run | job | state |
|---|---|---|
| `37325663925` | recorder, 9h | in progress since 14:33Z, ends **~23:42Z** |
| `37362701516` | snapshot recorder, 75m | in progress since 19:20Z |
| `37368059222` | **lead/lag layer-1** | **QUEUED, no runner assigned since 20:10Z** |

**Existing assets reused, not rebuilt** -- 29 `gemini_*` / `l5_*` scripts and
the `gemini_execution` / `gemini_research` packages, including
`complement_reachability`, `capacity_replay`, `capacity_ladder`,
`complement.py`, the frozen analyzers, both maker studies, the loss-
management harness and the L5 inventory/replay machinery. The one
reconciliation worth naming: **section C's question was already answered in
September** by `gemini_complement_reachability.py`; I used that result rather
than writing a third implementation of it.

---

## A. Is there a measurable information lead? **UNKNOWN -- BLOCKED, not negative**

The lead/lag job has been **queued for 13+ minutes with no runner** (account
concurrency is consumed by the two collectors). The admissibility probe has
not executed, so there is **no verdict on any feed**: no admitted venues, no
timestamp units, no clock offsets, no PMUS state. I will not characterise
feeds I have not probed.

What is already established and bears on A:

* **Gemini's book updates at 1 Hz; its display surface at ~10s** (median lag
  9.33s, p90 42.57s). Measured today from the recorder tape.
* **LRRR publishes at exactly 1 Hz.**
* So **sub-second and 1s horizons are not measurable on these feeds**
  regardless of what is true. 5s/10s/30s are.
* **V2's incorporation test already failed for the LRRR->book direction**:
  sign agreement 56.25% / 52.00% / 46.67% at lags 1/2/3 checkpoints, every
  Wilson interval straddling 50%, correlation **negative** at all three
  lags. If anything the Gemini book leads LRRR, not the reverse.

**A is the binding unknown for any predictive rule, and one of its four
sub-questions is already answered negative.**

---

## B. Can we capture Gemini's spread without toxic fills? **NO, on the evidence we have**

The spread is **genuine** -- proven today: the executable ladder and the
contract display surface, two independently parsed endpoints, report the same
distribution (median 0.290 vs 0.280; max 0.97 vs 0.96). So there is a real
~29c spread to capture.

But every maker fill we can evidence was toxic:

| policy | fills | settle win | EV/fill | EV/opportunity |
|---|---|---|---|---|
| MAKER p25 | 7 / 23 | **0.0%** | -0.2629 | -0.0800 |
| MAKER p50 | 7 / 23 | **0.0%** | -0.3114 | -0.0948 |
| MAKER p75 | 7 / 23 | **0.0%** | -0.3600 | -0.1096 |
| TAKER | 23 / 23 | 43.5% | +0.0065 | **+0.0065** |

**0 of 7 maker fills won**, against 43.5% for the taker on the same
directional decisions. p75 fills at a worse price with a worse post-fill
excursion **without filling more often**. The maker fee being 4x smaller
($0.07 vs $0.44 total) nowhere near pays for it. State-aware cancellation
recovered 0.0000-0.0626 of the gap; **no variant reached the taker**.

Caveat held firmly: **7 fills off 19 prints is not a sample**, and these are
validation-tape numbers. But the direction is consistent with the mechanism
(you fill when the market runs through you) and with the loss-management
path study, which found the same adverse-selection signature.

---

## C. Can sequential inventory create sub-$1 effective pairs often enough? **The opportunity exists in hindsight and does not survive causality**

New computation, q1, fees included, **causal ordering enforced** (leg 2 at or
after leg 1; ordering verified -- 0 of 17 windows disagreed between
timestamp and checkpoint order):

| | min | median | max | windows < $1.00 |
|---|---|---|---|---|
| **SIMULTANEOUS** (both legs same instant) | 1.050 | 1.200 | 1.350 | **0 of 17** |
| **SEQUENTIAL** (leg 2 later) | 0.740 | 0.930 | 1.240 | **11 of 17** |

This is the single most important structural number in the report, and it
cuts both ways:

1. **Simultaneous pairs are never lockable.** Not once in 17 windows. Any
   "buy YES + NO below $1" framing is dead on this venue, exactly as
   CLAUDE.md warns.
2. **Sequential pairs are lockable in 11 of 17 windows -- WITH HINDSIGHT.**
   The computation picks the best (t1, t2) knowing both. A causal policy at
   T+45 cannot know DOWN will be 0.21 at T+150. **So 11/17 is an UPPER
   BOUND on any sequential policy, not an achievable rate.**

And we already measured the achievable rate. `complement_reachability.json`
(2026-09-20, 21 real first-leg fills):

* **1 of 21 first legs ever became matched inventory -- 4.8%**
* **complement reach rate 2.04%** (98 complement quotes, 2 reaching prints)
* median pair basis when it did happen: **0.93**
* **77.57% of complement notional arrived AFTER we stopped quoting**

**65% in hindsight versus 4.8% in practice is the finding.** The second leg
is not merely uncertain -- it is usually unavailable while we are there to
take it. Note also that the best sequential legs above repeatedly require
waiting until **T+180**, and the selection-gap work established that by then
the cheap side's ask has often disappeared entirely (one-sided leaders had
no ask in 100% of cases).

**LOCKABLE_INVENTORY frequency: 4.8% measured, 65% hindsight-bounded.**
Time-to-lock, capital-before-lock, max residual and locked margin are in the
September artifact for the single case that occurred; one case does not
support distributions and I am not presenting them as such.

---

## D. Is directional residual itself positive-EV anywhere? **Not demonstrably**

* **V2 (discovery, 11 windows, OOS sealed):** D's **sign** separates -- UP
  wins 94.23% after D>0 vs 53.57% after D<0 -- but the venue prices the sign
  in (UP VWAP moves 0.5075 -> 0.8348) and the residual edge is ~10pp. D's
  **magnitude is anti-informative at the top**: monotonicity fails both
  ways, and the largest-|D| bucket pays -20.07pp because the price has moved
  further than the edge.
* **V1:** 0 of 32 candidates cleared the $0.01 bar -- but that was a scale
  mismatch (grid at +/-5/25bps vs observed median 1.48bps), so it is a null
  selection, not evidence.
* **Early-window analyzer's +7.79pp UP edge is a base-rate artifact.** UP's
  win rate was 77.78% at *every one* of nine checkpoints, identically,
  because 7 of 9 windows settled UP. Not evidence, and I am not re-reading it
  as such.
* **Live:** cohort 1 realized **-$2.45** on 5 fills; the underdog cohort's 5
  fills are filled but unsettled.

---

## E. Best currently defensible causal policy: **NO_TRADE**

On this evidence the highest-defensible-EV action at essentially every
observed state is **NO_TRADE**, and I am not going to dress anything else up
as a candidate. The reasons are ordered by how firmly they are established:

1. maker fills are toxic (0/7) and no cancellation variant repairs them;
2. the complement reaches 2% of the time, so sequential locking is not
   reachable at a usable rate;
3. the one information channel tested (LRRR -> book) shows no lead and a
   negative correlation;
4. every simultaneous pair costs >$1.
5. the only positive EV anywhere (TAKER +0.0065/opportunity) is inside noise
   on 23 observations and contradicted by live realized -$2.45.

---

## F. Capacity and dollars/day: **what would be REQUIRED, not what is available**

Measured capacity (V2/V1 capacity ladder, snapshot discovery): economic
two-sidedness **100% at q1, 87.5% at q5-q100**, pair cost 1.28-1.31. Depth
is *not* the binding constraint -- two-sidedness and complement availability
are. The historical ladder agrees: both sides fillable 42.1% at q1/q5/q10
and 41.4% at q25, flat across sizes.

BTC5m runs **288 windows/day**. So:

| target | required net EV/window at q1 | required EV/window if only 4.8% of windows lock |
|---|---|---|
| $20/day | $0.069 | $1.45 per locking window |
| $100/day | $0.347 | $7.23 |
| **$480/day** | **$1.667** | **$34.72** |
| $5/window | $1.667 -> $480/day | -- |

**$1.667 net per window at q1 is arithmetically impossible**: a q1 BTC5m
contract pays at most $1.00 gross. $480/day at q1 would require net EV
greater than the instrument's entire payout. Reaching it needs size:
**~q25-q50 sustained on both legs with a locked margin near 7c**, i.e. 25-50
contracts of two-sided depth in every one of 288 windows. Measured
two-sided depth supports q5-q10 routinely and q25 about 41% of the time --
and the complement, which is what a lock needs, reaches 2%.

**On current evidence the defensible figure is $0/day, because no candidate
survives.** If the complement-reach rate were lifted to 50% at q10 with the
observed 0.93 median pair basis, that would be ~$0.07 x 10 x 0.5 x 288 =
**~$100/day** -- which is the honest shape of the upside, and it depends
entirely on a complement-availability improvement we have no mechanism for.

---

## G. Evidence still missing before shadow

1. **The lead/lag verdict** (A) -- blocked on a runner, not on analysis.
   External->LRRR, external->book, PMUS->book are all unmeasured.
2. **Maker fills at n >> 7.** The adverse-selection result is directionally
   consistent but statistically thin.
3. **A causal complement-availability model.** 4.8% vs 65% is the gap that
   decides C; we have the two endpoints and nothing in between.
4. **The underdog cohort's SETTLED / CASH_CONFIRMED** -- 5 fills still
   unaudited; the audit script needs a cohort parameter.
5. **Run 1's ~100 windows** (~23:42Z), which will roughly 6x every sample
   above and is the cheapest improvement available.

## Binding constraint

**COMPLEMENT AVAILABILITY, then ADVERSE SELECTION.** Not information, not
fees, not capacity, not settlement prediction. The spread is real and wide,
depth reaches q25, and sub-$1 sequential pairs exist in hindsight in 65% of
windows -- but the second leg is there for us 2% of the time, and when we
rest to capture the spread instead, we fill only when we are wrong.
