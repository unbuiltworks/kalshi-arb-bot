# Is the repair side systematically left behind? Read-only study.

No order placed, no fill inferred. `TRADE_AT_PRICE`, `TRADE_THROUGH_PRICE`,
`MARKETABLE` and `NO_INTERACTION` are four distinct observations and **none
of them is a fill** — queue position is unknown and unmodelled throughout.

## What the inventory state is, and why

Calling an action a *repair* needs a realized inventory to repair. We have
none of our own in these windows — nothing was ever placed — and inventing
one would mean inferring our own fills, which this study is forbidden to do.

So the trajectory is **L5's own realized inventory**. His activity rows are
completed trades, so his position at each instant is observed fact. What is
hypothetical is only the resting order we never placed; the state it would
have repaired is real.

One convention makes both sides comparable: prices are carried in **own-side
terms** — what the acquirer pays for the outcome being bought (wire for UP,
`1 − wire` for DOWN). Under it a resting buy interacts whenever
`own_side_market_price <= offer_own_price`, for either side, because a DOWN
buy at `q` is a sell of YES at `1 − q` and the inequality flips twice.

## Coverage — and a headline I had to throw away

Twelve windows carry maker-copyable actions. **Seven have zero prints in the
authenticated tape.** A window with no prints cannot show an interaction, so
counting its offers as non-interacting produced a first-pass headline of
*94.5% expired without interaction* which measured nothing but our own
tape coverage. Those 145 offers are dropped.

| window | offers | prints | |
|---|---:|---:|---|
| 1600z | 6 | 2 | tape |
| 1615z | 16 | 268 | tape |
| 1630z | 19 | 321 | tape |
| 1645z | 22 | 213 | tape |
| 1930z | 9 | 13 | tape |
| 1515z / 1530z / 1545z / 1730z / 1745z / 2045z / 2100z | 145 | 0 | **excluded** |

**5 windows, 72 offers, all on 2026-09-23.** Five windows of one day is not
five independent days, and that bounds every number below.

## Headline

| arm | n | interaction | expired w/o interaction | adverse p50 | p75 | p90 |
|---|---:|---:|---:|---:|---:|---:|
| **REDUCE_RESIDUAL** (repair) | 36 | **16.7%** | **83.3%** | 0 | **0.2700** | **0.3300** |
| INCREASE_RESIDUAL (same state, other side) | 34 | **44.1%** | 55.9% | 0 | 0.0100 | 0.0500 |
| FLAT_OPEN | 2 | 50.0% | 50.0% | 0.0800 | — | — |

Interaction mix — repair: 30 `NO_INTERACTION`, 5 `TRADE_THROUGH_PRICE`, 1
`TRADE_AT_PRICE`. Deepening: 19 `NO_INTERACTION`, 15 `TRADE_THROUGH_PRICE`.

**FLAT_OPEN cannot serve as the control.** L5's residual is zero only before
his first trade of a window, so n = 2. The comparison that carries weight is
`INCREASE_RESIDUAL`: the *same inventory state at the same moment, resting on
the other side*. That is the paired control the question actually needs.

Repair orders interact at **roughly a third** the rate of same-state
deepening orders, and their adverse tail is **an order of magnitude larger**
at p75 (0.27 vs 0.01).

## Coverage sensitivity

| min prints | windows | repair n / interaction | deepen n / interaction | repair adv p75 | deepen adv p75 |
|---|---:|---|---|---:|---:|
| ≥1 | 5 | 36 / 16.7% | 34 / 44.1% | 0.2700 | 0.0100 |
| ≥50 | 3 | 26 / 23.1% | 29 / 37.9% | 0.2700 | 0.0300 |
| ≥200 | 3 | 26 / 23.1% | 29 / 37.9% | 0.2700 | 0.0300 |

The direction survives dropping the thin-tape windows.

## Cheap repairs versus expensive repairs

| repair own-price | n | interaction | adverse p75 | pair @ offer p50 | pair @ deteriorated p50 |
|---|---:|---:|---:|---:|---:|
| <0.20 | 2 | 50.0% | 0 | 0.850 | 0.898 |
| <0.40 | 3 | 33.3% | 0 | 1.008 | 1.753 |
| <0.60 | 4 | **0.0%** | 0 | 1.069 | 1.443 |
| <0.80 | 14 | **0.0%** | **0.3100** | 0.972 | **1.302** |
| ≥0.80 | 13 | **30.8%** | 0.0700 | 1.062 | 1.162 |

**The mid-range repair is the one that never interacts.** The 0.60–0.80
bucket — 14 offers, the largest — has a **0% interaction rate** and the
largest adverse tail, and its implied pair cost walks from 0.97 to 1.30. The
≥0.80 bucket interacts 30.8% of the time. That is the shape the question
asked about: a repair becomes executable once it is expensive.

By time to expiry, repairs with **<300s left never interacted at all**
(n=14, 0%).

## Is Probe 08 representative?

Probe 08 offered a repair at 0.56 and the market ran to 0.75 — **+0.19**.

    observed repair adverse move:  p50 0   p75 0.2700   p90 0.3300   max 0.6500
    Probe 08's 0.19 sits at the 69th percentile of 36 observed repair offers

**Representative, and if anything mild.** It is above the median and below
p75. Probe 08 was not an outlier; it was a fairly ordinary draw from a
distribution whose upper half is worse than what Probe 08 experienced.

## What this does not establish

* **Five windows, one day.** Not five independent days. The single largest
  weakness here, and it is a data-availability limit, not a choice.
* n = 36 repair offers. Percentile estimates at p90 rest on a handful.
* An observed trade at our price is **not** a fill. We would have been
  somewhere in a queue we cannot see, so every interaction rate here is an
  **upper bound** on what a resting order would actually have achieved.
* The tape may be incomplete inside covered windows too; 1600z carries 2
  prints.
* No threshold is optimised, no pair-cost ceiling, no chasing rule, no
  directional prediction, no sizing change and no Probe 10 treatment is
  proposed or implied by any of this.
