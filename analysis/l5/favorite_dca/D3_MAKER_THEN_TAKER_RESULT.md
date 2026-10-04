# D3_MAKER_THEN_TAKER — the gate fails, and the mechanism is now pinned

Shadow/replay only, $1 scale, 28 untouched windows. D0's controller and D2's
maker entry price preserved exactly. The wait was derived from the lifecycle
and frozen in `PREREGISTRATION_D3.md` **before** these numbers existed. No
scaling, no $200 envelope, no live orders. The decomposition identity
reconciles to **0.000000** in all 28 windows for all three variants.

## D0 / D2 / D3 side by side

| | **D0** taker | **D2** maker-only | **D3** maker→taker |
|---|---|---|---|
| intended signals | 195 | 195 | 195 |
| **maker fills** | 0 | **110** | **6** |
| maker misses | — | 93 | 189 |
| taker fallbacks attempted / filled | — / — | — / — | **184 / 184** |
| signals invalidated during the wait | — | — | **5** |
| actions captured in total | 195 | 102 | **190** |
| gross before fees | +3.8959 | +1.1117 | **+3.7579** |
| fees | −4.0741 | −1.1281 | −3.8404 |
| maker rebate, MODELLED (excluded from net) | 0.0000 | 0.2194 | 0.0104 |
| **net** | −0.1781 | **−0.0164** | −0.0824 |
| **mean net/window** | −0.00636 | **−0.00059** | −0.00294 |
| **median net/window** | −0.01560 | **+0.01770** | −0.02420 |
| positive windows | 13/28 | **16/28** | 10/28 |
| **leave-best-1** | −0.01934 | −0.02148 | **−0.01342** |
| **leave-best-3** | −0.04482 | **−0.03545** | −0.03599 |
| **worst window** | −0.4613 | −0.8663 | **−0.2350** |
| peak capital / turnover | 3.50 / 3.87× | **2.12** / 3.01× | 3.22 / 3.84× |
| max residual / **max drawdown** | 3.6 sh / 0.61 | 2.5 sh / 0.89 | 3.6 sh / **0.39** |
| exits | 141 | 86 | 137 |

## Advancement gate: **FAILED**

The gate required positive mean **and** median **and** leave-best-1 **and**
leave-best-3. D3 is negative on all four. No scaling study follows.

## Why — and this is the finding

**The derived wait captures almost no maker economics: 6 fills out of 195
intended signals, a 3.1% capture rate, against D2's 56.4%.**

The two experiments now bracket the mechanism:

| resting time | maker capture | what it captures |
|---|---|---|
| D2: one full 60 s period | **56.4%** | the adverse subset (−$0.15 of gross on those actions) |
| D3: 0.713 s | **3.1%** | essentially nothing |

**Maker capture is bought with time, and time is exactly what a momentum
controller cannot spend.** The lifecycle-derived wait — the shortest interval
in which a cancel can be confirmed and a fresh state read — is three orders
of magnitude shorter than the window D2 needed, and in that interval our
passive bid is simply not reached. So D3 collapses into "D0, delayed":

- it crosses on **184 of 195** signals;
- its acquisition cost is **$184.00 taker** against **$6.00 maker**;
- total execution saving from the maker leg is **$0.1241** (price improvement
  $0.0707 + taker fee avoided $0.0534) — against the **$2.4911** D2 earned
  on its 102 fills.

### The cost of waiting, measured

Price movement between the signal and the fallback, per share, across 184
fallbacks: **mean +$0.00087, median $0.0000**, p90 +$0.0100. **136 of 184
fallbacks crossed at an unchanged price**, 31 worse, 17 better — cumulative
+$0.1600 per-share moved against us.

So at 0.713 s the opportunity cost is small (as the sub-second timescale
would suggest) — but so is the benefit. D3's gross comes in at **+$3.7579
against D0's +$3.8959**, i.e. waiting cost **−$0.138 of gross**, and it saved
**$0.234 of fees** (partly because 5 signals were invalidated and skipped
altogether). Net result: D3 is **better than D0 by $0.096** and **worse than
D2 by $0.066**, and negative.

### What D3 did do well

Three real improvements, none of which rescue the sign:

- **best leave-best-1** of the three (−$0.0134), so its small loss is the
  most evenly spread;
- **best worst window** (−$0.2350 against D0's −$0.4613 and D2's −$0.8663);
- **best drawdown** ($0.39), because the 5 invalidated signals are precisely
  the ones where momentum had already turned — the conditional re-check
  works as designed, it just has almost nothing to re-check.

## The combination you hoped for is not available at this cadence

> D0 opportunity capture + some D2 execution savings − wait/slippage cost.

Measured, the three terms do not coexist. The +$4.0459 of gross that D2
missed is recovered by D3 — its gross is 96.5% of D0's — but it is recovered
**at taker prices**, which is the cost that made D0 negative in the first
place. The execution saving does not survive the shortening of the wait:
**$2.4911 → $0.1241, a loss of 95% of the benefit for a 99% reduction in
resting time.**

That is a monotone trade, not a tunable corner, and I am not going to search
for a wait in between — that is the parameter sweep the preregistration
forbids, and on 28 windows with a $0.178 total deficit it would find one by
chance.

## What this leaves

The lane now has a precise diagnosis rather than a candidate:

1. **D0's gross edge is real and broadly distributed** — 23/28 windows,
   positive median, +$0.0990/window after removing the best three.
2. **Fees consume it**: gross/fees = 0.956.
3. **Maker execution can pay for the fees** — D2 proved the saving is worth
   ~2.4¢ per captured share, about 17× the deficit.
4. **But passive fills and momentum are in direct conflict**: the fills
   arrive only when the move does not, and waiting long enough to be filled
   is the same act as missing the move.

Any remaining route has to break that conflict rather than balance it, and
the two candidates visible from here are **not** execution timing:

- **turnover reduction** — D0 pays taker fees on 141 exits as well as 195
  entries; resting *exits* (your stated second lever, still uncombined) does
  not fight momentum, because a reversal exit is leaving a position rather
  than chasing one. That is the one untested lever with a mechanism behind
  it;
- **the venue's own maker compensation**, which is currently excluded from
  every net here as unverified. Modelled, it is $0.2194 on D2's fills; if it
  were real and credited, D2's net becomes positive — but that is an
  accounting claim about a rebate we have never seen credited, and I will not
  move it inside net on modelling alone.

No scaling. The $200 ceiling remains unused: there is still no positive
process to scale. Verified compensation remains **$0.0000**.
