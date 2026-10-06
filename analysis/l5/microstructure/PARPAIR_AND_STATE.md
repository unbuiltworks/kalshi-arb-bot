# The $1.00 complete-set purchases, and the 80-clip state machine

**Historical study only. No P&L optimized, no parameter fitted, no deployable
candidate built, no live orders. Prospective S1 untouched** (`441c375e…b6fe`).
The 80-share lattice and the par-pair finding are taken as **frozen input**
from tag `l5-microstructure-frozen-2026-10-06`, not re-estimated here.

---

# Part 1 — What are the par pairs for?

**Definition, keyed on the TRANSACTION and never the block:** equal-quantity
opposite-outcome fills at prices summing to exactly 1.0000.

| | BTC15m | BTC5m |
|---|---:|---:|
| pairs | **92** | **48** |
| windows containing one | 12 / 12 | 15 / 23 |
| pair legs as a share of fills | 5.8% | 25.7% |
| **pair legs as a share of volume** | **9.7%** | **28.8%** |
| **L5 taker on BOTH legs** | **100%** | **100%** |
| pairs per window (p50 / max) | 9 / 15 | 3 / 7 |

## The seven hypotheses

| # | hypothesis | BTC15m | BTC5m | verdict |
|---|---|---|---|---|
| H1 | initializes a new market | 2.2% are the first L5 action; p50 **175** prior actions | 18.8%; p50 **7** prior actions | **refuted on 15m**, weak on 5m |
| H2 | replenishes matched inventory after passive depletion | passive shares 60 s before **463.6** vs after **445.6**; only **42%** have more after | 80.0 vs 90.0; **50%** | **refuted** — no depletion-then-refill signature; it is a coin flip |
| H3 | reduces directional imbalance | abs-imbalance delta **0.0 … 0.0**; **0%** of pairs change residual | identical | **refuted exactly** — arithmetic, then confirmed in data |
| H4 | precedes a new passive ladder cycle | passive fill within 30 s **before 96.7%**, **after 95.7%** | 35.4% / 47.9% | **refuted on 15m** (symmetric — it sits *inside* continuous passive flow); weakly supported on 5m |
| H5 | occurs at regular 80-share quantities | only **23.9%** at 80; 35.9% on the 20-grid; 47 distinct sizes | 25.0%; 39.6%; 31 distinct | **partial** — 80 is the modal size but is a minority |
| H6 | occurs preferentially at the opening | pairs p50 **637 s** after open vs **423 s** for all fills; 3.3% vs 4.1% in the first 30 s | 177 s vs 184 s | **refuted** — on 15m they skew **late** (TTE fraction remaining p50 **0.29**) |
| H7 | correlates with incentive/reward periods | — | — | **UNDETERMINABLE** |

**A correction to my own earlier reading.** I previously said "22 of 23 BTC5m
windows open with both outcomes in the same second." Keyed properly on the
transaction, only **10 of 23 (43%)** BTC5m windows and **2 of 12 (17%)** BTC15m
windows actually *begin* with a par pair. The earlier figure counted any
same-second two-sided activity, not atomic pairs.

**H7 cannot be answered here and is not estimated.** No credited rebate or
reward appears in any artifact this repository holds for these windows, the
operating model forbids assuming one, and the archive spans 2026-08-21…24 —
far too short to resolve an incentive regime against a baseline. What would
settle it is the venue's credited rebate/reward ledger for L5's address over a
span containing a regime change.

## What they actually are, economically

| | BTC15m | BTC5m |
|---|---:|---:|
| gross margin, every single pair | **exactly $0.00** | **exactly $0.00** |
| cash out vs settlement payout | 3,812.93 / 3,812.93 | 1,761.50 / 1,761.50 |
| model-implied fee (0.07·p·(1−p) per leg) | **$72.85** | **$33.94** |
| net after model-implied fee, per pair (p50) | **−$0.54** | **−$0.47** |

**Every par pair is a guaranteed-zero-gross-margin trade that is strictly
loss-making once the operating model's own fee formula is applied** —
**$106.79** of model-implied fees across both populations for zero gross
return. The fee is *model-implied, not credited*: it comes from
`L5_OPERATING_MODEL.md`'s recorded identity that a BUY's `usdcSize` exceeds
`price × size` by exactly `0.07·p·(1−p)`. No rebate is credited in anything we
hold.

**So what the pairs do is narrow and specific:** they inject matched inventory
(+q matched, residual *exactly* unchanged) at par, as a **taker**, mid-window,
at a measurable cost. They do not open markets, do not rebalance, do not
refill after depletion, and do not start ladder cycles. The only coherent
remaining readings are **volume generation for a volume-linked incentive** or
**matched-inventory accumulation treated as worth paying the spread-free fee
for** — and distinguishing them needs the credited reward ledger, not more
chain data.

---

# Part 2 — The 80-clip state machine

Every fill is assigned to exactly one unit: an **INJECTION** (par-pair leg) or
a **CLIP** (maximal run of consecutive passive fills at one price, broken by a
price change, a >10 s gap, or observable replenishment).

## Lattice fit — strong

| | BTC15m | BTC5m |
|---|---:|---:|
| clips | 1,936 | 177 |
| **share explainable by ONE ≤80-share clip** | **96.4%** | **94.4%** |
| implied latent clips per observed clip | 1866×1, 68×2, 1×3, 1×4 | 167×1, 10×2 |
| clip volume needing >1 latent clip | 11.6% | 10.0% |
| clips landing on the 20-grid | 22.2% | 37.8% |

## Transition rules — measured

| transition | BTC15m | BTC5m |
|---|---:|---:|
| `MOVE_LEVEL` (new price) | **56.8%** | 14.7% |
| `TWO_SIDED_ACQUISITION_MOVE_LEVEL` | 30.3% | **37.8%** |
| `MATCHED_INJECTION` (atomic $1 pair) | 4.5% | 21.3% |
| `REPLENISH_SAME_PRICE` | 5.2% | 4.9% |
| `TWO_SIDED…_REPLENISH_SAME_PRICE` | 2.0% | 0.9% |
| `OPEN_SIDE` (+ two-sided variant) | 1.2% | 20.4% |
| `WITHDRAWAL` (terminal clip per side) | 24 events, TTE p50 **102 s** | 46 events, TTE p50 **80 s** |

**Moving the level dominates.** On BTC15m 87.1% of all transitions are a move
to a new price; replenishing a level in place is **7.2%**. The ladder is
re-priced far more often than it is topped up — consistent with the 1,931
price-change clip closes against 109 observed replenishments.

## Does it *explain* the sequence? Two measures, and they disagree

**Assignment coverage is 100% of fills and 100% of volume on both populations
— and that is the WEAK measure, true by construction.** Every fill is either a
pair leg or lands in some clip, so near-total coverage demonstrates nothing.
It is reported only so it cannot be mistaken for explanatory power.

The strong measure is whether the current state predicts the next one:

| | BTC15m | BTC5m |
|---|---:|---:|
| transitions | 2,028 | 225 |
| base rate (modal transition) | 0.568 | 0.378 |
| 1st-order accuracy, next given current | 0.573 | 0.634 |
| **lift over base rate** | **1.01** | **1.68** |
| entropy, unconditional → conditional | 1.612 → 1.508 bits | 2.358 → **1.358** bits |

> **On BTC15m the state machine does not explain the sequence — it
> re-describes it.** Knowing the current transition buys **0.10 bits** and
> lifts prediction by **1%** over simply always guessing `MOVE_LEVEL`.
>
> **On BTC5m it genuinely does explain a substantial part**: **1.0 bit** gained
> and a **1.68×** lift, because those windows are short, have few actions, and
> follow a recognisable OPEN → acquire → WITHDRAW arc.

---

# Part 3 — What fraction is explained, and what is not

## Explained

| | BTC15m | BTC5m |
|---|---:|---:|
| volume assigned to the representation | 100% | 100% |
| — as atomic $1 injections | **9.7%** | **28.8%** |
| — as passive 80-lattice clips | **90.3%** | **71.2%** |
| clips needing only one ≤80 latent clip | **96.4%** | **94.4%** |
| sequence structure explained (lift over base rate) | **~1%** | **~68%** |

## Not explained

1. **The transition sequence on BTC15m.** Lift 1.01. A first-order state
   machine on these units carries almost no information about what L5 does
   next. Either the real state includes variables not in this representation
   (book, inventory, time), or the sequence is close to memoryless.
2. **11.6% / 10.0% of clip volume** needs more than one latent 80-clip, and 70
   BTC15m clips imply 2–4 of them. Whether those are several clips at one
   price or one larger order is **not decidable** from executions.
3. **77.8% (15m) / 62.2% (5m) of clip totals are off the 20-grid.** Most clips
   are *partially* consumed by an unknown amount, so the lattice constrains
   the ceiling, not the actual exposure.
4. **The quantity of any individual fill** — it is the counterparty's take,
   not a choice of L5's, and nothing observable predicts it (|ρ| ≤ 0.29 across
   22 features, from the frozen study).
5. **Why the injections happen at all.** Zero gross margin, negative after the
   model-implied fee, mid-window, as taker. H7 is undeterminable from this
   data.
6. **Everything in `LIMITS.md`** — above all unfilled and cancelled orders,
   which is why every clip count remains a **lower bound** and why a standing
   ladder cannot be separated from a continuously re-priced quote.

## The honest answer to the question asked

> **Can the observed L5 sequence be substantially explained as a state machine
> operating on 80-share latent clips plus atomic $1 complete-set injections?**

**As a representation: yes.** It assigns 100% of volume, and 96.4%/94.4% of
passive clips are consistent with a single ≤80-share latent clip. The
vocabulary — clip, move level, replenish, two-sided acquisition, injection,
withdrawal — covers everything L5 is observed to do.

**As an explanation of the sequence: only on BTC5m.** There it gains a full
bit and lifts prediction 1.68×. On BTC15m the same machine gains 0.10 bits and
lifts prediction by 1%, which is not explanation.

**The representation is therefore a good description of L5's action
*vocabulary* and a poor model of his action *timing and selection* on the
longer contract.** What is missing is not more clip structure — it is the
state he is conditioning on, which this representation deliberately excludes
and which the frozen study showed is itself weakly related to everything
observable.

**No deployable candidate follows from this, and none is proposed.**
