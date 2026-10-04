# Causal attribution of the ~$31 gross gap: it is the reversal exit, not the gate

Preregistered 2×2 using **only already-frozen rules**. No threshold, cadence,
sizing or execution change was introduced to make the cells comparable. 28
untouched windows, $1 scale. Primary measure is **gross before fees**.

Cells A and D are the existing frozen runs and reproduce exactly: A =
V1-no-seed (−1.17787 net/window), D = D0 (−0.00636 net/window).

## One mechanical difference, accounted for rather than hidden

**Entry counts differ by rule, not by cadence.** All four cells evaluate on
the same 60 s marks. The strengthening gate is an *admission filter*: it
rejects 192 of the 387 opportunities, leaving 195. Nothing was re-timed or
re-sized to equalize them, and the action-level attribution below prices
those 192 rejected purchases directly, so the difference is measured rather
than assumed away.

**One mechanic had to be made explicit for cell C.** D0's trail needs an
open "episode" with a running peak to measure a reversal from, and that
episode is normally opened by a strengthening read — which cell C does not
have. So in cell C the **purchase itself opens the episode**, with the peak
initialized at that purchase's favored mid. This is D0's own episode
semantics attached to V1's entry rule; it introduces no new number. Without
it the trail would have no peak, no reversal could ever fire, and cell C
would silently collapse into cell A.

| cell | gross total | gross/window | median gross | gross-positive % | LB1 gross | LB3 gross | worst gross | entries | exits | avg hold (s) | peak capital | max residual | fees | net/window |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **A_neither** | -27.0638 | -0.9666 | +1.1944 | 57.1% | -1.1677 | -1.5765 | -10.7467 | 387 | 0 | 324 | 14.35 | 17.6 | 5.9165 | -1.17787 |
| **B_strengthening_only** | -13.3905 | -0.4782 | +0.7166 | 60.7% | -0.5887 | -0.7887 | -7.9373 | 195 | 0 | 358 | 11.15 | 11.1 | 2.1299 | -0.55430 |
| **C_reversal_only** | +10.0346 | +0.3584 | +0.4178 | 85.7% | +0.3422 | +0.3074 | -0.2148 | 387 | 279 | 48 | 8.03 | 8.1 | 11.4202 | -0.04948 |
| **D_both_=_D0** | +3.8959 | +0.1391 | +0.1132 | 82.1% | +0.1259 | +0.0990 | -0.1584 | 195 | 141 | 52 | 3.50 | 3.6 | 4.0741 | -0.00636 |

| effect on GROSS | value |
|---|---|
| total gap to explain (D − A) | +30.9597 |
| strengthening gate alone (B − A) | **+13.6733** |
| reversal exit alone (C − A) | **+37.0984** |
| sum of the two main effects | +50.7717 |
| **interaction** (D − A) − (B − A) − (C − A) | **-19.8120** |
| strengthening given reversal (D − C) | -6.1387 |
| reversal given strengthening (D − B) | +17.2864 |

### Action-level attribution — every V1 purchase, held to settlement (gross, pre-fee)

| purchases | n | total gross | mean per purchase | median | won % | mean price paid |
|---|---|---|---|---|---|---|
| REJECTED by D0's strengthening gate | 192 | **-13.6733** | -0.07121 | +0.03104 | 67.2% | 0.6812 |
| ADMITTED by the gate | 195 | **-13.3908** | -0.06867 | +0.04167 | 78.5% | 0.8196 |
| **all V1 purchases** | 387 | **-27.0641** | -0.06993 | | | |

### D0 reversal exits — proceeds vs holding those exact shares to settlement (gross, pre-fee)

| | |
|---|---|
| exits | 141 |
| exit proceeds | 161.7641 |
| the same shares held to settlement | 144.4776 |
| **exiting beat holding by** | **+17.2865** |
| per window: median | -0.6022 |
| windows where exiting beat holding | 11 of 28 |

## What this says

**1. The reversal exit is the edge. The strengthening gate is not.**

The exit alone is worth **+$37.10** of gross; the gate alone **+$13.67**.
And cell C — reversal exits with *no* gate — has the **best gross in the
whole programme**: +$0.358/window, **85.7% of windows gross-positive**,
leave-best-1 **+$0.342** and leave-best-3 **+$0.307**, worst window only
−$0.215. By the robustness bar this lane has used throughout, **cell C's
gross is the first thing to pass it comfortably.**

**2. The two levers are strongly subadditive: interaction −$19.81.**

They overlap because they attack the same loss. Once reversals are cutting
losing inventory, refusing to open some of it adds little — and in fact,
**given the reversal exit, adding the strengthening gate makes gross
*worse* by $6.14** (D − C). The gate earns its place only through fees: it
removes 192 entries and 138 exits, cutting the fee bill from $11.42 to
$4.07, which is why D0's *net* beats C's (−$0.006 vs −$0.049/window) while
its gross is 2.6× smaller.

**3. The gate abstains; it does not discriminate.**

| | n | gross if held | per purchase | won % | mean price |
|---|---|---|---|---|---|
| rejected by the gate | 192 | −$13.67 | **−$0.0712** | 67.2% | 0.6812 |
| admitted by the gate | 195 | −$13.39 | **−$0.0687** | 78.5% | 0.8196 |

The purchases the gate *rejects* lose 7.1¢ each; the ones it *admits* lose
6.9¢ each. **Statistically the same.** The gate's entire +$13.67 is
explained by making 192 fewer losing purchases, not by picking better ones —
it admits more-favored, more expensive positions that win more often and pay
proportionally more for the privilege.

**4. The exit's contribution is genuine and is about *when*, not *what*.**

D0's 141 reversal exits realised **$161.76** against **$144.48** had those
exact shares been held to settlement: **+$17.29**, which is precisely the
D − B effect. Selling into the trail beats holding. Note the shape: the
**median window is −$0.60** and only **11 of 28** windows benefit, so this
is a mean effect carried by the windows where the move continued — but
cell C shows the other half of what exiting buys, which is *distributional*:
converting one binary settlement outcome per window into many small realised
ones takes gross-positive windows from 57.1% (A) to **85.7%** (C).

**5. Holding to settlement is the actual destroyer of value.**

Every purchase in cell A, taken on its own and held, loses ~7¢ — a mean of
−$0.0699 across all 387. Cells A and B, which hold, have average holding
times of **324 s and 358 s** and gross of −$27.06 and −$13.39. Cells C and
D, which exit, hold for **48 s and 52 s** and have gross of +$10.03 and
+$3.90. **The sign of this strategy's gross is set by holding time, and the
reversal exit is what controls it.**

## What follows, and what does not

This is attribution, not a recommendation, and nothing here is promoted. But
it does relocate the open question. The programme has been treating D0 as
the controller and its fee bill as the problem; this decomposition says the
*gate* is the part carrying least of the edge, and that the configuration
with the strongest and most robust **gross** (cell C) is also the one with
the worst fee burden — 279 exits against D0's 141.

So the next question is neither execution timing (closed by D2/D3/D4) nor
capital (closed by the envelope study). It is whether cell C's gross
advantage survives any reduction in its turnover — and that is a strategy
change requiring its own preregistration, not something to tune here.

No parameter search, no maker changes, no scaling, no live orders. Fees and
net are reported above as secondary, exactly as specified.
