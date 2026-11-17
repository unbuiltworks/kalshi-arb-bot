# PMUS multi-leg atomicity — verification

**Date:** 2026-11-17. **Question:** does Polymarket US support atomic,
all-or-none execution across complementary YES/NO contracts, such that a
BTC5m pair either completes on both legs or neither?

**Verdict: no. There is no venue-guaranteed atomic pair execution.** The
documented findings are confirmed. No change to the order engine is
required or warranted, and none was made.

## What the venue documents

| Mechanism | What it guarantees | Source |
|---|---|---|
| `tif: TIME_IN_FORCE_FILL_OR_KILL` | all-or-none for **that one order's own quantity** | PMUS order API, `tif` enum |
| `tif: TIME_IN_FORCE_IMMEDIATE_OR_CANCEL` | immediate partial fill, remainder cancelled — for that one order | same |
| `POST /v1/orders/batched` | "Create up to **20 orders** in a single request" | `api-reference/orders/create-multiple-orders` |
| `POST /v1/orders/batched/cancel` | "Cancel up to 20 orders in a single request" | `api-reference/orders/cancel-multiple-orders` |

The batched create endpoint returns `{"createdOrderIds": ["<string>"]}` —
a **plain list of independently created order ids**. Nothing in the
endpoint's documentation offers all-or-none semantics, linked orders,
contingent orders, a combo instrument, or a rollback of one leg when
another fails. There is no language of atomicity anywhere on it.

So the distinction the owner asked for holds exactly:

- **FOK on one order** is a genuine all-or-none guarantee, scoped to that
  order's quantity. It says nothing about any other order.
- **Batching N FOK orders** submits N independent all-or-none orders in
  one HTTP request. Each is matched on its own. Any subset can fill.
  Request-level validation may well be atomic — a malformed batch is
  rejected whole — but **gateway validation is not exchange execution**,
  and conflating the two is the error this check existed to prevent.

Not pursued, per the owner's constraint and because none is documented
for this venue: bonded FOK, FIX linked orders, combo instruments,
international split/merge mechanisms.

## What our adapter does

Verified by inspection of `src/l5_pmus_transport.py`,
`src/pmus_micro_executor.py`, `src/l5_btc5m_execution.py` and
`src/l5_order_body.py`:

- **No batched submission path exists.** `batched` appears nowhere in the
  execution path. Every order goes out as its own `submit(intent)` call.
  `src/l5_preview_session.py` lists the three `batched` routes only as
  known venue endpoints, not as code that calls them.
- **Each order carries its own idempotent client order id** and is
  reconciled on its own venue-confirmed events, by exact id, through
  `/v1/order/{id}` — the route the repo already established works, where
  `/v1/orders/{id}` 404s and `/v1/orders` 501s.
- **`src/l5_order_body.py` knows the documented `tif` values** and
  refuses undocumented ones, and refuses `post_only` together with FOK
  (an order that must rest cannot also be all-or-none-immediate). The
  default body is byte-identical to the one proven in production.
- **One-leg fills are a normal state, not an error.** The controller's
  completion path, the residual phase projection
  (`src/l5_residual_phase.py`) and the inventory ranking all take a
  one-sided fill as an ordinary input.

## Can we use batch FOK in `l5-btc5m-live` without rebuilding the engine?

Technically yes — one order body per leg, posted to a different route.
**But it buys nothing for this programme, so it is not on the path to the
live test.**

1. It gives **no atomicity**, which was the only reason to want it.
2. The BTC5m strategy is **maker-first**. A maker order must rest before
   matching (`participateDontInitiate`); FOK is immediate-or-dead. The
   two are mutually exclusive, so batch FOK cannot carry the entry legs
   at all — only a crossing completion, which is one order, and one order
   needs no batch.
3. It would make reconciliation harder, not easier: one response
   carrying several ids, each still needing its own confirmation.
4. Saving one HTTP round trip is not a measured constraint anywhere in
   this system.

**Batch FOK is optional and is not a prerequisite for live validation.**
The existing maker-first hybrid stays primary. The engine is unchanged.

## Consequences already implemented

Because nothing is bonded:

- Complementary YES/NO orders are treated as **independent under all
  circumstances**.
- Residual detection is driven by **venue-confirmed fills**, never by an
  assumed hedge.
- A completion that does not fill leaves the residual exactly as it was —
  pinned by `TestResidualIsIndependent` in
  `tests/test_l5_completion_crossing.py` and
  `test_an_unfilled_completion_leaves_the_residual_exactly_as_it_was` in
  `tests/test_l5_btc5m_live_loop.py`.
- A cancellation never reverses an execution — pinned by
  `test_a_cancel_that_loses_to_a_fill_is_booked_not_assumed_away`.
- Uncertain order states fail closed and stop submission until every such
  order reads clean — `TestAnUncertainOrderFailsClosed`.
