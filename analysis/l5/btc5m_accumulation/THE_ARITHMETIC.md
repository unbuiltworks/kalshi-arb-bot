# Why spread capture cannot work on BTC5m, and what survives

Measured 2026-11-13 on the 187-window tape. This is the whole branch in
one inequality.

## The three numbers

```
1. SIMULTANEOUS maker pair     bid_up + bid_down  =  0.9900
   (21,224 observations)        -> +1.00c, exactly the spread

2. REALIZED pair, resting at the touch on both sides
   both legs filled in 187 of 187 windows   (100%)
   realized cost                           =  1.0100 median
                                              1.0256 mean
                                              -> -1.00c a pair

3. THE GAP                                 =  2.00c, lost to timing
```

**The structure is sound and the execution is not.** At any instant a
maker pair costs 0.9900 -- a real, free cent, present in every one of
21,224 observations. Rest on both sides to collect it and you realize
1.0100 instead. You lose two cents to timing, double the one cent you are
trying to capture.

## Frequency is not the constraint

Both legs filled in **187 of 187 windows**. You do not need many and you
do not get few -- you get every single one. The loss is purely price.

## Why, mechanically

Your UP bid fills when UP's price **falls** to it. Your DOWN bid fills
when DOWN's price falls, which is when UP **rises**. Each leg is filled by
the market moving ONTO it, so each gives back about a cent of the spread
it was meant to earn. Two legs, two cents.

To profit you need

```
spread  >  timing cost        i.e.   spread > 2c
```

BTC5m offers **1c**. That single inequality explains every negative result
on this branch: 17 entry gates, flip hedging (t -5.88), realized pair
costs of 1.1852-1.3251, the band floor, the clip sweep, 5x size, and the
whole 18-cell under-bid sweep from 2c to 15c where the pair cost stayed
pinned at 1.00 at EVERY depth.

It also explains why a wide-spread venue was the right instinct. The live
PMUS sports slate turned out to be 1c too (261 markets, median 1.0c,
p90 2.0c), so that door is closed on the same arithmetic.

## What escapes it

| | touch-resting pair | deep straddle |
|---|---|---|
| earns | the **spread**, 1c | **probability variation**, 84c |
| fill price | moves against you ~1c a leg | fixed: 8c or nothing |
| intended -> realized | 0.99 -> **1.01** | 0.16 -> **0.16** |
| needs | tight spreads | a round trip |

The straddle is not trying to capture the spread. It bets that P(up)
visits 8% and 92% at DIFFERENT TIMES, harvesting variation in the
probability itself. A fixed limit forty cents from the market cannot be
filled at a worse price, so there is no timing cost to lose. That is why
it is the only survivor.

## The one open question

`entry_exit_grid.py`: enter 8c both sides, and sell a lone leg the moment
it recovers to 10c.

```
net +5.88 c/w   se 1.96   t +3.01   OOS +5.29
pair 10.2%   exit 42.8%   held 43.9%
34 cells searched -> bar 2.66. CLEARS IT.
```

The only result in this program to clear its own multiple-comparisons bar
AND hold out of sample, and it adds no risk: worst case stays -8c a share.
It also does not need pairs, which matters because the live shadow's pair
rate is 2.8% against the tape's 10.2%.

**The decisive number, now being measured live as `own_peak_after_first`:
of the legs that fill at 8c, what fraction reach 10c before the cancel?**
The tape says 42.8%. If live agrees, this is a strategy. If live says 5%,
the tape was flattering it and the branch is done.
