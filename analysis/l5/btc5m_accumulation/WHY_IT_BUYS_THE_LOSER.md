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

---

# Resting at several prices at once (8 + 16 + 20): leverage, not edge

Owner asked 2026-11-14: if 0.08 and 0.16 are both positive, why not rest
both? Measured by `ladder_prices.py`, same tape and fill rule.

| rungs | net c/w | t | at risk | **c/w at the same $1 cap** |
|---|---|---|---|---|
| **0.08 only** | +3.74 | **+1.67** | 16c | **+23.4** |
| 0.16 only | +3.12 | +1.20 | 32c | +9.8 |
| 0.20 only | +0.75 | +0.29 | 40c | +1.9 |
| 0.08 + 0.16 | +6.87 | +1.54 | 48c | +14.3 |
| 0.08 + 0.16 + 0.20 | +7.61 | +1.12 | 88c | +8.7 |
| 0.08 + 0.12 + 0.16 + 0.20 | **+10.18** | +1.13 | 112c | +9.1 |

**The ladder looks better and is worse.** Four rungs earns +10.18 c/w
against the single rung's +3.74 -- nearly triple -- while tying up SEVEN
TIMES the capital. Normalise to the same $1.00 of approved per-window
exposure, which is what the caps actually ration, and the single rung
earns +23.4 against the ladder's +9.1.

Under the real $1.00/window approval:

```
0.08 only   $1.00 / $0.16 = 6 shares a side  -> 6 x 3.74 = +22 c/w
4-rung      $1.00 / $1.12 = 1 share per rung ->  1 x 10.18 = +10 c/w
```

Same dollar at risk, half the return. `t` moves the same way (1.67 single
vs 1.12-1.54 laddered), so a ladder is also SLOWER to prove.

## Why

Edge per cent at risk is **0.234 at 0.08, 0.098 at 0.16, 0.019 at 0.20**.
Every rung above 0.08 dilutes the average. Exposure is the rationed
resource (`SERIES_MAX_TOTAL_EXPOSURE_USD` $5, approval $1 a window), so
every cent should sit at the highest edge per cent -- a single rung at the
top of the plateau.

A ladder would only make sense with spare exposure that could not
otherwise be deployed. There is none: $1.00/window already funds 6 shares
a side at 0.08 and the live campaigns use 5.

**If you want more from the same cap, add SHARES at 0.08, not rungs above
it.** This is the same conclusion the earlier ladder work reached ("same
t, pure leverage"); this records the arithmetic behind it.

---

# Recovery time, and what the pair's second leg is actually worth

Owner pushed back 2026-11-14 that the 5m was not exhausted. The 15m
shadow had just shown a lone leg winning 27.8% against 2.5% on 5m, and
the obvious mechanism is RECOVERY TIME rather than window length as such.
If so it must also operate inside a 5-minute window, where a leg filled
at t=30s has four minutes to come back and one filled at t=240s has
thirty seconds. Measured by `recovery_time.py`.

## The mechanism does NOT operate within 5m

| fill time | n | wins | win rate |
|---|---|---|---|
| 0-60s | 7 | 0 | 0.0% |
| 60-120s | 44 | 1 | 2.3% |
| 120-180s | 51 | 1 | 2.0% |
| 180-240s | 51 | 2 | 3.9% |
| 240-300s | 9 | 0 | 0.0% |

Flat at 0-4% whenever it fills. So whatever makes the 15m lone leg worth
27.8% is NOT simply more seconds on the clock, and the 15m result needs
its own explanation (or is the small-sample artifact its confounds
suggest).

## What the second leg of a pair is worth

In the 19 windows where both sides touched 0.08, the FIRST-filled side
went on to win **17 times, 89.5% (+/- 7.0%)**. That is not a coincidence:
the second side only collapses to 0.08 because the first side RECOVERED,
so by the time we buy the second leg we already hold the likely winner.

```
hold only the first leg:  0.895 x 0.92 + 0.105 x (-0.08) = +0.8147
hold the pair:                                              +0.8400
break-even needs the first leg to win 92%
```

**The second leg is approximately fairly-priced insurance.** 89.5%
against a 92% break-even is indistinguishable at n=19. So the pair's
+0.84 is NOT where the edge lives -- in a pair window we would do nearly
as well holding the first leg alone. This corrects how this strategy has
been described throughout: "the pair is the only profitable branch" is
true of the ACCOUNTING and false of the MARGINAL decision.

It is also the adverse-selection law again, applied to the straddle's own
second leg: that leg fills precisely when our existing position has
improved, which is exactly the trigger that was shown to be adversely
selected for patient completion and for hedging.

## The cutoff result, and why it is NOT a finding

Stopping the resting orders early looked like a win:

| cutoff | BOTH | ONE | NONE | net c/w | t |
|---|---|---|---|---|---|
| none | 19 | 162 | 6 | +3.74 | +1.67 |
| 240s | 7 | 165 | 15 | +4.11 | +1.79 |
| 180s | 1 | 117 | 69 | +4.00 | **+1.95** |
| 120s | 0 | 57 | 130 | +1.30 | +0.98 |

Seven cutoffs were swept, so the bar is `sqrt(2 ln 7) = 1.97` and the
best cell reaches **1.95**. It does not clear. This is the same procedure
that produced the retracted exit rule (+5.88 c/w) and the per-side
"edge" (t +2.10), and it gets the same verdict: **not established.**

## The standing conclusion

187 windows can no longer distinguish a real improvement from a lucky
slice, and each further search makes that worse rather than better. The
dataset is used up. What can still move the answer is more DATA -- the
live phase-1 campaigns, and the 15m shadow -- not more ideas.

---

# BTC's own volatility does not predict the pair rate

`regime_filter.py` found no predictor clearing its bar, but every
predictor it tried came from the previous window's OWN UP PRICE -- chop,
flips, whether it paired, how deep the loser got. It never used the
underlying. That left the most mechanical candidate untested: a pair
needs the price to travel 0.08 -> ~0.92 AND BACK inside five minutes,
which needs a large round trip in BTC itself, so realized volatility
should predict pairing almost by construction. And unlike the price-path
predictors it is measurable BEFORE the window opens, so it could support
a real "rest this window or sit it out" decision.

Measured by `vol_regime.py` from `btc_index_1s`, volatility computed from
ticks STRICTLY before each window's start.

| prior 5m vol | n | pair% | net c/w | t |
|---|---|---|---|---|
| Q1 calmest | 46 | 8.7% | +0.17 | +0.05 |
| Q2 | 46 | 6.5% | +0.17 | +0.04 |
| Q3 | 46 | **19.6%** | **+12.35** | **+2.17** |
| Q4 most volatile | 49 | 6.1% | +2.37 | +0.57 |

Q3 clears the four-bucket bar of `sqrt(2 ln 4) = 1.67`. It is still
nothing, for two reasons.

**It is not monotonic.** If volatility drove pairing, Q4 would be the
best bucket. It is the WORST -- 6.1% against Q3's 19.6%. A mechanism
produces a gradient; this produces a spike in the middle. Any story about
high vol being directional rather than choppy would be invented after
seeing the data, which is exactly how the retracted exit rule and the
per-side artifact happened.

**The continuous test kills it.** Bucketing gives four chances to find a
lucky bucket. Correlation gives one:

```
corr(prior vol, net pnl)     = +0.0166   t = +0.23
corr(prior vol, paired 0/1)  = -0.0307   t = -0.42
```

Essentially zero, and the pairing correlation is NEGATIVE. The Q3 spike
was manufactured by the bucketing.

**Closed.** Regime prediction is now tested on both the price path
(regime_filter) and the underlying (here), and neither predicts. There is
no remaining predictor class for deciding which 5-minute windows to sit
out.

---

# Asymmetric pricing: 0.20 on one side, 0.08 on the other

Owner asked 2026-11-14 whether resting 0.20 and 0.08 and having both fill
still counts as a pair. **It does** -- any two opposite legs redeem exactly
1.00, so that pair costs 0.28 and locks +0.72. And the 0.20 leg fills far
more often, because its side only has to dip to 0.20 rather than 0.08.
Smaller round trip, more pairs, smaller prize.

**This was genuinely untested.** The full sweep rests both sides at the
SAME price and `side_asymmetry.py` measures each side separately but still
at a common price. Neither can see an asymmetric COMBINATION. Measured by
`asymmetric_pair.py`, with every pairing evaluated BOTH WAYS (UP@p1/DOWN@p2
and UP@p2/DOWN@p1) and averaged, so the tape's 56.1% DOWN drift -- the
confound that made the per-side result look real at t +2.10 -- cannot
contaminate it.

| rest at | pair cost | locks | pair rate | net c/w | t |
|---|---|---|---|---|---|
| **0.08 / 0.08** | 0.16 | +0.84 | 10.2% | **+3.74** | **+1.67** |
| 0.08 / 0.16 | 0.24 | +0.76 | 15.0% | +3.43 | +1.54 |
| 0.08 / 0.20 | 0.28 | +0.72 | 16.6% | +2.25 | +1.02 |
| 0.08 / 0.30 | 0.38 | +0.62 | 22.7% | +0.43 | +0.21 |
| 0.16 / 0.20 | 0.36 | +0.64 | **23.0%** | +1.94 | +0.76 |

**The pair rate behaves exactly as predicted** -- 10.2% to 23.0%, more than
double. **And net falls monotonically the whole way.** Every asymmetric
combination is worse than 8/8.

## Why more pairs still loses

The prize shrinks (+0.84 -> +0.62) but that is the smaller effect. The
bigger one is that pairs are 10-23% of windows and a LONE LEG is the other
77-90%, and the lone leg is worth **~2.5c no matter what was paid for it**.
So lifting a side from 0.08 to 0.20 turns the frequent branch from a
5.5-point overpayment into a 17.5-point one. The 6.4 points of extra pair
frequency cannot pay for 12 extra cents of overpayment on four windows in
five.

Same arithmetic as the symmetric 0.16/0.20 results and the ladder,
reached by a new route: **lifting any leg off 0.08 buys frequency with
expectancy at a bad exchange rate.**

12 combinations were tested so the honest bar is `sqrt(2 ln 12) = 2.23`
and nothing approaches it -- which also means these results do not
definitively RANK the combinations. What they establish is that nothing
beats 8/8, with a monotonic ordering and a mechanism, which is the
opposite of the volatility quartile spike that was discarded.

---

# Selling the longshot: the fill condition and the longshot condition exclude each other

Owner asked 2026-11-14 about selling longshots rather than buying them.
Every sweep on this branch rests BUY orders, so this was genuinely
unmeasured. It is also not settled by the position-level argument that
selling UP at p equals buying DOWN at 1-p: that equivalence is about the
PAYOFF, not about when you get FILLED, and the fill is the whole story
here.

Measured by `sell_side.py` with the adverse rule mirrored -- a resting
SELL at p fills when that side's bid RISES through p.

| price | fill% | wins given fill | EV c/leg | t |
|---|---|---|---|---|
| 0.08 | **99.7%** | **50.1%** | **-42.02** | **-11.55** |
| 0.20 | 99.2% | 50.4% | -30.16 | -8.30 |
| 0.40 | 94.1% | 53.1% | -12.35 | -3.50 |
| 0.50 | 87.2% | 57.4% | -6.42 | -1.92 |

At **t -11.55** this is the most statistically conclusive result on the
branch -- far more certain than anything measured pointing positive.

## Why, and it is the sharpest statement of the whole program

Compare the two directions at the SAME price:

```
BUY  at 0.08:  fills 53.5%,  what you own wins 11.5%   ->  +1.87 c
SELL at 0.08:  fills 99.7%,  what you are short wins 50.1%  -> -42.02 c
```

**The fill condition and the longshot condition are mutually exclusive.**
A side sits at 0.08 only while nobody wants it. The moment somebody is
willing to PAY 0.08 for it, it is not a longshot any more -- and that
moment is exactly when a resting sell fills. You never sell a longshot;
you sell a thing that has just stopped being one, which resolves a coin
flip against you.

The buy side is the mirror and it is why 0.08 works at all: a resting buy
fills ONLY when the side has genuinely collapsed, which is both why it is
cheap and why it loses 88.5% of the time -- and the 11.5% is the entire
edge.

## The complete picture

The price curve is now measured for BUYING from 0.01 to 0.95 and for
SELLING from 0.03 to 0.50. There is exactly one positive region in the
whole space: **buying at 0.02-0.20, peaking at 0.08**.

*You can only buy things cheaply while they are collapsing, and you can
only sell them once they have stopped.* Every variant tested on this
branch -- favourites, ladders, patient completion, asymmetric pricing,
40/40, and now selling -- is a different attempt to escape that
asymmetry, and the market does not permit it.

---

# One side per window, alternating: identical economics, half the orders

Owner asked 2026-11-14 about resting only UP one window, only DOWN the
next. This never pairs, so it tests whether the pair matters -- and it
does not, which is consistent with the 89.5% first-leg finding above.

| | net c/w | t | risk/window | per $ at risk |
|---|---|---|---|---|
| straddle (BOTH sides) | +3.74 | +1.67 | 0.16 | **0.23** |
| alternate, phase-averaged | +1.87 | +1.67 | 0.08 | **0.23** |

**Exactly half the expectancy for exactly half the exposure, and the same
t.** Linearity again: each leg stands alone, so dropping one drops half
the EV and half the risk. No edge is gained or lost.

## Where it IS useful

Orders per window go from 2 to 1, so the SAME `L5_LIVE_AUTH_MAX_OPEN`
covers twice as many windows:

```
straddle     2 orders/window -> budget 100 covers  50 windows
alternating  1 order/window  -> budget 100 covers 100 windows
```

Resting 10 shares on one side instead of 5 on each gives identical
exposure (0.80/window) and identical expectancy (10 x 1.87 = 5 x 3.74)
while halving submissions. That is the constraint that truncated campaign
1 at 24 of 46 windows and forced the 48 -> 100 budget raise.

## Caveats

- The two phases measured +1.13 and +2.61 c/w on this sample. They must
  average to half the straddle, so that spread is sampling noise on which
  side each window happened to get -- it washes out over a long run and
  does NOT over one campaign. A single alternating campaign is noisier
  than a straddle campaign of the same length.
- Never pairing means the +0.84 events disappear entirely and the result
  rests on single legs winning at 11.5%. Same mean, lumpier path.
- It is NOT a config change: the controller has STRADDLE and ACCUMULATE
  only, so single-side alternating would be new code in the live path.

**Verdict: worth doing if the order budget binds, which it has. Not worth
doing for edge, because there is none to gain.**
