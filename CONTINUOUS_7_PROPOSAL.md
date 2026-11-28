# CONTINUOUS-7 — the same question, with both state seams closed

Research purpose, unchanged:

> **REAL FILL → COHERENT AUTHORITATIVE REFRESH → INVENTORY CHANGE →
> CHANGED L5 ACTION**

C6 reached link one and broke at link three. Both P0s are now closed.

## Hashes

```
executable_sha  a5ff91753be11d1aba8961c5bdf2a2fc76ebf2bdbb90b7486b67acb45bc856ef   NEW
envelope_sha    b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17   UNCHANGED
bundle_sha      28f117d3c794bdde94de44bdeb90f113c75e8a9bb180aba9886cda6310ac8bb6   NEW
```

The envelope has not moved once across seven windows. **No bound changed**:
these are state-integrity fixes, not risk changes.

## P0a — coherent authoritative state

A working order that leaves `active_orders` with no fill row and no terminal
row is **PENDING_RECONCILIATION**. `_refresh` re-reads authoritative sources
until the venue explains it, bounded by `MAX_RECONCILE_ATTEMPTS = 6` and
`MAX_RECONCILE_SECONDS = 30` — a deadman, not a sleep: every attempt is a
fresh read and nothing is assumed. **`decide()` is never reached while any
order is pending**, and at the bound the window stops with
`UNKNOWN_TERMINAL` rather than hand over a book it knows may be stale.
**Disappearance is never read as a fill.**

The read was widened to give the gate its evidence: terminal rows from order
history, and **`/v1/mytrades`**, a *different* authority from order history
carrying `order_id` and `client_order_id`. When two halves disagree, a third
read resolves it.

Fill application is **idempotent by construction** — every refresh rebuilds
inventory from the venue's whole fill list, so there is no "already seen" set
to drift. Five re-reads of one fill leave inventory identical.

### The C6 replay

| | |
|---|---|
| YES 0.01 @ 0.21 known working | ✓ |
| `active_orders` drops it, fill history empty | reads 1–2 |
| controller receives `yes=0/no=0` | **never happens** |
| controller emits `OPEN` | **never happens** |
| fill history exposes YES 0.01 @ 0.21 | read 3 |
| inventory | `yes_qty 0.01`, `spend 0.0021`, `residual +0.01`, `imbalance 0.01`, `n_fills 1` |
| **then** the unchanged controller runs | |
| **resulting L5 action** | **`COMPLETE` NO 0.01 @ 0.77** and **`EXTEND` YES 0.01 @ 0.20** |

C6 said `OPEN` / `OPEN` at that moment. The action changes, and it changes to
the one the inventory implies.

## P0b — terminal order withdrawal

`withdraw_orders` runs on **every** exit, fail-closed ones included: enumerate
strategy-owned working orders, cancel each by canonical venue `orderId`,
re-read authoritatively, repeat within `MAX_CLEANUP_MUTATIONS = 8` and
`MAX_CLEANUP_SECONDS = 45`, and end in **`STOPPED_FLAT_ORDERS`** or
**`STOPPED_WITH_LIVE_ORDERS` / `CLEANUP_UNCONFIRMED`**.

Cleanup mutations are **safety mutations**, counted apart from the strategy
budget. C6 ended with zero strategy slots; a cleanup borrowing from that
budget could never run when it is most needed. The path can **only cancel** —
no place, no SELL, no crossing, no forced flatten, no strategy change — so it
cannot create exposure. A structural test pins that.

Every report now carries `orders_live_after_stop` and `inert`, so **no window
can describe itself as inert while an order may still be executable**.

C5 and C6 are **not** retroactively cleaned.

## What the reporting caught before you could

The first verification returned **`terminal_history_readable: false`** with no
error: `order_history` reports unreadable if *any* requested status fails, and
the gate was asking for `("cancelled","closed","expired")`. The terminal
evidence was silently gone. The failure mode would have been safe but
pathological — a cancel-driven disappearance could never be explained, so it
would consume the bound and stop the window every time. Now it asks only for
`("cancelled",)`, the filter C6's own reconciliation proved the venue serves,
and which returned a venue-closed order under it.

## Cohort

```
cohort_id             COHORT-2026-09-18-C7
budget                6
consumed / remaining  0 / 6
predecessor           COHORT-2026-09-18-C6/ledger.jsonl
predecessor sha256    3996eb94bcdabf1e0b272d8dcf3282915f2570330453894985e3202bb1a265d3
predecessor consumed  6
predecessor intact    true
```

Fresh, chained to C6. C5 stays **3/6 with its UNKNOWN**, C6 stays **6/6**;
neither is recycled and neither ledger was altered.

## Proposed authorization statement

> I authorize ONE bounded live Gemini BTC5m continuous-inventory window using
> authorization ID `OWNER-2026-09-18-GEMINI-CONTINUOUS-7`, executable SHA
> `a5ff91753be11d1aba8961c5bdf2a2fc76ebf2bdbb90b7486b67acb45bc856ef`, envelope
> SHA `b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17`,
> bundle SHA
> `28f117d3c794bdde94de44bdeb90f113c75e8a9bb180aba9886cda6310ac8bb6`, and
> mutation cohort `COHORT-2026-09-18-C7` with its bounded budget of 6 slots,
> plus its separate bounded cleanup budget of 8 safety cancels. Its sole
> purpose is to observe real fill → coherent authoritative refresh → inventory
> change → changed L5 action. No pair-cost ceiling is added. Fail closed on
> unreadable or ambiguous authoritative state, on an unexplained
> disappearance, or on an actual risk-envelope violation. Terminal withdrawal
> may cancel strategy-owned resting orders and may do nothing else. No second
> window, automatic cohort, rollover, budget reset, scaling, SELL, IOC/FOK,
> taker crossing, forced flatten, strategy/controller change, risk-envelope
> change, or CONTINUOUS-8 without new approval. C5 stays 3/6 with its UNKNOWN,
> C6 stays 6/6, and the historical 12/12 ledger stays byte-for-byte.

**No CONTINUOUS-7 will run without that statement.**
