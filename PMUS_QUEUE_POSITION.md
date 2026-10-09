# Is there a place to rest behind the dominant PMUS quote?

**Answer: no. The available evidence rules it out, on two independent grounds
that both hold in the October data.** It also retracts the central piece of
evidence from my previous message.

Collector: **existing**, `src/pmus_book_tape.py` →
`data/s3_cohort/pmus_btcupdown15m_20261006T232125Z.jsonl`. 2-second polling,
2026-10-06/07, 21 BTC15m windows, 6,500 full-depth snapshots of ~36 levels a
side, plus `bbo` rows carrying cumulative `shares_traded` and `last`. Nothing
new was built. Error rate in the capture: **27.7%** (2,486 failed polls).

Script: `analysis/pmus/reconcile/queue_position.py`.

---

## 1. The 500×500 pattern does not replicate — retracted as a general claim

**0 of 5,574 October book snapshots show a 500×500 touch.** The pattern that
carried my previous conclusion was specific to the 2026-08-30/09-02 BBO
capture and does not describe PMUS BTC15m a month later. You were right not to
treat that NO-GO as proven.

The October book is not thinner, though — it is **deeper**. Touch queue median
**1,553 shares** (per-window medians range 842 → 3,102), against 500 in
August. The structural obstacle is larger here, not smaller. The conclusion
survives; that particular evidence for it does not.

## 2. What the 500 (and the 1,553) actually is — persistent, not replenished

Classifying all 5,916 touch-level transitions, each consecutive pair of
snapshots at the same touch price scored against volume printing at or below
that price between them:

| class | n | share |
|---|---|---|
| **PERSISTENT** (size unchanged, no volume at/below touch) | 5,285 | **89.3%** |
| TOUCH_PRICE_MOVED | 362 | 6.1% |
| **REPLENISHED** (size unchanged *despite* volume at/below touch) | 214 | 3.6% |
| ADDED | 32 | 0.5% |
| CANCELLED (size fell, no volume) | 21 | 0.4% |
| **CONSUMED** (size fell, volume ≥ the fall) | **2** | **0.03%** |

So: a **persistent queue**, occasionally replenished, essentially never left
smaller. Sell flow reached the touch bid in 216 of 5,916 transitions — about
**11 events per 15-minute window** — and in 214 of those 216 the level was
back to the same size within 2 seconds. **Net drawdown of the touch level was
observed twice in 20 windows.**

This is the mechanism that reconciles high volume with no fills, and it is now
measured at the level rather than inferred: an order resting behind ~1,553
persistent shares does not advance, because the queue in front of it is
refilled as fast as it is hit.

### What these snapshots can and cannot decide

**Can:** whether a size change coincided with printed volume at or below the
touch. A fall matched by volume is consumption; a fall with no volume is a
cancellation; an unchanged size despite volume is a refill inside 2 s.

**Cannot:** order identity, our own FIFO position, and whether a refill comes
from the same participant. Two orders of 250 replacing one of 500 reads
identically to one order of 500 persisting. Anything inside a 2-second gap is
unordered. **REPLENISHED and CONSUMED-then-refilled are not separable here** —
that is the whole of item 7.

## 3. Queue and reaching volume by depth (UP side; DOWN mirrors it)

Placement is a point in time; only volume printing at or through the level in
the **180 s after** placement counts, against the queue standing there at
placement.

| ticks behind touch | queue at placement, p50 | volume through in 180 s, p50 | queue cleared | level empty |
|---|---|---|---|---|
| 0 (touch) | 1,553 | 34,375 | 73% | 0.0% |
| 1 | 908 | 31,067 | 66% | 4.6% |
| 2 | 725 | 30,689 | 62% | 4.6% |
| 3 | 735 | 7,103 | 53% | 9.1% |
| 5 | **200** | **0** | 50% | 40.9% |

**This is the trade-off, measured.** Resting five ticks back shortens the queue
roughly eightfold and leaves you alone on the level 41% of the time — and the
median volume reaching that price in three minutes is **zero**. Deeper
placement swaps a queue you cannot clear for flow that never arrives. It is
not a better combination of fill probability and price; it is worse on both
once price is reached, because an unfilled leg at a better price is still
unfilled.

**A correction inside this analysis.** My first version of this table compared
each snapshot's queue against the *whole window's* volume and produced
clearance ratios of 90–170×. That was meaningless — volume printing before a
quote exists cannot clear its queue — and it is exactly the inference item 4
warns against. The numbers above are post-placement only.

Note also that "queue cleared" is **necessary and not sufficient** for a fill.
It does not account for our FIFO position, which this data cannot show, and
the `last`-price attribution behind the volume column is right about 73% of the
time where it commits (scored against the real prints feed in
`CROSS_VENUE_FLOW.md`).

## 4. Prints: available, but not joinable to this book

The real PMUS prints feed (`tid`, price, amount, exchange-assigned buy/sell)
covers **2026-08-31 → 09-02**. The full-depth book covers **2026-10-06/07**.
There is no overlap, so exchange-reported prints could not be joined to the
laddered book, and the volume columns above rest on cumulative-volume deltas
with `last`-price attribution rather than on true prints. That is a real
weakness in this result and the cheapest thing to fix.

## 5. Minimum evidence to separate queue priority from replenishment

Ranked by sufficiency:

1. **Our own resting order's acknowledged queue position or partial fills.**
   Definitive, and only the authenticated order path can provide it — which
   means a live order, which is not authorised and which I have not placed.
2. **Order-level (L3) data with per-order IDs.** Would show whether a refill
   is a new order behind ours or the same order amended. PMUS does not expose
   an order-by-order feed in the captured spec.
3. **Sub-second prints joined to per-level size changes at the same
   resolution.** Not definitive about FIFO, but it would separate
   REPLENISHED from CONSUMED-then-refilled and bound how much of the touch
   level is genuinely consumed. **This is achievable with what exists**: the
   prints feed plus `pmus_book_tape.py` run concurrently at 1–2 s on the same
   windows.

**Can the existing collector provide it? Partly — item 3 only, and not today's
capture.** `pmus_book_tape.py` polls aggregated price levels over REST at 2 s.
It has no order identity, so it can never answer 1 or 2, and its 2-second
granularity is coarser than the refill it is trying to observe. Running it
concurrently with the prints feed on the same windows would deliver item 3 and
would be the first result here resting on true trades rather than inferred
ones.

## 6. Verdict

**No defensible placement opportunity behind or around the dominant PMUS
market maker, on this evidence.** Two independent grounds, both from October
data:

- **At the touch and just behind it**, the queue is persistent and refilled:
  net drawdown observed twice in 5,916 transitions. Priority does not accrue,
  so our position in a 1,553-share queue is not improved by waiting.
- **Deeper**, the queue shortens eightfold but the reaching volume falls to
  zero. There is nothing to be filled by.

**What I am not claiming.** Not that PMUS BTC15m is structurally unsuitable for
all time — the August and October captures differ enough that I will not
generalise a month to a venue. Not that this is proven to the standard a live
probe would need. And the three observations that would most change it are
named above, the cheapest being a concurrent prints-plus-ladder capture on the
same windows, which needs no new service and no orders.

My recommendation is to **deprioritize PMUS BTC15m for L5 replication** and,
if you want one more measurement first, spend it on that concurrent capture
rather than on another policy sweep.

## Labels

No fill is claimed anywhere. Queue sizes are public ladder levels, attributed
to no trader. A cleared queue is necessary, never sufficient. Volume columns
derive from cumulative-volume deltas with `last`-price attribution (≈73%
accurate where it commits), not from exchange prints. Live trading remains
disarmed; nothing was deployed and no risk limit was touched.
