# EP015 — CAP4-LIVE-3: it traded, the EP012 release fired, and the UNREADABLE hold stopped it

**Authorization:** `OWNER-2026-09-21-GEMINI-BTC15M-Q1-CAP4-LIVE-3` — SPENT, DISARMED.
**Run:** [35664464001](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35664464001),
armed step 22:53:22Z–23:13:32Z, conclusion success.
**Readiness that preceded it:** [35662739043](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35662739043)
PASS 11/11 on `e86ffe0`, manifest `2725926f…`.
**Cohort** `COHORT-2026-09-21-BTC15M-CAP4-3`, **3 of 12 consumed**, 9 unused and they stay unused.

```
spec 7fda21b1…  expected == resolved, checked before the latch
executable 38f75180…   envelope 3aed2098…   bundle f1983885…
symbol GEMI-BTC15M2609212315-UP   entered tte 878.4s   band [840, 880]
```

**Both validation goals were met.** The corrected EP012 accounting was exercised
by a real fill, and the bounded UNREADABLE recovery was exercised by a real
venue condition. Neither was induced.

## The complete sequential trace

```
23:00:30  cycle  1  tte 869.1  RUNNING  0 actions   yes 0.41/0.50  no 0.50/0.59
   …      cycles 2–58          RUNNING  0 actions   — above the frozen [0,300]
                                                       quoting window, silent
23:09:48  cycle 58  tte 311.5  RUNNING  0 actions   yes 0.90/0.95  no 0.05/0.10
23:10:41  cycle 59  tte 258.3  RUNNING  SURFACE OPENS
          23:10:47  PLACE  OPEN YES 0.90 q=1  ack 200  …302688   (mutation 1)
          23:10:48  PLACE  OPEN NO  0.02 q=1  ack 200  …302731   (mutation 2)
23:10:58  cycle 60  tte 241.9  desired YES moved 0.90 → 0.95
   …      cycles 60–69         RETAINED, mutations 0 each cycle
                               (the 30s floor and one-rung-per-outcome holding
                                the moved price OFF the book, as frozen)
23:12:30            FILL   NO 1 @ 0.02   maker (m:true)   fee 0.0000
23:12:32  cycle 70  tte 147.4  UNREADABLE — CROSSED book, HOLD 1/5
23:12:41  cycle 71  tte 138.9  UNREADABLE — HOLD 2/5
23:12:52  cycle 72  tte 127.4  UNREADABLE — HOLD 3/5
23:13:02  cycle 73  tte 118.0  UNREADABLE — HOLD 4/5
23:13:13  cycle 74  tte 106.7  UNREADABLE — HOLD 5/5
23:13:24  cycle 75  tte  95.9  BOUND_EXCEEDED → WITHDRAWING
          23:13:25  CANCEL YES 0.90                        (mutation 3)
23:13:27            TERMINAL_WITHDRAWAL rounds 2, CLEANUP_CONFIRMED, remaining []
          STOPPED_FLAT_ORDERS   orders_live_after_stop 0
```

The refused book on every held cycle was **yes 0.98 bid / 0.98 ask, no 0.02 /
0.02** — a bid at its own ask, which `crossed()` refuses. It never recovered.

## Every desired action and every actual mutation

| cycle | desired | actual mutation | outcome |
|---|---|---|---|
| 59 | OPEN YES 0.90 q=1 | PLACE, ack 200 `…302688` | rested 158s, never filled, cancelled on withdrawal |
| 59 | OPEN NO 0.02 q=1 | PLACE, ack 200 `…302731` | **rested 102s → FILLED 1 @ 0.02, maker, fee 0.0000** |
| 60–69 | OPEN YES 0.95 q=1 (×10) | **none — RETAINED** | the frozen rest floor and one-rung-per-outcome; 0 mutations |
| 60–69 | OPEN NO 0.02 q=1 (×10) | **none — already resting at that price** | KEEP |
| 70–75 | *(nothing — the policy was never shown the book)* | none | bounded UNREADABLE hold |
| 75 | terminal withdrawal | CANCEL `…302688`, ack 200 | CLEANUP_CONFIRMED |

**3 mutations of 12.** Desired-price movement cost zero mutations across ten
cycles — the RETAIN lifecycle doing exactly what it was built for.

## Authoritative inventory after the fill

```
before   yes 0  no 0  matched 0  residual  0  imbalance 0  spend 0
23:12:30 FILL NO 1 @ 0.02
after    yes 0  no 1  matched 0  residual -1  imbalance 1  spend 0.02
         avg_no_cost 0.02   same_side_run {leg: NO, len: 1}   n_fills 1
```

**Peak imbalance: 1.** The CAP-4 headroom (4) was never approached. No matched
pair was formed, so there is no effective pair cost to report.

## Durable commitment after every order transition — the EP012 fix, live

```
cycle 58  CLEAR    committed 0      resting 0   spend 0
cycle 59  CLEAR    committed 0      resting 0   spend 0      (before the places)
cycle 60  BLOCKED  committed 0.92   resting 2   spend 0      0.90 + 0.02
…
cycle 69  BLOCKED  committed 0.92   resting 2   spend 0
cycle 70  BLOCKED  committed 0.90   resting 1   spend 0.02
          released ['l5cp-owner-2026-no-9f05be57']
cycle 71–75        committed 0.90   resting 1   spend 0.02
```

**This is the defect EP012 fixed, observed not reproducing.** The filled NO
order's $0.02 reservation was released on the very next authoritative read, at
the same moment its $0.02 appeared in filled spend. Committed went 0.92 → 0.90,
not 0.92 → 0.92. Under the old code that $0.02 would have stayed charged
alongside the same cash in `inv.spend()` for the rest of the window — the
mechanism that produced LIVE-1's phantom $2.16.

Total committed capital at its peak: **$0.92** of the authorized $4.00.

## The HOLD: duration and outcome

```
holds 6   longest_run 6   bound {max_consecutive 5, max_s 60.0}
bound_exceeded True   recoveries 0
first hold 23:12:32 (tte 147.4)   last 23:13:24 (tte 95.9)   span 51.4s
```

Six consecutive crossed books over 51.4 seconds. It hit the **cycle** bound
first (6 > 5) at 51.4s of the 60s wall-clock bound — both were measured from
the census and the tighter one bound, as designed.

During the hold: **zero placements, zero cancels, zero replacements, zero
mutation slots**; the resting YES 0.90 stayed exactly where it was; inventory
stayed NO 1 and was never assumed flat. The stop reason survived into the
report verbatim rather than being overwritten (EP013 defect 1):

> `authoritative state unreadable for 6 consecutive cycle(s) over 51.4s, past
> the bound of 5 cycle(s) / 60.0s: CROSSED book: a bid at or above its own ask.
> No unreadable run longer than 5 cycles has ever recovered, so this is
> withdrawn rather than waited out.`

**The bound is corroborated by the event it stopped.** The census said no
unreadable run longer than five cycles has ever recovered; this one reached six
and did not recover either.

## Ultimate stop reason and terminal state

**Stop:** the bounded UNREADABLE hold exceeded — a legitimate frozen-controller
terminal condition, not a risk breach and not a budget exhaustion. Nothing about
CAP-4, committed cash or imbalance bound: peak committed was $0.92 of $4.00 and
peak imbalance was 1 of 4.

```
withdrawal  TERMINAL_WITHDRAWAL, rounds 2, cancelled 1, failed [], remaining []
            cleanup_consumed 1, CLEANUP_CONFIRMED
state       STOPPED_FLAT_ORDERS        orders_live_after_stop 0
after       open_orders 0, fills 1, trades 1, orders_seen 1,
            order_history ORDERS_FOUND (1 row), terminal_seen 1
```

**Terminal live orders: zero.**

## Economics — every account kept separate

Contract settlement, public read after expiry:

```
GEMI-BTC15M2609212315-UP   status settled   marketState closed
resolutionSide  yes        resolvedAt 2026-09-21T23:15:07.711Z
settlementValue 86656.85973684209
```

The UP contract resolved **YES**. We held **NO 1**, so our leg resolved worthless.

| account | value | basis |
|---|---|---|
| cost | **−$0.02** | 1 NO @ 0.02, venue-confirmed fill |
| payout | **$0.00** | resolutionSide yes; the NO leg pays nothing |
| fees | **$0.00** | the venue's own `orderUpdate` carries `n: "0.0000"`, `m: true` |
| maker rebate actually credited | **none observed** | no credit read back; none is assumed |
| matched-pair economics | **n/a** | `matched_qty 0` — no pair was formed |
| directional residual P&L | **−$0.02** | the whole position was the residual |
| spread capture | **$0.00** | the one fill was the entry, not a round trip |
| **reconstructed P&L** | **−$0.02** | cost 0.02 − payout 0.00 − fees 0.00 |
| **cash-attributable REALIZED P&L** | **UNCONFIRMED** | see below |

**REALIZED stays unconfirmed.** The contract is settled and the direction is
not in doubt, but no authenticated cash movement has been read back on our
account for this settlement. A settled contract says what it resolved to, never
what our account received. The reconstructed −$0.02 is arithmetic over confirmed
fills; it becomes REALIZED when a credit or debit is attributable, and not
before.

Worst-case authorized exposure was $4.00. Actual peak committed capital: $0.92.
Actual reconstructed loss: **two cents**.

## What this window establishes, and what it does not

**Establishes, mechanically:**

1. the corrected commitment accounting releases a filled order's reservation on
   the next authoritative read, with no double count — observed, not modelled;
2. the bounded UNREADABLE hold survives a transient, mutates nothing while
   held, preserves inventory and resting orders, and withdraws cleanly at the
   bound;
3. the RETAIN lifecycle holds a moved price off the book for ten consecutive
   cycles at zero mutation cost;
4. the real stop reason now survives into the report;
5. `n_rows_for_symbol` reads 0 with `n_rows_on_account` 2 on the real payload.

**Does not establish:** anything about expectancy. One fill on one contract is
not evidence about profitability, and the $0.02 price it filled at is a
2-cent lottery ticket on a market that had already resolved in the other
direction — the frozen surface quoting the far tail of a book at tte 258 with
the outcome nearly decided. That observation belongs to the quoting-interval
question, not to this packet.

Nothing here is REALIZED.
