# Late-window settlement calibration, BTC15m

**Verdict: NO-GO for a bounded live directional experiment. The positive
case is specifically INSUFFICIENT DATA, and I can now say by how much.**

Read-only. No service, no deployment, no live authorization, no risk limit
touched. Reproduce with `python3 -I analysis/l5/stage_f/late_window_calibration.py`.

## A correction to the prior verdict document

`DIRECTIONAL_LEANING_VERDICT.md` states "no BTC spot or settlement-reference
price series exists in this repository" and treats the hypothesis as
untestable for that reason. **That is wrong.** `data/ref_lag/` (41 files,
68 MB, recorded 2026-08-31/09-01) contains:

| record | count | carries |
|---|---|---|
| `pbook` | 303,134 | best bid/ask AND both sizes |
| `settlement` | 33,772 | `resolution_side`, `settlement_value`, `source` |
| `spot_trade` | 13,885 | BTCUSD trade prints |
| `spot_rfr` | 6,415 | the reference rate itself |
| `window` | 1,172 | `strike`, `strike_at`, `expiry`, `effective` |

`source` is `GRR-KAIKO_RFR_BTCUSD_60S` -- the actual settlement reference,
not a proxy. So the owner was right that a 403 from one proxy proves
nothing: the series was already on disk. No new collector is needed, and
none is proposed.

## The three questions, kept separate

1. **Predicting L5's next side** -- prior work, 0.7039 OOS by persistence.
   NOT re-tested here and not relevant to the rest.
2. **Predicting settlement outcomes** -- tested here, NEW.
3. **Positive EV at executable prices** -- tested here, NEW.

Questions 2 and 3 had genuinely not been tested before. This is new
evidence, not a restatement.

## Method

- **74** BTC15m windows with book + authoritative settlement + expiry;
  **34,246** two-sided book ticks inside those windows.
- **Chronological, non-overlapping** split: 45 train (08-31 13:45 ..
  09-01 03:00) / 29 OOS (09-01 03:15 .. 09-01 14:00). No window in both.
- **Executable prices only.** To buy UP you pay the ASK; to buy DOWN you
  SELL YES at the BID, so own cost is `1 - bid`. Midpoints and last trades
  are not transactable and are not used.
- **Conservative execution:** buying at the ask IS taking, so the PMUS
  published taker fee `theta*p*(1-p)`, `theta=0.0695`, is deducted.
- **Clustered by window.** Ticks inside one window share ONE settlement
  draw. Every statistic is computed per window first, then aggregated, with
  the standard error from between-window spread. Treating 34,246 ticks as
  independent would shrink every interval ~100x and is the easiest way to
  manufacture a false edge here.
- Buckets: time-to-expiry x own-cost band x side, reported separately.
- **No threshold was optimised on the holdout.** The rule to test OOS was
  to be chosen on TRAIN only; none qualified, so nothing was evaluated out
  of sample.

## Results

Base rate **P(UP settles yes) = 0.5135** over 74 windows -- no side bias.

**No bucket has a 95% confidence interval excluding zero on the positive
side.** Point estimates are negative in 21 of 24 buckets, between -0.02 and
-0.31 per contract. Two buckets are SIGNIFICANTLY NEGATIVE:

| tte_s | own_cost | side | net EV | 95% CI (clustered) | windows |
|---|---|---|---|---|---|
| 180-360 | 0.00-0.20 | UP | **-0.1356** | [-0.1585, -0.1127] | 7 |
| 180-360 | 0.80-1.00 | UP | **-0.3071** | [-0.6140, -0.0002] | 10 |

The two largest positive point estimates both straddle zero by a wide
margin:

| tte_s | own_cost | side | net EV | 95% CI (clustered) | windows |
|---|---|---|---|---|---|
| 180-360 | 0.00-0.20 | DOWN | +0.1206 | [-0.2339, +0.4751] | 7 |
| 180-360 | 0.20-0.40 | DOWN | +0.0419 | [-0.2820, +0.3658] | 9 |

This is what a fairly priced binary market produces: buying at the
executable price and holding to settlement loses approximately the spread
plus the fee.

## How underpowered the positive case is

Implied per-window standard deviation of net EV: **0.4849**.

Required edge to be worth taking: taker fee at p=0.50 is 0.0174, plus half
of a one-tick spread 0.0050 -> **break-even 0.0224**.

| edge to resolve at 95% | windows needed | hours of BTC15m |
|---|---|---|
| break-even, 0.0224 | **1,804** | 451 |
| 3c, worth doing | **1,004** | 251 |
| 5c, clearly worth it | **361** | 90 |

**We have 74.** The test is short by 5x to 24x in window count for the
edges that would matter. So even a real 3c edge could not be confirmed by
this sample -- which means authorising capital on it would be authorising
capital on an unmeasurable signal, exactly what the owner's own rule
forbids.

## Two further limits, stated rather than buried

- **This is GEMINI data** (`GEMI-BTC15M...`), not PMUS. Different fee
  schedule, liquidity, and maker rebate. It is evidence about this product
  CLASS, suggestive for PMUS, not a measurement of PMUS execution.
- **Strike coverage is 37/74.** The `p_hat` versus `p_up` mispricing model --
  the thing that would make this a genuine signal rather than a calibration
  check -- has only half these windows, so it is weaker still and was not
  run.

## What would change the verdict, and what it costs

**361+ PMUS BTC15m windows** with strike coverage, joined to settlement.
At four windows an hour that is **~90 hours** of continuous recording for
the loosest useful threshold.

`l5-pmus-observer` is ALREADY RUNNING and recording continuously, so this
accrues at no cost and needs **no new service and no deployment**. The
honest next step is to let it accumulate and re-run this exact script
against PMUS windows, not to authorise a live directional probe now.

## GO / NO-GO / INSUFFICIENT DATA

**NO-GO**, on two independent grounds:

1. No bucket shows positive EV with an interval excluding zero, and two
   buckets are significantly negative.
2. The sample cannot resolve an edge of the size that would matter, so a
   GO would rest on a signal we cannot measure.

No market, window count, capital figure, entry rule or hedge rule is
proposed, because item 3 of the owner's design -- "marginal expected net
value is positive and sufficiently robust to estimation error" -- is not
satisfiable on this evidence. Proposing numbers would dress an unmeasured
signal in the appearance of a plan.
