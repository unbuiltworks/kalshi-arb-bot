# CONTINUOUS-5 — the window ran, and it did not answer the question

**Authorization `OWNER-2026-09-18-GEMINI-CONTINUOUS-5` is SPENT.** Latch on
disk in `INTENT_PERSISTED`, cohort ledger carries 3 of 6 slots, workflow and
driver constants disarmed again. Actions run 35394655434 on `d7aa0bce`.

```
executable_sha  3194e6912c343a41c4aefe975f8ac7f5c975be5bb6cbb7bfe5ae9f535ac3abf1
envelope_sha    b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17
bundle_sha      61c7abf3f82d2c2fee30b544fa7ac23b0e4550a5a46cf5b4229743d61dec8c61
```

All three unchanged across the window. Installing and removing the
authorization moved none of them, which is what the detached signature is for.

## The answer, first

> **FILL → REFRESH → INVENTORY CHANGE → CHANGED L5 ACTION: NOT OBSERVED.**

**Nothing filled.** Inventory was `0/0` at every decision, so there was no
inventory change and therefore no changed action to see. The two orders that
reached the venue were still resting, unfilled, when the window ended.

The reason is not the strategy and not the controller. **The window lived nine
seconds.** It placed at 21:05:25 and died at 21:05:32 on a cancel the venue
rejected — leaving no time for a maker quote to be hit.

## Action-by-action

Contract `GEMI-BTC05M2609182110-UP`, entered at **TTE 281.8s**, expiry
21:10:00Z. Two cycles. Every field below is from the new per-cycle capture.

### Cycle 1 — 21:05:24.08, TTE 275.9s

| | |
|---|---|
| authoritative inventory before decision | `yes 0 / no 0`, matched 0, residual 0, imbalance 0, spend 0, `n_fills 0` |
| working orders before | none |
| new fills since prior cycle | none (read 1, `pre_decide`, readable) |
| decision | **`OPEN` YES 0.01 @ 0.26** · **`OPEN` NO 0.01 @ 0.46** |
| holds | none |
| plan | keep 0, place 2, cancel 0 — **2 mutations** |
| budget | 0/6 consumed before |

Both placed and accepted, `status 200`:

* YES `145828836250441342` — ack `orderId` a **string**
* NO `145828836250441357` — ack `orderId` a **string**

A `post_cancel` refresh ran at 21:05:25.58 (still `n_fills 0`) before the
places, exactly as the reconciliation fix requires.

### Cycle 2 — 21:05:31.60, TTE 268.4s

| | |
|---|---|
| authoritative inventory before decision | `yes 0 / no 0`, `n_fills 0` — read 3, `pre_decide`, both orders seen open |
| working orders before | NO 0.46, YES 0.26 |
| new fills since prior cycle | **none** |
| decision | **`OPEN` YES 0.01 @ 0.38** · **`OPEN` NO 0.01 @ 0.46** |
| holds | none |
| plan | **keep 1** (NO 0.46) · **place 1** (YES 0.38) · **cancel 1** (YES 0.26) — 2 mutations |
| budget | 2/6 consumed before |

The `KEEP` is worth noting on its own: the NO rung was unchanged, so it was
kept rather than re-placed — **no slot spent, queue position retained**. That
is the behaviour the budget design exists to protect, and this is the first
live window to exercise it.

The YES re-price to 0.38 never went out. The cancel that had to precede it was
rejected, and the window stopped before any place.

## What actually went wrong

```
order.cancel  params {"orderId": 145828836250441342}      <- INT
              ack    {"status": 400, "error": {"code": -1013,
                                               "msg": "Invalid parameters"}}
```

**The venue publishes `orderId` in two types and we used the wrong one.**
`order.place`'s ack returns it as a **string**; the open-orders **listing**
returns the same id as an **int**. In cycle 1 the working set came from the
place ack, so it held the string. In cycle 2 the `pre_decide` refresh called
`WorkingOrders.reconcile`, which rebuilds the working set **from the listing** —
replacing the string with the int. The cancel then sent the int and the venue
refused it.

This is the third member of the same `-1013` family, and the first two make the
pattern plain:

1. CONTINUOUS-2 sent `clientOrderId` where `orderId` was required — wrong *field*.
2. CONTINUOUS-4's cancel ack returned `200` with an empty result and was read as
   confirmation — wrong *reading*.
3. CONTINUOUS-5 sent the right field in the wrong *type*.

It was invisible until now because **CONTINUOUS-4 never refreshed mid-window**.
Its working set only ever came from place acks, so it only ever held strings.
The reconciliation fix — which is correct and stays — is what first routed a
listing-sourced id into a cancel.

**The fail-closed behaviour was exactly right.** The driver refused to treat a
400 as a cancellation, recorded slot 2 `UNKNOWN`, raised `ReconcileRequired`,
and stopped the window with `SUBMISSION_UNKNOWN_RECONCILE_REQUIRED`. Three
slots spent, three left, nothing sent afterwards.

## Authoritative reconciliation

Post-window read, 21:05:33:

| | |
|---|---|
| fills | **`[]`** |
| order history (`statuses=filled`) | readable, `NO_ORDERS_ON_RECORD`, 0 rows |
| open orders | **2**, both `status open`, both `filledQuantity 0` |
| | NO `145828836250441357` @ 0.46, `createdAt == updatedAt` 21:05:25.646Z |
| | YES `145828836250441342` @ 0.26, `createdAt == updatedAt` 21:05:25.603Z |
| position | none |

**The rejected cancel caused no venue mutation.** The YES order it targeted was
still open afterwards with `updatedAt` identical to `createdAt` — the venue
never touched it. That is the same class of evidence that resolved
CONTINUOUS-2's slot 2, but **slot 2 of this cohort stays `UNKNOWN` until you
say otherwise**: appending a RESOLVE is a deliberate act, not a cleanup.

## Accounting

| stage | value |
|---|---|
| SUBMITTED | 2 × `order.place` (accepted 200), 1 × `order.cancel` (rejected 400/-1013) |
| FILLED | **none** |
| SETTLED | n/a — no position to settle |
| REALIZED | **$0.00** |

Cash committed: **$0.00**. Nothing was at risk beyond two resting maker orders
totalling $0.0072 of exposure had both filled. Contract was still `active` /
`open` at window end; with zero position its resolution has no P&L consequence.

Gemini confirmed realized remains **$0.00**, and CONTINUOUS-4's −$0.0006 stays
**EXPECTED**, not realized.

## Mutation budget

| | |
|---|---|
| cohort | `COHORT-2026-09-18-C5` |
| budget | 6 |
| consumed | **3** — PLACE, PLACE, CANCEL |
| remaining | 3 |
| outcomes | slot 0 `ACCEPTED`, slot 1 `ACCEPTED`, slot 2 **`UNKNOWN`** |
| predecessor | historical ledger `89cf9de6…`, 12/12, **byte-identical** |

The cohort ledger is committed. It very nearly was not: the evidence commit
staged `continuous_ledger.jsonl` but not `cohorts/*/ledger.jsonl`, so the
window would have spent its budget on the runner and discarded the record —
and the next window would have read the cohort as **0 consumed**. That is a
budget reset by accident, the one thing the cohort mechanism exists to
prevent. Found and fixed before arming; a test now pins both lists.

## The capture worked

This is the first Gemini window with a complete action-by-action record, and
every field the objective needs was there when it mattered:

* `decisions` — 2 cycles, each with the inventory the decision saw, the
  working orders, the desired rungs with prices, quantities and **labels**,
  the itemized plan, and the budget;
* `authoritative_reads` — 3, each carrying the driver's **own** `when`
  (`pre_decide`, `post_cancel`, `pre_decide`), `n_fills` and `new_fills`;
* `mutations_observed` — 3, each with the inventory and budget in force **at
  the call**, and the rejection recorded against the cancel that failed;
* `refresh_link` — `linked: true`, 3 reads joined to 3 driver refreshes;
* `decision_capture` — `decide_calls 2`, `action_labels {"OPEN": 4}`, **zero
  record errors**.

The observer changed nothing: 2 decide calls for 2 cycles, 3 wire frames for 3
mutations.

## What this window did establish

1. **The corrected loop runs live.** Authoritative refresh before every
   decision, and again after a cancel when a place is pending — observed, not
   pinned by test.
2. **`KEEP` works on a real venue.** An unchanged rung cost no slot.
3. **The budget is real.** 3 spent, 3 left, durably recorded.
4. **Fail-closed works on a real ambiguity.** The window stopped rather than
   guess, and left the venue in a state the next read explained completely.
5. **A latent type defect is now visible** that no previous window could have
   exposed.

What it did **not** establish is the objective itself. That still needs a
window that survives long enough for a maker order to be hit.

## The smallest fix, and why I did not make it

`order.cancel` must send the venue's `orderId` as a **string**. The fix is one
coercion, in either of two places — `WorkingOrders.reconcile` normalising
`venue_order_id` to `str(...)`, or `Driver.cancel` sending `str(oid)`.
Normalising at reconcile is better: it fixes the *source* of the wrong type
rather than one consumer of it.

Both live in `ws_continuous.py`, **the hashed executable**. Changing it moves
`executable_sha` and invalidates the authorization by design. So it is not made
here. It is a decision for you, and it needs a new authorization naming the new
hash.

**Nothing else should change.** No controller tuning, no pair-cost gate, no
sizing change, no orderUpdate parser.
