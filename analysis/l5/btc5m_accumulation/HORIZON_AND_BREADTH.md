# Can other 5-minute markets help? 2026-11-15

Asked by the owner: *"Do we think using the other 5 minute markets can
help? They all seem to follow the same outcomes."*

## 1. There are no other 5-minute markets

Probed `gateway.polymarket.us/v1/market/slug/{slug}` for the current
aligned window, every asset against every horizon:

| asset | 5m | 15m | 30m | 1h |
|---|---|---|---|---|
| btc | **OPEN** | **OPEN** | 404 | **OPEN** |
| eth | 404 | 404 | 404 | 404 |
| sol | 404 | 404 | 404 | 404 |
| xrp | 404 | 404 | 404 | 404 |

Also checked doge, ltc, ada, bnb at 5m: all 404. PMUS lists BTC up/down
only, at three horizons. **Breadth is not available on this venue.** Any
concurrency has to come from BTC at another horizon, i.e. the same
underlying.

## 2. The correlation worry is aimed at the wrong thing

Two separate points.

**Direction barely matters to a straddle.** Resting 8c on both sides
pays +84c on a pair, loses 8c on a lone leg that settles worthless and
wins 92c on one that comes back. Which way BTC settles is nearly
irrelevant; what matters is whether the *path* visits both tails inside
the window. So "they follow the same outcomes" is true and mostly beside
the point.

**Correlation never reduces expected value.** EV adds linearly across
lanes whether or not they are correlated; only variance reduction needs
independence. What correlated lanes cost us is smoothing -- and that is
not cosmetic here, because the $11/day loss cap is SHARED. Two
correlated lanes draw down together and trip one stop early, so
correlated concurrency buys less than its EV suggests.

## 3. The horizon question was already tested, and it is decaying

`BTC15M_WINDOW_LENGTH.md` pre-registered the mechanism (round-trip
probability grows with time). Recomputed on the full shadow files today:

| | BTC5m | BTC15m |
|---|---|---|
| windows | 257 | 57 |
| pair rate | 7.39% CI[4.8, 11.3] | 8.77% CI[3.8, 18.9] |
| lone-leg win rate | 5.65% CI[3.3, 9.4] | 17.31% CI[9.4, 29.7] |
| net c/share/window | +4.11 CI[+0.26, +7.96] | +15.86 CI[+4.90, +26.82] |
| t | +2.09 | +2.84 |
| **net c/share/HOUR** | **+49.3** | **+63.4** |

Per window 15m looks 4x better. **Per hour, which is the comparison that
matters, it is only ~29% better** -- 4 windows an hour against 12.

And the advantage is concentrated in the early sample, exactly as the
2026-11-14 retraction said:

| | n | lone-win | net c/w |
|---|---|---|---|
| first half | 28 | 25.0% | +26.57 |
| second half | 29 | 10.7% | +5.52 |
| 2026-11-13 | 19 | 23.5% | +22.74 |
| 2026-11-14 | 38 | 14.3% | +12.42 |

**On the recent half, 15m yields +22.1 c/share/hour against 5m's +49.3.**
The horizon advantage does not survive its own second half.

Unresolved: a 380-window replay put the 15m lone-leg win rate at 1.4%,
against this shadow's 17.31%. Different populations and methods; the
disagreement is not closed and it is the single measurement that decides
whether the 15m edge is real.

## 4. Verdict

No to breadth: it does not exist. Not yet to a second horizon: the data
does not currently support it, concurrency is last in the promotion order
(PATIENCE -> FREQUENCY -> SIZE -> CONCURRENCY), and BTC15m is another
session's lane.

The binding constraint is not market count. It is that **neither horizon
has a single live fill**, and BTC5m's t=+2.09 is below the
multiple-comparisons bar of sqrt(2 ln 12) = 2.23 after ten closed
searches. One validated lane is worth more than three unvalidated ones.

Collection note: the 15m shadow file ends `2026-11-14-1445z`, ~22h stale.
That service restarts on every push to its branch and the project is at
Railway's 20-volume limit, so it cannot be given durable storage.

LABELS: everything above is SHADOW. No order was placed, nothing is
realized, and the net figures are settlement arithmetic on hypothetical
fills at an assumed-available size (`min_touch_shares_*` is unrecorded).
