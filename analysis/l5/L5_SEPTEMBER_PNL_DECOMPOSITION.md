# September P&L decomposition — and a correction to the headline number

## CORRECTION FIRST: trading net is −$5,048, not +$7,126

I reported L5's September trading net as **+$7,125.74 (+0.459% of spend)**,
on "settlement-observed markets only". That filter was `redeem_qty > 0`.

**A market L5 lost completely redeems nothing, so it has no redemption row.**
The filter therefore excluded losers by construction. It dropped **708 of
4,217 resolved markets — 16.8%, every one of them a total loss.**

Corrected, counting every market resolved at least 10 minutes before the
harvest, with zero-redemption treated as the total loss it is:

| | value |
|---|---:|
| spend | $1,563,497.12 |
| redeem | $1,558,449.24 |
| **trading net** | **−$5,047.88** |
| **as % of spend** | **−0.323%** |
| incentives | +$20,490.13 |
| **total** | **+$15,442.25** (+$1,544/day) |

| cohort | n | spend | P&L | % | total-loss markets |
|---|---:|---:|---:|---:|---:|
| two-sided | 2,477 | 1,514,247 | −4,928.41 | −0.325% | 1 |
| one-sided only | 1,740 | 49,251 | −119.47 | −0.243% | **707** |

**"+$7,125.74" and "one-sided markets return +32.6%" are both withdrawn.**
The one-sided cohort is flat (−0.24%), not a profit centre; 4 of 10 days
positive; win rate ~58% on tiny stakes. Nearly all of the survivorship damage
sat in that cohort, because a one-sided loser redeems nothing while a
two-sided market almost always redeems something.

### What this changes

**L5 in September is an incentive harvester whose trading is deliberately
near break-even.** The book loses 32 basis points of turnover; rebates pay
four times that loss. That is a coherent, disciplined design — run enormous
maker volume at a controlled small trading loss, collect the rebate — and it
is *not* the "trading is independently profitable" story I told. My earlier
claim that "both halves pay" was an artifact of the filter.

## The bucket decomposition holds up (two-sided is unaffected)

Two-sided markets have exactly **1** total-loss case, so the survivorship
filter barely touched them and this table stands.

| bucket | n | spend | trading P&L | P&L/$ | median pair | **first-leg won %** |
|---|---:|---:|---:|---:|---:|---:|
| A <0.98 | 1,363 | 833,266 | **+26,592** | +3.196% | 0.9089 | **68.5%** |
| B 0.98–<1.00 | 224 | 310,983 | −2,718 | −0.874% | 0.9893 | 50.4% |
| C 1.00–1.03 | 207 | 208,542 | −8,512 | −4.082% | 1.0124 | 42.0% |
| D 1.03–1.10 | 209 | 104,514 | −7,608 | −7.301% | 1.0561 | 36.8% |
| E >1.10 | 469 | 58,016 | −12,723 | −21.931% | 1.2528 | **29.0%** |

### Pair cost is a proxy for how badly the first leg is already doing

`first-leg won %` falls monotonically **68.5 → 50.4 → 42.0 → 36.8 → 29.0**.

The mechanism is arithmetic, not psychology: buy UP at 0.40, price rises, and
DOWN gets cheap — a sub-par pair means *you were right*. Price falls and DOWN
gets expensive — an above-par pair means *you were wrong*. **Expensive
completions are not bad decisions; they are hedges on already-losing
positions.** Judging them by their own pair cost is judging a stop-loss by
its fill price.

## The counterfactual: was completing better than holding the first leg?

For each two-sided market: actual P&L versus keeping only the first leg and
holding it to settlement.

| bucket | n | ACTUAL | first-leg-only | **delta** |
|---|---:|---:|---:|---:|
| A <0.98 | 1,363 | +26,592 | +36,033 | **−9,441** |
| B 0.98–<1.00 | 224 | −2,718 | −5,907 | **+3,189** |
| **C 1.00–1.03** | 207 | −8,512 | −20,558 | **+12,047** |
| D 1.03–1.10 | 209 | −7,608 | −6,724 | −884 |
| E >1.10 | 469 | −12,723 | −10,930 | −1,793 |
| **all two-sided** | 2,472 | −4,969 | −8,085 | **+3,116** |

**Above-par cohort (C+D+E): −28,843 actual vs −38,212 if left one-sided —
completing was better by +$9,369.**

## The owner's binary, answered — and the answer is band-specific

> *If >$1 completions systematically reduce a dangerous residual and improve
> total expected settlement economics, our hard ceiling is blocking an
> important L5 risk-management behaviour.*

**True, and decisively, in 1.00–1.03: +$12,047 across 207 markets.** That
band is genuine loss-mitigation on positions that were going wrong 58% of the
time. Our ≤1.00 ceiling forbids exactly it.

> *If >$1 completions lose trading money and are economically rational only
> because of incentives, our ceiling is doing exactly what we want.*

**Also true — above 1.03.** D and E are −$884 and −$1,793 versus holding: no
better than riding the loser, and E burns 21.9% of the spend it touches. The
ceiling is right to refuse those.

**And note bucket A: completing cheap was −$9,441 WORSE than riding the
winner.** Hedging when you are right 68.5% of the time costs money. He does
it anyway, because certainty is the point and because the volume earns rebate.

## What I am not concluding

- **No recommendation to move the ceiling.** The finding is that value sits in
  a narrow 1.00–1.03 band; whether to buy it is the owner's call, and the
  case is weaker than it looks because the whole trading book is *negative*.
  On a venue without equivalent subsidy the ceiling protects us from the very
  behaviour that only pays through rebate.
- **Observational, not causal.** L5 chose which markets to complete. The
  counterfactual holds his first-leg size fixed and assumes no other action;
  in reality he might have extended, or not opened at all.
- **Incentives are excluded from the counterfactual.** Completing generates
  maker volume that earns MAKER_REBATE, so the trading-only comparison
  understates completion's value to him — and overstates it for us if we lack
  the rebate.
- **Ten days, one venue, one lane** (BTC/ETH up/down), and BTC direction is
  autocorrelated across concurrent windows, so the 1,740 one-sided markets are
  nothing like 1,740 independent draws.

## On the 41.1% one-sided cohort

The owner's caution was right and the data supports it. These are **not**
demonstrably "pure directional" by intent — public fills cannot show
complement quotes that never filled. What is now measurable is that they are
**economically flat** (−0.24%), tiny (average $28 of spend), and lose
completely 707 times out of 1,740. That profile is more consistent with
*failed completion attempts* than with deliberate directional betting.
