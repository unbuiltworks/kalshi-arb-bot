# RETRACTED: the recovery-exit result was lookahead

`entry_exit_grid.py` and `exit_on_recovery.py` reported that selling a
lone 8c leg when it recovers to 10c was worth **+5.88 c/w at t +3.01, OOS
+5.29** -- the only result in this program to clear its own
multiple-comparisons bar. **It is an artifact. Do not build it.**

## The bug

Both scripts classify a window as PAIR using the WHOLE window, and only
consider exiting if the other side never filled:

```
if len(filled) == 2:  return PAIR          # decided with hindsight
s = next(iter(filled))
if peak_after[s] >= exit_at: return EXIT   # only reached when no pair
```

That is "exit, but only in the windows that were never going to pair" --
which no live system can know at the moment it must decide.

## Causally, it loses

Selling the moment a held leg recovers, with no lookahead:

| exit | causal net c/w | t | OOS |
|---|---|---|---|
| hold | **+3.74** | +1.67 | +3.03 |
| 0.10 | **-2.89** | **-7.98** | -2.61 |
| 0.12 | -3.23 | -7.63 | -2.45 |
| 0.15 | -3.26 | -6.59 | -2.45 |
| 0.20 | -2.67 | -4.27 | -2.13 |

Significantly NEGATIVE, and worse than doing nothing.

## Why

When UP fills at t=100 and recovers to 10c at t=150 you SELL. If DOWN then
fills at t=250 you have forfeited the +84c pair and hold a lone DOWN leg.
You traded an 84-cent outcome for 2 cents.

    forfeited pairs   10.2% x 84c  =  -8.57 c/w
    exits gained      ~43%  x  2c  =  +0.86 c/w
    net change                        -7.7 c/w   (+3.74 -> about -3.9)

observed -2.89. **The pairs are the entire edge and exiting destroys them.**

## The lesson

The error survived a 34-cell grid, an out-of-sample split, a
multiple-comparisons bar and several rounds of reporting. What caught it
was trying to IMPLEMENT it -- which forces the question a backtest never
has to answer: *what do we actually know at the instant we must decide?*

Any rule whose backtest branches on the window's OUTCOME is suspect. The
straddle's own accounting is safe because it never does -- a leg fills or
it does not, and settlement is applied at the end. The exit rule branched
on "did this window pair", which is only knowable afterwards.

## Where this leaves the straddle

Back to plain HOLD: +3.74 c/w, t +1.67 on the 189-window tape, and
NEGATIVE at the live pair rate of 2.8%. Unproven, and currently losing.
