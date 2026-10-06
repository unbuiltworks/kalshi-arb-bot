# OPENING-30: the first thirty seconds, every window, both contract lengths

**Historical study only. No strategy, no optimization, no S2.** Clusters were
built from the normalized event sequences *after* they existed; no P&L entered
the clustering or the labelling. Prospective S1 untouched.

Reconstructed at native resolution from T=0 (the market's own opening second)
to T+30 s: every genuine archived book observation and every L5 fill, at its
own second. **A second with no archived observation carries no event** — nothing
is bucketed and no sub-second state is manufactured.

## The headline

> **L5's first fill lands 6–7 seconds after the open, and the distribution is
> the same on both contract lengths: p25/p50/p75 = 6 / 7 / 7 seconds.**

| | BTC15m | BTC5m |
|---|---|---|
| windows | 12 | 23 |
| windows with a fill in the first 30 s | **12 (100%)** | 9 (39%) |
| first fill, seconds after open — p25/p50/p75 | **6 / 7 / 7** | **6 / 7 / 7** |
| first fill — min..max | 6..10 | 4..9 |

This is the tightest and most reproducible regularity in the entire study: two
independent populations, two contract lengths, identical to the second at every
quartile. Note the participation difference — he is present in the opening 30 s
of *every* BTC15m window but only 39% of BTC5m ones.

## The seven questions

**1 — When does he first enter?** Above. Never at T+0: the minimum observed is
4 s (BTC5m) and 6 s (BTC15m), so he is not racing the open.

**2 — Where does he enter?**

| | BTC15m | BTC5m |
|---|---|---|
| first side (up / down) | 5 / 7 | 7 / 2 |
| mechanism at first fill | **10 resting-bid** / 2 mint | 4 resting-bid / **5 mint** |
| offset below touch — p25/p50/p75 | 0 / **0** / 2 ticks | −4 / **−1** / 1 ticks |
| share exactly **at** the touch | **50%** | **0%** |
| entry price — p25/p50/p75 | 0.42 / **0.50** / 0.56 | 0.41 / **0.47** / 0.52 |
| spread at entry (p50) | 0.01 | 0.01 |
| pair-sum of bids at entry (p50) | 0.99 | 0.99 |
| **touch move over the 5 s before entry (p50)** | **0.00** | **0.00** |

Two things reproduce: entry is **near 0.50** into a **1-tick spread**, and
there is **no movement precondition** — the median move in the five seconds
before he enters is exactly zero on both. What differs is aggression: at the
15m open he sits *exactly at* the touch half the time; at the 5m open he never
does, entering inside or above it (p25 −4 ticks).

**3 — How much does he enter with?** **Bimodal, and not conditional on
anything observable.**

| | BTC15m | BTC5m |
|---|---|---|
| first quantity — p25/p50/p75/max | 5.0 / 6.0 / 75.0 / **80.0** | 5.7 / 16.9 / 68.0 / **80.0** |
| distinct values | 9 of 12 | — |
| ρ vs price / offset / 5 s move / seconds-after-open | −0.19 / +0.38 / +0.07 / −0.39, **all p > 0.2** | — |

Two clusters — roughly **3–6 shares** and **68–80 shares** — with an identical
maximum of **80** on both populations. With n = 12 and n = 9 none of the
correlations approaches significance, so **initial size is not explained by the
observable state**. Per `LIMITS.md` §2 the small cluster is most likely a
partial take against a larger resting order rather than a small order.

**4 — Does he expose both outcomes immediately?** **Yes, and simultaneously
when he does.**

| | BTC15m | BTC5m |
|---|---|---|
| opposite fill within 30 s | 7 of 12 (58%) | **9 of 9 (100%)** |
| first → opposite, p50 | **0 s** | **0 s** |
| share in the **same second** | **57%** | **100%** |
| pair-sum at the opposite fill (p50) | 0.99 | 0.99 |

The opposite leg arrives in the *same second* as the first in the majority of
cases, with **no change in pair-sum or spread**. A reactive second leg cannot
be simultaneous with the first; **pre-existing two-sided resting liquidity
can.**

**5 — Is liquidity waiting before the market moves?**

| | BTC15m | BTC5m |
|---|---|---|
| apparent continuous size at his fill price beforehand (p50 / p75) | 6 s / 59 s | **66 s** / 67 s |
| his price already existed 5 s before | 58% | 56% |
| his price already existed 10 s before | 42% | 67% |
| first-fill mechanism | 10 resting / 2 mint | 4 resting / 5 mint |

Mostly yes. Note the archive covers ~60 s **before** the nominal open, which is
why BTC5m shows 66 s of apparent presence — his eventual fill price was quoted
through the pre-open period. On BTC15m, with the first fill at ~7 s, a 6 s
median means the price was present from essentially the opening observation.
**Caveat:** size at a price is not ownership (`LIMITS.md` §1 and §7).

**6 — What happens after the first fill?** **More fills immediately, at the
same price.**

| | BTC15m | BTC5m |
|---|---|---|
| windows with a second fill in 30 s | 12 of 12 | 9 of 9 |
| first → second, p50 | **0 s** | **0 s** |
| second fill on the **same side** | 75% | 44% |
| second price vs first | **same 8**, lower 2, higher 2 | same 4, lower 2, higher 3 |
| fills in the 30 s horizon — p50 / max | 8 / 24 | 4 / 8 |
| inter-fill gaps in the horizon — p50 / p90 | **0 s** / 5 s | — |

The dominant continuation is **same second, same price** — a level being
consumed in pieces, not a new decision.

**7 — Does opening inventory steer the next action?** At the first fill
inventory is zero by construction, so this tests the second action against the
first. BTC15m: second fill same side 9 / opposite 3. BTC5m: 4 / 5. ρ(first qty,
second qty) = +0.14 (p = 0.68) — **no detectable effect at this sample size.**

## Clusters

All 12 BTC15m windows are **distinct** at an 8-second prefix of the normalized
sequence, so there is no repeated literal opening script. The shared structure
is at the level of *phases*, not of exact sequences — which is what the state
machine below encodes. Typical shapes:

```
OO|.|.|.|.|.|drdrdr|.          book settles, then a 3-fill burst at t+6
.|.|OO|.|.|.|.|ururdmdmdm      quiet, book update, then a 5-fill mixed burst
.|.|.|.|.|OO|urdm|.            simultaneous up+down entry at t+6
```
*(`O` = an archived observation that moved a touch; `u`/`d` = an L5 fill on
up/down; suffix `r` resting-bid-consistent, `m` mint-consistent; `.` = a second
with no event.)*

## The smallest repeated opening state machine

Four states. Every transition is conditioned only on information observable at
that moment.

```
   ┌──────────────┐  books publish, both touches established
   │ S0  PRE-OPEN │  (archive covers ~60s before T=0)
   └──────┬───────┘
          │ T=0 .. T+~5s : no L5 execution, book settles
          ▼
   ┌──────────────┐  no movement precondition (5s move p50 = 0.00)
   │ S1  WAIT     │  spread ~1 tick, pair-sum ~0.99
   └──────┬───────┘
          │ T+6..7s  (p25/p50/p75 = 6/7/7, BOTH populations)
          ▼
   ┌──────────────────────────────────────────┐
   │ S2  ENTER                                │
   │   price ~0.50, at or just off the touch  │
   │   size bimodal (~3-6 or ~68-80, cap 80)  │
   │   15m: 10/12 resting-bid, 50% at touch   │
   │   5m : 5/9 mint, 0% at touch             │
   └──────┬───────────────────────────────────┘
          │ dt = 0 s  (p50, both populations)
          ▼
   ┌──────────────────────────────────────────┐
   │ S3  CONSUME  ── same second, same price  │◀─┐
   │   8/12 second fills at the SAME price    │  │ repeats while the
   │   median inter-fill gap 0 s              │  │ level is consumed
   └──────┬───────────────────────────────────┘──┘
          │ in 57% (15m) / 100% (5m) of two-sided cases: dt = 0 s,
          │ with NO spread and NO pair-sum compression
          ▼
   ┌──────────────────────────────────────────┐
   │ S4  TWO-SIDED  ── opposite leg active    │
   │   consistent with liquidity that was     │
   │   ALREADY resting, not a reaction        │
   └──────────────────────────────────────────┘
```

**What the machine deliberately does not contain:**

* **No movement trigger into S2.** The median 5-second move before entry is
  exactly 0.00 on both populations; adding a trigger would describe neither.
* **No size rule in S2.** Initial quantity is bimodal and uncorrelated with
  every observable tested (all p > 0.2), and is in any case the counterparty's
  take rather than his exposure.
* **No "wait for the opposite" edge into S4.** There is no compression to wait
  for — pair-sum and spread are unchanged — and in the majority of cases S4 is
  entered in the same second as S2.

The machine is therefore **mostly a clock and a price level**: wait ~7 s, rest
near 0.50 at or just off the touch on both outcomes, and let the levels be
consumed. Everything that looks like a decision afterwards — the repeats, the
opposite leg — is consistent with liquidity that was already there.

**This is a description, not a strategy, and it is not sufficient to build
one:** the archive cannot show the orders that did not fill, so the machine
specifies *where executions happened*, never *what was exposed*.
