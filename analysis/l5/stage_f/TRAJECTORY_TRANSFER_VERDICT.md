# Does L5's inventory trajectory contain a transferable edge at our size?

**Date:** 2026-11-01
**Verdict: NO-GO.** The trajectory's apparent destination — a 0.96 pair VWAP —
is **manufactured by a program that loses money**. Reaching it costs L5
**−$2,842.95**, which is **17.3% of his entire +$16,436 BTC15m trading net**.
Two of our existing gates are what keep us out of it.

**Reused, not rebuilt:** `analysis/l5/inventory_trajectory/trajectory.py` and
its `TRAJECTORY_ANSWER.md`, `src/l5_inventory_translation.py`,
`src/l5_state_trajectory.py`, `src/l5_expectancy_replay.py`,
`analysis/l5/historical_l2/`, `data/l5_strand_replay/`. No new module, service
or branch.

---

## 1. What the repository already established (items 1, 3)

`TRAJECTORY_ANSWER.md`, 527 windows / 62,020 fills, cost basis `usdcSize`:

- **L5 never sells.** 66,949 BTC15m trades, every one a BUY. Positions are
  only ever redeemed at settlement, so the pair VWAP can move in exactly one
  direction — by buying *more*.
- **At first pair formation the median pair cost is 0.9900**, at fill #3, ~49s
  into the window. Only **27.9%** of his own entries clear our 0.96 ceiling.
- The 0.9624 lifetime figure is an **emergent running VWAP**, not a price he
  acquires pairs at.
- Its conclusion: *"The 0.96 ceiling is not mistuned. It is applied at the
  wrong point in the path."*
- Same-side persistence 69.2%; repairing imbalance is a **coin flip (48.3%)**.

That is the trajectory reconstruction (item 1) and the transition census
(item 3), already done. I did not redo it. What was **not** done is the
size-normalized economics, which is the actual question here.

## 2. Normalized to our envelope, the destination is unreachable (item 2)

The existing decline is reported at **deciles of each window's own fill
count**. Our constraint is **absolute** — we get 3.17 fills — so I recomputed
the running pair VWAP against absolute fill number:

| fill # | windows | median VWAP | p25 | p75 | median \|imb\| |
|---|---|---|---|---|---|
| 2 | 161 | 0.9900 | 0.9600 | 1.0100 | 8.03 |
| **3** | 261 | **0.9900** | 0.9650 | 1.0250 | 19.19 |
| 4 | 333 | 0.9916 | 0.9644 | 1.0300 | 24.09 |
| 10 | 449 | 0.9942 | 0.9524 | 1.0308 | 41.22 |
| 25 | 394 | 0.9903 | 0.9590 | 1.0305 | 76.16 |
| 50 | 332 | 0.9937 | 0.9638 | 1.0244 | 138.04 |
| 100 | 291 | 0.9876 | 0.9623 | 1.0106 | 153.10 |
| 150 | 209 | 0.9810 | 0.9569 | 1.0023 | 194.71 |
| 200 | 118 | 0.9831 | 0.9625 | 1.0014 | 187.32 |

**The median pair VWAP never reaches ≤0.96 at any absolute fill count.** It
sits between 0.981 and 0.994 from fill 2 all the way to fill 200.

This is not a contradiction of the decile table — it **locates** it. The
decline to 0.96 does not happen progressively with accumulation. It happens in
each window's **final fills**, which the percentage view puts at 100% and the
absolute view never reaches because windows end at different lengths. The
paired within-window test in `TRAJECTORY_ANSWER.md` §4 agrees: fill 100 =
0.9829 → final = 0.9599.

At our fill count the achievable matched margin is **+0.0084 to +0.0100 per
contract** — about one cent. Against a measured residual loss of
**−$0.2315/unit**, one cent of matched margin on ~2 matched contracts does not
pay for a single stranded leg.

## 3. What the final fills actually are (items 4, 5)

Splitting every window's fills at the 90th percentile of its own fill count:

| bucket | n | own_px p10 | **p50** | p90 | maker | bought the side that **LOST** | median T+ |
|---|---|---|---|---|---|---|---|
| first 90% | 56,791 | 0.110 | 0.420 | 0.810 | 88.4% | 56.0% | 429s |
| **last 10%** | 6,543 | **0.020** | **0.110** | 0.963 | 83.0% | **65.9%** | **792s** |

**57.5% of last-10% fills are at an own-price below 0.20; 47.5% below 0.10.**
The VWAP destination is reached by buying the **cheap, losing side late**.

### The economics of that program, on L5's own realized settlements

Late fills (last 10% of a window) at own-price < 0.20:

| | |
|---|---|
| fills | **3,765** |
| contracts | **80,171.6** (mean clip 21.3) |
| mean own-price paid | **0.0567** |
| the side **lost** | **3,707 / 3,765 = 98.5%** |
| market-implied loss rate (1 − price) | 94.33% |
| **realized** loss rate | **98.46%** |
| **edge per contract (win rate − price)** | **−0.0413** |
| spent | **$4,000.06** |
| redeemed | **$1,157.11** |
| **NET** | **−$2,842.95** |
| net per contract | **−$0.0355** |
| net per fill | **−$0.7551** |

**He pays 5.67¢ for something worth 1.54¢.** This is not averaging down into a
better position; it is adding cheap units to the denominator of a statistic
while losing 3.55¢ on every contract.

**Incentives do not rescue it.** At his measured **$0.098/fill**, the program
earns **+$368.97** of incentive against **−$2,842.95** of trading loss — still
**−$2,474**. Our venue credits us **$0.00**
(`UNVERIFIED_NO_VENUE_CREDIT_RECORD`), so for us it is the full −$2,843 shape.

**And it is not one era's artifact.** Net per fill by month: 2026-03
**−$0.9821**, 2026-04 **−$0.7787**, 2026-08 **−$0.7516**. Stable across a
~90× change in volume.

### Why buying the cheap side cannot help, stated plainly

Holding the winning side, a pair redeems $1.00 — and the winning side **alone**
also redeems $1.00. Buying the losing complement at 0.0567 therefore buys
nothing and costs 0.0567. It pays only when you are wrong about which side
wins, which happens 1.54% of the time against a 5.67¢ price. The marginal EV
of the purchase is −0.0413 regardless of what else is held, because the
existing legs are sunk.

## 4. The single largest transferable trajectory mismatch

> **Our engine refuses to buy a leg below own-price 0.20 and refuses any
> imbalance-increasing fill after T+600. Those two gates are exactly what
> excludes us from L5's late cheap-side accumulation — a programme that costs
> him $2,843, or 17.3% of his BTC15m net.**

The mismatch is real, it is the largest one in the trajectory, and **it
transfers negatively.** From the previous action census: `MIN_FIRST_LEG_PRICE
0.20` sits against 30.0% of his ADDs (p10 own-price 0.060), and
`ENTRY_CUTOFF_600` against 36.7% of them. The two gates that look most
restrictive when counting fills are the two doing the most economic work.

This also retires the pair-ceiling question on new evidence rather than on the
earlier instruction alone: the 0.96 ceiling being "applied at the wrong point
in the path" is **true as a description and harmless as a defect**, because the
path's destination is reached by losing money. Moving the ceiling to admit the
origin (0.99) would admit the origin of a programme whose end is −$2,843.

## 5. Missing order-book evidence (item 7) — stated, not assumed

**For L5's 556 BTC15m windows we have ZERO order-book depth.** The canonical
sequence carries his executed price, quantity, maker/taker flag and
settlement, and no book. What exists is not a substitute:

| source | what it is | covers L5's windows? |
|---|---|---|
| `l5_canonical_sequence.csv.gz` | fills: price, qty, maker/taker, settlement | **no depth at all** |
| `analysis/l5/historical_l2/bbo_offset.json` | a BBO-offset study (`fills`, `placed`, `tables`, `accuracy_bound`) | not a depth tape |
| `data/l5_strand_replay/residual_books_20261025.jsonl` | **290** full 8-level ladders | **PMUS, one day (2026-10-25), our own residual states** — not L5's venue or era |

**Consequence, and I am applying it rather than noting it:** any policy whose
case rests on L5-era executable depth **cannot be replayed** and would be
`INSUFFICIENT EVIDENCE`. I have not treated a single one of his fill prices as
liquidity available to us.

**The NO-GO verdict does not depend on the missing depth, and that is the
right way round.** It rests on the prices L5 *actually paid* against the
settlements that *actually occurred* — a statement about his trades, not about
our executability. Better depth evidence could only tell us whether we could
have joined a programme that loses money; it cannot make it profitable.

## 6. GO/NO-GO

**NO-GO.** No policy change proposed, so no replay was run under item 6 — the
candidate the trajectory points to is rejected on L5's own realized outcomes
before executability is even reached.

| | |
|---|---|
| largest trajectory mismatch | late cheap-side accumulation below own-price 0.20 |
| its economic impact **to L5** | **−$2,842.95** (−$0.0355/contract, −17.3% of his net) |
| its economic impact **to us if copied** | the same shape with **$0.00** incentive offset |
| our gates that prevent it | `MIN_FIRST_LEG_OWN_PRICE = 0.20`, `ENTRY_CUTOFF_S = 600` |
| recommended change | **none** |

Accumulated program state after this and the two prior audits: the engine
differs from L5 by **capital**, by **two clock restrictions that are either
validated or null**, and by **one price floor that is actively saving us
money**. None is a defect.

### What I am not claiming

- Not that L5 is unprofitable. He nets **+$16,436** on BTC15m. The claim is
  narrower: the specific trajectory behaviour that produces the attractive
  0.96 pair VWAP is the loss-making part of his book, and **without it he
  would have made ≈$19,279**.
- Not that the 0.96 ceiling is optimally placed. It is placed at the origin
  and the repo is right that this is the wrong point in the path. The new
  finding is that this costs us nothing worth having.
- Not that no trajectory edge exists anywhere in his book — only that the
  largest size-normalizable one is negative, and that the measurement needed
  to look for a smaller one (L5-era depth) does not exist in this repository.

No infrastructure created, no strategy deployed, no live risk increased,
nothing armed.
