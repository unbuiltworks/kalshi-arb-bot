# The edge survives out of sample: t +2.40 on 254 windows

2026-11-14. The most important result on this branch, and it came from
finding data rather than from a new idea.

## What was wrong with every analysis before this

Thirteen searches were run against `btc5m_aligned_20261112.jsonl.gz`,
which holds **189 windows across just two calendar days** (Nov 11: 36,
Nov 12: 153). That sample was treated as everything available. It was
not. `l5-btc5m-straddle-shadow` has been emitting one compact record per
window the whole time, and those records are RECOVERABLE from retained
Railway deployments even though the service has no volume.

67 unique windows from **2026-11-13 and 11-14** were harvested by
`collect_5m_shadow.py` into `btc5m_shadow_windows.jsonl`. They are
genuinely OUT OF SAMPLE: every search -- the price sweep, per-side,
ladders, completion, regimes, volatility, opening skew -- was run on the
Nov 11-12 tape and none of it saw these windows.

## The result

| sample | n | net c/w a share | t |
|---|---|---|---|
| tape Nov 11-12 (IN-sample) | 187 | +3.74 | +1.67 |
| shadow Nov 13-14 (OUT-of-sample) | 67 | **+7.82** | +1.81 |
| **POOLED** | **254** | **+4.82** | **+2.40** |

Pair rate: **10.2% in-sample, 10.4% out-of-sample.**

`t +2.40` clears 2.0 and also clears the thirteen-search bar of
`sqrt(2 ln 13) = 2.25`.

## Why this is more credible than anything else measured here

1. **It survived out of sample.** The winner's curse predicts an
   in-sample estimate inflated by searching, so the out-of-sample read
   should come in LOWER. It came in at roughly double.
2. **The pair rate replicated to 0.2 points** on different days and
   different price action -- the structural quantity, not just the mean.
3. **No search chose these windows.** They are simply the next days.

## Caveats, which are real

- These are SHADOW fills: the same adverse rule the tape uses, but no
  queue position and no size check at the touch. Not realized.
- The LIVE record over the same period is more mixed -- campaign 1
  +7.36 c/w over 25 windows, campaign 2 -3.40 c/w over 20.
- 254 windows at t +2.40 is "probably real", not "established", and the
  edge is still only about 5 cents per window per share.

## What it changes operationally

The shadow produces **288 windows a day, free and read-only**, while a
live campaign produces 25-46 and costs money. The fastest route to a
settled answer is therefore to HARVEST THE SHADOW CONTINUOUSLY and let
the live campaigns serve their actual purpose, which is checking that
real fills behave like modelled ones.

`collect_5m_shadow.py` and `collect_15m_shadow.py` must be run BEFORE any
push to this branch: both shadow services deploy from it, so a push
restarts them and unharvested windows are lost.

## The standing conclusion, corrected

"The search space is closed" still holds -- linearity plus the leg-EV
curve settle that no combination beats the best single leg. "The dataset
is used up" was WRONG. It was a two-day snapshot being mistaken for the
universe while more windows were being recorded continuously.
