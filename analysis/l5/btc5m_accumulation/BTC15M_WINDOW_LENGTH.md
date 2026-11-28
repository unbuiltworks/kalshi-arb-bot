# The 15-minute window: the one lever that is not closed

After ten closed searches on BTC5m, window LENGTH was the only untested
idea with a mechanism: price range scales roughly with the square root of
time, so a 15-minute window gives ~1.73x the range of a 5-minute one for
the SAME 8c legs. More range means both tails get touched more often, and
a leg bought at 8c has three times as long to come back.

**This was predicted before the data was looked at**, which matters: the
multiple-comparisons penalty that applies to a found-by-search result
(the per-side "edge", which was a directional artifact) applies much less
to a hypothesis stated in advance.

## First 22 windows, 2026-11-13/14

| | 15m shadow | BTC5m tape |
|---|---|---|
| windows | 22 | 187 |
| pair rate | **18.2%** | 10.2% |
| lone-leg WIN rate | **27.8%** (5 of 18) | **2.5%** |
| net c/w a share | **+31.45** | +3.74 |
| t | **+3.04** | +1.67 |

```
13 windows  ONE, leg lost   -0.08
 5 windows  ONE, leg WON    +0.92   <- the driver
 4 windows  PAIR            +0.84
net = (-1.04 + 4.60 + 3.36)/22 = +0.3145
```

**The pair rate is NOT the main difference.** The lone leg winning 27.8%
of the time against 2.5% on 5m is, and it is worth +4.60 of the +6.92
total. That is the recovery mechanism the hypothesis predicted: three
times the window, three times the chance the collapsed side comes back.

## Why this is NOT yet a result

1. **n = 22.** The 5m tape has 187 and still only reaches t +1.67.
2. **Directional skew.** DOWN settled 12 of the 18 ONE windows (67%). The
   identical confound made the per-side "edge" look real at t +2.10
   before the base rate explained it. A sample that drifts one way makes
   whichever side we hold look skilled.
3. **No fill-size verification.** `min_touch_shares_*` is unrecorded, so
   every fill is assumed available at any size. The 5m shadow's
   assumptions have been optimistic before.
4. **It is a SHADOW.** No order was placed; nothing here is realized.

## Collection

`l5-btc15m-straddle-shadow` deploys from a working branch, so every push
restarts it, and the project is at Railway's 20-volume limit so it cannot
be given durable storage. Its history therefore lived only in the log
buffer -- which is why it appeared to have ONE window when it actually had
22 spread across retained deployments.

`collect_15m_shadow.py` harvests every retained deployment's logs into
`btc15m_shadow_windows.jsonl` in this repo, keyed by market and
idempotent. **Run it before any push to this branch**, or that push
restarts the shadow and the unharvested windows are gone.

## What would settle it

~96 windows is a day of collection. At that n, a 27.8% lone-leg win rate
would be distinguishable from 2.5%, and the DOWN/UP mix would have had a
chance to even out. Until then this is a promising signal with a
plausible mechanism and two live confounds, not a finding.
