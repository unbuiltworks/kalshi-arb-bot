# L5_MAKER_EXECUTION_V1 — preregistered lifecycle

Registered **before** any maker order is sent. Nothing below may be changed
to improve a fill rate after the fact; a change is a new version with its own
registration.

## Why passive actions currently produce nothing

`pmus_translate` computes `restable = l5px < our_touch` — L5's price is
strictly inside our spread, so copying him means RESTING, not crossing. For
a restable action the frozen rule then asks the observed interaction class:

```
got = authenticated.get(row["action_key"])
if klass is None:
    return {"ok": False, "reason": T_UNEVALUABLE, "mode": MAKER}
```

In replay that refusal is right and must stay: with no interaction evidence
we cannot prove a hypothetical resting order would have filled, and counting
it as filled is how a backtest lies.

**Live, the same refusal is backwards.** We do not need to prove the fill.
We can place the order and let PMUS answer. `STRICT_NOT_EVALUABLE` is
therefore a statement about *evidence*, not about *executability* — and
Probe 06 spent 43 of 52 actions on it while taking the other 6.

So `L5_NATIVE` is untouched and stays the immutable control. Maker execution
is a **second treatment** that consumes exactly the actions the frozen rule
labels `mode: MAKER` and refuses `T_UNEVALUABLE`, and supplies the missing
evidence empirically instead of assuming it.

## The ladder

Reusing `L5_PASSIVE_SURFACE_SHADOW_V1`'s states, which exist precisely so a
maker order is never assumed filled:

    INTENT          the frozen rule said MAKER at L5's price. Nothing more.
    PLACED          a post-only order is resting at the venue.
    BOOK_REACHED    the touch later moved to or through our price.
                    Executability. NOT a fill.
    TRADE_FLOW      a print occurred at or through our price. Real opposing
                    flow. Still NOT our fill.
    FILLED/PARTIAL  the VENUE says so on an authoritative order refresh.
                    The only thing that moves inventory.
    SETTLED         the market resolved.
    REALIZED        account cash confirmed it.

Nothing below `FILLED` books inventory, and `FILLED` comes only from
`/v1/order/{id}`, never from book movement.

## Placement rules

- **P1 PRICE** — rest at exactly L5's price. Never improve it, never chase.
  Copying his price is the replication; improving it is a different policy.
- **P2 POST-ONLY** — `participateDontInitiate: true`, already what
  `l5_order_body.from_intent` emits for `mode == MAKER`. A post-only order
  the venue would have to cross is rejected rather than silently taken.
  Live 2026-09-05/06, 24 of 24 NO-side post-only orders priced in NO terms
  were rejected; the wire price stays in long (YES) terms.
- **P3 SIZE** — λ-scaled desired quantity, floored to the venue increment.
  Unchanged from the taker path.
- **P4 ENVELOPE** — the same $5 gates, reading `risk_exposure_usd`. A
  resting order is counted as if it fills in full.

## Lifecycle rules

- **L1 NO REPRICE** — a resting order is never moved. If the book drifts
  away, it rests or it is cancelled; it is not re-pegged.
- **L2 NEVER CROSS TO FILL** — an unfilled maker order is never converted
  into a taker order. Only a *new* L5 action that the frozen rule
  independently classifies `mode: TAKER` may produce an aggressive order.
  This is the rule most likely to be eroded by a desire for fills, so it is
  written first and tested.
- **L3 CANCEL ON L5 REVERSAL** — cancel when L5's own later observed
  behaviour takes the opposing side in the same market. His reversal is
  evidence; our impatience is not.
- **L4 WINDOW GUARD** — cancel at the existing no-new-orders boundary so no
  resting order is carried into settlement.
- **L5 AUTHORITATIVE TERMINALITY** — before any cancel, refresh the order.
  A terminal order is never cancelled. Any uncertain mutation stays
  fail-closed.

## Evidence preserved per maker order

Kept apart, never collapsed:

| field | source |
|---|---|
| L5_NATIVE decision (side, price, size) | frozen rule |
| PMUS maker translation (price, desired, executable, quantization delta) | translation layer |
| post_only flag as sent | order body |
| rest duration (placed, terminal, seconds) | our clock, both ends recorded |
| touch at placement / best touch while resting | authenticated WS book |
| BOOK_REACHED, TRADE_FLOW | authenticated WS book |
| actual fill (cum, leaves, avgPx) | `/v1/order/{id}` |
| maker/taker classification at fill | venue commission fields |
| commission / rebate | venue, separately, never netted |
| inventory consequence | inventory store, after the fill |

## Obstruction, measured before anything is relaxed

One live order and $5 may mechanically prevent faithful maker replication:
a resting order occupies the slot for its whole life, where a taker order
occupies it for milliseconds. That obstruction is **measured, not assumed**:

- `maker_blocked_by_one_live` — maker intents refused MAX_LIVE_ORDERS
- `maker_blocked_by_envelope` — refused on the $5 risk bound
- `slot_occupancy_s` — seconds the single slot was held by a resting order
- `intents_lost_while_resting` — L5 maker actions arriving while blocked

No limit changes until these numbers exist.
