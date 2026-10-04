# D0 / D1 — removing C's mandatory two-sided taker seed

Historical replay, the same 28 untouched windows, $1 scale only. Frozen by
`PREREGISTRATION_D.md`, committed before any D number existed. C is
re-run unchanged by the same code path and reproduces its published net
(−$4.388, −$0.1567/window) exactly. No new threshold. No live orders. **The
prepared C live probe was not executed.**

## The hypothesis, answered

> Does removing the seed preserve enough of C's directional component to
> produce positive net expectancy?

**No — but it comes within 4.4% of its own fee bill.**

Removing the seed removes **all** of the matched loss (C −$19.837 → D0
$0.000, because D0 never holds both sides at once, so matched inventory is
identically zero). But the directional component does **not** survive
intact: **+$17.081 → +$3.091, a fall of 82%**. The two effects very nearly
cancel, and D0 lands at **−$0.0064 per window** — a near-perfect wash rather
than the positive expectancy the hypothesis predicted.

**D1 is decisively worse**: −$1.0607 per window. Permitting opposite
inventory, even purely passively, reintroduces the longshot accumulation
that Variant B already exhibited — max residual **115.7 shares** against
D0's 3.6, and a **settlement residual of −$22.566** that swamps the
+$9.280 of genuinely cheap matched pairs it buys.

## Side by side

| metric | C (control, with seed) | **D0** (no seed) | **D1** (no seed + passive opposite) |
|---|---|---|---|
| **net per window** | -0.1567 | -0.0064 | -1.0607 |
| **median window net** | -0.1452 | -0.0156 | -0.5590 |
| net total | -4.388 | -0.178 | -29.701 |
| positive windows (of 28) | 5 | 13 | 6 |
| worst window | -0.687 | -0.461 | -4.043 |
| best window | +0.243 | +0.344 | +0.344 |
| leave-one-best-out, per window | -0.1715 | -0.0193 | -1.1128 |
|  |  |  |  |
| directional realized P&L | +17.081 | +3.091 | -10.651 |
| matched inventory economics | -19.837 | +0.000 | +9.280 |
| settlement residual | +4.491 | +0.804 | -22.566 |
| verified compensation | 0.0000 | 0.0000 | 0.0000 |
| fees | -6.123 | -4.074 | -5.764 |
| **= net** (identity residual 0.000000) | -4.388 | -0.178 | -29.701 |
| gross before fees | +1.735 | +3.896 | -23.937 |
| gross ÷ fees | 0.283 | 0.956 | -4.153 |
| maker rebate, MODELLED only | 0.0000 | 0.0000 | 0.8425 |
|  |  |  |  |
| peak capital, worst window | 6.00 | 3.50 | 12.06 |
| capital turnover, per-window mean | 2.39 | 3.87 | 2.74 |
| max residual, worst window (sh) | 6.6 | 3.6 | 115.7 |
| terminal residual, summed (sh) | 36.8 | 36.8 | 570.7 |
| max drawdown, worst window | 0.58 | 0.61 | 10.50 |
| extensions | 195 | 195 | 195 |
| exits | 161 | 141 | 141 |
| profitable exits | 120 | 57 | 56 |
| passive opposite fills | 0 | 0 | 152 |

| window | settle | flips | extends | exits | C net | **D0 net** | D1 net | D0 peakcap | D0 \|resid\| | D0 maxDD |
|---|---|---|---|---|---|---|---|---|---|---|

The preregistered identity — directional + matched + settlement residual +
verified compensation − fees = net — reconciles to **0.000000 in all 28
windows for all three variants**.

## A restatement I owe you

You quoted C's decomposition as **−$19.837 matched / +$15.449 directional**,
which is what I reported last time. That was a **two-term** split in which
the directional line was computed as a plug (`net − matched`) and therefore
silently absorbed the settlement value of the terminal residual.

Under the five-term identity this preregistration fixed, C's
decomposition is:

| | two-term (as previously reported) | **five-term identity (now)** |
|---|---|---|
| matched | −19.837 | −19.837 |
| directional | +15.449 | **+17.081** |
| settlement residual | *(inside directional)* | **+4.491** |
| fees | −6.124 | −6.123 |
| net | −4.388 | −4.388 |

The net is identical and the matched figure your hypothesis rests on is
unchanged. Only the directional line is restated, and it moves in the
direction that makes the hypothesis *more* plausible, not less — which is
why it matters that the answer is still no.

## Why the directional component collapsed

C and D0 run the **same 195 extensions** and differ only by the seed. After
C's first reversal the residual is flattened to zero in both, so from that
point the two portfolios differ by exactly one thing: C is carrying a block
of matched inventory bought at two taker half-spreads, and D0 is not.

So the directional fall is not the exits behaving differently — it is
**scale**. C's seed roughly doubles the inventory the trail is working
(peak capital $6.00 against $3.50), and the directional line scales with it.
Per dollar of peak capital, C's directional is $2.85 and D0's is $0.88, so
the seed's inventory was also *more* productive directionally than the
extensions alone. The seed bought directional throughput at a price
(−$19.837) that was far higher than the throughput was worth.

Two further asymmetries worth recording:

- **D0's exits are less often profitable**: 57 of 141 against C's 120 of
  161. C's exits were frequently selling shares whose average cost had been
  dragged down by the seed's cheap leg; without it, the trail exits at a
  basis set purely by taker entries at the touch.
- **D0 turns capital over faster**: 3.87× per window against C's 2.39×, on
  41% less peak capital, with the worst-window drawdown essentially
  unchanged ($0.61 vs $0.58) and the worst-window residual nearly halved
  (3.6 vs 6.6 shares). **D0 is the most capital-efficient and lowest-risk
  configuration in this entire lane.**

## The real binding constraint, stated precisely

| | D0 |
|---|---|
| gross before fees | **+$3.896** |
| fees | **−$4.074** |
| gross ÷ fees | **0.956** |
| net | **−$0.178** |

D0 is a **gross-positive, fee-negative** strategy. It earns $0.139 per
window before costs and pays $0.145 per window in taker fees, missing
breakeven by **4.4% of its own fee bill**. C's equivalent ratio is 0.283 and
D1's is negative, so D0 is the first configuration in this lane where the
gross economics and the cost of trading are even the same size.

That is an observation, not a proposal. It does identify where any edge
would have to come from — the fee line, which means maker execution or lower
turnover, not a better trail — and `Am` already established that maker fills
on the favored side are obtainable under the same discipline (216 of 387
periods, zero taker fees). I am **not** running that variant: it would be a
new candidate and this preregistration covers D0 and D1 only.

## The scaling gate — not opened

The preregistration fixed the gate: no capital envelope and no $5 projection
unless the $1 economics are positive on **mean AND median AND with the
single best window removed**.

| | mean | median | leave-one-best-out |
|---|---|---|---|
| D0 | −$0.0064 | −$0.0156 | −$0.0193 |
| D1 | −$1.0607 | −$0.5590 | −$1.1128 |
| C | −$0.1567 | −$0.1452 | −$0.1715 |

**All three fail all three tests.** No envelope study was run and no $5
figure is computed. D0's failure is by less than two cents a window on every
measure, and 13 of 28 windows are positive against C's 5 — the closest this
lane has come to a wash — but a wash is not positive expectancy, and the
gate was set in advance precisely so that this would not be argued either
way after the fact.

## Per window

| 09-30-1215z | UP | 3 | 4 | 3 | -0.057 | -0.057 | -0.697 | 1.07 | 1.0 | 0.10 |
| 09-30-1230z | UP | 0 | 5 | 2 | -0.687 | -0.016 | -0.572 | 2.04 | 2.0 | 0.06 |
| 09-30-1245z | UP | 0 | 6 | 4 | +0.041 | +0.157 | -0.239 | 1.96 | 0.0 | 0.07 |
| 09-30-1300z | UP | 3 | 5 | 4 | -0.251 | -0.052 | -0.052 | 1.06 | 1.0 | 0.08 |
| 09-30-1315z | DOWN | 1 | 7 | 4 | -0.241 | -0.135 | -0.953 | 2.21 | 2.1 | 0.22 |
| 09-30-1330z | DOWN | 1 | 5 | 2 | -0.338 | +0.086 | +0.086 | 2.98 | 3.1 | 0.09 |
| 10-02-1100z | UP | 1 | 8 | 5 | -0.069 | +0.034 | +0.034 | 3.01 | 3.0 | 0.08 |
| 10-02-1115z | DOWN | 3 | 5 | 4 | -0.022 | +0.055 | +0.055 | 1.11 | 1.2 | 0.18 |
| 10-02-1130z | UP | 0 | 8 | 5 | +0.002 | +0.090 | -0.380 | 3.05 | 0.0 | 0.11 |
| 10-02-1145z | DOWN | 1 | 7 | 7 | +0.118 | +0.280 | +0.280 | 1.05 | 0.0 | 0.11 |
| 10-02-1200z | DOWN | 2 | 6 | 5 | -0.263 | -0.055 | -0.055 | 1.11 | 1.0 | 0.21 |
| 10-02-1300z | DOWN | 2 | 7 | 5 | -0.182 | -0.095 | -0.484 | 2.04 | 1.0 | 0.16 |
| 10-02-1315z | DOWN | 6 | 5 | 4 | -0.238 | -0.156 | -0.156 | 1.20 | 1.0 | 0.23 |
| 10-02-1430z | DOWN | 0 | 8 | 6 | +0.240 | +0.344 | +0.344 | 1.68 | 2.0 | 0.12 |
| 10-02-1730z | DOWN | 1 | 6 | 6 | -0.115 | +0.026 | -1.265 | 1.26 | 0.0 | 0.30 |
| 10-02-1745z | UP | 3 | 7 | 4 | -0.113 | -0.016 | -4.043 | 2.04 | 2.0 | 0.12 |
| 10-02-1800z | UP | 2 | 7 | 6 | -0.282 | -0.163 | -0.545 | 1.35 | 1.0 | 0.38 |
| 10-02-1815z | DOWN | 0 | 9 | 6 | -0.151 | -0.049 | -1.929 | 3.08 | 0.0 | 0.12 |
| 10-02-1830z | DOWN | 0 | 7 | 6 | -0.290 | -0.132 | -2.047 | 2.13 | 0.0 | 0.20 |
| 10-02-1845z | UP | 2 | 8 | 5 | +0.243 | +0.318 | -1.606 | 2.72 | 3.0 | 0.21 |
| 10-02-1900z | DOWN | 1 | 11 | 8 | -0.525 | -0.461 | -2.564 | 3.50 | 3.0 | 0.61 |
| 10-02-1915z | UP | 3 | 6 | 6 | -0.350 | -0.094 | -3.538 | 1.10 | 0.0 | 0.16 |
| 10-02-1930z | UP | 2 | 10 | 10 | -0.079 | +0.029 | +0.029 | 1.11 | 0.0 | 0.21 |
| 10-02-1945z | UP | 2 | 6 | 4 | -0.084 | +0.066 | -2.446 | 1.95 | 2.0 | 0.21 |
| 10-02-2000z | UP | 0 | 7 | 3 | -0.150 | +0.064 | -2.647 | 3.03 | 3.1 | 0.12 |
| 10-02-2015z | DOWN | 5 | 6 | 5 | -0.336 | -0.245 | -0.158 | 2.24 | 0.0 | 0.29 |
| 10-02-2030z | DOWN | 1 | 10 | 6 | -0.070 | +0.019 | -2.184 | 3.16 | 1.0 | 0.21 |
| 10-02-2045z | UP | 0 | 9 | 6 | -0.141 | -0.021 | -1.966 | 3.14 | 3.1 | 0.20 |

## Labels

`historical replay` throughout. Verified compensation is **$0.0000** in all
three variants; D1's $0.8425 maker rebate is modelled and sits outside every
net above. Nothing submitted, filled, settled or realized.
