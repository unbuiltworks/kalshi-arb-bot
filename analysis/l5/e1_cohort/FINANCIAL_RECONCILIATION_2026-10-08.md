# E1 cohort — financial reconciliation, 2026-10-08

| | |
|---|---|
| **status** | E1 **DISARMED**. Read-only reconciliation. Nothing armed, reset or changed. |
| session | `E1-COHORT-20261008T005816Z-051888d8` |
| windows | **3 of 12 consumed, 9 remaining** (not 11) |
| placements | **40** (20 in 1215z, 20 in 1230z) |
| attribution | **by order id**, from E1's own append-only tape |

`LABEL: SETTLED, VENUE-CONFIRMED. The engine's own settlement ledger holds
zero rows, so no row in E1's ledger carries REALIZED.`

---

## 1. The headline

**All twenty 1215z first legs FILLED, inside 2.47 seconds, and the position
settled in our favour.** No complement was ever submitted. This is the same
runaway as 1230z, except that in 1215z the orders were *hit*.

| | 1215z | 1230z |
|---|---|---|
| orders | 20 | 20 |
| terminal state | `ORDER_STATE_FILLED` ×20 | `ORDER_STATE_CANCELED` ×20 |
| sum `cumQuantity` | **2.60** | 0 |
| each | 0.13 @ wire 0.49 (`avgPx` 0.4900) | 0.13 @ wire 0.54–0.57 |
| outcome side | `OUTCOME_SIDE_YES` | `OUTCOME_SIDE_NO` |
| our role | `passiveExecution` ×20 — **maker on every fill** | never traded |
| submitted | 12:18:05.286 → 12:18:07.753 | 12:33 window |
| complement submitted | **no** | **no** |
| appears in any trade row | 20 of 20 | **0 of 20** |

Post-only held: every one of the twenty fills is recorded as our
`passiveExecution`, so the maker-first restriction was never violated. The
restriction worked. The concurrency invariant did not.

## 2. The money, E1-attributed

From the venue's own position-resolution record for the market, whose net
position matches E1's filled quantity **exactly** (`2.6000` vs 2.60):

```
cost                $1.2740     = 2.60 x 0.49, exact
cash_value          $2.6000     resolved "Yes" at $1.00/share
avg_px              0.4900
our commissions     $0.0000     per fill, venue-reported, 20 of 20
settlement fees     null        field absent on the row
afterPosition.cost  $0.0000     position closed
```

| | USD |
|---|---|
| debit (cost of 2.60 shares) | −1.2740 |
| settlement credit | +2.6000 |
| our maker commissions (venue-confirmed) | 0.0000 |
| rebates | none claimed, none credited |
| **net** | **+1.3260** |

**The one gap, stated rather than netted:** the resolution row's `fees` is
`null`, not `0`. Other strategies' resolution rows on this account *do*
carry a populated `fees` (e.g. `0.1181`), so `null` may mean no fee was
charged or may mean the field was not populated for this row. The net above
is therefore **+$1.3260 less any unreported settlement fee**, which the
venue has not disclosed on this record. It is not asserted as zero.

## 3. A figure I published and have withdrawn

I earlier reported "debits $1.2740, fees $1.4595, settlement $5.6900."
**The $1.4595 and $5.6900 are withdrawn.** They came from summing the money
fields of every activity row naming a cohort slug. Those rows include the
**counterparty's** aggressor commission — one carries
`commissionNotionalTotalCollected: 28.9200` — and other strategies' activity
in the same markets. E1 was the resting maker on all twenty fills, so none
of it is E1's. Only `$1.2740` survives, because it comes from our own
position record and reconciles exactly to 2.60 × 0.49.

**The PMUS account is shared.** The activities feed holds 1,100 rows: 901
trades, 92 position resolutions, 5 transfers, 2 deposits, mostly other
strategies at sizes like 16.21 and 23.49 shares. Nothing may be attributed
to E1 except through an order id, and account-level settlement cashflows
must never be apportioned to E1 by inference. Size alone is not enough
either: 0.13 is E1's signature, but the id is the proof.

## 4. Why no balance cross-check is offered

`currentBalance` is `241.62915`. It cannot corroborate a $1.33 E1 result,
because the same account took deposits and other strategies' trades over the
same period. A balance delta here would be an **UNATTRIBUTED** cashflow in
the strict vocabulary, and is not used.

## 5. Exposure

Zero, confirmed three independent ways:

```
/v1/orders/open          {"orders": []}
/v1/portfolio/positions  {} / availablePositions []
/v1/account/balances     openOrders 0, assetNotional 0, unsettledFunds 0,
                         balanceReservation 0, marginRequirement 0
```

The 1230z twenty are `ORDER_STATE_CANCELED` with `cumQuantity 0` and appear
in **no** trade row, which is independent confirmation that the cancel
caught them before any fill. The 0100z window placed nothing.

## 6. What the defect actually cost, and what it risked

The worst case I gave during the safety stop — $1.1804 — described only the
resting 1230z orders. It was the wrong number for the wrong window.

In 1215z the runaway **filled**: twenty first legs in 2.47 seconds, a
**2.60-share unhedged directional position**, 20× the intended single-leg
size, with no complement and no pair economics whatsoever. It resolved YES
and paid +$1.3260.

**That is luck, not a control.** The identical sequence on a "No" resolution
loses the full $1.2740. The dollar bounds held throughout — $1.2740 is
inside the $1.56 per-window and $5.00 cohort limits — because the bounds are
denominated in dollars and the failure was in **concurrency**. A gain
produced this way is not evidence of a working strategy and is not counted
toward the $480/day target: there was no matched pair, no spread capture and
no rebate. It was a directional bet the engine was never authorised to take.

## 7. Complement management

**Never executed.** Zero complement orders in either window, no ledger row
mentioning one, and no second venue order id. With 2.60 shares of filled
first leg sitting in 1215z, complement logic had every reason to act and did
not — because the cohort's process had already been stopped and disarmed by
then, and because the runaway consumed all twenty attempt slots on first
legs, leaving no slot for a complement even had it tried.

So the fee-inclusive ≤$0.96 pair ceiling, the tranche sizing and residual
handling remain **completely untested against a real fill**. That is now the
single largest unproven area in the engine.
