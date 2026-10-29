# RESIDUAL_CAP_k2 / PROSPECTIVE_NET probe — HARD STOP

**ARMED 13:08:20Z. DISARMED 13:21Z. ~13 minutes, one armed market, ZERO
complete informative settled markets.** Stopped on registered hard stops, not
on P&L. No code, policy, k or envelope change was made while armed, and none
has been made since.

## Stop reason

Three registered conditions, all on the same root fault:

1. **Accounting/reconciliation discrepancy.** Authoritative cash moved
   **−$0.54000** while the ledger posted **nothing**.
2. **Unresolved authoritative order state.** `ORDER_STATE_UNCERTAIN` ×89,
   `PENDING_RESOLVE_ERROR` ×49, `PROGRESS_ERROR` ×15.
3. **Settlement refused on an incoherent book**, 91+ times:

```
L5_LIVE_SETTLE_REFUSED  market 1300z  reason INCOHERENT_BOOK
  violation      QUANTITY_WITHOUT_COST
  side UP   qty 0.9000   cost 0   fills_booked 0
  why  "quantity with no cost basis and no fill that attested a zero price;
        the cost of these shares is unknown, not zero"
  submissions_allowed false
  why  "a settlement computed against an incoherent book posts a wrong
        realized P&L and moves the loss halt with it"
```

**The guard worked.** It refused to settle rather than post a number it could
not justify, blocked further submissions, and the executor then refused to
re-arm itself. Realized P&L is **unchanged at −1.10260000** — nothing false was
booked, and the loss halt did not move on a fiction.

## Authoritative venue truth

```
drift       {"1300z|UP": {"ours": "0.9000", "venue": "0"}}
venue_net   {}                       gross_net_agrees  false
open_orders_at_venue 0               quarantined  []
```

**The venue reports no position for 1300z at all**, and the market is
`MARKET_STATE_EXPIRED`. Our book holds 0.90 UP that the venue does not.

## Cash

| | |
|---|---|
| pre-arm authoritative (12:52:03Z, flat) | $60.59505 |
| post-stop authoritative (13:34:27Z) | **$60.05505** |
| cash change | **−$0.54000** |
| ledger change | **0.00000** |
| **unreconciled** | **−$0.54000** |

A reading consistent with every observation — and **not** posted, because it
is a hypothesis and not an authoritative per-order fact: 0.90 UP was bought
for ~$0.54 (≈$0.60/contract), 1300z expired with UP losing, the position
redeemed to zero, and the venue therefore shows nothing. The cost basis never
reached our book because the `/v1/order` reads were failing, so the book kept
the quantity and lost the price. Confirming that needs the authoritative order
rows, which is the first step of any repair.

## Market 1300z, as far as it is observable

| | |
|---|---|
| NATIVE-eligible actions | 36 |
| submitted | 2 |
| cap binds | **34** |
| other refusals | 82 `MAKER_ONLY_NO_TAKER`, 89 `ORDER_STATE_UNCERTAIN` |
| fills | not establishable — no cost basis, `fills_booked 0` |
| matched / residual economics | **not computable on an incoherent book** |
| settled P&L | **none — settlement refused** |
| gross cash deployed (book) | $0 (the book never received the cost) |
| fees / credited rebates | none observed |

**Action retention reads 0.056, and that number must not be attributed to the
policy.** With 0.90 UP booked and orders unresolvable, prospective residual
stayed pinned and the cap refused nearly everything. The retention is a
*consequence* of the fault, not a measurement of `PROSPECTIVE_NET`. On the one
prior clean market the same family scored 0.679 realized-only; this probe
produced no clean measurement at all.

## What this probe does and does not say about the policy

**Does not implicate it.** The fault is order-state resolution and cost-basis
recovery under failing venue reads. The gate never mis-admitted anything; the
`RESIDUAL_ABOVE_CAP` refusals were all conservative.

**Does not clear it either.** Zero informative markets settled coherently, so
the prospective-net treatment has still never been measured end to end on live
money. The 1130z fixture result (peak 3.24 → 1.79) remains its only evidence,
and that is a replay, not a live settlement.

**One interaction worth flagging for review:** prospective accounting makes
unresolved orders *more* costly than realized-only did. An order whose terminal
state cannot be established stays in `live_orders` and therefore keeps
consuming prospective capacity indefinitely. Realized-only would have ignored
it. That is arguably correct — an order we cannot account for is exposure we
cannot account for — but it means a venue read outage now converts directly
into a trading stoppage, and it is why 34 of 36 eligible actions were refused.

## Operational context

One HTTP 429, recovered in 3.1s. Then `unmet_demand 6504` against
`demand_rps 10.25` vs `transmitted_rps 0.68` — the known P1 cooldown
busy-wait, amplified by the retry storm from the failing order reads. Known,
unchanged, and not the cause.

## State now

DISARMED and verified: `armed false`, `owner_arm_token false`, `halt false`,
0 live orders, 0 parked, 0 quarantined, `pending_terminal []`. `PMUS_Q1_LIVE`
is `NO` and the arm token is replaced.

**1300z remains unsettled and the book remains incoherent by design.**
Submissions stay blocked until the 0.90 UP is resolved against authoritative
venue records. **No repair has been attempted** — establishing venue truth
came first, and reconciling it is a separate authorized step. The −$0.54000
stays explicitly unattributed rather than being posted as a trading result.
