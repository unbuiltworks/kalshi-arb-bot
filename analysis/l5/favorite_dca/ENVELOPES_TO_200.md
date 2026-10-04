# Capital envelopes to $200 — the smallest envelope reaching $5/window is **none**

$200 is recorded as a **ceiling, conditional on validation, not an
instruction to deploy and not live authorization.** Nothing here is a live
order. Historical replay, 28 untouched windows, rules unchanged — only the
per-period notional scales, sized by a rule that reads capital and never
P&L (`alloc = envelope ÷ peak capital at $1`).

## The short answer

**No envelope between $25 and $200 defensibly reaches $5 per window, for any
candidate.**

- **D0, D1 and C are negative at every envelope.** Scaling them is the thing
  you told me not to do, and the tables below are the arithmetic of why.
- **B is the only variant whose mean reaches $5** (at the $50 envelope,
  +$5.42). It fails the test you just added: **its leave-best-1 is negative
  at every single envelope** — −$1.89 at $50, −$9.51 at $200 — as is
  leave-best-3. Its median window is −$4.35 at $50 and its `P(net < 0)` is
  **0.68**.

So on the repeatability-and-risk-control standard you set, **B's $5 is
disqualified rather than merely fragile**, and the $200 ceiling never comes
into play.

## Why no envelope can flip a negative rule — algebra, not sample

The venue's taker fee is `θ · p(1−p)` **per share**, so both the gross
economics and the fee bill are strictly proportional to size. Net ÷ capital
is therefore **scale-invariant until displayed depth binds**, and once depth
binds it gets *worse*, never better. D0's gross ÷ fees ratio of **0.956** is
a property of the rule, not of the size it is run at.

D0 is the cleanest demonstration: capital rises 8× from $25 to $200 while
the loss per window rises **16×** (−$0.068 → −$1.089). That is
**superlinear decay** — touch participation climbs from 3.8% to 11.2% of
displayed depth and the share of orders taking more than 10% of the touch
goes 4.6% → **21.0%**, so size starts truncating fills and leaving residual
behind. Even the nominal envelope stops being reachable: at the $200 setting
D0's measured peak capital tops out at **$182.16** because depth caps the
allocations.

**$5 per window at ≤$200 needs 2.5% net on the envelope. The measured
figures are −0.6% (D0), −2.6% (C) and −5.0% (D1). The gap is expectancy, and
no amount of the authorized ceiling closes it.**

### D0  — capital implied for $5/window: **UNREACHABLE AT ANY CAPITAL net per window is negative**

| envelope | mean net/win | **median** | pos % | **$5+ %** | worst window | max DD | peak capital | max residual | turnover | fees | **P(net>=5)** | **P(net<0)** | **leave-best-1** | **leave-best-3** | touch participation | orders >10% of touch |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| $25 | -0.0676 | **-0.1116** | 46.4% | **0.0%** | -2.909 | 3.99 | 24.63 | 25.4 sh | 3.834× | 28.451 | **0.0000** | **0.5357** | **-0.1599** | **-0.3490** | 3.8% | 4.6% |
| $50 | -0.1350 | **-0.3398** | 46.4% | **0.0%** | -5.597 | 7.76 | 49.03 | 50.7 sh | 3.814× | 56.291 | **0.0000** | **0.5357** | **-0.3192** | **-0.6982** | 5.2% | 6.7% |
| $75 | -0.2292 | **-0.5746** | 42.9% | **10.7%** | -8.293 | 11.54 | 73.45 | 76.1 sh | 3.809× | 84.078 | **0.1071** | **0.5714** | **-0.5068** | **-1.0731** | 6.5% | 9.7% |
| $100 | -0.3732 | **-0.8165** | 42.9% | **10.7%** | -11.059 | 15.31 | 97.93 | 101.4 sh | 3.804× | 111.859 | **0.1071** | **0.5714** | **-0.7460** | **-1.5024** | 7.6% | 11.3% |
| $150 | -0.7037 | **-1.3494** | 42.9% | **10.7%** | -16.617 | 22.85 | 144.35 | 152.2 sh | 3.795× | 167.318 | **0.1071** | **0.5714** | **-1.2668** | **-2.4021** | 9.5% | 13.3% |
| $200 | -1.0892 | **-1.9332** | 42.9% | **14.3%** | -22.286 | 30.40 | 182.16 | 202.9 sh | 3.790× | 222.768 | **0.1429** | **0.5714** | **-1.8378** | **-3.3354** | 11.2% | 21.0% |

### D1  — capital implied for $5/window: **UNREACHABLE AT ANY CAPITAL net per window is negative**

| envelope | mean net/win | **median** | pos % | **$5+ %** | worst window | max DD | peak capital | max residual | turnover | fees | **P(net>=5)** | **P(net<0)** | **leave-best-1** | **leave-best-3** | touch participation | orders >10% of touch |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| $25 | -2.0413 | **-1.1586** | 21.4% | **0.0%** | -8.380 | 21.77 | 25.00 | 239.8 sh | 2.706× | 11.722 | **0.0000** | **0.7857** | **-2.1432** | **-2.3449** | 2.1% | 3.1% |
| $50 | -3.2274 | **-1.6793** | 25.0% | **0.0%** | -16.760 | 43.54 | 49.48 | 479.5 sh | 2.797× | 22.455 | **0.0000** | **0.7500** | **-3.3992** | **-3.7730** | 2.8% | 4.1% |
| $75 | -4.6428 | **-2.6005** | 25.0% | **0.0%** | -25.140 | 65.32 | 74.04 | 719.3 sh | 2.755× | 33.665 | **0.0000** | **0.7500** | **-4.9350** | **-5.4840** | 3.5% | 4.6% |
| $100 | -5.7745 | **-2.4634** | 28.6% | **3.6%** | -33.581 | 87.09 | 97.73 | 959.0 sh | 2.790× | 44.196 | **0.0357** | **0.7143** | **-6.2512** | **-7.0373** | 4.1% | 4.6% |
| $150 | -7.8004 | **-3.3018** | 32.1% | **7.1%** | -46.427 | 123.35 | 130.45 | 1438.5 sh | 2.791× | 64.888 | **0.0714** | **0.6786** | **-8.4981** | **-9.6073** | 4.9% | 6.7% |
| $200 | -9.9197 | **-3.6173** | 32.1% | **10.7%** | -57.063 | 147.25 | 173.94 | 1918.0 sh | 2.799× | 86.434 | **0.1071** | **0.6786** | **-10.8391** | **-12.2772** | 5.7% | 8.2% |

### C  — capital implied for $5/window: **UNREACHABLE AT ANY CAPITAL net per window is negative**

| envelope | mean net/win | **median** | pos % | **$5+ %** | worst window | max DD | peak capital | max residual | turnover | fees | **P(net>=5)** | **P(net<0)** | **leave-best-1** | **leave-best-3** | touch participation | orders >10% of touch |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| $25 | -0.6638 | **-0.6263** | 17.9% | **0.0%** | -2.861 | 2.31 | 24.99 | 27.3 sh | 2.374× | 25.215 | **0.0000** | **0.8214** | **-0.7284** | **-0.8456** | 2.6% | 3.6% |
| $50 | -1.3311 | **-1.2527** | 17.9% | **0.0%** | -5.723 | 4.63 | 49.98 | 54.6 sh | 2.369× | 50.191 | **0.0000** | **0.8214** | **-1.4612** | **-1.6958** | 4.0% | 4.4% |
| $75 | -1.9564 | **-1.8790** | 17.9% | **0.0%** | -8.584 | 6.94 | 74.97 | 81.9 sh | 2.372× | 74.788 | **0.0000** | **0.8214** | **-2.1505** | **-2.4990** | 4.9% | 6.8% |
| $100 | -2.5886 | **-2.5056** | 17.9% | **0.0%** | -11.445 | 9.25 | 99.96 | 109.3 sh | 2.382× | 99.449 | **0.0000** | **0.8214** | **-2.8470** | **-3.3081** | 5.7% | 8.8% |
| $150 | -3.9056 | **-3.9075** | 17.9% | **7.1%** | -17.168 | 13.88 | 149.94 | 163.9 sh | 2.399× | 148.826 | **0.0714** | **0.8214** | **-4.2943** | **-4.9834** | 7.2% | 12.9% |
| $200 | -5.2871 | **-5.3490** | 17.9% | **7.1%** | -22.890 | 18.51 | 199.92 | 218.5 sh | 2.407× | 198.118 | **0.0714** | **0.8214** | **-5.8086** | **-6.7275** | 8.6% | 14.9% |

### B  — capital implied for $5/window: **44.95**

| envelope | mean net/win | **median** | pos % | **$5+ %** | worst window | max DD | peak capital | max residual | turnover | fees | **P(net>=5)** | **P(net<0)** | **leave-best-1** | **leave-best-3** | touch participation | orders >10% of touch |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| $25 | 2.7801 | **-2.1753** | 28.6% | **10.7%** | -11.631 | 27.68 | 25.00 | 149.6 sh | 0.989× | 5.826 | **0.1071** | **0.7143** | **-0.8723** | **-2.3331** | 1.1% | 1.6% |
| $50 | 5.4157 | **-4.3511** | 32.1% | **17.9%** | -23.263 | 55.36 | 48.86 | 299.1 sh | 0.989× | 11.609 | **0.1786** | **0.6786** | **-1.8948** | **-4.6017** | 1.7% | 2.3% |
| $75 | 8.0577 | **-6.0495** | 32.1% | **21.4%** | -34.894 | 83.05 | 71.44 | 448.7 sh | 0.989× | 17.358 | **0.2143** | **0.6786** | **-2.9103** | **-6.7624** | 2.1% | 3.4% |
| $100 | 10.4418 | **-7.6526** | 32.1% | **25.0%** | -46.526 | 110.73 | 92.49 | 598.3 sh | 0.989× | 23.086 | **0.2500** | **0.6786** | **-4.1936** | **-8.8669** | 2.5% | 3.9% |
| $150 | 15.1077 | **-10.2773** | 32.1% | **25.0%** | -69.788 | 166.09 | 137.06 | 897.4 sh | 0.989× | 34.555 | **0.2500** | **0.6786** | **-6.8656** | **-13.0215** | 3.2% | 5.4% |
| $200 | 19.7991 | **-13.2485** | 32.1% | **28.6%** | -93.051 | 221.46 | 182.36 | 1197.0 sh | 0.989× | 46.018 | **0.2857** | **0.6786** | **-9.5116** | **-17.1481** | 3.9% | 6.2% |

## Liquidity — where these rows stop being defensible

The tape is **top of book only**, so the only honest participation measure
is the share of the *displayed* touch each order consumed. Beyond it this
replay has nothing to say and silently assumes the touch price.

| | D0 | D1 | C | B |
|---|---|---|---|---|
| orders > 10% of touch, at $100 | 11.3% | 4.6% | 8.8% | 3.9% |
| orders > 10% of touch, at $200 | **21.0%** | 8.2% | 14.9% | 6.2% |

**D0 is the size-sensitive one** because it holds the least inventory per
dollar of capital and therefore trades the largest clips for a given
envelope. I would not defend D0 above the **$75–$100** band, and D1/C/B
remain inside the touch to $200 — which matters not at all, since none of
them is positive there.

## P(net ≥ $5) and P(net < 0), the figures the objective turns on

| variant | envelope | P(net ≥ $5) | P(net < 0) |
|---|---|---|---|
| B | $50 | 0.179 | 0.679 |
| B | $200 | 0.286 | 0.679 |
| D0 | $200 | 0.143 | 0.571 |
| D1 | $200 | 0.107 | 0.679 |
| C | $200 | 0.071 | 0.821 |

The best `P(net ≥ $5)` anywhere in this study is **0.286**, paired with a
**0.679** chance of losing money in the same window. A target framed as
"≥$5 per traded window" is not met by a 29%-of-windows outcome whose
complement is a two-in-three chance of a loss.

## What this does and does not change

- The $200 ceiling is noted and will be respected as a hard maximum if and
  when anything is ever validated. **It is not used as a reason to scale.**
- No capital figure is proposed for $5/window, because proposing one would
  require a positive rule and there isn't one.
- Variant D remains the live research line on the strength of its $1
  economics (gross +$3.896 vs fees $4.074, 13 of 28 windows positive,
  worst-window drawdown $0.61 on $3.50 of capital), **not** on anything in
  this envelope study.
- The binding constraint is still the fee line, and it is now quantified
  twice: scale-invariant in theory, and superlinearly adverse in practice.

Failure to reach $5 authorizes nothing. No live orders. Everything here is
`historical replay`; verified compensation remains $0.0000 throughout.
