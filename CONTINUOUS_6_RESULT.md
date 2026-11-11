# CONTINUOUS-6 — the fill happened, and the refresh could not see it in time

**Authorization `OWNER-2026-09-18-GEMINI-CONTINUOUS-6` is SPENT.** Actions run
35400258025 on `e6905cac`. Tree disarmed again; hashes unmoved across arm,
window and disarm.

```
executable_sha  7dc3daaefcbc39488611fbb94efdb3830c84f760aa866d53dcfaeb99b2aa01ff
envelope_sha    b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17
bundle_sha      3bd2872fdb462ea48aa28dc04b299232c9df6da45f88030af96faca5f993497d
```

## 9. The target chain, first

> **REAL FILL → AUTHORITATIVE REFRESH → INVENTORY CHANGE → CHANGED L5 ACTION**

| link | observed? | evidence |
|---|---|---|
| REAL FILL | **YES** | YES 0.01 @ 0.21 filled **22:12:44.125Z**, during the window |
| AUTHORITATIVE REFRESH | **YES** | read #11 at **22:12:44.392Z**, 267 ms later |
| INVENTORY CHANGE | **NO** | `n_fills` still 0 — the fills read had not caught up |
| CHANGED L5 ACTION | **NO** | cycle 8 at 22:12:45.110 still classified both legs `OPEN` |

**The chain broke at link three, and we now know exactly why, to the
millisecond.** This is a sharper answer than C5's "no fill at all".

### The new defect: the authoritative refresh is two reads with different latencies

`_refresh` builds from `authoritative_state`, which is **two separate venue
reads**:

* `active_orders` → the **working set**
* `order_history(statuses=("filled",))` → the **inventory**

At 22:12:44.392 the first had already dropped the filled order — read #11
shows only `…dd1a33` resting — while the second still returned nothing. So the
working set knew the order was gone and inventory still read flat. The
controller saw `yes 0 / no 0` and, correctly given that input, said `OPEN`.

A fill is therefore visible in one half of the refresh up to a second before
the other. The driver derives inventory from the slower half only.

This is the **third** distinct seam defect, and the family is now clear:

1. CONTINUOUS-4 — a cancel ack read as confirmation. *Wrong reading.*
2. CONTINUOUS-5 — an orderId sent as int not string. *Wrong type.*
3. CONTINUOUS-6 — inventory read from the slower of two reads. **Wrong clock.**

## 1. Cycle-by-cycle

Contract `GEMI-BTC05M2609182215-UP`, entered TTE 206.8s, **8 cycles**,
22:11:33 → 22:12:47. Every inventory below is the authoritative pre-decision
read.

| cyc | time | tte | inventory | working before | decision | plan |
|---|---|---|---|---|---|---|
| 1 | 22:11:40.3 | 199.7 | `0/0` | — | `OPEN` YES 0.21 · `OPEN` NO 0.52 | place 2 |
| 2 | 22:11:48.1 | 191.9 | `0/0` | NO 0.52, YES 0.21 | `OPEN` YES 0.21 · `OPEN` NO 0.60 | **keep** YES · cancel NO 0.52 · place NO 0.60 |
| 3 | 22:11:56.9 | 183.1 | `0/0` | NO 0.60, YES 0.21 | `OPEN` YES 0.21 · `OPEN` NO 0.69 | **keep** YES · cancel NO 0.60 · place NO 0.69 |
| 4 | 22:12:05.7 | 174.3 | `0/0` | NO 0.69, YES 0.21 | unchanged | **keep 2 · 0 mutations** |
| 5 | 22:12:12.9 | 167.1 | `0/0` | NO 0.69, YES 0.21 | unchanged | **keep 2 · 0 mutations** |
| 6 | 22:12:19.3 | 160.7 | `0/0` | NO 0.69, YES 0.21 | unchanged | **keep 2 · 0 mutations** |
| 7 | 22:12:25.7 | 154.3 | `0/0` | NO 0.69, YES 0.21 | unchanged | **keep 2 · 0 mutations** |
| 8 | 22:12:45.1 | 134.9 | `0/0` | **NO 0.69 only** | `OPEN` YES 0.01 · `OPEN` NO 0.90 | cancel + place 2 = **3 needed, 0 left** |

**No HOLDs** in any cycle. Sixteen `OPEN` classifications, no `EXTEND`, no
`COMPLETE` — because inventory never moved off zero.

Cycles 4–7 are the budget doing its job: the surface was unchanged, so all
four cycles were pure `KEEP` and cost **nothing**. Cycle 8's market had moved
hard (YES 0.21 → 0.01) and the plan needed 3 mutations with 0 left, so the
window stopped on **budget** — not on an error. C5 managed 2 cycles in 9
seconds; C6 ran 8 cycles in 74.

## 2. Every order

| slot | order | id | what happened |
|---|---|---|---|
| 0 | YES 0.01 @ 0.21 | `145828836251359298` | **FILLED 22:12:44.125Z** |
| 1 | NO 0.01 @ 0.52 | `145828836251359311` | **CANCELLED** — our cancel, 22:11:48.8 |
| 3 | NO 0.01 @ 0.60 | `145828836251361495` | **CANCELLED** — our cancel, 22:11:57.3 |
| 5 | NO 0.01 @ 0.69 | `145828836251363146` | **LIVE at termination**, then venue-**CLOSED at expiry** 22:15:00.124Z, `filledQuantity 0` |

**The cancel fix is proven live.** Both cancels sent the canonical **string**
and the venue answered **200** — the `-1013` is gone.

The cancel ack is still `{"orderId":"","symbol":"","status":""}`, the empty
shape CONTINUOUS-4 found, so `confirmed` stayed **False** and the order stayed
in the working set; the **next authoritative refresh** is what removed it
(reads #4 and #6). Ack as receipt, venue as authority — as designed.

The two cancelled orders appear in neither the filled nor the cancelled
history query, and are absent from active orders. "Cancelled" here rests on
the window's own refresh watching each disappear from the open list
immediately after its cancel, plus their absence from filled history — stated
as the inference it is.

## 7. Orders live after the controller stopped

**One: NO 0.01 @ 0.69, `145828836251363146`.** It was still resting when the
window terminated at 22:12:47 and stayed live for another 2m13s. The venue
closed it unfilled at expiry, 22:15:00.124Z.

**It was not touched.** No cleanup mutation was performed — the C6
authorization was spent and no cleanup was authorized. It was observed through
venue closure exactly as instructed. This time it did not fill; C5's did.

## 3 & 4. Fills, timestamps, and the inventory each decision saw

One fill: **YES 0.01 @ 0.21 at 22:12:44.125Z**, `filledQuantity 0.01`,
`remainingQuantity 0`.

Every decision — all eight — saw `yes 0 / no 0 / n_fills 0`. Cycle 8 saw it
**985 ms after the fill had already happened**, because the fills read lagged.
That is the finding.

## 5. Classifications

`{"OPEN": 16}`, zero HOLDs, zero `EXTEND`, zero `COMPLETE`. Zero record errors
in the capture; `refresh_link` linked all 11 reads to the driver's own labels.

## 6. Cohort

`COHORT-2026-09-18-C6`: **6 of 6 consumed, 0 remaining.** Slots 0,1,3,5
`ACCEPTED` places; 2,4 cancels. A 7th mutation (cancel NO 0.69 at cycle 8) was
**refused by the budget** before any frame went out — which is why the wire has
6 entries and the observer recorded 7 mutation attempts. The bound held.

`COHORT-2026-09-18-C5` remains frozen at **3 of 6** with its `UNKNOWN` intact.
The historical ledger remains 12/12, byte-identical.

## 8. Settlement and accounting

Contract **SETTLED**: `resolutionSide "no"` at **22:15:07.583Z**. Our filled
leg was **YES**, so it pays **$0.00**.

| stage | value |
|---|---|
| SUBMITTED | 4 × place (all 200), 2 × cancel (both 200) — 6 slots |
| FILLED | **1** — YES 0.01 @ 0.21, cost **$0.0021** |
| position | unmatched **YES 0.01** — pure directional residual, no matched pair |
| SETTLED | yes, against it |
| EXPECTED | **−$0.0021** |
| REALIZED | **$0.00** |

**EXPECTED, not REALIZED**, on the same footing as C4's −$0.0006 and C5's
−$0.0046: no attributable cashflow has ever been observed on this venue. The
three are **never summed** — one matched pair and two unmatched residuals are
different objects.

## What is now the blocker

Two things, and they are different in kind.

**A. The refresh's split-read latency** is what stopped the research chain. The
working set and inventory come from two reads that do not agree for up to a
second, and inventory uses the slower one. Until that is closed, a fill can
land mid-window and the controller will keep deciding as though it is flat —
which is the exact failure the reconciliation fix was built to prevent, just
arriving through a different door.

**B. The post-stop resting-order lifecycle**, which the owner has named the
next P0. C5's leftover order filled after termination; C6's did not. Both
outcomes came from the same gap: **STOP stops the controller, not the orders.**

Neither is implemented here. The evidence is preserved and reported first.
