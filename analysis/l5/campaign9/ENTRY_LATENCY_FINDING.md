# The strongest missed opportunity is entry latency, not inventory valuation

From `data/pmus_decision_tape/pmus_decision_states_20260927.jsonl` (315
`L5_LIVE_DECISION` rows, every one carrying `latency_s`) and the Oct-26
residual book tapes. Read-only. No production change.

## 1. We join the maker queue a median 18 seconds after L5 acted

`latency_s` is the lag from L5's action to our evaluation of it. All 315
decisions carry it:

| | min | p10 | p25 | **median** | p75 | p90 | max |
|---|---|---|---|---|---|---|---|
| `latency_s` | **1.34** | 6.62 | 10.52 | **18.11** | 26.87 | 31.87 | **33.11** |

**82.5 % of decisions are taken more than 10 s after L5 acted.** Submitted
and refused decisions have almost the same median (19.7 s vs 17.6 s), so
this is not a selection effect of slow decisions being refused.

The **min of 1.34 s matters**: the feed *can* deliver in about a second, so
this is not a fixed venue publication delay we must accept. But I cannot
cleanly split our share from the feed's, because the tape carries no feed
publish timestamp — only L5's action time and our receipt. **How much of the
18 s is recoverable is therefore unquantified.** The max of 33.11 s sitting
almost exactly on the measured 34 s submission cadence suggests a large part
is our own batching, but that is an inference, not a measurement.

## 2. What 18 seconds costs: the book has already moved a full tick

Median absolute mid drift over the latency window, measured on the real
Oct-26 book tapes:

| lag | n | median \|mid drift\| | p75 | p90 |
|---|---|---|---|---|
| **18 s** (median latency) | 68 | **0.0100** | 0.0300 | 0.0400 |
| **32 s** (p90 latency) | 64 | **0.0300** | 0.0400 | 0.0800 |

The tick is **0.01** and the spread is **0.01**. So at the median we quote
against a book that has moved **one full tick** — a whole spread — and at
the p75 of the p90 latency, **four ticks**.

## 3. This is the mechanism behind the adverse selection, and it is mechanical

The settled evidence already said the maker edge nets to nothing:
break-even completion pair cost **0.9532** against an achieved **0.9530**,
with the note that a resting completion *"fills when the market moves toward
it, which is exactly when the leg we already hold is becoming a winner."*
That has been a description without a cause. Here is a cause:

- quote a price derived from an 18-second-old book;
- if the market moved **our way**, the touch has run past us and we do not
  fill;
- if it moved **against us**, we are resting at a stale-dear price and we do
  fill.

Filling selectively on the unfavourable branch is adverse selection by
construction. It also explains the queue arithmetic directly: `queue_ahead`
running **745×–808×** the quantity we need is what arriving late at a price
level looks like. **We are not losing a race for queue position; we arrive
after the queue has formed.**

## 4. Quantified, per window and in capital

Matched surplus is **$0.04697 per contract** at the achieved pair cost. A
1 ¢ adverse entry on a ~2-contract clip is **$0.02 per order**. At ~5
submissions and a 60.9 % fill rate, ≈3 fills per window:

| drift assumption | cost per window |
|---|---|
| median 1 ¢ | **≈ $0.06** |
| p75 3 ¢ | **≈ $0.18** |

Against a measured matched surplus of **$0.1933/window**, the p75 case is
**the entire edge** and the median case is about a third of it.

**Capital required: $0.00.** This is an entry-timing defect, not an
allocation question. That is what makes it the strongest item on the board —
every other candidate measured this month needed either more capital, a
limit change, or a fill rate we do not have.

**What I am not claiming:** that closing the latency recovers that money.
The 1 ¢ is the drift we quote into; how much of it is recoverable depends on
the unresolved split in §1, and a faster entry also means filling more often
on both branches. The honest statement is that entry latency is the largest
*measured* leak with the lowest cost to attack, not that it is $0.18/window
of recoverable profit.

## 5. The settlement-probability question cannot be answered on our own trades

You asked for a per-decision-point reconstruction of BTC spot versus strike,
time to settlement, estimated P(UP), live prices, inventory and depth.
**The spot series does not exist for our decision points.**

| input | coverage | our live windows |
|---|---|---|
| BTC spot (`data/ref_lag/*.jsonl`, 41 files) | **2026-08-31 → 09-01** | — |
| settlement outcomes (`analysis/l5/taker_entry/strike_resolved.json`) | 2026-08-30 → 08-31, **Kalshi** KXBTC15M/KXETH15M | — |
| our campaign tapes C6/C7/C8 | **2026-10-24 → 10-29** | 17 settled windows |

There is **no overlap**. I can build and out-of-sample test a spot-vs-strike
probability model on Aug 30–31 Kalshi windows, but I cannot apply it to a
single one of our own decisions, and no join exists that would let me
compare our actions against a calibrated P(UP) at the moment we took them.
Inventing a spot series for October, or scoring October decisions on an
August model without the October spot, would be fabrication.

**And the latency finding argues the valuation hypothesis is not where the
money is.** A better P(UP) tells you what price is fair. Our problem is that
we are quoting at a price that was fair 18 seconds ago. A sharper fair value
quoted into a moved book is still quoted into a moved book.

## Single strongest missed opportunity

> **Entry latency: a median 18 s between L5's action and our quote, against a
> book whose mid moves a full tick in that time. Worth ≈$0.06/window at the
> median drift and ≈$0.18/window at the p75, on a measured matched surplus of
> $0.1933/window. Capital required: $0.00.**

**The one thing that would settle it**, and it is cheap: the decision tape
already carries `latency_s`. Add the feed's own publish timestamp alongside
it in the record-only path already prepared, and the 18 s splits into *our*
batching versus the feed's delay. If most is ours, it is an engineering fix
with no capital and no limit change. If most is the feed's, then L5-action
entry is **structurally** adversely selected and the strategic answer is to
stop depending on L5's action for entry timing — which is a far larger
finding than any threshold.

That measurement needs one extra field in a record we are already writing.
It does not need a service, a limit change, or capital.
