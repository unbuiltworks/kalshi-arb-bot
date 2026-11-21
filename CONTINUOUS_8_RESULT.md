# CONTINUOUS-8 — the chain is proven

**Authorization `OWNER-2026-09-19-GEMINI-CONTINUOUS-8` is SPENT.** Latch on
disk, cohort `COHORT-2026-09-18-C8B` at 11 of 12, tree disarmed at `7f407ea2`.
Window from Actions run 35412596894 on `5dd744e9`; read-only reconciliation
from run 35412979586.

```
executable_sha  e32e31f2711c10d99a499717d654bee76468d55171b3f5f13d5bff8439cd369f
envelope_sha    b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17
bundle_sha      bfc4ec1e3a7efcff19778ae0f9f3c0f8dc7752c949d256c59eb7f5d0fb2d9fe6
```

All three byte-identical before, during and after. **The envelope has not
moved across eight windows.**

## The answer

> **DID C8B LIVE-PROVE FILL → COHERENT REFRESH → INVENTORY CHANGE → CHANGED
> L5 ACTION?**
>
> # YES.

```
cycles 1-8   inventory 0/0, n_fills 0   ->  OPEN YES 0.31 · OPEN NO
01:36:28.553Z  YES 0.01 @ 0.31 FILLED, as MAKER (aggressor false, fee 0)
read 14, 01:36:37.087Z   coherent, attempts 1, n_fills 1,
                          new_fills [YES 0.01 @ 0.31]
cycle 9      inventory yes 0.01, residual +0.01, spend 0.0031
             ->  EXTEND YES @ 0.12 · COMPLETE NO @ 0.69
```

Eight cycles of `OPEN`/`OPEN` on flat inventory, one real fill, one coherent
authoritative refresh that carried it, and the very next decision came back
`EXTEND`/`COMPLETE`. `classify()` derives those labels from the residual
alone, so they are not reachable from an empty inventory: the changed action
*is* the inventory change, observed.

**Nothing was done to manufacture it.** Every rung joined the bid
(`ticks_behind_bid: 0`), every order was `maker-or-cancel` at 0.01, no taker
cross, no SELL, no IOC/FOK, no forced flatten, no pair-cost ceiling, no
controller change. The venue's own trade record says `aggressor: false`.

## The window

Contract `GEMI-BTC05M2609190140-UP`. The scheduler waited **151.4s over 23
polls** and entered at **TTE 278.3s** — inside the authorized 240:280 band.
Window 01:32:50 → 01:36:39, **229 seconds**, 9 cycles.

| cy | utc | tte | inventory | presented book (YES bid/ask) | depth at touch | decision | plan | budget |
|---|---|---|---|---|---|---|---|---|
| 1 | 01:35:28.748 | 271.3 | `0/0` f0 | 0.31 / 0.58 sp 0.27 | bid 0.31 @ **483.0** · ask 0.58 @ 10.0 | OPEN YES 0.31 · OPEN NO 0.42 | place 2 | 0/12 |
| 2 | 01:35:42.835 | 257.2 | `0/0` f0 | 0.31 / 0.43 sp 0.12 | bid 0.31 @ **0.01** · ask 0.43 @ 5.0 | OPEN YES 0.31 · OPEN NO 0.57 | keep 1, place 1, cancel 1 | 2/12 |
| 3 | 01:35:50.357 | 249.6 | `0/0` f0 | 0.31 / 0.38 sp 0.07 | bid 0.31 @ 0.01 · ask 0.38 @ 5.0 | OPEN YES 0.31 · OPEN NO 0.62 | keep 1, place 1, cancel 1 | 4/12 |
| 4 | 01:35:59.550 | 240.4 | `0/0` f0 | 0.31 / 0.38 sp 0.07 | bid 0.31 @ 0.01 · ask 0.38 @ 0.01 | unchanged | **keep 2 — 0 mutations** | 6/12 |
| 5 | 01:36:06.113 | 233.9 | `0/0` f0 | 0.31 / 0.38 sp 0.07 | bid 0.31 @ 0.01 · ask 0.38 @ 0.01 | unchanged | **keep 2 — 0** | 6/12 |
| 6 | 01:36:13.497 | 226.5 | `0/0` f0 | 0.31 / 0.35 sp 0.04 | bid 0.31 @ 0.01 · ask 0.35 @ 10.0 | OPEN YES 0.31 · OPEN NO 0.65 | keep 1, place 1, cancel 1 | 6/12 |
| 7 | 01:36:20.948 | 219.1 | `0/0` f0 | 0.31 / 0.35 sp 0.04 | bid 0.31 @ 0.01 · ask 0.35 @ 0.01 | unchanged | **keep 2 — 0** | 8/12 |
| 8 | 01:36:27.391 | 212.6 | `0/0` f0 | 0.31 / 0.33 sp 0.02 | bid 0.31 @ 0.01 · ask 0.33 @ 15.0 | OPEN YES 0.31 · OPEN NO 0.67 | keep 1, place 1, cancel 1 | 8/12 |
| 9 | 01:36:37.170 | 202.8 | **`y 0.01` f1 resid +0.01** | 0.12 / 0.31 sp 0.19 | bid 0.12 @ 10.0 · ask 0.31 @ 5.0 | **EXTEND YES @ 0.12 · COMPLETE NO @ 0.69** | keep 0, place 2, cancel 1 → **3 needed, 2 left** | 10/12 |

No holds at any cycle. `action_labels`: **OPEN 16, EXTEND 1, COMPLETE 1**.

## The book capture earned itself immediately

**9/9 cycles presented-book readable · 9/9 depth readable · 9/9 touch
agreements · 0 disagreements · 0 unreadable · 0.549s total cost, max 0.128s.**

And it answers "why did this one fill" directly, with no inference:

* the **YES bid sat at 0.31 the whole time** — our own rung;
* the **YES ask walked down** 0.58 → 0.43 → 0.38 → 0.35 → 0.33, a seller
  coming to meet it;
* the **depth at our price read `0.01`** from cycle 2 onward — that is *our
  own order, alone on the bid*, with the 483.0 that had been there at cycle 1
  gone;
* at cycle 9 the book had collapsed through us to 0.12 / 0.31.

C7 had to be explained by reading the book backwards out of our own prices.
This one is read off the record.

## Mutations — 11 of 12, all accepted, no UNKNOWN

| slot | kind | detail | outcome |
|---|---|---|---|
| 0 | PLACE | YES 0.31 | ACCEPTED 200 → `145828836256673007` |
| 1 | PLACE | NO 0.42 | ACCEPTED 200 → `…673013` |
| 2 | CANCEL | NO 0.42 | ACCEPTED 200, `cancel_confirmed false` |
| 3 | PLACE | NO 0.57 | ACCEPTED 200 → `…679011` |
| 4 | CANCEL | NO 0.57 | ACCEPTED 200, `cancel_confirmed false` |
| 5 | PLACE | NO 0.62 | ACCEPTED 200 → `…682805` |
| 6 | CANCEL | NO 0.62 | ACCEPTED 200, `cancel_confirmed false` |
| 7 | PLACE | NO 0.65 | ACCEPTED 200 → `…695957` |
| 8 | CANCEL | NO 0.65 | ACCEPTED 200, `cancel_confirmed false` |
| 9 | PLACE | NO 0.67 | ACCEPTED 200 → `…704088` |
| 10 | CANCEL | NO 0.67 | ACCEPTED 200, `cancel_confirmed false` |

**Stopped on budget**, not on an error: cycle 9 needed 3 mutations with 2
left. Every cancel sent the canonical **string** `orderId`; every ack was an
empty `result`, recorded `cancel_confirmed: false` — an empty 200 is still
not confirmation.

**Terminal cleanup, accounted separately:** `CLEANUP_CONFIRMED`, rounds 1,
**`cleanup_consumed 0`**, cancelled `[]`, failed `[]`, on its own 8-slot
budget that the strategy's 12 never touched.

## After STOP, authoritatively

`orders_live_after_stop: 0`, `STOPPED_FLAT_ORDERS`, `inert: true`.

Read-only reconciliation of **all six** C8B orders after settlement — no
`--apply-from`, nothing written to the ledger:

| order | leg | price | venue record |
|---|---|---|---|
| `…673007` | YES | 0.31 | **`status filled`, `filledQuantity 0.01`** |
| `…673013` | NO | 0.42 | `cancelled`, `filledQuantity 0` |
| `…679011` | NO | 0.57 | `cancelled`, `filledQuantity 0` |
| `…682805` | NO | 0.62 | `cancelled`, `filledQuantity 0` |
| `…695957` | NO | 0.65 | `cancelled`, `filledQuantity 0` |
| `…704088` | NO | 0.67 | `cancelled`, `filledQuantity 0` |

`active_orders: []` on every read. Filled history: exactly **1** row.
`/v1/mytrades`: exactly **1** trade — `price 0.31, amount 0.01,
timestampms 1789781788553, aggressor false, fee_amount 0, tid
1893456011790529`.

## Settlement and accounting

Contract **SETTLED `resolutionSide "yes"`** at **01:40:07.057Z**,
`settlementValue 81126.978`, `marketState closed`, three agreeing reads,
`flapped false`.

| stage | value |
|---|---|
| SUBMITTED | 6 places + 5 cancels, all accepted 200 |
| FILLED | **1** — YES 0.01 @ 0.31, maker, 01:36:28.553Z |
| SETTLED | contract resolved **YES**; the leg we hold is the winning side |
| REALIZED | **$0.00** |

From the engine's own `settlement_report`:

* `matched_pair_economics`: **null** — there is no matched pair. A single YES
  leg is **pure directional residual**, and nothing is netted into a matched
  result that does not exist.
* `directional_residual_pnl`: `redeems_per_unit 1`, `cost_basis_per_unit
  0.31`, **`gross_usd "0.0069"`**, status
  **`SETTLED_NOT_YET_CASH_CONFIRMED`**.
* `realized`: **null**.

So: **EXPECTED +$0.0069**, the first positive settlement number on this
venue, and it is **EXPECTED, not REALIZED**, on exactly the same footing as
C4's −$0.0006, C5's −$0.0046 and C6's −$0.0021. No attributable cashflow has
ever been observed here. The four are different objects and are never summed.

It is also worth saying plainly: a 0.01-contract directional leg that
happened to settle the right way is **not evidence of edge**. It is one
sample of carried exposure, and the historical measurement of this residual
is negative out of sample. The window's result is the *chain*, not the
$0.0069.

## Two dispatches refused first, and both were right

Neither spent anything — no latch, no slot, no frame:

1. **01:07Z** — `terms_status` GET returned `ReadTimeout`, so the preflight
   refused. **Not a terms problem:** the unarmed step *three seconds earlier
   in the same job* read `hasAcceptedLatest true, acceptedVersion 1,
   latestVersion 1` with a 200, and the next attempt read `ACCEPTED` again. A
   transient read, failing closed exactly as designed.
2. **01:17Z** — terms `ACCEPTED`, preflight agreed, and the arm refused on
   `GEMINI_CANARY_AUTH_EXPIRES` still reading `2026-09-19T00:00:00Z` from the
   09-18 windows. My error when arming; a test now asserts the expiry is in
   the future whenever the tree is armed.

The entry-band scheduler worked on all three dispatches — 195.4s→TTE 279.9,
158.9s→TTE 277.8, 151.4s→TTE 278.3.

## THE FINDING: two authoritative sources, one of them slower

**The coherence gate and the inventory do not read from the same place.**

* `unexplained_disappearances` explains an order from
  `fills ∪ trades ∪ terminal_seen`.
* `CP.recover` rebuilds inventory from **`fills` alone**.

At **read 13, 01:36:31.194Z — 2.64s after the fill** — the YES order was gone
from `active_orders`, `order_history(filled)` was still empty, and
`/v1/mytrades` already carried the trade. So the gate said **coherent on
attempt 1, correctly**, while the inventory it carried forward still read
**flat**. The NO 0.67 replacement went out 16ms later against that flat
inventory, while the venue held YES 0.01.

What this is, precisely:

* **No decision was corrupted.** `decide()` ran 9 times; cycle 9 used read 14,
  which had `n_fills 1`. The C6 defect — `OPEN` emitted 985ms after a real
  fill — did **not** recur, and the gate is why.
* **But the post-cancel refresh did not achieve its stated purpose.** Its own
  docstring: *"A FILL CAN ARRIVE BETWEEN THE CANCEL AND THE REPLACEMENT …
  precisely how a replacement gets sized against stale zero inventory."* Here
  the fresher source existed, the gate used it, and the inventory did not.
* **Harm in this window was zero.** The rung was NO 0.01 @ 0.67 — the
  complement direction, so it could not widen the residual — and every bound
  held.

My assessment: this is a **material state-integrity defect by this
program's own standards**, narrow and well understood — build inventory from
`fills ∪ trades`, deduplicated by `order_id`/`tid` — and it did **not**
corrupt the C8B result. It belongs fixed before autonomous continuous
operation, not as a reason to re-run one-window canaries.

## What C8B established

1. **The target chain, live.** Fill → coherent refresh → inventory change →
   changed action, with the changed action being `EXTEND`/`COMPLETE`, which
   flat inventory cannot produce.
2. **The book capture works and pays for itself** — 9/9 on both sources, 9/9
   agreement, and it explains the fill directly rather than by inference.
3. **Entry scheduling works** — three dispatches, three in-band entries.
4. **12 slots was the right call and still bound**, at cycle 9, on the fill
   cycle itself.
5. **Zero orders live after STOP**, for the second window running.
6. **Every venue-shape fix from C2/C4/C5/C6 held** under a window that used
   all of them.
