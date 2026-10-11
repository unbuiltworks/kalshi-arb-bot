# What happens after the first side: inventory construction, not speculation

All 17,046 canonical L5 markets, conditioned on the first fill.

## Sequence statistics

| quantity | value |
|---|---|
| markets with ≥2 buys | 15,356 (1,690 single-fill) |
| **P(next fill SAME side)** | **0.6962** |
| **P(next fill SWITCHES)** | **0.3038** |
| fills per market | p50 **7**, p90 147, max 756 |
| time to next fill | p10 0 s, **p50 12 s**, p90 106 s |
| price step on a same-side continuation | p10 −0.08, **p50 0.00**, p90 +0.12 |
| same-side fills before first switch | **p50 2**, p90 7, max 45 |
| `gain_per_unit` before first switch | p10 −0.5906, **p50 −0.06**, p90 +0.44 |
| eventual matched fraction | p10 0.0000, **p50 0.7226**, p90 0.9702 |
| terminal residual fraction | p10 0.0298, **p50 0.2775**, p90 1.0000 |
| markets ending FULLY matched | **138 / 17,046 (0.81%)** |

## Attainable pair cost at the first switch

`p_first + p_switch`: p10 0.7718 · p25 0.90 · **p50 0.99** · p75 1.09 · p90 1.24.
Only **40.7%** are ≤ 0.96 and 43.9% ≤ 0.97.

Split by how he opened:

| opening | pair p50 | ≤0.96 |
|---|---|---|
| DEAR open | 0.98 | 44.3% |
| CHEAP open | 0.9869 | 41.5% |

**Even L5 does not usually get a cheap pair at his first switch.** The median
first-switch pair is ~0.99 — above our own ≤0.96 spring target.

## Directional speculation, or inventory construction?

**Inventory construction.** The evidence:

* He opens DEARER **60.15%** of the time (10,253 / 17,046 by his own fill
  price), so the opening side has structure — it is not random.
* **Given a DEAR open, P(next side is the CHEAP complement) = 0.7219.**
  **Given a CHEAP open, P(next side is the DEAR complement) = 0.6877.**
  Whichever way he starts, he crosses to the other side of 0.50 about 70% of
  the time.
* Median eventual matched fraction **0.7226** — he ends up mostly paired.
* **CORRECTION.** I previously read `gain_per_unit` (−0.06 median) as L5
  "buying into adverse movement". That was wrong: `gain_per_unit` is
  `switch_price − own_weighted_cost`, and in a binary market the complement
  prices to ~`1 − own`, so the quantity is **mechanically negative for a dear
  open and positive for a cheap open** (DEAR-first −0.2196, CHEAP-first
  +0.1400). It measures the gap between the two sides, not P&L.
  The right measure of "is he underwater when he switches" is the movement of
  **his own side**, `price_move_before`, and its median is **+0.0000 in both
  regimes** — he switches from a *flat* position, not a losing one.
* He never sells (2 fills in 17,046 markets); exits are redemption only.

So the first side is **where he begins assembling a pair**, not a directional
bet — and the dearer tendency is a property of *where he starts*, not of a
view he then holds. Note the asymmetry is real but modest: a dear open is only
slightly more likely to be followed by the cheap complement (0.7219) than the
reverse (0.6877).

**Unresolved:** only 0.81% of markets end fully matched and the median
terminal residual is 27.75%, so "inventory construction" does not mean
"completed pairs". Most markets end materially unbalanced and are carried to
settlement.
