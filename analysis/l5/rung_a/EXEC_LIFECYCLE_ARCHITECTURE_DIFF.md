# Architecture diff: proven PMUS executor vs `pmus_native_money_path`

## Responsibility map, before

| responsibility | proven path (`pmus_q1_executor`, used live by `pmus_micro_executor` / `pmus_spring_l5_session`) | native path (`pmus_native_money_path`) | verdict |
|---|---|---|---|
| submit | `place_limit_buy`: preview -> fresh-BBO cross guard -> persist intent -> `POST /v1/orders` | `_post`: `transport.request("POST","/v1/orders")` | **duplicated, and the native one did not work** — `PmusTransport` has no public `request` (R4 predecessor, 2345z: AttributeError) |
| ACK interpretation | `order_id` extracted, explicitly "ACK is not fill" | `_order_id(ack)`, same doctrine | duplicated, consistent |
| order-state polling | `wait_for_fill` -> `_order_status` -> `GET /v1/order/{id}` in a bounded loop | **none.** `unbooked_fills()` asked `tp.fills()` behind `hasattr` | **absent in the native path.** `PmusTransport` has no `fills`, so the guard was always false and nothing was ever read back |
| terminal classification | `order_state_str`, `_filled_qty`, `terminal_rejection` (REJECTED + zero cum = terminal no-fill) | none; a failed cancel became `mark_unknown("CANCEL_FAILED")` | **duplicated concept, opposite outcome**: R4's FILLED and REJECTED orders were both left nonterminal |
| fill booking | `authoritative_fill` -> `{price, qty}` with the NO -> own-terms inversion; `Halt` if filled and no price | `_book_authenticated_fills` -> `eng.on_fill`, fed by the dead `fills()` seam | **native booked nothing in production** |
| cost basis | from the venue's own `avgPx`, inverted for NO | same intent, never reached | native had no basis: R4 showed `ours 0 / venue 10` |
| mandate terminal release | n/a (q1 predates the guard) | `release_terminal` writes `order_terminal`; **did not** `mark_terminal` the registry | half-done: $9.90 stayed reserved across R4 |
| inventory | caller's ledger | `eng.bk` via `on_fill` | single owner, but never updated |
| reconciliation | `preflight` + campaign-level position reads | `sweep_all`, `close_out` | duplicated |
| reporting | `ledger_append` | `mp.submitted` in memory only | **native invisible to the report** (fixed in `492aae00`) |
| taker completion | `wait_for_fill` | `_execute_taker` -> `venue.completion_fill(intent)` keyed on `intent["venue_order_id"]` | **third dead seam: nothing ever set that key**, so every completion returned None and failed closed |

Three separate consumer/producer mismatches, all invisible to 72 green tests
because every double implemented the seam production lacked: `tp.request`,
`tp.fills`, `intent["venue_order_id"]`.

## Consolidated shape, after

    LOCK5 intent
      -> mandate admission (GuardedTransport, one rule, all mutation verbs)
      -> PmusTransport.submit_body   preview -> echo -> ONE POST
      -> GET /v1/order/{id}          every nonterminal order, every tick
      -> pmus_order_lifecycle.classify   FILLED / PARTIAL / REJECTED_NO_FILL /
                                         CANCELLED_NO_FILL / WORKING / UNKNOWN
      -> canonical inventory         eng.on_fill at the venue's own avg price,
                                     own terms, once per increment
      -> registry.mark_terminal + order_terminal   releases ONLY the unfilled part
      -> LOCK5 decides again

`src/pmus_order_lifecycle.py` is the single classifier and reuses q1's
`order_state_str`, `_filled_qty` and `terminal_rejection` verbatim; it adds only
the own-terms average price, which q1 coupled to its own fixed `QTY`.

`NativeMoneyPath.on_book_state` now calls `resolve_open_orders()` **before**
`eng.on_book`, and returns `refused: ORDER_STATE_UNKNOWN, halted: True` when
any order cannot be read — never treating unreadable as unfilled.

## What the 25-window execution-aware replay established, and what it did not

`src/pmus_native_exec_replay.py` drives the frozen tape through
`NativeMoneyPath.on_book_state`, so every fill must arrive via a submitted
order and an authoritative read.

It **did** establish the third dead seam: before the `venue_order_id` fix,
**0 of the 44 frozen completions executed** — every one abandoned at
`_remainder_uncertain`. That is a wiring defect, now fixed.

It has **not** produced a trustworthy net-per-window figure, and the number it
currently prints is being **discarded rather than reported as a finding**:

* it reports `exec_mean 13.496` against `frozen_mean 6.1167253591`, i.e.
  execution constraints would *double* profit, which cannot be true;
* it reports `exec_completions 0` while earning more than the arm that
  completed 44 — so the P&L is coming from over-filled inventory, not from the
  policy;
* `filled_commitment` reaches $154 on a $50-clip window, against
  `cumulative_submitted` $343 — the harness fills far more than the frozen arm.

Cause, in my own harness: `ExecReplayVenue.advance()` calls `maker_fill` per
order per observation on the *remaining* quantity with a shared `qstate`, while
the frozen scorer calls it once per side per observation on the resting clip
and retires the quote. The harness double-counts fills. Until that is
equivalent to the frozen fill model on a window where nothing else differs, no
figure from it means anything.

**Consequence: items 6, 7 and 8 of the request are NOT established.** The
execution-aware mean, the delta from $6.1167253591 and the per-completion loss
reasons all wait on a fill model proven equivalent to frozen before execution
constraints are layered on.

Two constraints that must stay stated when that number does come:

* **Complement rejection is unmeasured.** R4's DOWN leg was rejected 94 ms
  after submission. That is one observation. `reject_rule` defaults to None and
  any figure produced with a rule is a sensitivity, never a forecast.
* **Fees stay where frozen puts them** (maker fills booked with `fee=None`), so
  the replay isolates execution and does not silently move the fee model.
