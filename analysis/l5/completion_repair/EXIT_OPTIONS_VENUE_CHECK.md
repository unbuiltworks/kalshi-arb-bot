# Stranded-inventory exit options: what PMUS actually supports

Read-only. Sourced from this repository's own recorded venue evidence, not
from a live call: the PMUS credentials live only on the Railway service, so a
fresh API probe is not possible from a development session.

## The three requested options, verified

### 1. Buy the complementary outcome, post-only maker — SUPPORTED, and it is
the path that was repaired.

This is `l5_book_completion.decide` -> `TS.complement_order` ->
`FaithfulExecutor.handle`. Every quote is post-only and `_would_cross`
refuses any price that would take the touch.

### 2. Sell the existing position — NOT AVAILABLE in the observed venue
vocabulary.

The order vocabulary, from authoritative `/v1/order` rows read by the
read-only recovery service on **2026-09-26** and pinned in
`l5_live_execution.VENUE_SIDE_WORDS`:

```
side ORDER_SIDE_BUY  + outcomeSide OUTCOME_SIDE_YES + ORDER_INTENT_BUY_LONG
side ORDER_SIDE_SELL + outcomeSide OUTCOME_SIDE_NO  + ORDER_INTENT_BUY_SHORT
```

Across `src/`:

| token | occurrences |
|---|---|
| `ORDER_ACTION_BUY` | 5 |
| any `ORDER_ACTION_SELL` | **0** |
| `ORDER_INTENT_BUY_LONG` | 12 |
| `ORDER_INTENT_BUY_SHORT` | 14 |
| any non-BUY intent | **0** |

`l5_order_body.build` hardcodes `"action": "ORDER_ACTION_BUY"`, and both
intents the venue has ever shown us are BUYs. The venue's `side` word
describes the YES token, so **"sell" is how this venue spells "buy NO"** —
`l5_defensive_completion` maps `DOWN -> {intent: ORDER_INTENT_BUY_SHORT,
side: ORDER_SIDE_SELL}` for exactly that reason.

**So "selling the position" is not a distinct exit here. Reducing a DOWN
position is done by buying UP, which is the complement purchase in option 1.**
A matched pair then redeems exactly $1.00, which is why a pair at or below
$1.00 is a non-negative exit and why that became the hard line in the gate.

**Limit of this finding.** Absence from our recorded vocabulary is strong
evidence, not proof: we have never issued a close/reduce request, so we have
never seen it refused either. A definitive answer needs a read-only probe
from the service, where the credentials are — proposed, not performed:
`GET /v1/markets/<slug>` for an order-type list and one
`ORDER_ACTION_*`-rejecting dry request against a resting-only endpoint. Until
that runs, the sell branch must record itself as
`SELL_NOT_SUPPORTED_BY_VENUE` rather than be built speculatively.

### 3. Hold to settlement — SUPPORTED, and it is the default.

Every refusal in `decide` is a hold, each with its own named reason.

## What this means for the unified exit decision

The requested three-way evaluator collapses to **two live branches and one
recorded-unavailable branch**, because the venue offers one economic action:
buy an outcome. The remaining real choice is *how* to buy the complement:

| branch | mechanism | ceiling | status |
|---|---|---|---|
| maker complement | `l5_book_completion` | **1.00** hard | repaired, disarmed |
| crossing complement | `l5_defensive_completion` | 1.20 | flag-gated, NOT authorized |
| hold | the default refusal | — | always available |
| sell to close | none observed | — | `SELL_NOT_SUPPORTED_BY_VENUE` |

Crossing is explicitly out of scope ("Do not enable taker crossing"), so the
authorized decision space for stranded inventory is exactly: **maker
complement at a pair <= $1.00 within the owner's residual budget, or hold.**
That is what the repaired gate now decides, every 5 s, for as long as the
inventory is unmatched and the window is open.

## Per-action economics the record should carry

For each evaluation, with the figures the engine already has:

* **executable price** — `complement_order`'s post-only quote at the
  completion depth, with the touch recorded beside it.
* **available depth** — `bid_shares` / `ask_shares` from the authenticated
  book snapshot, already passed in.
* **fees** — `commission: 0.0000` on every maker fill this account has had;
  taker theta 0.0695 is not in scope because crossing is disabled.
* **locked P&L if filled** — `qty * (1 - pair_own_cost)`, now recorded as
  `locked_pnl_usd`.
* **expected loss if unfilled** — the basis of the unmatched leg if its
  outcome loses: `held_cost_usd` for the whole stranded quantity. On 1445z
  that was -$0.7067 and it is what actually happened.
* **fill probability** — **NOT ESTIMABLE from our data.** PMUS publishes no
  print endpoint (`/trades`, `/history`, `/ohlc`, `/candles`,
  `/price-history` all 404); only a cumulative `sharesTraded` lower bound
  exists. So joint maker-fill probability cannot be measured from replay, and
  no number should be recorded as though it were. What the record can carry
  honestly is queue position evidence: our own price versus the touch, and
  the displayed size ahead of us.

The last point is why the live validation's objective is a **fill** and not a
submission, and why resting at the touch is argued from the asymmetry of
costs rather than from an estimated fill rate: an unfilled completion costs
the whole basis (-$0.37/unit on 1445z) while resting at the touch instead of
two ticks back costs at most 2 ticks (-$0.02/unit) — 18x in favour of queue
position.
