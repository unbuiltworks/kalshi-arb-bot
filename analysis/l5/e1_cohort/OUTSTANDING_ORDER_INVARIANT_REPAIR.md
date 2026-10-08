# E1 — the outstanding-order invariant, repaired

| | |
|---|---|
| **status** | **DISARMED. NOT DEPLOYED. NO LIVE RUN AUTHORIZED.** |
| **new fingerprint** | `5e15bdc02c40416b1137e8bff94f7f07262277a3f76c47a6b1d99655f32a8da3` |
| **superseded** | `5e1413ca…1fe0` (now listed structurally non-armable) |
| **invariants** | 191 → 212 |
| **session** | `E1-COHORT-20261008T005816Z-051888d8` preserved, not reset |

`LABEL: FROZEN, DISARMED ARTIFACT — NOT SHADOW, NOT SUBMITTED, NOT FILLED,
NOT SETTLED, NOT REALIZED.`

---

## 1. What went wrong

The frozen spec has declared `max_outstanding_orders: 1` since the cohort was
created, and `runner.eligible()` has always carried the clause that enforces
it:

```python
if snapshot.get("our_outstanding_orders"):
    r.append("E3_OUR_ORDER_IS_OUTSTANDING")
```

`pmus_provider.py` published that field, two lines in a snapshot dict:

```python
"our_outstanding_orders": 0,      # literal
"our_open_residual": None,        # literal
```

So the rule compared against a constant. It could not fire. On 2026-10-08 the
`cpc-btc-updown-15m-2026-10-08-1230z` window accumulated **twenty resting
orders**, one per placement slot, 0.13 shares each, `OUTCOME_SIDE_NO` at
own-side 0.43–0.46 — worst case **$1.1804**, **zero fills**.

This is my miss, and it is a specific kind. I verified that the bound existed
in the spec and that E3 existed in the runner. I never checked what fed E3.
Hours earlier in the same session I had found a test stranded in `conftest.py`
that pytest never collects — a guard that could not fail — and I did not
generalise that finding to code that moves money.

## 2. The cancel, verified against venue state

Not against the cancellation API's own response. Each of the twenty order ids
was re-read individually from `GET /v1/order/{id}`, plus `GET /v1/orders/open`:

```
ids_checked          20
state_histogram      {"ORDER_STATE_CANCELED": 20}
sum_cumQuantity      0.0
non_terminal_remaining {}
open_orders_now      0
trades_for_the_20    0
positions            [{}]
balances             openOrders 0, assetNotional 0, currentBalance 241.62915
VERIFIED_AGAINST     GET /v1/order/{id} per id + GET /v1/orders/open
```

No fill raced the cancel, by two independent readings: every one of the
twenty ids came back in `canceledOrderIds` (a filled order cannot be
cancelled), and every one now reads `ORDER_STATE_CANCELED` with
`cumQuantity 0`. **Current unmatched exposure: zero.**

## 3. The repair

### `analysis/l5/e1_cohort/order_state.py` (new, read-only)

Establishes outstanding orders and filled inventory from
`GET /v1/orders/open` and `GET /v1/orders?limit=100` over
`src.pmus_auth.ReadOnlyTransport`, which raises `PermissionError` on any
method but GET. It builds no order body and names no mutating path.

| rule | behaviour |
|---|---|
| partial fill | **outstanding** — it has leaves at the venue and can still fill |
| unrecognised state | **outstanding** — not in `LIVE_STATES` or `TERMINAL_STATES` |
| missing state | **outstanding** |
| unconfirmed submission | **outstanding** — `pending_ids`, charged at the frozen $0.60 ceiling |
| unreadable GET | `ok: False`, truthy sentinel, **refuses** |
| unparsable body | `ok: False` — an unrecognised shape is not "no orders" |
| read older than 5 s | `ok: False` — measured across the read, not from our own timestamp |
| open-vs-history disagreement | `ok: False` — the cancellation race is reported, not resolved conveniently |
| exposure | own-side (`1 − wire` for the non-long side), aggregate of open leaves **plus** filled inventory |
| scope | this cohort's slugs; a row with no slug is **counted**, because it cannot be excluded |

The failure mode is the design: `outstanding_for_snapshot()` returns an `int`
on a good read and the **truthy** string `ORDER_STATE_UNKNOWN_FAIL_CLOSED` on
a bad one, so the same `if snapshot.get(...)` refuses either way. There is no
code path from a failed read to a permissive zero.

### `pmus_provider.py`

The two literals are gone. `order_state(pin)` is asked once per snapshot,
immediately before the snapshot is built — the same discipline already applied
to book freshness. With no credentials it fails closed **without opening a
socket**, which is also why the offline suite makes no network call.

### `runner.py`

* `E3_ORDER_STATE_NOT_ESTABLISHED:<reason>` — named, so a fail-closed is never
  mistaken for a resting order.
* `E3_OUR_ORDER_IS_OUTSTANDING:<count>` — the count is reported.
* `E3_AGGREGATE_VENUE_EXPOSURE_<x>_WOULD_EXCEED_5.00` — the independent
  venue-side reading of aggregate exposure plus the attempt about to be sent.
  **Terminal**, because a risk limit is terminal.
* **Startup reconciliation.** Before the first window opens, `run_cohort`
  asks the venue what already exists. Pre-existing outstanding orders or an
  open residual stop the cohort with `STARTUP_RECONCILIATION_REFUSED`. It
  **cancels nothing**.
* **Submission intent recorded before the POST.** `pending_order_ids` gets an
  entry before the request goes out and keeps it until a venue read resolves
  it. A `SubmissionAmbiguous` therefore leaves it in place — which is what
  makes the runaway impossible even when an acknowledgement is lost.

### `pacing.py`

`E3_OUR_ORDER_IS_OUTSTANDING` / `E3_OUR_RESIDUAL_IS_OPEN` move from exact
match to prefix (they now carry detail), and `E3_ORDER_STATE_NOT_ESTABLISHED`
is paced — nothing was placed and the next read may succeed.
`E3_AGGREGATE_VENUE_EXPOSURE_*` is deliberately **not** paced.

## 4. Tests

`test_e1_outstanding_orders.py`, 37 tests. Full package suite re-run.

The two that matter most:

* `test_the_loop_places_ONE_order_and_then_waits` — the real `run_cohort`,
  forty iterations, an always-eligible book, a fake venue that rests every
  accepted order. **One placement.**
* `test_twenty_would_have_happened_without_the_repair` — the counterfactual.
  Same loop, same bounds, same book, snapshot publishing the old literals.
  It runs away. Without this, the test above would prove nothing.

Also: `test_the_two_literals_that_caused_the_runaway_are_gone` asserts on the
**provider's source**, because the lesson of this defect is that a gate can
exist while nothing feeds it; and
`test_order_state_module_cannot_mutate_anything` proves the read-only claim
independently rather than on the strength of a comment.

## 5. Remaining risks, stated plainly

1. **One market at a time is assumed by the scope rule.** Outstanding orders
   are counted within this cohort's slugs. Correct for E1 (one market,
   one outstanding order) and **not** sufficient for concurrency, which is
   locked anyway.
2. **The 5-second staleness bound is a judgement, not a measurement.** Venue
   order-state latency has not been characterised. If reads routinely take
   longer, E1 will pace on `ORDER_STATE_STALE` and place nothing — safe, but
   it would look like a market problem when it is a latency problem.
3. **Two extra GETs per decision iteration**, paced at ~1 s. Not measured
   against any rate limit. If the venue throttles, the reads fail and E1
   fails closed.
4. **`pending_order_ids` lives in memory.** A crash between the POST and the
   acknowledgement loses the intent; the next process recovers through startup
   reconciliation against the venue instead. That is the right authority, but
   it is a different mechanism, and it only fires at startup.
5. **`HANDOVER_TTE_S = 60.0` in `pmus_provider` vs `120.0` in the inherited
   `SPEC["timing_explicit"]`** — still flagged, still deliberately unchanged.
6. **E1's candidate lookahead is one hour** (`window_start_for(now)` plus
   three successors) — unchanged, still flagged.
7. **Nothing here has run against the live venue.** Requirement 7 (verify
   against actual venue state in read-only mode) is the next step and needs a
   deployment of the read-only path, not of a trading engine.

## 6. What this does not do

No strategy change. No bound, price, quantity, tranche or stopping rule
touched. No parameter changed. Not deployed. E1 remains **DISARMED**, session
preserved, evidence intact.
