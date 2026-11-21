# Entry quality: findings

Run against `analysis/l5/ENTRY_QUALITY_PREREGISTRATION.md` (committed
c2bcf7d4) and its Amendment 1 (committed before the replacement feature was
run against any label). **The holdout was not opened and remains sealed.**

**Headline: the entry filter fails the pre-registered bar and its separation
is inside the noise. But a piece of arithmetic that needs no statistics
answers the owner's question anyway, and the answer is larger than a filter.**

---

## 1. The identity that kills the first hypothesis

We open as a maker **at our own side's touch** — `touch_join` is true on
**2371 of 2371** filled opens. On a binary book the complement's ask is
`1 − our_bid`. So:

```
entry_px + opp_ask == 1.0000 exactly      2279 / 2371    96.1%
```

> **Completing immediately, at the complement's ask, always costs exactly
> $1.00. There is no entry price at which instant completion is profitable.**

Every cent of paired edge must therefore come from the complement filling as
a **maker, inside the spread**. Both legs passive costs `1 − spread`:

```
best attainable pair cost   =  1 − spread
best attainable gross edge  =  spread
```

This also explains the measured **70.6% above-par completion share** without
appealing to bad judgement. Once the book moves at all after our first fill,
`1 − spread` is gone, and the complement's ask — a guaranteed $1.00 pair — is
what remains on the screen.

## 2. The spread filter does not clear the bar, and the sample cannot settle it

TRAIN, 52 episodes (37 paired / 15 stranded), spread at selection:

| | n | mean | 95% CI | median | ≤ $0.01 |
|---|---:|---:|---|---:|---:|
| PAIRED | 37 | 0.0192 | [0.0132, 0.0252] | 0.010 | 54.1% |
| STRANDED | 15 | 0.0167 | [0.0072, 0.0261] | 0.010 | **73.3%** |

The direction is the hypothesis's: stranded entries came from thinner books.
The magnitude does not survive being tested.

```
AUC(spread)        0.4135     bootstrap 95% CI [0.2604, 0.5789]   <- includes 0.5
strand rate, spread <= 1c     11/31 = 35.5%
strand rate, spread  > 1c      4/21 = 19.0%      Fisher exact p = 0.2304
```

Threshold sweep on TRAIN (refuse the entry when spread < θ):

| θ | retained | incidence | paired kept | stranded avoided | paired lost |
|---|---:|---:|---:|---:|---:|
| 0.005 | 52/52 | 0.2885 | 100% | 0 | 0 |
| **0.010** | 46/52 | 0.2826 | 89.2% | 2 | 4 |
| **0.015** | 21/52 | **0.1905** | **46.0%** | 11 | 20 |
| 0.020 | 20/52 | 0.2000 | 43.2% | 11 | 21 |
| 0.025 | 10/52 | 0.2000 | 21.6% | 13 | 29 |
| 0.040 | 4/52 | 0.2500 | 8.1% | 14 | 34 |

θ = 0.015 reaches the break-even band at 19.05% — and **discards 54% of the
paired episodes**, which pre-registration §8-2 defines in advance as *"a
rejection of the strategy, not an improvement to it."* Everything below it
does nothing. This is falsifier §9-3, degenerate separation, firing exactly as
written.

**So no rule is proposed and the holdout is not spent.** Its indicative dollar
delta at θ=0.015 is +$15.33 over 52 episodes, which is the one argument on the
other side; it is recorded, not acted on, because a threshold picked off a
sweep whose AUC interval contains 0.5 is a fitted number, not a finding.

## 3. What the arithmetic says, with no statistics involved

Attainable gross edge at entry = `spread × planned contracts`, both knowable
before the order goes in. Over 78 filled, labelled episodes:

```
attainable edge at entry      median $0.0575    mean $0.0889    max $1.248
measured cost of one stranded market                        -$1.9120
observed residual incidence                                   28.2%

required attainable edge per retained market
  = incidence/(1-incidence) x 1.9120                        = $0.7511

episodes that clear it:  1 of 78   (1.3%)
```

**The median episode offers a thirteenth of the edge it needs to fund its own
share of the stranding risk.** That is not a statement about which entries we
picked. It is a statement about the entire opportunity set we are trading.

Robustness, because −$1.9120 is an estimate with a wide interval: at the
**optimistic** end of its 95% CI (−$0.6657) the requirement is $0.2615, and
the median episode still falls **4.5×** short.

Cross-check on the same arithmetic, from the other direction: for the 33
paired episodes with known matched quantity, `spread × matched` gives a median
attainable gross edge of **$0.145** and a mean of **$0.197**. The measured
paired mean is **+$0.2851**. The difference, ~$0.09, is close to the measured
maker rebate of ~$0.094 per market (7 credited fills × $0.0134). Two
independent routes to the same economics, which is the strongest evidence here
that the model of the edge is right.

## 4. Size is not a lever, and this is why

Edge scales as `spread × q`. Residual exposure — and therefore the cost of a
stranded market — also scales with `q`. **Size cancels out of the ratio.**
Throughput cancels too, as the owner already established. What is left:

> **Spread and incidence are the only two levers on this ratio. Nothing else
> in the engine touches it.**

At the observed median spread of $0.01 and ~5.75 contracts, break-even
incidence is **2.9%**. We run at 28.2%. Alternatively, at the observed
incidence, the required spread is **~$0.13** per market — against a measured
median of $0.01 and a maximum anywhere in the sample of $0.14.

## 5. What this answers, and what it forecloses

The owner asked: *"what first legs should we never have taken?"*

**Answer: 77 of the 78 we can measure** — not because we chose badly among
them, but because the prize on offer was roughly an order of magnitude too
small for the risk required to win it. No entry-price rule, no completion
aggressiveness, no post-imbalance repair and no rebate changes that ratio,
because none of them changes the spread.

Foreclosed by §1 and §4, and not worth further work:
- pricing the first leg differently — immediate completion costs $1.00 at any entry price;
- improving completion queue position inside the above-par regime — §0 of the pre-registration;
- trading more, or trading bigger — both cancel.

Still open, and the only two things that are:
1. **Spread selection.** Are there enough live markets with spreads at the
   top of the observed range to run this at all? The selector already records
   `bid`/`ask`/`bid_touch_qty` on every admitted candidate, so the
   distribution of the *available universe* — not only of what we chose — is
   already on disk and can be measured with no live risk.
2. **Incidence at the 3% level.** Two studies have now failed to move it
   (repairing EXTEND, entry filtering). A third attempt needs a materially
   new hypothesis, per the anti-drift rule, not another parameter.

If the available universe has no wide-spread tail, the honest conclusion is
that this venue and market class cannot support the strategy at our size, and
that is a finding about where to deploy rather than how to tune.

---

*Read-only. No live change. R5F configuration unchanged. Holdout sealed.*
