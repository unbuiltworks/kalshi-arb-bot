# Proposal: one maker-only window, to measure our own queue position

**This is a proposal and not an authorization.** Nothing in it has been
executed. Production is disarmed at `6116675ed` and stays there unless the
owner arms it explicitly. Requested for explicit owner approval per PR #208
item 3/5.

---

## 1. The one question offline work cannot answer

Every fill number this program has is a **bracket**, and the bracket is
wider than the decisions it is being used to make:

| | OOS pessimistic | OOS optimistic |
|---|--:|--:|
| at the touch (`d=0`), net/entry | **−0.001446** | +0.008850 |
| two ticks behind (`d=2`), net/entry | **+0.016341** | +0.047599 |

The sign at the touch depends on which end you take. The cause is a single
unmeasured quantity: **where our own order sits in the queue, and whether it
fills without the opposite touch crossing it.** `src/l5_queue_depletion`
states the reason plainly — there is no trade-print series, and a log query
for `prints_at_our_price` across the running deployment returns **zero**
records. No further replay closes this. Only our own resting orders do.

## 2. What would be measured

For each of at most two resting orders, recorded per order rather than in
aggregate:

1. **Queue ahead at placement** — displayed size at our price, which the
   presubmit record already captures.
2. **Whether it filled without the opposite touch reaching our price.** This
   is the whole experiment. Every `PROVEN` fill we have is one where the
   touch crossed us; a fill *without* that is direct evidence that resting
   depth earns queue priority, and it is the one observation that converts
   the bracket into a number.
3. **Time from acknowledgement to fill**, on a single clock. Item 1
   established that the published durations mixed a process clock with the
   collector's, inflating them; this must be recorded same-clock.
4. **Time from first-leg fill to the completion quote reaching the venue** —
   ROUTE B's latency with real inventory, which has never been observed
   because it has never had a position to complete.
5. **Partial fills, if any.** We have **zero** in the venue-confirmed record,
   so even one observation is new information.
6. **Settled cashflows**: per-leg cost, matched redemption, residual, fees
   and any credited rebate, each on its own line.

### Observation acceptance gates

**My first version of this section was too weak and is replaced.** It said
"confirm the observation path is reached and would append". That would have
passed on `pre_submit` alone and still produced an unusable record — see
`ORDER_OBSERVER_TRACE.md`, where the live path turned out to have every
venue-confirmed half of a record and **no lifetime observation at all**,
because `observe` was reached only for shadow coids. That is fixed; these
gates are what verify the fix on real data rather than trusting it.

In order, and gate 1 decides whether the window is spent at all:

1. **Before arming, on a disarmed tick.** `report()` must show
   `orders > 0` from the shadow rehearsal with at least one record whose
   `capture.lifetime_observation == PRESENT`. That proves the observation
   machinery runs against real venue data, with real prints and a real
   ladder. **If this fails, do not arm.** The window is worth nothing
   without it.
2. **Within the window, before the second leg is quoted.**
   `precapture_ok(coid)` must be true for the live order — that is
   `pre_submit_book`, `queue_estimate` and `fee_accounting` all `PRESENT`.
   The probe may decline an uncalibratable order on exactly this test.
3. **At the end.** `completeness(coid)["usable_for_calibration"]` must be
   **true**, with at least two observations and **zero** coverage gaps over
   `MAX_OBSERVATION_GAP_S`. A record with a gap is not a failed experiment,
   but it is not a calibration either and must not be used as one.
4. **Always reported.** `orders_straddling_a_restart`. A record whose events
   span a restart is still evidence; it simply may not be read as one
   continuous observation.

## 3. Configuration — no limit changes requested

Everything here is the configuration already deployed. **No variable change
is being asked for.**

| control | value | why |
|---|---|---|
| markets | **one** BTC15m window | the existing single-market restriction |
| live order slots | **2** | unchanged; one per side |
| cash | **$12** no-recycling | unchanged |
| worst-case deficit | **$1** | unchanged, and the real bound on the cost |
| total exposure | **$1** | unchanged |
| taker crossing | **off** | maker-only; no authorization sought |
| watchdog | independent, unchanged | must be verified live before arming |
| size | **1 contract a leg** | the minimum that produces a queue observation |
| **depth** | **2 ticks behind each touch** | the only depth that survives the conservative arm in the simultaneous replay (+0.0163/entry); the touch does not (−0.0014). In the SEQUENTIAL case depth barely matters — all three landed within 0.0004 — so this choice is made on the entry, which is where the edge is |
| **TTL** | **none before 120 s** | the 60 s TTL flips the d=2 pessimistic cell to −0.0036, and the sequential replay found the benefit of waiting saturates by 120 s. Cancelling early destroys exactly the fills that make this work |

The depth and the TTL are the two settings this experiment would exercise
differently from ROUTE B's current `completion_depth_ticks() = 0`, and both
are chosen from the measured table above rather than from preference.

## 4. What it costs, stated as a loss not a profit

At 1 contract a leg and a 0.95 pair the entire upside is **$0.05**. This
experiment is **not** justified by its expected profit and must not be
reported as a profit test.

The bound that matters is the downside: one leg fills, the other does not,
and the residual is carried to settlement. Worst case is governed by the
**$1 worst-case deficit**, which is enforced independently and was verified
active in production (`max_worst_case_deficit_usd: 1.00`,
`worst_case_deficit_check: ACTIVE`). The measured expectation of carrying one
contract is **−$0.0236**.

## 5. Pre-registered outcomes

Declared before the window so the result cannot be re-interpreted after it:

| observation | reading |
|---|---|
| a leg fills **without** the opposite touch reaching our price | the optimistic arm is supported; queue priority at depth is real |
| every fill comes **with** the touch crossing us | the pessimistic arm is the right one, and the touch-priced completion is not viable |
| no leg fills in the window | one sample, and the pessimistic arm stands unrefuted; **not** evidence of a defect |
| one leg fills and the complement does not | the residual case; record the carry against −$0.0236 and the ROUTE B latency that preceded it |
| a partial fill occurs | first one on record; the partial-fill path gains its first calibration point |

**One window is one sample.** It cannot establish a fill rate, and no
scaling decision should follow from it. Its purpose is to replace an
*assumption* with an *observation* on the single quantity that the whole
bracket turns on.

## 6. Stop conditions

Any one of these ends the window immediately and leaves the system disarmed:

- worst-case deficit would exceed **$1**, or exposure **$1**, or cash **$12**;
- more than **2** live orders, or any order outside the one approved market;
- any ambiguous submission or a POST whose terminal state is unknown —
  halt and reconcile before anything else, per the standing rule;
- the watchdog latches, or its own record goes stale;
- any taker/crossing path becomes reachable;
- the window's own cutoff, after which no new order is placed and resting
  orders are retired on the existing schedule.

Afterwards: settlement audit, then **no repeat** without separate approval.
A completed probe does not authorize the next one.

## 6b. What the sequential replay changed about why this is worth doing

`SEQUENTIAL_COMPLETION.md` found that once one leg has filled alone, **every**
outcome is negative: the gated policy returns ≈−0.0186/leg against −0.0236
for holding and −0.0642 for crossing out, and **every** sequential pair is
dearer than 0.96. The rescue is damage limitation, not profit.

So this experiment is **not** a test of the rescue. Its value is entirely in
the entry: the simultaneous two-sided pair at `d=2`, which is the only cell
that was positive under the conservative arm (+0.0163/entry OOS). The one
observation that decides whether that cell is real is **whether a leg fills
without the opposite touch crossing it** — and that is what gate 3 above
makes recordable.

If the window produces a completed pair at a dear price, that is **not** the
result to celebrate; it is the damage-limitation case, and it should be
reported as such.

## 7. What it still will not establish

- Not profitability. One window at one contract is a measurement, and
  settled realized net stays **−$15.6109** (**−$13.4809** rebate-adjusted)
  until real cashflows say otherwise.
- Not a fill curve, a rate, or a regime.
- Not ROUTE B's steady-state behaviour — only its first latency observation
  with real inventory.
- Not the rebate. It accrues or it does not, and it is counted only when
  credited and source-accounted.
