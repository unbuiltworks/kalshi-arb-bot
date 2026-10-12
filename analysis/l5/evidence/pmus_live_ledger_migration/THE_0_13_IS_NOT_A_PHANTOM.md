# The 0.13 DOWN is a REAL settled position, not a phantom

This contradicts the premise of PR #185 and it changes the correct remedy. It
is reported before any production change, per the owner's instruction to stop
and report an exact discrepancy.

## What PR #185 says

> On 2026-10-10 a cancelled, never-filled order left a `0.1300 DOWN` position
> in our book for `cpc-btc-updown-15m-2026-10-10-1345z`. The venue's own record
> for `D0VC6VJDMZ7J`: `cumQuantity 0`, `avgPx 0.0000`, `ORDER_STATE_CANCELED`.

That reading of order `D0VC6VJDMZ7J` is almost certainly right. The inference
drawn from it -- that the 0.13 therefore never existed -- is not.

## What the venue's own position record says

From the authenticated activity walk, the `POSITION_RESOLUTION` row for that
market (`updateTime 2026-10-10T14:00:15.101975936Z`, side
`POSITION_RESOLUTION_SIDE_LONG`):

    beforePosition                      afterPosition
      netPositionDecimal  -0.1300         netPositionDecimal   0.0000
      qtySoldDecimal       0.1300         qtySoldDecimal       0.1300
      qtyBoughtDecimal     0.0000         qtyBoughtDecimal     0.1300
      avgPx                0.3500         avgPx                None
      cost                 0.0455         cost                 0.0000
      realized             0.0000         realized            -0.0455
      outcome              "No"

`0.13 x 0.35 = 0.0455` exactly. The venue held a real short-YES (= DOWN/NO)
position of 0.13 shares at an average price of $0.3500, and resolved it on
2026-10-10T14:00:15Z for a realized loss of **-$0.0455**.

The account really did own those shares. A *different* fill produced them;
`D0VC6VJDMZ7J` being cancelled-unfilled rules out one source, not all of them.

## So the defect is the missing COST, not a fictitious QUANTITY

The engine has been right the whole time, in its own words:

> quantity with no cost basis and no fill that attested a zero price; **the
> cost of these shares is unknown, not zero**

Unknown, and now known: `0.0455`. The book's quantity was never the problem.

## Why this matters more than $0.0455

`adopt_flat_account` -- PR #185's route to clearing this -- **posts no P&L**.
Clearing 0.13 that way would delete a real, venue-confirmed realized loss and
start the new ledger from a basis known to be wrong. The entire purpose of
moving to a venue-reconciled ledger is to stop exactly that. The dollar amount
is trivial; treating a confirmed loss as a bookkeeping artifact is not, and it
is the second time a cost-less quantity has appeared (the engine's own log
cites `2.25 DOWN shares at 2045z with no cost`).

## What the migration does to it anyway

The venue now reports `0` for this market, because the position genuinely
settled. A ledger reconstructed from fresh venue evidence therefore contains no
1345z position at all: the incoherent book disappears, the 38-and-counting
`SETTLE_REFUSED` loop ends, and the `UNCERTAIN` disarm clears -- not because
the position was fake, but because it is **settled and gone**.

That is the right outcome by the right mechanism. It needs no flat-anchor
adoption and no phantom deletion.

## Consequence for the new ledger's opening balance

The reconstructed ledger starts at realized `0.00` with no history, because
venue position records are the only authoritative source and they are
point-in-time. Two figures must therefore be carried as an explicit opening
note rather than silently dropped:

  * `-0.08550000`  the last log-derived `realized_attributed` floor, cache-
                   derived, now unreproducible;
  * `-0.0455`      this market's venue-confirmed settled loss, which is
                   already inside the ten-market settlement reconciliation at
                   `analysis/l5/pmus_manual_audit/results/`.

They overlap by an unknown amount and MUST NOT be added together. Neither is
realized-with-attributed-cashflow.
