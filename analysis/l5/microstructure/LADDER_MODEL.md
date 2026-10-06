# The smallest latent ladder consistent with L5's observed fills

**Historical reconstruction only. No P&L optimized, no strategy fitted, no S2
built, prospective S1 untouched** (`441c375e…b6fe`, asserted, never written).

Evidence base: **3,166 BTC15m fills / 12 windows** and **373 BTC5m fills /
23 windows**, zero chain chunk holes, every fill carrying a real block
timestamp, against a whole-second event-driven L2 archive with median 49 bid
levels. Read `LIMITS.md` alongside this.

---

## 1. The model

> **L5 rests ~80-share clips at a small number of price levels on BOTH outcomes
> at once, from at or before the market open; the clips are consumed in
> irregular pieces by whoever lifts them; he re-quotes a level when the touch
> moves and occasionally replenishes it in place; he tilts size against
> inventory; and he stops working a side when it is nearly worthless and nearly
> expired. A material fraction of his matched inventory does not arrive from
> the book at all — it arrives as atomic complete-set pairs at exactly $1.00.**

### 1.1 Exposed clip size

| | BTC15m | BTC5m |
|---|---:|---:|
| clip total ≈ **80**, excess over local background | **26–30×** (n=334–351) | **31–32×** (n=72–74) |
| p90 of all clip totals | **exactly 80.0** | **exactly 80.0** |
| secondary: 20 / 40 / 60 | 2.3× / 2.7× / 2.2× | 4.4× / 6–9× / — |
| **25 / 50 / 75** | 1.6× / 1.5× / **0.94×** | 1.2× / 1.7× / 6.0× (n=2) |
| clips on a 20-grid | **23.1%** | **37.8%** |
| clips on a 25-grid | 4.1% | 4.9% |

**The 25/50/75/100 hypothesis is refuted.** 75 carries *no* excess at all on
BTC15m. The lattice is **80, on a 20-grid**, and it survives the entire
gap-break sweep (2 s → ∞).

**The apparently irregular quantities are partial consumption of regular
clips**, and this is visible in the arithmetic rather than inferred:

```
50.000 + 30.000                                             = 80.0000
 6.000 + 74.000                                             = 80.0000
 4.410 + 75.590                                             = 80.0000
32.000 + 38.000 + 10.000                                    = 80.0000
78.540 +  1.460                                             = 80.0000
10 + 7 + 36.314 + 9.620 + 0.020 + 8.520 + 8.520             = 79.9939
64.000 +  7.000 +  9.000   (BTC5m)                          = 80.0000
50.000 +  5.000 +  5.000 + 20.000   (BTC5m)                 = 80.0000
```

61% of the individual fills inside multi-fill clips are **non-integer**, yet
their totals land on 80. One third of all ≈80 clips are multi-fill.

**100 is a per-fill ceiling, not an order size.** Zero of 3,166 BTC15m fills
exceed 100 and 8 sit at exactly 100 — but clips reach **306**. On BTC5m the
largest single fill is 82.73, so the ceiling is evidenced only on 15m and may
be a venue or counterparty limit rather than anything of L5's.

### 1.2 Levels

Minimum, from executions alone: **≥2 price levels per outcome simultaneously**
(272 BTC15m instances of ≥2 distinct prices inside a single block, 31 with
≥3), observed up to **6 levels within 2 seconds**, spanning **2–3 ticks** (p90).
The true ladder can only be wider — unfilled levels are invisible.

### 1.3 Two-sidedness, and the atomic pair

Same-block, opposite-outcome fills of **identical quantity** at
**complementary prices**:

| | BTC15m | BTC5m |
|---|---:|---:|
| pairs | 104 | 48 |
| summing to **exactly 1.0000** | **90.4%** | **100%** |
| share of all shares acquired | **11.3%** | **28.8%** |
| windows that *open* this way (**corrected**) | **2 of 12** | **10 of 23** |

**CORRECTION (supersedes the first draft).** An earlier version of this file
said "22 of 23 BTC5m windows open this way". That figure counted any
same-second two-sided activity. Keyed properly on the TRANSACTION, the windows
whose first L5 action is an atomic par pair are **10 of 23 (BTC5m)** and
**2 of 12 (BTC15m)**. The pairs are not predominantly an opening phenomenon —
on BTC15m they skew late (TTE fraction remaining p50 0.29).

In the opening pairs one leg is visible in the book — often **from before the
nominal open** — and the other has **no visible size at all** and classifies
mint-consistent. This is the CTF complete-set primitive, directly observed.

**And it is a TAKER action, verified at transaction level.** Of the 95 BTC15m
pairs, **91 (95.8%) are a single transaction**, each containing **exactly two**
L5 legs, and the venue's own topic layout puts L5 as **taker on both legs in
all 91**. So he is not passively receiving a set someone else assembled — he
**aggressively buys a complete set in one atomic transaction at exactly
$1.00**. This half of his flow is not liquidity provision at all.

**It is economically distinct and must not be pooled:**

| | pair cost |
|---|---:|
| atomic paired legs | **exactly 1.0000** (zero gross margin) |
| unpaired resting-bid acquisition, BTC15m | **0.9615** (up 0.5041 + down 0.4574) |
| unpaired resting-bid acquisition, BTC5m | 1.0066 |

So the sub-par pair economics come from the **unpaired resting-bid** flow; the
atomic pairs are break-even gross, fee-bearing, and **taken aggressively**.
Why he pays par as a taker is still **not observable here** — volume-linked
incentives would explain it, but per the operating model a rebate is never
assumed until credited and source-accounted. What *is* now settled is that the
action is deliberate aggression, not passive assignment.

### 1.4 Replenishment and re-quoting

* **Re-quote with the touch:** ρ(Δtouch, Δexecuted price) = **+0.56/+0.59**,
  reproducing in **11/11** BTC15m windows; a price change lands ~4 s later,
  while repeats at one price land in the same second.
* **Replenishment in place is directly observed but rare in the visible book:**
  size at a price *increased* between two fills **109 times** (BTC15m) and
  **13** (BTC5m). Against 1,931 / 202 clip closes caused by a price change,
  **moving the level is the dominant behaviour; topping it up is the exception.**

### 1.5 Inventory tilt

Holding depth-at-touch comparable, he adds **less** to the heavy side:
Cliff's δ **−0.10/−0.11** (BTC15m, negligible) and **−0.76/−0.87** (BTC5m,
large). Same sign in both populations and both directions of imbalance;
magnitude differs by an order of effect size.

### 1.6 Withdrawal

A side stops being worked when it is **nearly worthless and nearly expired** —
BTC15m TTE **102 s** at the last fill vs **482 s** across all fills
(δ **−0.805, large**), price p50 **0.03** — while **tolerating 227 shares of
residual**. Residual does not stop him; time and price do. Weaker on BTC5m
(δ −0.293).

---

## 2. The proposed six-phase structure, tested

| phase | verdict | evidence |
|---|---|---|
| **WAIT / REST** | **supported** | first fill 6/7/7 s (p25/p50/p75), *identical* on both populations; 18.2% (15m) / 44.3% (5m) of clip prices quoted before the nominal open |
| **MULTI-LEVEL TWO-SIDED LIQUIDITY** | **supported** | ≥2 distinct prices in one block ×272, up to 6 in 2 s; 22/23 BTC5m windows open both sides in the same second with no spread or pair-sum compression |
| **PARTIAL CONSUMPTION** | **supported, directly** | irregular fills summing to exactly 80; 61% non-integer pieces; 30% of clips multi-fill |
| **REPLENISHMENT / NEW LEVEL** | **supported, but asymmetric** | new level dominates (1,931 price-change closes) over in-place top-up (109 observed replenishments) |
| **INVENTORY TILT** | **supported, magnitude population-specific** | δ −0.10 (15m) vs −0.87 (5m) |
| **EXPIRY WITHDRAWAL** | **supported on 15m, weak on 5m** | δ −0.805 vs −0.293 |

**One phase is missing from the proposed chain and is required by the data:**
**ATOMIC TAKER PURCHASE OF COMPLETE SETS** — 11.3% (15m) and 28.8% (5m) of all
shares arrive as complete-set pairs at exactly $1.00, in a single transaction,
with L5 **taking both legs**. It is not a stage in the ladder sequence: it
bypasses the book entirely, is aggressive rather than passive, and carries its
own (zero gross margin) economics. Any model that describes L5 purely as a
passive multi-level quoter omits between a ninth and a quarter of his volume.

---

## 3. Evidence grades

### Directly observed — in the archive, no inference

* Every fill's time (block timestamp), outcome, price and quantity; 3,166 +
  373 of them, zero holes.
* The **≈80 clip lattice** and its 26–32× excess over local background.
* **Partial consumption**: irregular quantities summing to exactly 80.
* **Atomic $1.00 pairs**: same block, opposite outcomes, identical quantity,
  prices summing to exactly 1.0000 — and **91 of 95 are a single transaction
  carrying exactly two L5 legs, with L5 as TAKER on both**.
* **Replenishment events**: size at a price increased between two fills (109 /
  13 occurrences).
* The 100-share **per-fill** ceiling on BTC15m (0 of 3,166 above it).
* First-fill timing, 6/7/7 s on both populations.
* The full L2 ladder at every archived second (median 49 bid levels).

### Strongly inferred — one short, well-supported step

* **He rests ~80-share clips.** The clip totals are observed; that they
  correspond to *orders of that size* is the inference. Supported by the
  partial-consumption arithmetic, the p90 sitting exactly at 80 on two
  independent populations, and the 20-grid secondary structure.
* **He quotes ≥2 levels per outcome at once.** Multiple distinct prices inside
  one block cannot come from a single order.
* **He quotes both outcomes simultaneously.** Same-second two-sided openings
  with no compression, plus complementary-price pairs.
* **He re-prices with the touch** (ρ ≈ 0.57, 11/11 windows).
* **He withdraws on time-and-price, not on residual** (δ −0.805; 227 shares of
  residual carried).

### Weakly inferred — direction clear, magnitude or generality not

* **Inventory tilt.** Sign reproduces; magnitude differs by an order of effect
  size between populations (δ −0.10 vs −0.87).
* **Placement times.** The "price first continuously present" bound is an
  **upper** bound on placement and is **censored** for 10.6% (15m) / 21.7%
  (5m) of clips at the archive's own start. Where it is absent entirely (793 /
  136 clips) the price had **no visible size** beforehand.
* **The 100 per-fill ceiling.** Evidenced on 15m only; BTC5m's largest fill is
  82.73, and it may be a venue or counterparty limit, not L5's.
* **The 20-grid secondary levels** (20/40/60). Excess is 2–9× but on tens of
  clips, not hundreds.
* **Why he pays par as a taker on complete sets.** Zero gross margin,
  fee-bearing, and now known to be deliberate aggression rather than passive
  assignment. Consistent with volume-linked incentives, but no rebate is
  credited in anything we hold, so the motive stays unestablished.

### Fundamentally unobservable — no amount of this data will settle it

* **Orders that never filled, and every cancellation.** The ladder's true
  width, its depth beyond the filled levels, and its refresh cadence when not
  hit are all invisible. Every level count here is a **lower bound**.
* **Ownership of any resting size.** `size_at_price` is depth at a price;
  absence of size is not proof he had no order there. Several opening legs
  show *no visible size* yet filled.
* **His actual exposed quantity at any instant.** Observed quantity is the
  counterparty's take. Clip totals bound it from below.
* **Queue position**, and therefore what he would have been filled on had he
  been earlier or later.
* **Sub-second ordering.** Fills inside a block are ordered by `log_index` but
  not separately timed; the archive has no sub-second state at all.
* **Intent.** *Partly resolved:* the pairs are not self-matches — they are
  single transactions in which L5 is taker on both legs, so he is buying a
  complete set from a counterparty. What remains unobservable is **why** he
  pays par for it.

---

## 4. What would resolve the open questions

The two live hypotheses — a **standing multi-level two-sided ladder** versus a
**continuously re-priced narrow quote** — both fit every execution in this
archive, because the evidence that separates them is *unfilled orders*. Only a
dataset where order ownership is known can decide it, which is the prospective
experiment and not this one.

The question this section originally flagged — whether the $1.00 pairs are
self-matches — **has now been answered** by re-keying the pairing test from
block to transaction: 91 of 95 are one transaction with exactly two L5 legs
and L5 as **taker on both**, so they are complete-set purchases from a
counterparty, not self-matches. The open question is narrower and economic: at
zero gross margin and bearing fees, what makes paying par worthwhile. That
needs the credited rebate and reward ledger, not more public chain data.
