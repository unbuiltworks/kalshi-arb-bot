# Unequal inventory: the arithmetic, the signal, and the verdict

Phase 3 asked for probability-aware favourite accumulation and
complementary underdog hedging, with unequal inventory allowed when its
conservative expected value and worst-case risk justify it. This is the
measurement that was supposed to come first. **The verdict is: rejected on
evidence, and the precondition is a signal we do not have.**

## 1. The arithmetic settles most of it before any replay

Let the market's own implied probability of UP be `p`, and rest each leg
`delta` behind its touch, so `u = p − delta` and `d = (1 − p) − delta`. For
`n_up` UP and `n_dn` DOWN contracts settling at `S ∈ {0, 1}`:

```
net = n_up·S + n_dn·(1 − S) − n_up·u − n_dn·d

BALANCED (1,1):  net = 1 − (u + d) = 2·delta,  for EITHER outcome
FAVOURED (2,1):  EV  = p + 1 − 2u − d        = 3·delta
worst case (2,1) when the favourite loses     = 3·delta − p
```

So under a fair market **every leg earns its own `delta`, and expectancy
scales with the total contract count — balanced or not.** Unequal inventory
buys no extra expectancy per contract. It buys **variance**, and a worst
case that grows with the favourite's own price.

1230z is that worst case, at the venue's own basis: UP 2 @ 0.9100 and
DOWN 1 @ 0.5000 paid **1.4100** for a **1.0000** payout — **−$0.4100** when
DOWN won, against **+$0.5900** had UP won. PR #207 asserts the same two
figures from an independent fixture, and
`test_the_1230z_settlement_is_reproduced_exactly` reproduces them from the
controller.

## 2. The only independent signal we have is anti-predictive

"Independently estimated" has to mean independent **of the price**, because
the mid *is* the market's probability — weighting toward it pays spread to
repeat the price's own opinion. The only outside signal our recordings
contain is top-of-book size imbalance. Measured directly, on whether it
calls the eventual direction correctly:

| | in sample | out of sample |
|---|---|---|
| imbalance **agrees** with the mid | 63.6% (n=132) | 55.9% (n=279) |
| imbalance **disagrees** with the mid | **14.0%** (n=299) | **20.1%** (n=373) |

Where it agrees it is only restating the mid. Where it diverges it is wrong
**80–86%** of the time. That is not a weak signal, it is an inverted one —
resting size is *supply of liquidity* and the market trades through it.

## 3. Five weightings on identical windows

`analysis/l5/issue195/inventory_weighting_compare.py`. Every candidate frame
is an entry, PROVEN fills only, settlement at the window's final mid as a
labelled proxy, maker-only, no rebate assumed.

**OUT OF SAMPLE — 2026-10-25 ×12 windows, 2026-11-06 1130z**

| variant | net / attempt | net / contract | worst attempt |
|---|---|---|---|
| **balanced 1:1** | **−0.0774** | −0.0526 | **−0.7600** |
| favorite 2:1 (mid) | −0.1426 | −0.0663 | −1.5200 |
| underdog 1:2 (mid) | −0.0896 | −0.0396 | −0.9400 |
| favorite 2:1 (imbalance) | **−0.1573** | −0.0696 | −1.5000 |
| underdog 1:2 (imbalance) | −0.0749 | −0.0348 | −1.5200 |

**IN SAMPLE — 2026-10-28 ×3 windows, 2026-11-06 0300z**

| variant | net / attempt | net / contract | worst attempt |
|---|---|---|---|
| **balanced 1:1** | **−0.0451** | −0.0280 | **−0.7800** |
| favorite 2:1 (mid) | −0.0634 | −0.0277 | −1.5600 |
| underdog 1:2 (mid) | −0.0719 | −0.0282 | −0.9400 |
| favorite 2:1 (imbalance) | −0.1166 | −0.0465 | −1.3400 |
| underdog 1:2 (imbalance) | −0.0187 | −0.0080 | −1.5600 |

**Favourite accumulation is strictly worse than balanced on both axes, in
both samples**, and weighting it by the only independent signal available
makes it the worst of the five. The underdog-imbalance variant is better
*per attempt* in both samples — but it doubles the worst case (−1.52
against balanced's −0.76), and §2 shows the signal driving it is inverted,
so that result is the signal being wrong in a direction that happened to
pay. It is noise, not edge, and it is not implemented.

**Every variant is negative, balanced included.** This is a *relative*
comparison on overlapping entries with a lower-bound fill model; it is not a
P&L and no variant's number is a forecast.

## 4. What was built anyway, and why

`weighted_inventory_decision` in `src/l5_unmatched_disposition.py` prices the
action in full — both settlement outcomes, the worst case, the best case,
and **the balanced alternative always on the record** — and then refuses it
with `NO_INDEPENDENT_PROBABILITY_ESTIMATE`.

That is deliberately different from being unable to consider it. The
mechanism exists, the comparison is recorded, and the trade is admitted the
moment a calibrated independent estimate is passed and clears two floors: a
**5-point** probability edge over the mid, and **$0.01** of expectancy over
the balanced round at the same prices. `test_a_real_independent_edge_does_
admit_the_trade` pins that it is a price and not a prohibition.

Capacity binds first, as everywhere else: an unequal round whose worst case
exceeds the exposure headroom is refused whatever the estimate says.

## 5. Verdict

**Phase 3 is rejected on evidence for now.** Not because unequal inventory
is forbidden, but because its precondition — a probability estimate
independent of the price — does not exist in our data, and the one candidate
is inverted. This matches `CLAUDE.md`: *"Do not assume residual directional
exposure has predictive skill; historical evidence does not support that."*

To revisit it, the missing input is named precisely: a calibrated estimate of
`P(UP)` from a source outside this order book — BTC spot and realised
volatility over the window's own horizon is the obvious candidate and is not
in these recordings. Until then the controller quotes balanced pairs and
says so on every decision.
