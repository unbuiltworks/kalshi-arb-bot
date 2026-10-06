# Where L5 appears to place liquidity, and how much he exposes

**Historical study only. No strategy is fitted, no P&L is optimized, no S2 is
built here, and prospective S1 is untouched** (fingerprint asserted
`441c375e…b6fe`, never written).

Read `LIMITS.md` first — every rule below is bounded by it, above all by the
fact that **unfilled and cancelled orders are invisible** and that observed
"quantity" is the counterparty's take, not his exposure.

## Populations, kept separate throughout

| | BTC15m | BTC5m |
|---|---|---|
| L5 fills | **3,166** | **373** |
| windows | 12 | 23 |
| chain chunk holes | 0 | 0 |
| fills without a real block timestamp | 0 | 0 |
| discovery window (reported, never pooled) | `btc-updown-15m-1787300100` (398 fills) | none — never previously inspected |
| **confirmation set** | **11 windows, 2,768 fills** | **23 windows, 373 fills** |
| mechanism split (confirmation) | 1,894 resting / 832 mint / 27 inside / 15 unresolved | 163 resting / 144 mint / 2 inside / **64 unresolved** |

Archive resolution, measured before any inference and **identical on both**:
whole seconds (every microsecond zero), **zero duplicate `(token, second)`
rows**, event-driven (0.24% / 0.07% of consecutive pairs repeat a ladder),
median **49 bid levels** of depth. Nothing interpolated anywhere.

---

## Q1 — What movement precedes a purchase?

Measured against a control of archived seconds in the same windows ≥5 s from
any L5 fill.

**The single most reproducible fact: immediately before a fill, the book is
still.** The touch is *unchanged* over the second ending at the last
observation before the fill in **90.3%** of BTC15m resting-bid fills and
**86.5%** of BTC5m ones. That is the signature of resting interest being hit,
not of a reactive order.

**Over longer leads the touch falls into his fills — but only on BTC15m.**

| | BTC15m resting | BTC15m mint | BTC5m resting | BTC5m mint |
|---|---:|---:|---:|---:|
| Cliff's δ vs control, 5 s | **−0.185** | **−0.321** | +0.000 | +0.037 |
| Cliff's δ vs control, 30 s | **−0.186** | −0.184 | −0.045 | −0.012 |
| downward skew reproduces (30 s) | **11/11** | 9/11 | underpowered | underpowered |

**Exception, and it matters: this does not generalize to BTC5m**, where the
effect is indistinguishable from zero at every lead. "He adds as the outcome
falls" is a BTC15m statement.

He also fills into **thinner** books than baseline (depth at touch p50 98 vs
145.5, δ −0.155), and spread is unchanged (0.01 both) — he is not waiting for
a wide spread.

## Q2 — What determines quantity?

**Almost nothing observable does.** Across 22 candidate features — price,
offset, spread, movement at four leads, four depth measures, TTE, seconds
since open, inventory imbalance, matched inventory, previous fill size and
gap, apparent resting duration — **no |ρ| exceeds 0.29 in either population.**

| strongest correlate | BTC15m resting | BTC15m mint | BTC5m resting | BTC5m mint |
|---|---:|---:|---:|---:|
| previous same-side fill quantity | **+0.199** | +0.146 | **+0.282** | **+0.320** |
| reproduces per window | **11/11** | 8/11 | underpowered | underpowered |

Everything else — depth, TTE, imbalance, movement — sits below |ρ| ≈ 0.13.
Quantity by TTE bucket is flat (15.0 / 19.0 / 10.0 / 11.3 shares).

**The honest reading is in `LIMITS.md` §2:** quantity is fragmented (1,173
distinct values in 2,768 fills, 55% non-integer) and capped at exactly 100.
It is the counterparty's take against his resting interest, not a size he
chose per event. Mild autocorrelation with the previous same-side fill is what
you would expect from **one order being consumed in pieces**, which is also
what Q3 and Q4 independently show.

## Q3 — Does he reprice with the market?

**Yes, strongly, and this reproduces 11/11.**

| | BTC15m resting | BTC15m mint | BTC5m resting | BTC5m mint |
|---|---:|---:|---:|---:|
| ρ(Δtouch, Δexecuted price) | **+0.563** | **+0.589** | +0.465 | +0.483 |
| reproduces | **11/11** | **11/11** | underpowered | +1/1 |
| share of consecutive same-side fills at the **same** price | 0.401 | 0.356 | 0.402 | 0.446 |
| gap when the price **changed** (p50 / p90) | 4 s / 24 s | 4 s / 22 s | — | — |
| gap when the price was **unchanged** (p50 / p90) | **0 s** / 5 s | **0 s** / 3 s | — | — |

The two-regime structure is the finding: ~40% of consecutive fills land at the
**same price in the same second** (a level being consumed), and when the price
does move it moves **with the touch** within ~4 s.

## Q4 — Does he maintain multiple resting levels? *(inference)*

**Lower bound only** — unfilled orders are unobservable, so this cannot be the
ladder itself.

| | BTC15m | BTC5m |
|---|---:|---:|
| share of fills inside same-block multi-fill groups | **0.599** | 0.550 |
| groups with ≥2 distinct prices | **272** | 14 |
| groups with ≥3 distinct prices | 31 | — |
| **max distinct prices in one ≤2 s group** | **6** | 5 |
| price-span of a group (p90) | 2–3 ticks | — |

So in at least 272 BTC15m instances he was executed at **two or more distinct
prices inside a single block**, and in one case at **six levels within two
seconds**. A single resting order cannot do that. **Minimum latent ladder ≥ 2
levels, observed up to 6, spanning ~2–3 ticks.**

Latent-resting evidence, same caveat:

| | BTC15m | BTC5m |
|---|---:|---:|
| apparent continuous size at his fill price before the fill (p50 / p75) | **38 s** / 151 s | **64 s** / — |
| his price already existed 30 s before | **62.6%** | 49.2% |
| his price was at/above the touch 30 s before | 41.7% | — |

**A fill at time T is more often than not explicable by liquidity that was
already at that price before the market reached it.**

## Q5 — When does the opposite outcome start filling?

| | BTC15m | BTC5m |
|---|---:|---:|
| windows where the opposite side ever filled | 11/11 | — |
| first → opposite (p25 / p50 / p75 / max) | 0 s / **15 s** / 25 s / 129 s | **0 s** |
| fills before the opposite started (p50) | 6 | — |
| inventory on the first side when the opposite began (p50) | 131.6 sh | — |
| **pair-sum of the two bids, at first → at opposite** | **0.99 → 0.99** | **0.99 → 0.99** |
| **spread, at first → at opposite** | **0.01 → 0.01** | **0.01 → 0.01** |

**The decisive negative: there is no compression.** Neither the pair-sum nor
the spread moves between the first fill and the first opposite fill, in either
population. Two-sided acquisition is **not** triggered by the two touches
converging toward par. With ≥25% of windows starting both sides in the *same
second*, the pattern is consistent with **independently resting two-sided
liquidity**, not with a reactive second leg.

## Q6 — How does sizing react to inventory?

Holding the book comparable (same depth-at-touch bucket), when he is heavy on
a side he adds **less** to that side and **more** to the other. Direction is
identical in both populations and both directions of imbalance:

| | BTC15m | BTC5m |
|---|---:|---:|
| heavy UP: δ(heavy-side qty − light-side qty) | −0.096 *(negligible)* | **−0.755 *(large)*** |
| heavy DOWN: δ | −0.113 *(negligible)* | **−0.868 *(large)*** |
| heavy-side vs light-side median qty (15m) | 12.5 vs 19.0 / 11.0 vs 16.0 | — |

**Consistent in sign, wildly different in magnitude.** On BTC5m inventory is a
dominant determinant of size; on BTC15m it is a barely-detectable tilt. This
is the largest cross-population disagreement in the study.

## Q7 — What precedes him stopping on a side?

*No cancellation is inferred — the archive cannot show one. These are the
conditions at the last observed fill on a side.*

| | BTC15m | BTC5m |
|---|---:|---:|
| TTE at the last fill (p50) | **102 s** | 74 s |
| TTE at all fills (p50) | 482 s | 116 s |
| **Cliff's δ, last vs all** | **−0.805 *(large)*** | −0.293 *(small)* |
| price at the last fill (p50) | **0.03** | 0.49 |
| residual at the last fill (p50) | 227.5 sh | 38.1 sh |
| touch 30 s after the last fill | +0.01, 73% positive | — |

**On BTC15m this is the largest effect in the entire study.** A side stops
being added to when it is **nearly worthless and nearly expired** — price
~3 cents, ~100 s left — and the market then moves further away (73% of the
time). **Residual does not stop him**: he is carrying 227 shares of imbalance
at that point and keeps going until time and price run out.

## Q8 — Mechanisms were never mixed

Every table above is reported per mechanism. The two differ systematically:
mint-consistent fills carry **more** pre-fill movement on BTC15m (δ −0.321 vs
−0.185 at 5 s), a **less still** book in the final second (77.6% unchanged vs
90.3%), larger median size (19.0 vs 11.7) and a lower same-price share (0.356
vs 0.401). Pooling them would have produced a placement rule describing
neither.

---

# The smallest rule set that explains the observations

Given **only** information observable immediately before each action. Each rule
carries the evidence that supports it and the population it holds in.

> **R1 — Rest, do not chase.** Liquidity is already sitting at the price when
> the fill happens. *(Touch unchanged in the final second: 90.3% / 86.5%.
> Continuous size at the fill price beforehand: p50 38 s / 64 s. Price already
> present 30 s earlier: 62.6% / 49.2%.)* **Both populations.**

> **R2 — Rest at several prices at once, within a few ticks.** *(≥2 distinct
> prices in one block in 272 instances; up to 6 levels within 2 s; p90 span
> 2–3 ticks.)* **Both populations. Lower bound — the true ladder is
> unobservable.**

> **R3 — Rest on both outcomes independently, from the start.** *(Opposite
> side begins with **no** spread or pair-sum compression, 0.99 → 0.99 and
> 0.01 → 0.01; ≥25% of windows start both sides in the same second.)* **Both
> populations.**

> **R4 — Follow the touch when it moves, within a few seconds.** *(ρ(Δtouch,
> Δprice) = +0.56/+0.59, reproduces 11/11; price changes land ~4 s apart,
> while repeats at one price land in the same second.)* **Both populations.**

> **R5 — Tilt size against inventory: add less to the heavy side.**
> *(Direction identical in both; δ −0.10/−0.11 on BTC15m, **−0.76/−0.87** on
> BTC5m.)* **Both populations in sign; only BTC5m in magnitude.**

> **R6 — Withdraw from a side when it is nearly worthless and nearly expired,
> not when inventory is lopsided.** *(TTE 102 s vs 482 s, δ −0.805 large;
> price p50 0.03; residual 227 shares tolerated.)* **BTC15m; weaker on
> BTC5m (δ −0.293).**

> **R7 — Enter about 7 seconds after the open, at the touch, near 0.50, with
> no movement precondition.** *(p25/p50/p75 = 6/7/7 s, identical on both
> populations.)* See `OPENING30_FINDINGS.md`. **Both populations.**

**Deliberately absent — and this is a result, not an omission:**

* **No quantity rule.** Nothing observable predicts size (|ρ| ≤ 0.29 across 22
  features). Per `LIMITS.md` §2, observed quantity is the counterparty's take,
  not his exposure, so a size rule cannot be inferred from this data at all.
* **No "buy the dip" rule.** The pre-fill decline is real and reproduces 11/11
  on BTC15m but is **absent on BTC5m** (δ ≈ 0.000). R1 — resting interest
  being reached — explains both populations; a movement trigger explains only
  one.

### What would distinguish the remaining hypotheses

R1+R2+R3 (a standing two-sided multi-level ladder) and a continuously-repriced
single quote both fit the executions. The archive cannot separate them, because
the discriminating evidence is **unfilled orders**, which it does not contain.
Separating them needs a dataset where we know which orders are ours — the
prospective experiment, not this one.
