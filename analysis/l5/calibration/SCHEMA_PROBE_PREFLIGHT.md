# Private-WS schema probe — PREFLIGHT ONLY, NOT EXECUTED

Sole objective: **observe private-WS accept/open/cancel frames.** It is not
permitted to chase a fill, test profitability, or inform any strategy decision.
A probe that observes the frames and loses nothing is a complete success.

## Design

1. Subscribe `/v1/ws/private` (`ORDER_SNAPSHOT`, then `ORDER`, then `POSITION`)
   **and** `/v1/ws/markets` for the target slug, and prove both are receiving,
   **before** any submission. If either is not confirmed live, abort without
   submitting.
2. Submit exactly ONE order: **q=1, post-only, GTC**, at a price deliberately far
   from the touch and non-marketable.
3. Persist every private frame **verbatim**, with local wall clock, monotonic
   clock, and any venue timestamp untouched.
4. Prove REST `OPEN` state for the order id.
5. Cancel as soon as the accept/open frames are captured, or after
   `OBSERVE_MAX_S`, whichever comes first.
6. Prove terminal cancellation and **zero open orders** before the report.

## Price rule — the exposure bound

Price is set to **`min(0.01, touch - 0.20)`** in own terms and refused unless it
is at least **0.20 below the executable touch** at submission. So the worst case
if it somehow filled entirely is **$0.01**, and filling at all would require the
market to collapse 20+ ticks while we are live.

| bound | value |
|---|---|
| orders submitted, total | **1** |
| quantity | **1** |
| price | <= 0.01, and >= 0.20 away from the touch |
| **maximum possible economic exposure** | **$0.01** |
| observation window | `OBSERVE_MAX_S = 120`s |
| windows used | 1 |
| fill intent | **none** — a fill is a FAILURE of the design, not a goal |

## Hard guards

* **Crossing**: `post_only=True` + `TIF_GTC`, the pre-submit
  `X.would_cross(...)` check, AND the independent 0.20 distance rule. A
  post-only rejection is recorded as evidence and never retried nearer.
* **Staleness**: refuse to submit on `StaleFeed` or a book age above the existing
  threshold; abort rather than submit on stale data.
* **If the order fills at all**: stop immediately, record it, sweep, prove zero
  open orders, and report it as an unexpected outcome. Do not re-submit.
* **Cancellation proof**: the existing `cancel_and_resolve` with
  `CANCEL_CONFIRM_POLLS`; `UNKNOWN` halts and is never treated as cancelled.
* **Deadman**: cancel-all and prove-zero-open before any terminal report;
  restart policy **NEVER**.
* Arming still requires the triple key plus a probe-specific key, all unset.

## What it can and cannot establish

**Can**: whether `SUBSCRIPTION_TYPE_ORDER` pushes anything after the snapshot;
the field names and shapes of accept/open/cancel frames; whether they carry a
venue timestamp, a client and venue order id, and a state enum; delivery latency
against our own monotonic clock; and whether the snapshot reflects a live order.

**Cannot**: the FILL frame schema — whether a fill carries a venue trade id, fill
timestamp, executed price/quantity or maker/taker role. That is the field set the
calibration campaign actually depends on, and only a real fill reveals it. This
probe closes roughly half the observability gap and I will not overstate it.

## Status

**NOT EXECUTED. NOT ARMED. Zero orders submitted.** Requires explicit
authorization, separate from the calibration campaign authorization, because it
is a real submission.
