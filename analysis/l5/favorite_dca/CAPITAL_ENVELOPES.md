# Capital envelopes against the $5 / traded-window target

Historical replay, 28 untouched windows. **The strategy rules are unchanged**
— the only quantity that varies is the per-period allocation notional, and
the size for each envelope is set by a rule that reads capital and never P&L:

```
alloc(E) = E / (peak capital measured at alloc = $1)
```

then the replay runs and the **measured** peak capital is reported. No
threshold, cadence, reservation price or trail was touched. No sweep.

## The headline answer

| variant | net/window at $1 | **net ÷ peak capital, per window** | minimum peak capital for $5/window |
|---|---|---|---|
| A — favorite DCA, taker | −$1.178 | **−8.2%** | **unreachable at any capital** |
| Am — the same, rested at the touch | −$0.536 | **−4.5%** | **unreachable at any capital** |
| B — plus opposite-side resting | +$2.824 | **+11.1%** | **$44.95** implied; measured **$48.86** |
| C — two-sided favorite trail | −$0.157 | **−2.6%** | **unreachable at any capital** |

Three of the four candidates have negative measured expectancy, so **no
capital envelope reaches $5 per window** — scaling multiplies the loss. That
is visible in the efficiency column being essentially constant across every
envelope: −8.2% to −9.0% for A, −4.5% to −2.6% for Am, −2.6% to −2.8% for C.
**Capital is not the binding constraint on this sample; expectancy is.**

Only B reaches the target, at the $50 envelope: **+$5.42 per window on
$48.86 of peak capital**. The rest of this document is why that number
should not be acted on.

## Per envelope

### A — favorite DCA (taker)

| envelope | alloc | net/window | peak capital | turnover | max residual | max drawdown | fees | profitable exits | settlement residual | net ÷ capital |
|---|---|---|---|---|---|---|---|---|---|---|
| $1 base | 1.00 | −1.178 | 14.35 | 0.99 | 17.6 sh | 12.94 | 5.92 | 0 | 313 sh | −8.2% |
| $25 | 1.74 | −2.074 | 25.00 | 0.99 | 30.7 | 22.55 | 10.28 | 0 | 544 | −8.3% |
| $50 | 3.48 | −4.237 | 50.00 | 0.99 | 61.4 | 45.10 | 20.44 | 0 | 1,086 | −8.5% |
| $100 | 6.97 | −8.597 | 100.00 | 0.99 | 122.9 | 90.19 | 40.73 | 0 | 2,169 | −8.6% |
| $200 | 13.94 | −17.450 | 199.99 | 0.99 | 245.8 | 180.39 | 81.12 | 0 | 4,319 | −8.7% |
| $500 | 34.84 | −45.008 | 499.99 | 0.99 | 614.5 | 450.97 | 200.98 | 0 | 10,707 | −9.0% |

### Am — maker variant

| envelope | alloc | net/window | peak capital | turnover | max residual | max drawdown | fees | settlement residual | net ÷ capital |
|---|---|---|---|---|---|---|---|---|---|
| $1 base | 1.00 | −0.536 | 12.00 | 1.00 | 13.4 sh | 11.98 | 0.00 | 185 sh | −4.5% |
| $25 | 2.08 | −1.091 | 25.00 | 1.00 | 27.9 | 24.96 | 0.00 | 386 | −4.4% |
| $50 | 4.17 | −2.049 | 50.00 | 1.00 | 56.3 | 49.93 | 0.00 | 773 | −4.1% |
| $100 | 8.33 | −3.308 | 100.00 | 1.00 | 118.3 | 99.85 | 0.00 | 1,527 | −3.3% |
| $200 | 16.67 | −6.082 | 200.00 | 1.00 | 235.7 | 199.70 | 0.00 | 2,973 | −3.0% |
| $500 | 41.67 | −13.043 | 500.00 | 1.00 | 588.0 | 499.26 | 0.00 | 6,886 | −2.6% |

Am pays **zero taker fees** at every size, which is why its efficiency
improves with scale — larger resting orders fill a smaller fraction of
themselves, so less capital is committed per period. It is still negative
everywhere.

### B — plus opposite-side resting (the only one that reaches $5)

| envelope | alloc | net/window | **median window** | peak capital | turnover | max residual | max drawdown | fees | settlement residual | net ÷ capital |
|---|---|---|---|---|---|---|---|---|---|---|
| $1 base | 1.00 | +2.824 | **−2.175** | 25.39 | 0.99 | 151.9 sh | 28.11 | 5.92 | 1,257 sh | +11.1% |
| $25 | 0.98 | +2.780 | **−2.175** | 25.00 | 0.99 | 149.6 | 27.68 | 5.83 | 1,238 | +11.1% |
| **$50** | 1.97 | **+5.416** | **−4.351** | **48.86** | 0.99 | **299.1** | **55.36** | 11.61 | 2,459 | +11.1% |
| $100 | 3.94 | +10.442 | −7.653 | 92.49 | 0.99 | 598.3 | 110.73 | 23.09 | 4,842 | +11.3% |
| $200 | 7.88 | +19.799 | −13.249 | 182.36 | 0.99 | 1,197.0 | 221.46 | 46.02 | 9,460 | +10.9% |
| $500 | 19.69 | +41.648 | −30.054 | 444.46 | 0.99 | 3,007.5 | 553.64 | 114.41 | 22,219 | +9.4% |

### C — two-sided favorite trail (the only one that recycles capital)

| envelope | alloc | net/window | peak capital | **turnover** | max residual | max drawdown | fees | profitable exits | settlement residual | net ÷ capital |
|---|---|---|---|---|---|---|---|---|---|---|
| $1 base | 1.00 | −0.157 | 6.00 | **2.39** | 6.6 sh | **0.58** | 6.12 | **120 of 161** | 36.8 sh | −2.6% |
| $25 | 4.17 | −0.664 | 24.99 | 2.37 | 27.3 | 2.31 | 25.22 | 118 of 161 | 153 | −2.7% |
| $50 | 8.33 | −1.331 | 49.98 | 2.37 | 54.6 | 4.63 | 50.19 | 119 | 307 | −2.7% |
| $100 | 16.67 | −2.589 | 99.96 | 2.38 | 109.3 | 9.25 | 99.45 | 120 | 613 | −2.6% |
| $200 | 33.33 | −5.287 | 199.92 | 2.41 | 218.5 | 18.51 | 198.12 | 119 | 1,211 | −2.6% |
| $500 | 83.33 | −14.014 | 499.96 | 2.43 | 546.3 | 46.26 | 493.96 | 119 | 2,957 | −2.8% |

## Why B's $5 is not a result to act on

**1. It is one window.** At every envelope B's **median window is negative**
(−$4.35 at the $50 envelope) and only 9 of 28 windows are positive. The mean
is carried by 2015z, which alone is 130% of B's baseline total.

**2. Its drawdown exceeds its own peak capital.** Drawdown ÷ peak capital is
**1.11 at $25, 1.13 at $50, 1.25 at $500**. A strategy whose mark-to-executable
swing is larger than the capital it has committed is not a $50 strategy that
occasionally loses; it is a position whose valuation moves by more than the
cash behind it.

**3. Reaching $5 requires a 299-share directional residual.** That is
**$299 of settlement-contingent payoff exposure against $48.86 of peak
capital — about 6×**. The cash at risk is bounded by what was paid, but the
outcome is binary: those 299 shares pay $299 or $0. Asked directly whether
$5 requires unreasonable residual exposure: **for B, yes.** Six times
capital in binary settlement exposure, to earn $5, on a mean that one window
in 28 produces, is not a risk I would describe as reasonable, and the
preregistered rule that generates it is the degenerate fixed-notional case
already documented (a $1 order at a $0.03 reservation is 33 shares).

## Liquidity ceiling — the envelopes that are not defensible

The tape is **top of book only**, so the defensible liquidity is the
displayed touch and nothing deeper. Share of taker orders consuming more
than 10% of the displayed touch:

| envelope | A | B | C |
|---|---|---|---|
| $1–$50 | 1.6–3.9% | 1.6–2.3% | 2.4–4.4% |
| $100 | 5.9% | 3.9% | 8.8% |
| $200 | **10.1%** | 6.2% | **14.9%** |
| $500 | **18.9%** | **13.2%** | **38.2%** |

**The $200 and $500 rows are reported but not defensible.** At those sizes a
material fraction of orders would move past the displayed touch into depth
this tape cannot see, and the replay silently assumes the touch price for
all of it. Even at $1 the worst single order consumed 100% of a thin touch.
I would treat **$100 as the evidentiary ceiling** on this data, which is
enough to carry B's $50 configuration and not enough to carry anything
above it.

## Can capital be recycled rather than committed until settlement?

**Yes, and C is the measurement of it.** Capital turnover per window:

| variant | turnover | what it means |
|---|---|---|
| A | 0.99 | every purchase is committed until settlement |
| Am | 1.00 | same |
| B | 0.99 | same, and it is the worst case: B adds inventory and never exits |
| **C** | **2.39** | C turns its peak capital over ~2.4× per window |

C reaches the same inventory activity on **$6.00** of peak capital where A
needs **$14.35**, because the trail's exits return cash mid-window:
**120 of 161 exits were profitable against the cost basis of the shares they
closed**, and there was **no unexitable cleanup at any envelope**. So
recycling is real, it works mechanically, and it is the single clearest
positive finding in this lane.

But recycling buys **cheaper capital, not profit**. Its cost is fees
proportional to turnover: C's fees are the largest of any variant at every
envelope ($493.96 at the $500 envelope, essentially its own peak capital),
and that churn is exactly what holds C's efficiency at −2.6% instead of
somewhere near zero.

## What would have to change for $5 to be real

Not capital, and not size. The efficiency metric is flat across a 20×
capital range for every variant, so the per-window net is a property of the
rule and not of the envelope. $5 per window needs **positive expectancy per
unit of capital first**; the capital that then produces $5 follows
arithmetically ($45 at +11%, $100 at +5%, $250 at +2%). On this sample the
only positive expectancy belongs to a one-window tail, so the honest
statement is:

> **No candidate here supports $5 per traded window on evidence. The one
> that arithmetically reaches it does so with six times its capital in
> binary residual exposure and a negative median window, and I am not
> prepared to call that a path to the target.**

C's recycling and bounded risk ($0.58 worst drawdown on $6.00, 9% of
capital, against A's 90% and B's 111%) is the mechanism worth developing
further — but it needs an edge added to it, not more capital.

No live orders. No parameter sweep. No claim of realized profit: everything
here is `historical replay`.
