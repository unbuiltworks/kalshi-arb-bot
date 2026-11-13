# Why the straddle always ends up holding the unfavored side

Asked by the owner 2026-11-13, and the answer closes a line of thinking
that looked promising. Measured on the 189-window PMUS BTC5m tape.

## It is by design, and it is not avoidable

A resting BUY at 8c fills only when someone sells to us at 8c, which
happens only when that side's price FALLS to 8c -- that is, when the market
concludes it is losing. The favored side's price rises away from 8c and can
never fill.

So a passive deep bid is a structural buyer of whatever is collapsing. That
is not a defect in the controller; it is what a resting limit order IS. The
intended payoff is that BOTH sides collapse at different moments inside one
window (a round trip), leaving a matched pair bought for 16c that redeems
1.00. That is the ~11% case. In the other ~86% only the true loser ever
collapses, we hold it, and it settles at zero.

## The lone leg is worth ~2.5c no matter what we paid for it

One-sided legs only, breakeven = the price paid:

| paid | n | wins | win rate | breakeven | edge |
|---|---|---|---|---|---|
| 0.02 | 160 | 2 | 1.2% | 2.0% | -0.8 pts |
| 0.03 | 167 | 4 | 2.4% | 3.0% | -0.6 pts |
| 0.05 | 167 | 5 | 3.0% | 5.0% | -2.0 pts |
| 0.08 | 162 | 4 | 2.5% | 8.0% | **-5.5 pts** |
| 0.15 | 151 | 4 | 2.6% | 15.0% | -12.4 pts |
| 0.30 | 116 | 1 | 0.9% | 30.0% | -29.1 pts |

The win rate is FLAT at about 2-3% across a fifteen-fold range of price.
Conditional on being filled alone, the thing we hold is worth ~2.5c, full
stop. Any price above that overpays, and 8c overpays by 5.5 points.

This is the same fact as the -4.54c a leg, t -8.39, n 986 reported in
STRADDLE_VERDICT.md, seen from the price axis instead of the time axis.

## So why not rest where the lone leg is fairly priced?

Because the pairs disappear at the same rate. Full decomposition:

| price | both% | ONE c/w | NET c/w | **net per cent at risk** | t |
|---|---|---|---|---|---|
| 0.02 | 1.6% | -0.64 | +0.90 | 0.450 | +0.77 |
| 0.03 | 2.1% | -0.54 | +1.47 | 0.490 | +1.01 |
| 0.04 | 3.7% | -1.41 | +2.03 | 0.507 | +1.21 |
| 0.05 | 5.3% | -1.79 | +3.02 | 0.604 | +1.57 |
| 0.06 | 6.4% | -2.68 | +2.96 | 0.493 | +1.47 |
| 0.07 | 8.0% | -3.54 | +3.36 | 0.480 | +1.57 |
| 0.08 | 10.2% | -4.79 | +3.74 | 0.468 | +1.67 |
| 0.10 | 10.7% | -5.51 | +3.05 | 0.305 | +1.30 |

Edge per cent AT RISK -- which is what the exposure ceiling actually caps
us on -- is flat at ~0.45-0.50 from 0.02 to 0.08, falling off at 0.10.
Resting deeper buys a fairer losing leg and pays for it with rarer pairs,
in almost exact proportion. Every OOS sign agrees, and every t sits between
0.8 and 1.7.

**There is no depth that is better.** 8c was not a bad choice; nothing is.

## That flatness is itself the result

A market where every depth returns the same edge per dollar risked is a
market priced about right. It is consistent with a small real edge and it
is consistent with zero, which is exactly what t ~1.6 says. What it rules
out is a mispricing big enough to find by choosing a better price -- that
search is now closed along with the other five.

## No version of this avoids the unfavored side

- **Buy the favorite instead.** Closed already: a 1c median spread against
  4.87c of measured adverse selection (WHY_IT_CANNOT_WORK_HERE.md).
- **Complete the pair as soon as one leg fills.** When UP fills at 8c the
  YES ask is about 9c, so DOWN costs about 91c and the pair costs 99c for
  a 1.00 payout -- about 1c gross, as a TAKER, which is disabled by policy
  and would be eaten by fees regardless.
- **Buy both legs at once.** Costs more than 1.00. Dead by arithmetic.

Every passive strategy on a resolving binary buys the collapsing side.
That is the venue, not our design. The only question the strategy can ask
is whether the reversal case pays for it, and that needs sample size
rather than cleverness.

## Closed searches, for the record

| lever | result |
|---|---|
| cut deep rungs late in the window | falsified, and backwards |
| delay entry past the toxic first minute | no OOS benefit, destroys pair legs |
| rest wider (0.12-0.20) | edge erodes |
| rest narrower | same t |
| ladder instead of single rung | same t, pure leverage |
| **rest deeper, where the lone leg is fair** | **flat per dollar; no gain** |

---

# Extension to the FULL 1c-49c range, 2026-11-13

The owner asked whether the flat ~2.5% holds for 1-49. **It does not, and
the direction matters.** The table above stops at 0.30; everything between
0.09-0.14, 0.16-0.29 and 0.31-0.49 was never measured until now.

Measured by `lone_leg_full_range.py`, same tape, same adverse fill rule.
It reproduces the published decomposition at 0.08 exactly (BOTH 19, ONE
162, NONE 6 of 187) before reporting anything else.

| price | both% | one n | one win% | net c/w | t |
|---|---|---|---|---|---|
| 0.01 | 0.5% | 135 | 0.0% | -0.20 | -0.37 |
| 0.05 | 5.3% | 168 | 3.0% | +2.99 | +1.56 |
| **0.08** | 10.2% | 162 | 2.5% | **+3.74** | **+1.67** |
| 0.16 | 21.4% | 146 | 1.4% | +3.12 | +1.20 |
| 0.24 | 29.4% | 132 | 1.5% | -0.58 | -0.25 |
| 0.32 | 41.7% | 109 | **0.0%** | -3.64 | -1.48 |
| 0.40 | 58.3% | 78 | **0.0%** | -5.03 | -2.32 |
| 0.49 | 75.9% | 45 | **0.0%** | **-10.27** | **-6.43** |

**The lone leg does not stay at 2-3%. It decays to exactly ZERO.** From
0.32 to 0.49 the side we were left holding won **0 times out of 45-109
one-sided windows at every single price**. If the price fell that far and
never round-tripped, it never came back. Not once.

## This also closes "win more windows at a smaller prize"

That idea was right about the mechanism and wrong about the money, and the
sweep separates the two cleanly:

- **Win rate does rise, exactly as expected.** Pairs go from 0.5% at 0.01
  to **75.9% at 0.49**. Resting near the money wins three windows in four.
- **Net goes the other way, hard.** +3.74 c/w at 0.08 -> **-10.27 c/w at
  0.49**, t **-6.43**. The pair pays `1 - 2p`, which is 2c at 0.49, and
  that cannot fund a lone leg that is worth nothing and costs 49c.

Winning more windows is not the same as making more money, and here they
point in opposite directions over the whole range.

## 0.08 is at the optimum, and the optimum is not proven

Net c/w peaks on a plateau from about 0.05 to 0.16 and 0.08 is the top of
it. But choosing the best of 49 arms needs `sqrt(2 ln 49) = 2.79`, and
0.08 reaches **+1.67**. The peak is NOT established -- it is consistent
with a small real edge and with zero, which is what the plateau's
flatness said already. What is established, at t -2-to-6, is that
everything from 0.24 upward LOSES.

---

# Asymmetric pricing by side: looks like the best idea here, and is not

Owner asked 2026-11-13 whether to adjust, move, or cheapen the bid. The
symmetric sweep above answers the first and third. This answers the one
variant that was never tested: pricing the two sides DIFFERENTLY.

Settlement P&L is linear in legs, so each leg stands alone and the best
symmetric price is the best asymmetric price ONLY IF both sides behave
alike. Measured by `side_asymmetry.py`, same tape and fill rule:

| price | UP fills | UP win% | UP c/w | UP t | DOWN fills | DOWN win% | DOWN c/w | DOWN t |
|---|---|---|---|---|---|---|---|---|
| 0.05 | 99 | 4.0% | -0.51 | -0.48 | 89 | 12.4% | +3.50 | +2.08 |
| 0.08 | 107 | 7.5% | -0.30 | -0.21 | 93 | 16.1% | **+4.04** | **+2.10** |
| 0.10 | 107 | 7.5% | -1.44 | -0.99 | 96 | 18.8% | +4.49 | +2.16 |
| 0.30 | 133 | 21.8% | -5.83 | -2.27 | 125 | 34.4% | +2.94 | +1.03 |

Read naively this is the best result in the whole program: the DOWN leg
alone is **+4.04 c/w at t +2.10**, better than the symmetric straddle's
+3.74 at t +1.67, and the UP leg contributes nothing. It says drop the UP
leg and rest DOWN only.

**It is a directional artifact.** On this tape DOWN settled **105 of 187
windows, 56.1%, z +1.68**. A DOWN leg bought at 0.08 pays 1.00 whenever
DOWN settles, so a tape that drifted down makes DOWN legs look skilled.
Normalising each side's win-given-fill by its own base rate shrinks the
gap from 2.1x to 1.7x, and what remains rests on **8 and 15 wins** --
single-digit counts.

Two further reasons not to act on it:

1. **Multiple comparisons.** Seven prices x two sides is 14 arms, so the
   bar is `sqrt(2 ln 14) = 2.30`. The best cell reaches **2.10**. It does
   not clear, and the per-arm t values were never corrected for the search
   that produced them.
2. **It is the thing the operating model names.** "Do not assume residual
   directional exposure has predictive skill; historical evidence does not
   support that." Resting DOWN-only IS residual directional exposure. On a
   tape where BTC trended up the sign flips, and nothing here distinguishes
   a structural side preference from one day's drift.

**Verdict: asymmetry joins the closed list. 0.08 / 0.08 stands.** Testing
it would need tapes with opposite drift and a prediction fixed in advance
of seeing them -- not a sweep over sides on a single skewed sample.

## So, the three price questions, answered

| question | answer |
|---|---|
| adjust the price? | 0.08 is the measured PEAK of a 0.05-0.16 plateau |
| set the bid cheaper? | measured WORSE: 0.05 is +2.99 vs 0.08's +3.74. Cheaper buys a fairer losing leg and rarer pairs, in almost exact proportion |
| move prices (requote/follow)? | closed: the relative-depth grid, 2-15c below the bid, pins pair cost at exactly 1.0000 across 18 cells |
| different price per side? | closed above -- directional artifact, does not clear its bar |

---

# Patient maker COMPLETION: tested properly, and it fails for a general reason

Owner pushed back 2026-11-13 that the strategy could still be improved.
The strongest remaining idea, and the one the operating model itself names
as a first-class action, is COMPLETE: after one leg fills at 0.08, rest a
MAKER buy on the other side at ~0.88 so the pair costs 0.96 and still
redeems 1.00 -- +4c a share, locked, and needing only a recovery to about
0.12 rather than the full round trip to 0.92 that the cheap pair requires.

This was NOT the already-closed idea. The closed note covers TAKER
completion at market (DOWN's ask is ~0.91-0.93 when UP fills, so crossing
costs ~0.99-1.01 — about nothing, and taker is disabled anyway). Resting
and waiting is a different action, and a completed pair pays exactly 1.00
either way, so adverse selection on the PRICE of the completing leg is
irrelevant. On paper it looked like the best idea left.

Measured by `patient_completion.py`, same tape, same adverse fill rule,
causal by construction (the completing order is placed only after a fill
already observed). Baseline reproduces +3.74 c/w, t +1.67, 19 cheap pairs.

| complete at | pair cost | locks | completed | cheap pairs left | net c/w | t |
|---|---|---|---|---|---|---|
| — (baseline) | 0.16 | +0.84 | 0 | 19 | **+3.74** | +1.67 |
| 0.90 | 0.98 | +0.02 | 120 | 0 | -1.33 | -3.88 |
| 0.88 | 0.96 | +0.04 | 88 | 0 | -2.10 | -4.83 |
| 0.88, only after 240s | 0.96 | +0.04 | 32 | 7 | -2.25 | -1.74 |
| 0.88, only after 265s | 0.96 | +0.04 | 10 | 19 | +2.25 | +1.11 |

Every configuration is worse than doing nothing. Delaying it recovers the
cannibalised cheap pairs but never gets back to baseline.

## Cannibalisation is NOT the main reason. Adverse selection is.

The obvious explanation is that completing pre-empts the +0.84 cheap pair
(note the `cheap pairs left` column going to zero). That is real but
secondary: at 265s all 19 cheap pairs survive and it is STILL worse.

The actual mechanism, and it is sharper:

| | baseline would have |
|---|---|
| completions at 0.88, any time | **26% WON**, 74% lost |
| completions at 0.88 after 265s | **40% WON**, 60% lost |
| lone-leg win rate overall | **2.5%** |

**Completions cap a winner 10-16x more often than chance.** The opposite
side only becomes cheap when OUR side strengthens, and our side
strengthening is exactly when our lone leg was going to win. So the
completing order fires preferentially on the positions that were about to
pay 1.00, and caps +0.92 at +0.04.

```
capped winner   +0.92 -> +0.04  =  -0.88
saved loser     -0.08 -> +0.04  =  +0.12
break-even needs winners < 0.12/(0.88+0.12) = 12% of completions
measured                                      26-40%
```

## The general law, now confirmed twice

This is the same structure as the hedging result earlier in the program:
*deep hedges fire only when the favourite is already winning -- a tax on
winners, absent from losers.* Two different mechanisms, same finding:

> **In this market, any hedge or completion that triggers on your own
> position improving is adversely selected against your winners.** The
> trigger condition and the winning condition are the same event.

That is not a parameter to tune. It applies to completion at any price,
any delay, and to hedging generally, and it is why "lock in a smaller
sure thing" keeps measuring worse than holding. It is the same shape as
the martingale and lower-prize-higher-frequency results: a variance
reduction bought with expectation.

**Closed. 0.08/0.08, hold to settlement, no completion, no hedge.**
