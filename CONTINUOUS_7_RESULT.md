# CONTINUOUS-7 — both state seams closed, and nothing filled

**Authorization `OWNER-2026-09-18-GEMINI-CONTINUOUS-7` is SPENT.** Latch on
disk in `INTENT_PERSISTED`, cohort ledger 6 of 6, workflow and driver
constants disarmed again at `5dacd1f7`. Window from Actions run 35405627372
on `e66cc48d`; read-only reconciliation from run 35406822182 on `5dacd1f7`.

```
executable_sha  a5ff91753be11d1aba8961c5bdf2a2fc76ebf2bdbb90b7486b67acb45bc856ef
envelope_sha    b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17
bundle_sha      28f117d3c794bdde94de44bdeb90f113c75e8a9bb180aba9886cda6310ac8bb6
```

All three byte-identical before, during and after. Installing the
authorization moved none of them and removing it moved none of them, which
is the whole point of the detached signature. **The envelope has now not
moved across seven windows.**

## The answer, first

> **DID CONTINUOUS-7 LIVE-PROVE FILL → COHERENT REFRESH → INVENTORY CHANGE
> → CHANGED L5 ACTION?**
>
> # NO.

**Nothing filled.** Inventory was `0/0` with `n_fills 0` at all twelve
decisions, and the venue's own record confirms `filledQuantity 0` on every
order C7 created. With no fill there is no inventory change and no changed
action, so the chain is unproven — the same verdict as C5, for a completely
different reason.

It is not a defect this time. C5 died on a type error after nine seconds and
C6 died on an inventory clock it read wrong. **C7 ran its full course, in
control, and simply was not traded with.**

## What the window did

Contract `GEMI-BTC05M2609182330-UP`, entered at **TTE 136.3s**, expiry
23:30:00Z. Twelve cycles, 23:27:43 → 23:29:40 (117s).

| cy | utc | tte | inventory | decision | plan | budget |
|---|---|---|---|---|---|---|
| 1 | 23:27:54.697 | 125.3 | `0/0` f0 | **OPEN YES @0.01** · **OPEN NO @0.79** | place 2 | 0/6 |
| 2 | 23:28:04.584 | 115.4 | `0/0` f0 | OPEN YES @0.01 · **OPEN NO @0.95** | keep 1, place 1, cancel 1 | 2/6 |
| 3 | 23:28:14.793 | 105.2 | `0/0` f0 | OPEN YES @0.01 · OPEN NO @0.95 | **keep 2 — 0 mutations** | 4/6 |
| 4 | 23:28:23.193 | 96.8 | `0/0` f0 | unchanged | **keep 2 — 0** | 4/6 |
| 5 | 23:28:31.159 | 88.8 | `0/0` f0 | unchanged | **keep 2 — 0** | 4/6 |
| 6 | 23:28:39.254 | 80.7 | `0/0` f0 | unchanged | **keep 2 — 0** | 4/6 |
| 7 | 23:28:48.502 | 71.5 | `0/0` f0 | unchanged | **keep 2 — 0** | 4/6 |
| 8 | 23:28:59.430 | 60.6 | `0/0` f0 | unchanged | **keep 2 — 0** | 4/6 |
| 9 | 23:29:08.475 | 51.5 | `0/0` f0 | **HOLD:END_OF_WINDOW ×2** | cancel 2 | 4/6 |
| 10 | 23:29:17.021 | 43.0 | `0/0` f0 | HOLD:END_OF_WINDOW ×2 | nothing | 6/6 |
| 11 | 23:29:28.507 | 31.5 | `0/0` f0 | HOLD:END_OF_WINDOW ×2 | nothing | 6/6 |
| 12 | 23:29:36.398 | 23.6 | `0/0` f0 | — | nothing | 6/6 |

`decision_capture`: **12 decide calls for 12 cycles, 14 authoritative reads,
6 mutations, zero record errors**, `action_labels {"OPEN": 16,
"HOLD:END_OF_WINDOW": 6}`.

**Six consecutive `KEEP` cycles at zero cost** (3–8). C6 managed four. An
unchanged rung spends no slot and keeps its queue position, and this is the
longest live demonstration of it.

**The end-of-window floor fired exactly where the envelope says.** TTE
crossed `no_new_open_extend_tte_s 60.0` between cycles 8 and 9, and cycle 9
withdrew both rungs rather than carry quotes into expiry.

## Mutations — all six accepted, no UNKNOWN

| slot | kind | detail | outcome |
|---|---|---|---|
| 0 | PLACE | YES 0.01 × 0.01 | `ACCEPTED` 200 → `145828836252662220` |
| 1 | PLACE | NO 0.79 × 0.01 | `ACCEPTED` 200 → `145828836252662241` |
| 2 | CANCEL | `145828836252662241` | `ACCEPTED` 200, `cancel_confirmed false` |
| 3 | PLACE | NO 0.95 × 0.01 | `ACCEPTED` 200 → `145828836252668654` |
| 4 | CANCEL | `145828836252668654` | `ACCEPTED` 200, `cancel_confirmed false` |
| 5 | CANCEL | `145828836252662220` | `ACCEPTED` 200, `cancel_confirmed false` |

Every cancel sent `orderId` as a **string** and every one was accepted. The
C5 `-1013` type defect stays fixed. Every cancel ack came back with an empty
`result {"orderId":"","symbol":"","status":""}` and the driver recorded
`cancel_confirmed: false` on all three — **an empty 200 is still not
confirmation**, and the authoritative read is what settles it. That is C4's
lesson holding under a window that leaned on it three times.

All four places went out `timeInForce: maker-or-cancel` and the venue
accepted each as `OPEN`. **A MOC that would cross is rejected, so the venue's
own acks prove every rung was strictly passive.**

## The coherence gate (P0a) was exercised live, and held

At cycle 10's `pre_decide` refresh, **two orders left `active_orders` at
once** — `no-4cc6454e` and `yes-ea473a13`, the pair cycle 9 had cancelled on
an *unconfirmed* ack. That is precisely the shape that broke C6: a working
order gone from the book with the filled-history read still empty.

`unexplained_disappearances` found them in the venue's **terminal** record
and returned nothing pending. `reconcile_attempts 1`, `coherent true`. No
stall, no loop, no sleep, and at no point was disappearance treated as a
fill.

All 14 refreshes: `attempts 1`, `coherent true`.

**This also live-proves the one-line fix that landed hours before the
window.** The terminal reader asks `order_history(statuses=("cancelled",))`.
Had it still asked for the three-status filter, the whole read would have
come back unreadable, the gate would have found two unexplained
disappearances, looped six times over 30 seconds and stopped the window with
`UNKNOWN_TERMINAL` at cycle 10. The post-window `after` block carries all
three terminal rows with `filledQuantity 0`, so the evidence the gate used is
in the artifact, not inferred.

What the gate did **not** face is an ambiguity it could not resolve. It
explained a real disappearance on the first read. The deadman branch
(`UNKNOWN_TERMINAL` after 6 reads / 30s) remains test-covered only.

## Terminal withdrawal (P0b) — reached, confirmed, and had nothing to do

```
withdrawal   verdict CLEANUP_CONFIRMED   state STOPPED_FLAT_ORDERS
             rounds 1   cleanup_consumed 0   cancelled []   failed []
             remaining []   elapsed_s 0.0   cleanup_budget 8
orders_live_after_stop   0
```

**C7 is the first Gemini window to terminate with nothing resting.** C5 left
two orders live and one of them filled 57 seconds later, against us. C6 left
one live for 2m13s. C7 left none.

Note *why*, precisely: the controller's own end-of-window cancels at cycle 9
had already cleared the book 31 seconds before termination, so the cleanup
path verified flat and stopped. **`cleanup_consumed 0` means the cancel
branch of the withdrawal was not exercised live** — the path ran, read the
venue and confirmed, which is the good case, not proof that it cancels
correctly under a window that still has orders out. That still wants a live
demonstration.

The report keeps saying so itself: `inert true` with

> *FLAT ORDERS is not flat INVENTORY … says nothing about fills already
> taken.*

## Authoritative reconciliation of every C7 order

Read-only, after settlement, no `--apply-from`, nothing written to the C7
ledger. `analysis/gemini/reconcile_slot_20260918T2345{36,38,40}Z_C7s{0,1,3}.json`.

| order | leg | price | venue record |
|---|---|---|---|
| `145828836252662220` | YES | 0.01 | `filledQuantity 0`, `remainingQuantity 0.01`, `cancelled`, `cancelledAt 23:29:09.333Z` |
| `145828836252662241` | NO | 0.79 | `filledQuantity 0`, `cancelled`, `cancelledAt 23:28:05.582Z` |
| `145828836252668654` | NO | 0.95 | `filledQuantity 0`, `cancelled`, `cancelledAt 23:29:09.249Z` |

`active_orders` empty, `order_history(filled)` **0 rows**, `/v1/mytrades`
empty. `timeInForce_counts {"maker-or-cancel": 3}`, `quantity_counts
{"0.01": 3}`.

Every C5 and C6 order was re-read in the same pass: **all ten orders across
the three cohorts are terminal and nothing is live on the venue.**

The listing returns `"orderId": 145828836252662220` as an **int** beside
`"globalOrderId"` as a string — the exact split that killed C5 — and the
canonicalization handled it without comment.

## Settlement and accounting

Contract **SETTLED**: `resolutionSide "no"` at **23:30:07.664Z**,
`settlementValue 80999.568`, `marketState closed`, three reads agreeing,
`flapped false`.

| stage | value |
|---|---|
| SUBMITTED | 3 × place (accepted 200), 3 × cancel (accepted 200) |
| FILLED | **none** |
| SETTLED | contract settled NO; **no position held** |
| REALIZED | **$0.00** |

Cash committed **$0.00**. Had the YES 0.01 rung filled it would have settled
worthless; it did not fill, so there is nothing to report as expected P&L
either. **C7 contributes no matched-pair economics, no directional residual,
no fee and no rebate.**

Gemini REALIZED across all seven windows remains **$0.00**. C4's −$0.0006
(matched pair), C5's −$0.0046 and C6's −$0.0021 (both unmatched directional
residual) stay **EXPECTED** and stay separate; they are never summed.

## Why nothing filled — the finding that matters

The rungs the controller chose, against entry TTE, across the four windows
that produced decisions:

| window | entry TTE | rungs quoted | fills |
|---|---|---|---|
| CONTINUOUS-4 | 129.1s | — | **0** |
| CONTINUOUS-5 | 281.8s | YES 0.26 / 0.38, NO 0.46 | **1** |
| CONTINUOUS-6 | 206.8s | YES 0.01 / 0.21, NO 0.52 / 0.60 / 0.69 / 0.90 | **1** |
| CONTINUOUS-7 | 136.3s | YES 0.01, NO 0.79 / 0.95 | **0** |

Every action carried `ticks_behind_bid: 0` — the controller joined the bid
and never crossed. So the quoted price *is* a reading of the book. C7's book
was **YES bid 0.01 against NO bid 0.79, tightening to NO 0.95** within ten
seconds: a contract already decided, quoted 1 cent against 95, with the NO
bid climbing 16 cents in one cycle. A passive rung at either end of that
book needs someone to sell the winner at 0.95 or dump the loser at 0.01,
with ninety seconds left. Nobody did.

**The two windows that entered before ~200s TTE filled once each; the two
that entered after did not fill at all.** That is n=4 and it is a
correlation, not an established mechanism — but it points somewhere specific
and testable, and the mechanism it suggests is the obvious one: a 5-minute
BTC contract is frequently *already decided* two minutes out, and a
maker-only strategy has nothing to trade against once it is.

Entry TTE is currently **not chosen**. `symbol_selection` took the only
eligible contract, and which one that is depends on when the workflow
happens to run against the 5-minute grid. C7 got 136s by accident of the
dispatch clock.

**This is a proposal, not a change.** No controller, pricing, sizing,
envelope or selection parameter was touched, and none should be without
authorization.

## The observability gap this exposed

The per-cycle capture records what we decided — inventory, rungs, prices,
labels, plan, budget — but **not the book we decided against**. To answer
"why did nothing fill", the BBO at each decision is the missing field, and
above I had to reconstruct it from `ticks_behind_bid: 0` plus our own quoted
prices rather than read it. That inference is sound but it is an inference.

For a continuously-running system where fill rate is the thing being
managed, the decision record should carry the touch it was quoting against.

## What CONTINUOUS-7 did establish

1. **Both P0 state seams behave correctly in production.** A real
   double disappearance was explained from authoritative terminal evidence
   on the first read, and the window terminated with zero live orders.
2. **The full loop is clean.** 12 cycles, 6 mutations, 6 accepted, no
   UNKNOWN, no rejection, no stall, no ambiguity left behind.
3. **Six consecutive zero-cost KEEP cycles** — the budget discipline working
   over a real span.
4. **The end-of-window floors fire where the envelope says**, and withdrawal
   preceded expiry by 51 seconds.
5. **Every venue-shape defect found in C2/C4/C5/C6 stayed fixed** under a
   window that exercised all of them.
6. **The binding constraint has moved.** For four windows the question was
   whether the machinery could survive contact with the venue. It now does.
   The open question is no longer plumbing — it is whether this strategy,
   at this size, at this entry timing, gets traded with at all.

## What it did not establish

- **The target chain.** No fill, so no inventory change and no changed
  action. Still unproven live.
- **The cleanup cancel branch.** Reached and confirmed, but `cleanup_consumed
  0`: it has never actually withdrawn an order.
- **The `UNKNOWN_TERMINAL` deadman.** Test-covered only.
- **Any economics whatsoever.** Zero fills means zero evidence about
  matched-pair cost, residual behaviour, fees or rebates.

---

**No CONTINUOUS-8 is authorized and none is implied here.** The tree is
disarmed, C7 is spent at 6 of 6, and the three cohort ledgers — C5 frozen at
3 of 6 with its UNKNOWN, C6 at 6 of 6, C7 at 6 of 6 — are chained and
unrewritable.
