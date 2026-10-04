# FAVORITE_DCA — preregistration (Variants A and B)

Frozen before any result was computed. This lane is **not** L5 replication:
it is an independently testable strategy inspired by the same
continuous-inventory concept, and nothing it finds may be read back into the
L5 behavioral findings (where FAVORITE/FLIP was rejected and inverted — see
`analysis/l5/favorite_flip/FAVORITE_FLIP_RESULT.md`).

No live orders. No parameter sweep. No existing frozen experiment is touched.

## Variant A — FAVORITE_DCA (control)

1. Evaluate once per **60 s** of window time.
2. Favored outcome from authenticated PMUS state only: the **executable mid**
   `(bid + ask) / 2` of the single long-side book. `mid > 0.50` -> UP favored,
   `mid < 0.50` -> DOWN favored, `mid == 0.50` -> **no allocation** (recorded
   as a skip, not resolved by a tiebreak).
3. Allocate **$1** of notional to the favored outcome, **taker**, against the
   contemporaneous executable touch.
4. On a leadership flip, later allocations go to the new favorite. Repeated
   flips are allowed. Nothing is unwound.
5. Inventory is managed as **aggregate UP/DOWN**, never as independent pairs.
6. No complement-cost requirement (`.96` is neither imposed nor altered), no
   BTC spot, no trade-imbalance cancellation.

0.50, 60 s and $1 are **given**, not fitted, and are not varied in this run.

## The venue mechanics this rests on (already established, not re-derived)

One binary instrument per window; the book is the long (UP) side.

- A taker **UP** buy pays `ask` and consumes `ask_qty`.
- A taker **DOWN** buy pays `1 - bid` and consumes `bid_qty`, because
  `DOWN ask = 1 - UP bid` (`src/pmus_q1_executor.would_cross`).
- A resting **DOWN** bid at `p` is an UP **ask** at `1 - p`; a resting **UP**
  bid at `p` is an UP bid at `p`. Both are therefore modelled in one book.
- Fees come from `src/pmus_fees.py` (taker theta 0.0695 from 2026-09-17).
  Maker rebate `0.0125*p(1-p)` is **modelled and reported on its own line as
  UNVERIFIED**, is never netted into cost, and is excluded from the net
  figure that decides anything.

## Variant B — FAVORITE_DCA_OPPOSITE_REST

Identical periodic favored-side taker allocation, plus **one** passive bid on
the currently **non-favored** outcome, $1 notional, re-evaluated each period.
On a flip the passive quote moves to the newly non-favored outcome. Existing
inventory stays in the portfolio. An opposite-side fill is **not** called
locked profit.

### The reservation price — frozen here, derived, not fitted

A matched UP+DOWN share pays exactly **$1.00** at settlement. So a complement
share is worth acquiring only at a price that leaves the matched pair
non-negative after the fees already paid:

```
reservation = 1.00  -  avg_cost_favored_side  -  taker_fee_per_share_paid_so_far
```

with the modelled maker rebate deliberately **excluded** (it is unverified, so
including it would let an unverified credit buy a worse price). The quote is

```
price = min(reservation, opposite_ask - 0.01)      # post-only, never crosses
```

and is suppressed when `reservation < 0.01` or no favored inventory exists
yet. A price change of >= one tick is a **cancel and a new order**: queue
position is lost, exactly as `PMUS_PREFILL_CANCEL_STUDY` fixed.

This has one free quantity (the fee allowance) and it is read from the
authoritative fee module, not chosen.

### Fill discipline — the existing methodology, unchanged

**No touch = fill.** A resting order fills only when:

1. its price has become the touch, at which point `queue_ahead` is set from
   the depth observed at that level;
2. trades that consume **our** side deplete that queue (`dir: buy` consumed
   the ask, `dir: sell` consumed the bid; unknown-side trades fill nothing);
3. a print strictly **through** our price proves the level cleared and zeroes
   the queue (the trade-through rule that corrected the earlier study from 16
   fills to 175).

Depth below the touch is **not observable** in this tape (top-of-book only),
so an order resting behind the touch accrues no modelled fill until the book
comes to it. That is a stated limitation, not an approximation.

## Window eligibility — fixed before running

A window is scored only if: span >= 600 s, the last book lands within 30 s of
the scheduled window end, and the terminal touch is `0.99/0.99` or
`0.01/0.01`, which is what makes the settlement outcome determinable from the
tape. Periods with no book within +/-5 s of the 60 s mark are recorded as
skipped allocations and counted.

## Both replays required before any exit is designed

1. **hold to settlement** — no exit at all;
2. the full **mark-to-executable trajectory** through each window, so the
   user's actual manual cash-out rule can be applied later without re-running
   the simulation.

The exit is **not invented here**.

`mark-to-executable` is defined as: matched shares at **1.00** (riskless,
holding to settlement costs nothing) plus the residual at its executable exit
net of taker fee. The strict full-liquidation value, which also unwinds the
matched leg at `1 - spread`, is reported beside it so the choice is visible.

## Reported per window and in aggregate

gross settlement P&L | fees | verified rebates (separately, and currently
**$0 verified**) | net P&L | maximum capital | maximum residual | maximum
drawdown | peak executable P&L | time of peak | terminal P&L | leadership
flips | allocations.

$3 and $5 per window are computed **only** as a linear capacity implication
of the measured per-window net, explicitly not as validated profit.

## A vs B comparison, on identical windows

net settlement P&L | peak executable P&L | max drawdown | capital deployed |
matched inventory | residual | flips | opposite fills | fees | modelled maker
rebates | net per window — and the four mechanism questions: whether opposite
resting (a) lowers average total acquisition cost, (b) reduces residual risk
after flips, (c) changes capital requirements, (d) improves net P&L rather
than merely making the inventory look balanced.

## Labels

Everything here is `historical replay` on an authenticated tape. Nothing in
this lane is submitted, filled, settled or realized.
