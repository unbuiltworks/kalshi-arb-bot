# What $6.1167253591/window actually contains — refill audit

`scripts/audit_frozen_refill.py` walks the frozen fill model exactly as
`pmus_native_parity.replay_window` walks it and **reproduces the baseline
exactly**: mean `6.116725359099999999999999992`, **44 completions**. It is
therefore a valid control, and it changes nothing in the frozen arm.

## First: the "98 events" figure was my harness, not frozen

The 98 `fill with no live order` events came from the ORDER-BASED replay, which
I have since shown was itself defective (taker fills double-booked, orders
matched to stale prices, fills granted on locked rows). Walking frozen's own
loop, the true count is **10 of 136 maker fills granted**, in 4 of 25 windows.
I am correcting the number rather than auditing 98 events that do not exist.

## Partition of all 136 granted maker fills

| category | meaning | count |
|---|---|---|
| **A — re-quoted** | a REST intent was emitted for this side at this price, so a real new order justifies the fill | **126** |
| **B — persistent quantity** | a partial fill left unfilled quantity on a live order | **0** |
| **C — phantom refill** | the order's quantity was exhausted and no re-quote intervened | **10** |

There are no category-B events at all: the frozen fill model never partially
fills a clip and come back for the rest. Every questionable fill is a **full
clip granted again to an order that had already filled in full**.

## The 10 category-C events

| window | tick | side | quote px | qty | inv before (up/dn) | order qty | already filled | last re-quote |
|---|---|---|---|---|---|---|---|---|
| 2345z | 39 | down | 0.0200 | 50 | 150 / 200 | 50 | 50 | t 37 |
| 2345z | 53 | up | 0.9900 | 50 | 250 / 300 | 50 | 50 | t 45 |
| 2345z | 57 | up | 0.9900 | 50 | 300 / 300 | 50 | 100 | t 45 |
| 0030z | 29 | down | 0.9800 | 50 | 100 / 50 | 50 | 50 | t 25 |
| 0030z | 31 | down | 0.9800 | 50 | 100 / 100 | 50 | 100 | t 25 |
| 0030z | 33 | down | 0.9800 | 50 | 100 / 150 | 50 | 150 | t 25 |
| 0030z | 35 | up | 0.0100 | 50 | 100 / 200 | 50 | 50 | t 25 |
| 0030z | 37 | down | 0.9800 | 50 | 150 / 200 | 50 | 200 | t 25 |
| 0115z | 50 | up | 0.9800 | 50 | 50 / 50 | 50 | 50 | t 48 |
| 0230z | 27 | down | 0.9400 | 50 | 150 / 100 | 50 | 50 | t 25 |

Two properties stand out:

1. **Every one is at an extreme price** — 0.01, 0.02, 0.94, 0.98, 0.99. These
   are late-window near-certainty quotes, not mid-book quotes.
2. **0030z fills one 50-contract DOWN order five times at 0.98** — 250
   contracts against a 50 clip, with the last re-quote at tick 25 and fills at
   ticks 29, 31, 33, 37. That single order is the largest concentration.

Concentration: **2345z 3, 0030z 5, 0115z 1, 0230z 1**. The other 21 windows
have none — including **2300z, the +$54.02 outlier, which has 0 category-C
fills and 5 category-A fills.** The outlier is not a refill artifact.

## Frozen P&L three ways

| | mean $/window | completions | matched P&L | residual P&L | fees |
|---|---|---|---|---|---|
| **1. frozen as-is** | **6.116725359099999999999999992** | 44 | 8.343368380952380952380952368 | −1.152952380952380952380952380 | 1.0736906409 |
| **2. minus category C only** | **5.922062959099999999999999992** | 43 | 8.332415999999999999999999992 | −1.3420 | 1.0683530409 |
| **3. C as explicit replenishment** | **6.116725359099999999999999992** | 44 | 8.343368380952380952380952368 | −1.152952380952380952380952380 | 1.0736906409 |

Deltas: **(2) − (1) = −0.194662400/window (−3.18%)**, one completion lost.
**(3) − (1) = 0 exactly.**

Per-window effect of removing C: 0030z **3.0000 → 0.0000**, 0115z 5.7702 →
4.7702, 0230z −0.4518 → −1.3184, 2345z unchanged at −1.0696. All of the loss is
in three windows.

## Answer to the question that was asked

**$6.1167253591 is not materially a replay artifact.** 126 of 136 fills are
backed by an explicit re-quote. The 10 that are not are worth **$0.19/window,
3.2% of the mean**, and variant 3 shows **every one of them is recoverable by
an explicit replenishment that the frozen decision rules permit** — the cap
allows a fresh clip at each of those moments, so the policy would have
re-quoted there if it had been asked to.

Two limits on that reassurance, stated rather than buried:

* Variant 3 grants the replenishment fill **instantly at the same price**. In
  reality each refill needs a new order posted and filled — five sequential
  orders inside eight ticks in 0030z. That is a **throughput** assumption, not
  a fill-model assumption, and it is untested.
* The category-C prices (0.98, 0.99, 0.01, 0.02) are exactly where a
  post-only quote is most likely to be refused or to be adversely selected,
  which connects this to the open R4 rejection question rather than settling
  it.

No L5 historical evidence was consulted for repeated replenishment at these
prices; that remains open and is not claimed either way.
