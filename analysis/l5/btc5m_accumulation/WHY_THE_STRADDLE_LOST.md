# Why the live BTC5m straddle lost: a verified defect, and a bad default

2026-11-15. BTC5m's own data only. BTC15m and sports results are NOT
used as evidence here.

## Confirmed settled realized net P&L

**-$0.48** over 5 settled windows, 2 shares a side, venue-confirmed fills
(`cost_basis: AUTHENTICATED_FILLS`, `settled: true`, `provisional: false`).
Cost cycled $4.48. Nothing else about today is realized.

## The defect: completion and the cheap rung are not independent

`cpc-btc-updown-5m-2026-11-15-1925z`, traced through the live log:

```
t=234.4s  UP filled 2 @0.08        (p_up 0.264 at fill)
t=234.4s  completion places DOWN 2 @0.88   -> FILLED
          the cheap DOWN rung is STILL RESTING -> FILLED @0.08
```

Window ended holding 2 UP @0.08, 2 DOWN @0.08, 2 DOWN @0.88:

| | |
|---|---|
| cheap pair alone would pay | **+1.68** |
| extra 0.88 leg settled worthless | **-1.76** |
| realised | **-0.08** |

**Completing destroyed $1.76 in one window**, in a window that was
otherwise the best outcome the strategy can produce.

The code called this additive: *"no cancel is issued here and the cheap
rung above is untouched, so this can only ever add a second way for the
window to end matched"*. The two routes buy the SAME side. When both
fill, the expensive one is pure loss.

A second instance of the same class: in `1920z` the completion was
re-placed at t=253.5, 259.4 and 265.5 as three separate working orders
(`DR60NH6G`, `DR60PZP0`, `DR60RFP0`), 3x the intended exposure. None
filled, so it cost nothing this time.

## Priority 1 hypotheses, judged on BTC5m data

| # | hypothesis | verdict |
|---|---|---|
| 1 | first-leg adverse selection | **NOT SUPPORTED at n=479.** Lone-leg win rate is **9.81% (47/479), CI [7.46, 12.80]** -- ABOVE the 8% the price implies. The 5.65% (13/230) that suggested mild adverse selection did not survive doubling the sample. Legs still fill at a median t=175s of 300s, but the side that reaches 0.08 is not systematically the loser. |
| 2 | failure to complete | **inverted**. Completion fires too eagerly, not too rarely. |
| 3 | poor simultaneous placement | **no evidence**. Both legs rest from t~8s at one fixed price and are never requoted. |
| 4 | excessive cancellation / lost queue priority | **no**. The straddle issues no cancels at all; the defect is the opposite -- duplicate orders. |
| 5 | late-window inventory exposure | **yes**, this is the 1925z mechanism: the cheap rung fills AFTER the completion. |
| 6 | fee and slippage drag | **not measured**. Maker fills at a fixed own-price; no fee line appeared in the settled records. |
| 7 | incorrect probability estimates | **not supported at n=5**. Model direction was right in 4 of 5; mean p of the filled legs ~0.26 implies ~1.3 wins in 5, observed 1. Consistent with calibration. |
| 8 | stale data / execution delay | **no**. `stream_age_s` 0.007-0.064 on an authenticated WS, 1700-2600 book updates per window. |

## Strategy comparison (Priority 3), per share per window

**SUPERSEDED by the n=536 harvest -- see section "Measurement at n=536"
below.** The table here was computed on the first 257 windows and is kept
only to show what changed.

Inputs on 257 windows: P(BOTH) 7.39%, lone-leg win 5.65%, q 7.76%,
completion fill rate 40% (live, n=5, NOISY).

| | policy | EV (n=257) |
|---|---|---|
| **A** | straddle, completion **OFF** | **+4.11 c** |
| A' | straddle + completion **as deployed** | +3.57 c |
| A" | straddle + completion **fixed** | +0.19 c |
| E | no-trade baseline | 0.00 c |

Completing a lone leg locks +4.00c; waiting was worth +4.35c at q=7.76%
(breakeven q=7.35%) -- close enough then to be a coin flip.
**On every plausible parameter, completion ON is not better than OFF**,
and the campaign ran with it ON.

Note A" -- the corrected code -- models WORSE than A', because cancelling
the cheap rung forfeits the cheap-pair upside. The fix removes a
strictly-dominated behaviour (buying one side twice); it does not make
completion a good idea. A" is also very sensitive to the completion fill
rate, which n=5 pins badly.

## Measurement at n=536 (2026-11-15, after harvesting 279 new windows)

`collect_5m_shadow.py` recovered 279 windows the shadow had not yet
stored, more than doubling the set. Every figure below is SHADOW.

| | n=257 | **n=536** |
|---|---|---|
| lone-leg win rate | 5.65% | **9.81%** (47/479) CI [7.46, 12.80] |
| pair rate | 7.39% | 6.53% (35/536) CI [4.73, 8.95] |
| q (opp reaches 0.08 after first fill) | 7.76% | 6.96% (35/503) |
| lone-leg EV | **-0.0235**/share | **+0.0181**/share |
| **A: straddle, completion OFF** | +4.11 c | **+7.10 c/share/window** |
| t | +2.09 | **+4.74**  CI [+4.16, +10.04] |

Two things changed sign or conclusion:

1. **The lone leg is not adversely selected.** At 9.81% against 8%
   implied, holding it to settlement is POSITIVE EV (+0.0181/share), not
   negative. The whole "we buy the loser" reading rested on 13/230.
2. **Completion OFF is now decisive, not marginal.** Completing locks
   +4.00c; waiting is worth +7.53c; waiting wins above q=2.66% and q is
   6.96%. The earlier coin-flip was an artifact of the smaller sample.

Breakeven lone-win rate at this pair rate is **2.13%**; measured 9.81% is
4.6x it. t=+4.74 clears the multiple-comparisons bar sqrt(2 ln 20)=2.45.

**Chronological stability** -- the sign holds everywhere, the magnitude
does not:

| block | n | lone-win | net c/w | t |
|---|---|---|---|---|
| first half | 268 | 6.22% | +4.36 | +2.24 |
| second half | 268 | 13.45% | +9.85 | +4.33 |
| 2026-11-13 | 47 | 2.56% | +6.21 | +1.27 |
| 2026-11-14 | 244 | 7.66% | +5.20 | +2.47 |
| 2026-11-15 | 245 | 13.30% | +9.18 | +3.91 |

Every half and every day is positive. The lone-win rate TRENDS UP across
them (2.56 -> 7.66 -> 13.30), which is the one thing to distrust: it may
be a volatility regime rather than a stable property, and the worst day
is barely above the 2.13% breakeven.

## What is NOT established

n=5 live cannot separate any policy in that table. Measured live is
-4.80 c/share/window against a modelled +4.11; both are inside the noise
of five windows. The sign of this strategy is still unknown.

## Next measurement, in order of value

1. **Turn completion OFF** (`L5_BTC5M_COMPLETION_PAIR_COST=0`). It is the
   default; the campaign overrode it. DONE 2026-11-15: the variable is
   cleared on `l5-btc5m-live`, which is disarmed.
2. **Late completion**: complete only once the cheap rung can no longer
   realistically fill, cancelling it then. That keeps the cheap-pair
   chance for most of the window and still locks +0.04 on the residual.
   Needs the distribution of time-to-opposite-touch, which the shadow
   records can give at zero risk.
3. The lone-leg win rate at adequate n remains the number that decides
   the strategy, and the read-only shadow measures it for free.

LABELS: settled figures are venue-confirmed. Policy EVs are arithmetic on
shadow-measured rates, not realized results.
