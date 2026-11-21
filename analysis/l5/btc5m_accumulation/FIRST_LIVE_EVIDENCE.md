# First live BTC5m straddle fills, and what the sports ledger implies

2026-11-15. The first orders this strategy has ever placed live, and an
independent result from the sports lane that bears directly on it.

## 1. The live windows

Armed 18:40Z, 2 shares a side at 0.08 with patient completion. Blocked by
a shared-halt re-latch at 20:03:39Z. Five windows settled:

| window | fills | cost | settled |
|---|---|---|---|
| 1900z | UP 2 + DOWN 2, paired at 0.96 | 1.92 | **+0.08** |
| 1905z | lone DOWN 2 | 0.16 | -0.16 |
| 1920z | lone DOWN 2 | 0.16 | -0.16 |
| 1925z | UP 2 + DOWN 4, paired at 0.56 | 2.08 | -0.08 |
| 1930z | lone DOWN 2 | 0.16 | -0.16 |

**Settled net -0.48** on 4.48 of cost. Labels: venue-confirmed fills,
`cost_basis: AUTHENTICATED_FILLS`, `settled: true`, `provisional: false`.

WHAT IS VALIDATED. The pair mechanism. 1900z locked +0.08 independent of
outcome at a 0.96 pair, and the completion in 1925z produced a 0.56
average pair cost -- better than the 0.96 target, because the cheap
second leg filled after the completion. `up_won` was False at 1900z and
True in the other four, and the pair paid either way. The machinery does
what it was built to do.

WHAT IS NOT. Four lone legs, all DOWN, all lost (`up_won: true` in each).
At the shadow's 5.65% that is 0.23 expected wins, so observing zero is
entirely unsurprising. **n=4 decides nothing.** The 4-of-4 DOWN pattern
is consistent with adverse selection -- the side drifting toward 8c is
the side the market is leaving -- but it is equally consistent with a
five-minute uptrend.

## 2. The sports ledger: the same mechanism at real n

From the sports lane's own settled ledger (`fe3b9050`), 682 venue-
confirmed fills, 77 settled markets, **net -19.79**:

> 37 settled markets kept an unmatched leg and **0 of 37 won**, against
> 17.8 implied by their own prices. The completion policy completes a leg
> the market moves FOR and holds a leg it moves AGAINST, so what stays
> unmatched loses its whole price. EV about -0.26 per market opened,
> negative in every sport.

Zero against 17.8 expected is not sampling noise. That is systematic
adverse selection, demonstrated on a strategy whose shape is ours: rest
both sides, one fills, the unmatched leg is the one that moved away.

## 3. Why this threatens the straddle's breakeven

The breakeven pair rate depends entirely on how often a lone leg returns:

| lone-leg win rate | breakeven pair | EV at the measured 7.39% pair |
|---|---|---|
| 5.65% (shadow, 13/230) | 2.72% | **+4.03** c/share/window |
| 0% | 8.70% | **-1.20** c/share/window |

8.70% is the ORIGINAL figure, before the 2026-11-14 correction that
reopened this strategy. That correction rested entirely on lone legs
winning 5.65%. If they win ~0%, the original decision to close the 5m
straddle was right and the correction was wrong.

## 4. What must NOT be concluded

The sports result is **not** a measurement of ours. Its unmatched legs
sat at prices implying ~48% win; our lone legs sit at 8c implying ~8%.
Pooling the two samples (0 of 4 with 0 of 37 to claim "0 of 41") is
invalid and was nearly done here -- different populations, different
price points, different venues. It is a warning about a shared
mechanism, not evidence about this one.

## 5. The measurement that decides it

The lone-leg win rate for BTC5m legs bought at 8c, at adequate n. The
shadow says 13/230 = 5.65%; a 380-window replay said 1.4%; live says 0/4.
That disagreement was already the open question before today and it is
now the only number that matters: at 5.65% the strategy clears breakeven,
at 1.4% it does not.

It is measurable read-only, at zero risk, by the straddle shadow already
running. That is cheaper than buying the answer two shares at a time.

LABELS: section 1 is SETTLED, venue-confirmed. Section 2 is the sports
lane's own settled figure, relayed. Section 3 is arithmetic on a measured
shadow pair rate, not a realized result.
