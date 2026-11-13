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
