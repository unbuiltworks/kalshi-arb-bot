# Previous-window reversal — IN-SAMPLE DESCRIPTIVE ECONOMICS, NOT VALIDATION

> **These 178 windows generated this hypothesis and are permanently retired from
> validating it.** Everything below is biased upward by construction: the sample
> was selected by noticing this exact pattern in it. Nothing here is evidence the
> effect is real. It measures only how large the effect *looks* on its own
> generating sample — the quantity needed to decide whether a fresh preregistered
> test is worth running.

Rule, fully fixed: prev UP → buy 1 DOWN; prev DOWN → buy 1 UP. Entry at the
already-fixed first-eligible second (**885 s remaining, 1 distinct value across all
windows**). Hold to settlement. No skip, resize, complement, SELL, EXTEND,
threshold or progressive sizing. First chronological window untraded (no prior
outcome). Run `37248048577`, artifact `fixed-side-baseline-37248048577`.

## Aggregate

| | value |
|---|---|
| trades | **177** |
| UP purchases / DOWN purchases | 81 / 96 |
| wins / losses | 97 / 80 |
| win rate | **54.80%** |
| mean executable purchase price | 0.51504 |
| mean break-even probability incl. fees | **0.53475** |
| gross P&L | +5.84 |
| fees | 3.49 |
| **net P&L** | **+2.35** |
| net / traded window | **+0.01328** |
| positive % | 54.80 |
| sd / window · se of mean · **t vs zero** | 0.49742 · 0.03739 · **0.355** |
| cumulative P&L series | 177 points, final **+2.35** |

Aggregate wins, gross, fees and net are exact sums of the two disjoint legs below.
The aggregate's own longest-losing-streak and max-drawdown are in the committed
JSON; the per-leg figures are given below.

## The two directions, separately — and they are not alike

| | prev UP → buy DOWN | prev DOWN → buy UP |
|---|---|---|
| trades | 96 | 81 |
| wins / losses | 49 / 47 | 48 / 33 |
| win rate | 51.04% | **59.26%** |
| mean executable price | 0.5090 | 0.5222 |
| mean break-even incl. fees | 0.52844 | 0.54222 |
| **margin over break-even** | **−1.80 pp** | **+5.04 pp** |
| gross P&L | +0.14 | +5.70 |
| fees | 1.87 | 1.62 |
| **net P&L** | **−1.73** | **+4.08** |
| net / window | −0.01802 | **+0.05037** |
| median / window | +0.265 | +0.39 |
| positive % | 51.04 | 59.26 |
| longest losing streak | 5 | 5 |
| max cumulative drawdown | 5.82 | 4.15 |
| worst window | −0.74 | −0.70 |

**The user's concern was correct: the aggregate conceals the asymmetry entirely.**
All of the economic value is in **prev DOWN → buy UP** (+4.08). The other direction
is **negative** (−1.73). A 10.3 pp transition gap became a one-sided result.

### But there is a confound, and it is the most important thing here

Measured against their *unconditional* baselines, **both** cells improve by about
5 pp:

| cell | conditional win rate | unconditional baseline | improvement |
|---|---|---|---|
| prev DOWN → buy UP | 59.26% | always-UP 53.93% | **+5.33 pp** |
| prev UP → buy DOWN | 51.04% | always-DOWN 46.07% | **+4.97 pp** |

So the reversal tilt is **present and similar in both directions**. Only the buy-UP
cell turns a profit, and the reason is that it starts from a 53.93% unconditional
base while buying DOWN starts from 46.07%. **The profitable cell is the one where
the reversal tilt and this sample's UP drift point the same way.**

That means **the +4.08 cannot be cleanly attributed to reversal rather than to the
same 46-hour BTC drift that made always-UP nominally positive.** In this sample the
two explanations are collinear, and no amount of re-cutting these 178 windows can
separate them.

Precision, for scale: the "good" cell's +5.04 pp margin over break-even sits against
a 5.56 pp standard error on its own win rate — **0.91 SE**. The bad cell is −0.35 SE.
Neither is distinguishable from break-even.

## Capacity — UPPER BOUND, not executable, not realized

Scaled by displayed touch depth and the $200/window ceiling, with the venue fee
recomputed on the whole clip (the per-unit cent fee is wrong at size because the fee
is convex and cent-rounded):

| | ALL | prev UP → buy DOWN | prev DOWN → buy UP |
|---|---|---|---|
| windows | 176 | 96 | 80 |
| mean units / window | 309.2 | 311.9 | 305.9 |
| median units / window | 356.0 | 353.5 | 356.0 |
| binding: ceiling / touch depth | 120 / 57 | 66 / 30 | 54 / 27 |
| net total | +1225.27 | +23.77 | +1201.50 |
| **net / window** | **+6.96** | +0.248 | **+15.02** |
| median / window | +29.62 | +11.83 | +44.05 |
| worst window | −202.57 | −202.57 | −198.94 |

**Why this is a ceiling and not a forecast.** The tape carries only the displayed
touch — no ladder — so nothing beyond level [0] is assumed, and no market impact,
queue position, partial fill, or adverse selection from being the only buyer at
size is modelled. Buying ~310 contracts at a touch whose median displayed size is a
few hundred would move the price against you; this arithmetic assumes it would not.

Two further honest notes:

- **Displayed depth already binds a third of the time** — 57 of 176 windows (32.4%)
  could not supply the full $200 clip from the touch. Capacity is not free even
  before impact.
- The **worst window reads −202.57**, slightly past the $200 ceiling. That is an
  artifact of the sizing arithmetic: quantity is set from the *per-unit* cent fee,
  which rounds to $0.00 at extreme prices while the fee on the whole clip does not.
  A 1.3% breach, flagged rather than hidden.

---

## The five answers

### 1. Is the reversal effect economically positive after executable price + fees?

**Nominally yes, statistically no.** Aggregate net **+2.35** over 177 trades
(+0.01328/window), **t = 0.355**. Indistinguishable from zero, on the sample that
generated the hypothesis.

### 2. Present in both transition directions, or only one?

**Economically, only one.** prev DOWN → buy UP nets **+4.08**; prev UP → buy DOWN
nets **−1.73**.

**But the underlying tilt is in both** — each cell beats its unconditional baseline
by ~5 pp. The one-sidedness of the *profit* comes from break-even asymmetry, not
from the signal appearing in only one direction. This distinction is what creates
the drift confound above, and it is the single most consequential finding of this
follow-up.

### 3. How large is net/window relative to ≥$5 and ≤$200?

At unit size, trivially small: **+$0.0133/window** aggregate, **+$0.0504/window** in
the good cell — **0.3% and 1.0% of the $5 target**. Unit size uses ~$0.52 of the
$200 ceiling, so the target is unreachable without scale, by construction.

### 4. Scaled to the $200 ceiling on observed touch liquidity — descriptive upper bound?

**+$6.96/window aggregate; +$15.02/window in the prev-DOWN→buy-UP cell** (1.4× and
3.0× the $5 target). ~309 units/window on average.

**This is an upper bound and is not executable or realized.** Single touch level, no
ladder, no impact, no queue, no partial fills; depth already binds in 32% of windows;
and the worst window shows a −$202.57 breach from the fee-rounding artifact noted
above.

### 5. Is the magnitude large enough to justify the already-proposed fresh test?

**Yes — and this follow-up changes what the test must be, which is more valuable
than the magnitude itself.**

The magnitude clears the bar with room: a 3× margin over $5/window in the good cell
even before correcting for in-sample bias, and the test costs ~727 windows (~7.6
days) of recording that is already happening, with no free parameters and no new
execution capability (a single taker buy held to settlement — no SELL path needed).

**The required addition, from this follow-up:** the preregistration must test
**both cells** and require **prev UP → buy DOWN to also clear its break-even**.

That is now the decisive criterion, because in this sample the reversal tilt and the
UP drift are collinear. If only the buy-UP cell works again on fresh data, the
result is consistent with "BTC drifted up again" and proves nothing about reversal.
If the buy-DOWN cell *also* clears break-even on fresh data, the reversal
interpretation survives and the drift explanation does not. One sample, two
hypotheses, and only a two-sided test can separate them.

Also unchanged from before: sample size fixed in advance, evaluated **once**, on
windows starting after the preregistration timestamp, these 178 windows retired, and
the success criterion stated as the **proportion difference with its confidence
interval, not P&L** — because at ~310 units per window a near-50/50 binary is
enormously levered and noise will look like $15/window.

**Standing flag.** This remains a **directional** hypothesis, against the project's
own thesis that the strategy is not directional and that historical evidence does
not support residual directional skill. It is a cheap side-experiment, not L5
replication.

## Limits

- **In-sample, on the generating sample. Not validation.** Biased upward.
- 177 trades; the good cell has 81. Neither cell is 1 SE from break-even.
- Reversal and UP drift are collinear here and cannot be separated on this data.
- Capacity is a touch-only upper bound with no impact model.
- No live order placed, nothing deployed, no strategy modified.
