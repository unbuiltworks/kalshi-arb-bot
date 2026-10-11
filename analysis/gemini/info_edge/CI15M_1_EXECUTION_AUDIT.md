# CI15M-1 — canonical execution audit

**CLOSED.** Authorization `OWNER-2026-10-03-GEMINI-CI15M-1`, policy fingerprint
`5c113fab1f85b5c0327c1d9eb447661753921e25cb21393845d90fd810536329`, executed
from pinned SHA `730318b72b599445258ae5f5e7ce8d751cf84aee`, Actions run
`37139088501`. Twelve order slots, all twelve spent, **four fills**.

Sources, kept apart on purpose:

- **durable latch refs** `refs/heads/ci15m-latch/CI15M-1-slot0..11` — what we
  intended and what the submission ack said *at submission time*;
- **authenticated read** `/v1/prediction-markets/orders/history`, run
  `37140473036` step 32, read-only and unarmed, 12 rows — what actually
  happened;
- **public event read** for the settlement side.

The two disagree, and that disagreement is the finding.

## The twelve orders

| slot | orderId | symbol | px | ack at submit | FINAL (authenticated) | filled |
|---|---|---|---|---|---|---|
| 0 | 145828836710181412 | BTC15M2610031715-UP | 0.70 | cancelled | cancelled | 0 |
| 1 | 145828836710187183 | BTC15M2610031715-UP | 0.70 | cancelled | cancelled | 0 |
| 2 | 145828836710194348 | BTC15M2610031715-UP | 0.70 | **open** | **filled** | **1** |
| 3 | 145828836710202825 | BTC15M2610031715-UP | 0.71 | **open** | **filled** | **1** |
| 4 | 145828836710208003 | BTC15M2610031715-UP | 0.73 | **open** | **filled** | **1** |
| 5 | 145828836710212596 | BTC15M2610031715-UP | 0.72 | **open** | **filled** | **1** |
| 6 | 145828836710966485 | BTC15M2610031730-UP | 0.86 | cancelled | cancelled | 0 |
| 7 | 145828836710969558 | BTC15M2610031730-UP | 0.86 | cancelled | cancelled | 0 |
| 8 | 145828836710972473 | BTC15M2610031730-UP | 0.86 | cancelled | cancelled | 0 |
| 9 | 145828836710979141 | BTC15M2610031730-UP | 0.86 | cancelled | cancelled | 0 |
| 10 | 145828836710983772 | BTC15M2610031730-UP | 0.86 | cancelled | cancelled | 0 |
| 11 | 145828836710986624 | BTC15M2610031730-UP | 0.86 | **(none recorded)** | cancelled | 0 |

Slot 11's ack is absent from the durable record because the latch ref is
pushed *before* the venue call and no slot-12 ref exists to carry the
response. The authenticated read supplies it: it was a real submission and it
was cancelled. **All twelve slots were genuine venue submissions** — the
thing we were told not to assume, now established.

## Position and settlement

```
GEMI-BTC15M2610031715-UP   strike 84853.9   status settled   resolution yes (UP)
4 UP contracts @ 0.70, 0.71, 0.73, 0.72
principal   $2.86      avg entry  0.715
UP won -> payout $4.00 -> GROSS +$1.14
positions_now: []   (settled and gone, NOT never-held)
```

**Not REALIZED.** The settlement *side* is confirmed from the venue; the
**cashflow is not**. Fees are not venue-confirmed either — the maker formula
gives roughly $0.01/contract here, and no formula-derived fee is netted in.
`/v1/balances` and `/v1/transfers` read 200 and are reachable; that read is
what would close this line, and it has not been run.

## Two authorized caps were breached

| cap | authorized | actual |
|---|---|---|
| unmatched shares | 3 | **4** |
| unmatched principal | $2.70 | **$2.86** |

## Causal chain, corrected

The first account — "maker-or-cancel at a crossing price can never fill, so
zero fills were structurally guaranteed" — was **half wrong**, and the half
that was wrong is the half that cost money.

1. The frozen policy picks an **immediately executable** acquisition price:
   it buys UP at the ask. The transport sent `makerOrCancel: true`.
2. A post-only order at a **crossing** price is cancelled, in the same
   millisecond. Slots 0, 1 and 6–11 died that way: eight of twelve.
3. But slots 2–5 were submitted at prices that did **not** cross by the time
   they landed, so they **rested** — and then **filled**. MOC filling is not
   a contradiction; resting one tick inside is how a maker fills.
4. `_settle_order` read **only the submission ack**. For a resting order that
   ack says `filledQuantity: 0`, so the engine concluded `NOT_FILLED` and
   **never re-polled**. It stayed permanently blind to its own four fills.
5. Inventory therefore stayed FLAT. Every `intent_of` computed
   `unmatched_shares_after = 1`, and the share and principal caps were
   evaluated against **phantom-flat inventory**. That is how 4 shares and
   $2.86 got past a 3-share, $2.70 gate.
6. `one_outstanding_order` let the four coexist: it caught exceptions but
   never checked `res["ok"]`, and `_orders_of` returns `[]` for any body
   shape it does not recognise, so a failed or unrecognised active-orders
   read was read as "zero outstanding orders".
7. The OPEN gate was `if inv.fills` — a test on whether an OPEN had
   **filled**, not whether one had been **attempted**. Flat inventory meant
   OPEN kept re-firing at the re-read ask: 0.70, 0.70, 0.70, 0.71, 0.73,
   0.72. No chase logic existed; the chase was emergent, which is worse,
   because nothing named it.

## What this establishes, and what it does not

**Establishes.** Maker-or-cancel on this venue fills when the price rests
inside the book, at a price better than the ask — consistent with the
historical q10 pair, whose complement only filled after being repriced away
from the cross (0.52 cancelled, 0.62 filled 45 s later). It also establishes
that the live path's fill detection, outstanding-order invariant, OPEN
lifecycle and cap evaluation were all unsound, and that a simulator which
fills anything at its limit will hide exactly that.

**Does not establish.** Anything about profitability. Four q=1 fills that
settled the right way is noise; +$1.14 gross is not evidence of edge and is
not realized. It also does not vindicate maker execution: the R2 execution
research measured resting on the favoured side at **−69pp** accuracy against
taking it, and nothing here tests that.

## Fixes landed (commit 9f60961a)

- **A** cross-run durable budget from the latch refs; unreadable fails
  closed; proved from a fresh directory and a separate interpreter against
  real refs. CI15M-1 now refuses from any new runner: 12/12.
- **B** one outstanding order is hard — read must succeed, shape must be
  recognised, list must be empty; plus `order_is_resolved`, where
  open/accepted/pending/absent/unrecognised all block the next decision.
- **C** one OPEN **intent** per market, durable:
  `OPEN_NOT_ATTEMPTED / OPEN_SUBMITTED / OPEN_CANCELLED / OPEN_FILLED`. A
  cancellation never resets to never-attempted.
- **F** `ci15m_sim` refuses what the venue refuses; the CI15M-1 case is a
  regression test and resting maker orders are tested separately.

## Still outstanding before any CI15M-2 proposal

1. **Fill detection must re-poll order state** rather than trust the
   submission ack. This is the defect that breached the caps.
2. **Caps must be evaluated against reconciled inventory**, never against
   our own belief about it.
3. The execution question is now genuinely open and is a **strategy**
   decision, not a patch: MOC *does* fill, at better prices, but only in the
   states where resting is adversely selected.

No strategy parameter was changed by any of this work. CI15M-1 is closed and
is not re-armable.
