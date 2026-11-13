# The BTC5m straddle: what is measured, what is not, and what it costs to find out

Status as of 2026-11-13 00:15Z. Supersedes nothing; `WHY_IT_CANNOT_WORK_HERE.md`
still holds for favorite accumulation, which is closed for a different reason.

## One paragraph

Rest fixed limit buys on BOTH sides of a BTC5m market, deep (0.05-0.10).
A binary resolves, so the losing side's price collapses through every rung
and we end up holding it; the winning side rises away and never fills. That
happens in about 86% of windows and costs a little. In about 11% the market
swings deep BOTH ways inside one window, both sides fill, and the matched
pair redeems 1.00 for far less than 1.00. The 11% is supposed to pay for the
86%. On 189 recorded windows it does, by about 1.8x -- but at t = +1.58,
which is not a result.

## The decomposition, 189 windows, one share a side a rung

| config | PAIR c/w | RESID c/w | NET c/w | se | t |
|---|---|---|---|---|---|
| 0.08 single rung | +8.53 | -4.79 | +3.74 | 2.25 | +1.67 |
| 0.05-0.10 ladder | +43.22 | -23.93 | +19.29 | 12.23 | +1.58 |
| paired difference | | | +15.55 | 10.06 | +1.55 |

Every variant tried lands at t 1.1-1.7. Mean and standard error scale
together, so t does not move: these are ONE bet at different sizes, and the
ladder is 5.6x the stake with zero additional information.

The pair/residual split above groups by rung. The venue matches by quantity
instead. Verified 2026-11-13: rung-matched, quantity-matched and
leg-by-leg-linear totals agree to the cent on all 187 valid windows
(36.08 total, 19.29 c/w). Settlement P&L is linear in legs, so the grouping
is presentation only. The NET column is correct under any of them.

## Does it work? Probably. Not proven. Never measured negative.

Lead with the net, because the decomposition below reads as a verdict and
is not one:

| config | net c/w | se | t | P(edge > 0), naive |
|---|---|---|---|---|
| 0.08 single rung | +3.74 | 2.25 | +1.66 | ~95% |
| 0.05-0.10 ladder | +19.29 | 12.23 | +1.58 | ~94% |

It has never measured negative -- not at any price, not in sample, not out
of sample. Every price 0.05-0.15 is positive and 8 of 9 agree OOS. The live
record over-delivered on the parameter that matters: 2 pairs in 11 windows
is 18% against a 10.7% model rate.

The honest caveat is narrower than "it does not work": 80+ arms were
searched on ONE tape, so the naive 95% is optimistic, and these nested
price levels are not independent confirmations. That is an argument for
more data -- which is free -- not for abandoning the strategy.

### Within that net, where the money comes from

ESTABLISHED: a one-sided leg loses **4.54 cents**, se 0.54, **t -8.39**,
n 986. Resting a deep bid reliably buys something about to be worth
nothing.

NOT ESTABLISHED: that the pair case pays for it, at t +1.58.

Both are true, and stating them in that order has misled readers of earlier
drafts of this file, including its author. The one-sided bleed is a
COMPONENT already paid for inside the +3.74. The net is what gets traded.

## Five refinements tested. All five dead.

| lever | result |
|---|---|
| cut deep rungs late in the window | **falsified, backwards** |
| delay entry past the toxic first minute | no OOS benefit, destroys pair legs |
| rest wider (0.12-0.20) | edge erodes to +0.75 c/w |
| rest narrower | same t |
| ladder instead of single rung | same t, pure leverage |

The first deserves recording because the prediction was pre-registered and
came out inverted:

```
0s-60s      46 legs   0 wins    0.0% vs 8.2% breakeven   -8.2 pts
240s-300s   73 legs   5 wins    6.8% vs 6.9% breakeven   -0.1 pts
trend  rho +0.210   t +6.73
```

Zero winners in forty-six first-minute fills. An early collapse to a nickel
is information and does not revert; a late drift there is convergence on a
market that was always close, and is priced about right. Cutting late rungs
would have removed the only legs that break even.

Delaying entry fails for a mechanical reason worth remembering: a rung
reachable at T+40s is almost always reachable again at T+150s as the market
converges, so a delay does not prevent the fill, only postpones it. What it
reliably destroys is the first leg of a REVERSAL pair, which is the only leg
that earns anything.

## The live record, settlement-only

| when | config | windows | pairs | result |
|---|---|---|---|---|
| earlier 2026-11-12 | 0.08 single, ~5 sh | 5 | 2 | **+$6.29** |
| 23:10z | ladder, two defects | 1 | 0 | **-$0.55** |
| 23:30z-00:00z | ladder 0.05-0.08, 5 sh | 6 | 0 | **-$6.50** |
| unbooked, recovered 00:21Z | three windows never settled in the ledger | 3 | | **+$0.74** |
| **day** | | **12** | **2** | **about FLAT** |

The "+$7" was gross wins inside a session that was net roughly flat -- the
account's own 24hr figure read -$0.64 right after a +$3.69 payout and
-$1.21 after a +$1.91 one.

The +$0.74 line is not an estimate. The settlement recovery ran at boot on
2026-11-13 00:21Z and booked three windows the ledger had never recorded,
from the venue's own settlementPx: 16:30z +0.1232, 20:25z -0.7007, 20:35z
+1.3146. So the day's realized figure had been UNDERSTATED, and every
earlier report in this file's history that said "-$0.76" was reading an
incomplete ledger. Same run freed the $4.97 of ghost exposure:
`exposure_before 4.9729 -> after 0`.

2 pairs in 11 valid windows is 18% against a 10.7% model rate. Nothing in
the live record contradicts the model; nothing in it confirms the edge
either. 40% in five windows was a 1-in-12 run; 0 in six was a 51% coin
flip. Both are what ~11% looks like in tiny samples.

## Why six-window campaigns cannot answer this

The strategy wins 13.9% of windows (median +510c) and loses 86.1% (median
-45c). An 11:1 payoff at a 14% hit rate. Therefore:

```
P(no winning pair in  3 windows) = 71.2%
P(no winning pair in  8 windows) = 40.5%
P(no winning pair in 48 windows) =  0.4%
```

Four campaigns in ten are blank by construction, whether or not the edge is
real. Reading any single campaign as a verdict is a category error, and
every campaign run so far has been structurally incapable of settling
anything.

## What it costs to find out

Per-window sigma is 168.1 cents a share-rung, against a mean of 19.29.

| size | risk/win | windows to t=2 | if edge real | if edge ZERO |
|---|---|---|---|---|
| 1 sh/rung | $0.45 | 304 | +$59 | $0 +/- $29 |
| 2 sh/rung | $0.90 | 304 | +$117 | $0 +/- $59 |
| 5 sh/rung | $2.25 | 304 | +$293 | $0 +/- $147 |

Against the $50 daily halt over a 288-window day:

| size | day | expected max drawdown | halt |
|---|---|---|---|
| 1 sh/rung | +$56 +/- $29 | ~$23 | survivable |
| 2 sh/rung | +$111 +/- $57 | ~$46 | **trips on noise** |
| 5 sh/rung | +$278 +/- $143 | ~$114 | **trips on noise** |

**1 share a rung is the only size that can run a full day.**

And these two are THE SAME TRADE, which is the practical point:

| config | net c/w | worst case a window |
|---|---|---|
| 0.08 single rung, 5 shares | +18.70 | 40c |
| 0.05-0.10 ladder, 1 share | +19.29 | 45c |
| 0.05-0.08 ladder, 5 shares (ran 23:30z) | +55.73 | **130c** |

Same return, same risk, for the first two. So going back to 8c on both
sides is not a retreat -- it is one of two equivalent correct sizings, and
the simpler one. The third line is what actually ran, at 3.25x the stake,
and is the whole difference between -$6.50 and about -$2.

## The evidence is already free, and we were buying it

`l5-btc5m-public-recorder` has been live since 2026-11-11 21:02Z,
`restartPolicyType: ALWAYS`, appending public quotes to a 256MB volume at
`/data/btc5m_quotes.jsonl`. The committed tape ends 2026-11-12 13:30Z at 189
usable windows. The recorder did not stop.

So roughly **320 windows already exist**, past the 304 needed for t=2.
Tonight's campaign cost $6.50 and produced 6 windows; the recorder produced
~127 in the same stretch for nothing.

Getting them needs a way to read that volume. The GraphQL API refuses the
session's injected credential, there is no Railway CLI in the image, and
pulling thousands of rows through an agent context and re-emitting them to
disk is not trustworthy for a measurement this marginal.

## Recommendation

1. Pull the accumulated tape and rerun on ~320 windows. Free, no risk, and
   it either clears t=2 or it does not.
2. If it clears, go live at **1 share a rung** -- not 5.
3. Do not raise `MAX_WINDOWS` to buy live windows at $1-2 each while the
   recorder gives them away. The ceiling was never the real blocker; that
   framing was wrong.
4. Stop reading individual campaigns as verdicts.

## Scripts

All read-only, all `python3 -I analysis/l5/btc5m_accumulation/<name>`:

- `straddle.py` net by price
- `straddle_decompose.py` pair vs one-sided, per price, with OOS
- `ladder_decompose.py` the deployed ladder vs the single rung, paired
- `fill_timing.py` win rate by time of fill, pre-registered
- `ladder_entry_delay.py` entry delay, paired OOS
