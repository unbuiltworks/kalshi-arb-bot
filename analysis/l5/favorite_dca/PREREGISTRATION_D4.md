# D4_TAKER_ENTRY_MAKER_EXIT — preregistration

Committed **before** any D4 number was computed. Shadow/replay only, $1
scale, the same 28 untouched windows. D0's directional controller **and its
taker entries are frozen completely**. D3 closed the maker-entry timing
question and no entry wait is searched here. No threshold sweep, no scaling,
no live orders.

## What changes: the exit execution, and nothing else

At the exact point D0 generates its reversal exit, D4 places a **passive
maker exit at the economically correct own-side touch** instead of crossing:

- a long **UP** residual is sold by resting an **offer at the UP ask** —
  filled when an incoming buyer lifts it, so we receive the ask rather than
  the bid;
- a long **DOWN** residual is sold by resting an offer at the **DOWN ask**,
  which in the single long-side book is a **bid at the UP bid** — filled
  when an incoming seller hits it.

Fill discipline is the existing one, unchanged. **No touch = fill.**
Queue-ahead is taken from the depth displayed when our level becomes the
touch, depleted only by trades consuming our side (`dir: buy` lifts an
offer, `dir: sell` hits a bid), and zeroed by a print strictly through our
price.

## The maker-exit lifetime — from risk mechanics, not P&L

Two bounds, both built from quantities **already frozen elsewhere**. No new
number is introduced and no lifetime is searched.

**Bound 1 — flat before the next decision.** The controller re-decides every
60 s. Carrying a residual the reversal has already condemned across a
decision boundary would mean the next decision is taken with inventory that
should not exist. So the exit must be complete within the period that
triggered it, and the passive order's deadline is

```
next 60 s evaluation mark  −  0.713 s
```

where 0.713 s is D3's lifecycle reserve (measured cancel RTT p90 0.612 s +
one median book inter-arrival 0.101 s) kept back so the aggressive fallback
can still be executed **inside the same period**.

**Bound 2 — a risk abort at one further trail-width.** The reversal fired
because the favored side gave back `spread_at_peak + 2 × taker_fee(peak)`.
If, while we are resting, it gives back **one more** such width from the
level at which the exit triggered, the passive attempt is abandoned
immediately. The magnitude is the trail already frozen in
`PREREGISTRATION_C.md`; it is reused, not re-derived.

The lifetime is `min(bound 1, bound 2)`. **Bound 1 is the operational rule
(be flat before deciding again); bound 2 is the risk rule (stop paying for
price once the move has doubled).**

The known cost of this choice, stated in advance: D4 can remain exposed for
up to ~59 s after a reversal that D0 acts on immediately. **All of that
adverse movement is charged to D4** — the fallback is priced at the book in
force when it executes, never at D0's earlier exit price — and
`adverse_movement_while_waiting` is reported per exit.

## The fallback, in full

If the passive exit does not defensibly fill within the lifetime:

1. cancel;
2. require **confirmed cancellation** — modelled as the measured cancel RTT
   p90, 0.612 s;
3. refresh authenticated state — one median book inter-arrival, 0.101 s;
4. execute D0's risk exit **aggressively** at that refreshed state, if the
   exit condition still holds.

"Still holds" for a reversal exit is simply **that a directional residual
still exists** — the reversal itself already fired and is not re-tested.
That definition is fixed here so it cannot be reinterpreted afterwards.

Partial passive fills are kept, and only the unfilled remainder goes to the
fallback.

## Reported

Maker-exit capture rate, price improvement, avoided taker fees, adverse
movement while waiting, fallback count, gross before fees, total fees, mean
and median net per window, positive fraction, worst window, leave-best-1,
leave-best-3, drawdown, residual and capital — beside frozen D0 on the same
windows.

Plus the **matched counterfactual**: D0's aggressive taker execution on
**exactly the exits D4 captured passively**, so cheaper exit execution is
separated from the selection effect of which exits a passive offer wins.

## Advancement gate

Unchanged from D3: positive **mean AND median AND leave-best-1 AND
leave-best-3** before any scaling study. Nothing else counts as passing.
