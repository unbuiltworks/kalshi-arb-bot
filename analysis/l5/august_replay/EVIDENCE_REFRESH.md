# What bounds how long passive interest plausibly stays live

**Derived from the diagnostic window only** (`btc-updown-15m-1787300100`,
2026-08-21 08:15:00Z–08:30:00Z). The other 11 L5-active windows are the
generality test and were **not read** to produce these numbers — deriving the
bound from all 12 would leak the test set into the mechanism.

`evidence_refresh.py` computes no fill, no position and no cash figure. The
question answered is **not** "which timeout maximises P&L?" — that question is
not asked anywhere in this study.

## The three observables, from L5's own 398 fills against the public book

### 1. Inter-fill cadence on the same token — the time bound

Seconds between consecutive L5 fills on the *same* token (n = 396):

| p50 | p90 | p95 | p99 | **max** |
|---:|---:|---:|---:|---:|
| **1 s** | 12 s | 18 s | 48 s | **69 s** |

L5's passive interest on a side is consumed and re-established essentially
continuously. Across the whole window he is **never once** absent from a token
for longer than **69 seconds**.

This is the direct refutation of `order_lifetime_s: None`. S1 left a bid
untouched for **876 seconds** — 12.7× the longest gap L5 ever exhibits.

### 2. Offset below the touch at T−1 — the displacement bound

Ticks between the prevailing best bid and L5's fill price (n = 397):

| p50 | p90 | p95 | p99 | **max** |
|---:|---:|---:|---:|---:|
| **1** | 4 | 5 | **6** | 9 |

L5 is never filled at a price more than **9 ticks** behind the touch, and
99% of his fills land within **6**. An order sitting further behind the touch
than that has **no evidential basis** for being live interest of this kind.

S1's stranded UP order ended the window **62 ticks** behind the market.

### 3. Staleness of the fill price — why it is the weaker bound

Seconds the fill price had been *strictly below* the best bid when the fill
arrived (n resolved = 360, unresolved = 38, never defaulted):

| | at/above touch | p50 | p90 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|---:|
| all | **58.6 %** (211) | 0 s | 64 s | 106 s | 259 s | 420 s |
| positive only (n=149) | — | 32 s | 129 s | 170 s | 284 s | 420 s |

58.6% of his fills are at a price still *at or above* the touch. The positive
tail runs longer than the cadence bound, and that is not a contradiction: a
price one tick behind the touch can have been behind it for a long while if
the touch stepped up once and held. Because this measures the *price level's*
history rather than the *interest's* age, it is reported but **not** used as
the threshold.

## The rule this supports

Two causal triggers, each firing on observed market movement rather than on
the order having filled:

| trigger | threshold | read from |
|---|---|---|
| `REPRICE_ON_DISPLACEMENT` | touch is more than **6 ticks** above our price | p99 of L5's own fill-offset distribution (§2) |
| `REPRICE_ON_STALENESS` | our price has been strictly below the touch for more than **69 s** | max observed same-token inter-fill gap (§1) |

On firing, the order is cancelled and re-placed at the current best bid, with
`queue_ahead` re-measured at the new level. A replacement is **never** a fill
and is counted separately.

### Why these percentiles, and in which direction they are conservative

A *larger* threshold reprices *less* often, leaves an order stale *longer*,
and therefore manufactures *fewer* hypothetical fills. Both thresholds are
deliberately taken at the **loose** end of the evidence — the 99th percentile
of offsets rather than the median, and the **maximum** observed inter-fill gap
rather than p95 — so the rule errs toward understating S2's activity. No
threshold was chosen by looking at an S2 outcome, and none was swept.

### What this rule is not

* **Not a lifetime.** There is no fixed timeout. An order at the touch in a
  quiet book is never replaced, however old; an order the market has walked
  away from is replaced immediately, however young.
* **Not tuned.** Both numbers are read off two tables above, computed before
  any S2 code existed, from L5's behaviour and not from S2's results.
* **Not attributed.** `best_bid` is a price level in a public book. "The price
  was at or above the best bid" says it was at the front of the market, not
  that the order there was L5's.
