# I was measuring the wrong thing. The money is the liquidity reward, not the spread.

## 1. What the owner asked for, tested properly

"50-100 trades per window." The engine is capped at `max_live_orders = 1`, so that was
structurally impossible, not a strategy choice. I built the test that had never been run.

The existing `L5_SHADOW_TWO_SIDED` arm says of itself: **"PERMISSION to rest a second side
(the arm never seeks one)"** — it had permission and never quoted both sides. So its
-$0.1868/window verdict was never a verdict on market making, and my earlier report of it as
one was wrong.

`analysis/adverse_entry/replay_mm.py` quotes BOTH sides continuously and re-quotes after every
fill, with an inventory-imbalance cap as the residual control.

| | fills/window | match% | strands | net+rebate | t |
|---|---|---|---|---|---|
| single-order engine (today) | 0.25-0.50 | — | 28% of windows | — | — |
| continuous two-sided, TRAIN | **15.4-16.6** | 75-80% | **100%** | **-$99 to -$156** | -4.9 to -8.5 |
| continuous two-sided, held-out | **15.5-16.9** | 82-87% | **100%** | **-$18 to -$36** | -1.3 to -2.1 |

Frequency IS achievable: a 30-60x increase in fills. **And rebates finally pay real money:
$7.43-$15.16 credited, against $0.00 today.** But net got far worse, and every single traded
window stranded. Quoting behind the touch (1-3 ticks) helps TRAIN (-$114 -> -$56) and hurts
held-out, and lowers the match rate 80% -> 67%. Still deeply negative.

## 2. The number that ends the trading-edge search

Per contract, in the highest-volume configuration tested:

| | gross trading | fees | rebate | NET |
|---|---|---|---|---|
| TRAIN non-BTC | **-2.069c** | -0.235c | +0.226c | **-2.078c** |
| HELD-OUT BTC15m | **-0.577c** | -0.243c | +0.252c | **-0.567c** |

**The rebate already cancels the fees almost exactly.** The loss is entirely in the GROSS
trading result, before any fee. Maximum available rebate is 0.3125c/contract, so no rebate
programme can fix a negative gross edge — and frequency multiplies it, which is exactly what
the table above shows.

**Why: we chose the tightest-spread category on the venue.** From the 37,116-market universe
scan, median spread by category:

| crypto | economics | politics | culture | weather | soccer | tennis | esports | football |
|---|---|---|---|---|---|---|---|---|
| **0.010** | 0.010 | 0.014 | 0.018 | 0.020 | 0.030 | 0.170 | **0.2195** | 0.330 |

A two-maker-leg pair costs `yb+nb` = 1 - spread. In crypto that grosses **+1c/pair** against
0.6-2c/contract of adverse selection. In esports it would gross **+22c/pair**. We have been
market-making in the one category where the spread cannot cover the adverse selection.

## 3. THE ACTUAL BUSINESS, which I had not been measuring

`rewards_programme_share` is **0.96-1.00 in every category** (max spread 4.5c, politics 5.5c).
Essentially every market on the venue is in a liquidity-rewards programme. That pays for
RESTING QUOTES, not per fill — a different and much larger mechanism than the 0.31c maker
rebate I had been analyzing all day.

From `src/kalshi_reward_economics_chain.py`, measured from the venue's `/incentive_programs`:

- **headline pool $20 per 15-minute market** (period_reward in centi-cents; the period is the
  market's own 15-minute life, so it is $20 per market, not per day)
- **qualification is NOT the blocker**: min-side full-book depth 3,354-259,338 against Target
  Size 300-1,000
- the binding constraint is **competition**, scored on size and proximity to Reference Price.
  Band depth within 5c of mid on the five flagship legs: **0, 0, 10, 120, 516 contracts**
- at pair cost ~1.01, **~$111 supports ~110 contracts resting two-sided** — against a band
  depth of 120, a large share of a $20 pool every fifteen minutes
- `CREDITED_REWARD_USD = 0.00`, `ORDER_ENTRY = False`: **we have never placed one order on
  this venue**, so we have never generated a single reward credit to read
- trading drag on those series is -0.8% to -13.8% per turnover dollar — worse than our PMUS
  lane, and in OUR failure regime rather than L5's -0.037%

### The arithmetic that matters

```
one series pool = $20/market x 96 markets/day = $1,920/day
$480/day target = 25% share of ONE series' pool
   5% share -> $96/day      10% -> $192/day      25% -> $480/day
```

**This is the first time the $480/day target has had a plausible mechanism attached to it.**
It is not a trading edge. It is a share of a distributed reward pool.

The prior analysis deliberately refused to divide band depth into pool size, for three good
reasons: we do not have Kalshi's scoring function, the 5-cent band is our invention not the
venue's rule, and a market paying that rate on that little capital would not stay
un-arbitraged. Those reasons still stand. The number is not an estimate.

## 4. The experiment that closes it, and it is cheap

`P0_VERDICT: UNDECIDED` and the module names the one experiment: **read the credited-reward
side of an account that actually rests liquidity.** We cannot read it because we have placed
zero orders.

So the proposal is NOT a trading-edge bet. It is a reward-capture measurement:

- rest two-sided liquidity near the mid on ONE incentivized Kalshi 15-minute market
- a few consecutive markets, maker-only, no crossing
- measure **credited** rewards against measured trading drag
- expected cost if the reward share is zero: roughly the drag, ~1% of turnover
- expected gain if we capture even 10% of one $20 pool: ~$2/market

What it needs that we do not have: **Kalshi order entry** (never used), and **~$100-150 of
resting capital** against the $5 currently authorized. Both are owner decisions.

## 5. What I will not claim

I cannot make the current maker strategy profitable, and I spent this session proving it
rather than asserting it: cancellation, defensive completion, spread filters, sizing,
completion ceilings, continuous two-sided quoting, quoting behind the touch, and all five
crypto 15m series. Every one negative, most significantly so, and every apparently positive
held-out result was a single window (top window 131% then 100% of the total).

The reward pool is the only mechanism found that is large enough to reach the target. Its
share-of-score is unmeasured and unmeasurable without placing orders. I am not going to
present that as profit until the venue credits it.
