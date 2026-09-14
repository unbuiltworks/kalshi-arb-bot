# Canonical L5 run / switch / inventory-intention statistics

2,216 windows, 71,984 fills, 23,955 same-side runs, 21,739 switches.
Public fills and observable market data only. **Cancellations, resting orders,
queue position and maker/taker certainty remain UNKNOWN and are never
inferred** — a "run" counts FILLS, never orders.

## A COLUMN ERROR I MADE AND CAUGHT

My first pass keyed runs on `side` and found **2 switches in 2,216 windows**.
`side` is BUY/SELL and **71,982 of 71,984 rows are BUY** — L5 buys both
outcomes, so that column says nothing about inventory. The inventory side is
`outcome` (Up 35,801 / Down 36,183). Every number below is keyed on `outcome`.
The first version was not a weak result, it was a meaningless one.

## THE NUMBERS

| # | statistic | value |
|---|---|---|
| 1 | fills per same-side run | p25 1, **p50 2**, p75 3, p90 6, max 45, mean 3.00 |
| | runs of length 1 | 40.3% |
| | runs of length >= 3 | 36.8% |
| 2 | distinct prices per run | **p50 2**, p75 3, p90 4, max 26 |
| | runs at ONE price | **46.0%** |
| 3 | seconds between fills, all | p25 0, **p50 2.00**, p75 8, p90 20 |
| | same side | p25 0, **p50 1.00**, p75 5, p90 15 |
| | across a switch | p25 1, **p50 4.00**, p75 12, p90 29 |
| 4 | price step, consecutive same-side fills | **\|step\| p50 0.0100** (one tick), p75 0.02, p90 0.06 |
| 5-6 | **same-side fills at a DIFFERENT price than the previous fill** | **35,240 / 48,029 = 73.4%** |
| 8 | fills per 5 min | p50 **5.0**, p90 46.0 |
| | switches per 5 min | p50 **2.0**, p90 13.0 |
| 9 | \|cum_up − cum_down\| at a fill | p25 46.08, **p50 112.88**, p75 213.56, p90 317.10 |
| | fills taken while IMBALANCED | **99.7%** |

The all-fills median of **2.00 s** reproduces the known canonical reference
exactly, which is the check that this reconstruction is reading the right
tape.

## WHAT IT SAYS ABOUT OUR CLONE — AND IT IS DIRECT

**L5's modal same-side continuation happens AT A NEW PRICE.** 73.4% of
consecutive same-side fills are at a different price from the one before,
typically **one tick** away, a **median 1 second** later. Only 46.0% of runs
sit at a single price.

**Our clone treats exactly that condition as a reason to terminate.** In the
Gemini replay, 113–132 of ~140 orders end as `touch_moved` cancels; the PMUS
corpus showed the same mechanism, and the nicpar-gabpen forensics showed two
COMPLETEs killed at 1.2 s and 1.8 s on one poll.

So the behaviour our shell reads as "the market left my price, abandon this
attempt" is the behaviour L5 exhibits **73.4% of the time while continuing to
build the same side**. That is a specific, quantified behavioural divergence
and it is the hypothesis P0 asked for — **stated as a hypothesis, because
these fills cannot establish whether L5 requoted, rested through, or was
filled from an order already resting. That distinction is exactly what public
data cannot see.**

**L5 also lives imbalanced**: 99.7% of its fills are taken with non-zero
inventory imbalance, median 112.88 contracts. Our residual cap is 6 action
units. Those are not the same regime, and the cap is an owner rule, not a
finding.

## WHY OUR GEMINI REPLAY ACTS ONLY ~EVERY 50 s

**It is an action cap, not model slowness.** `MAX_ACTIONS = 6`
(`src/gemini_l5_shadow_v2.py:108`, mirroring `PMUS_SPRING_MAX_ACTIONS`), and
the replay reports `decisions_per_campaign_p50 = 6.0` — **the cap binds at the
median**. Six actions spread across a ~300 s window is ~50 s per action by
construction.

Two further filters compound it: of 120 windows only **29 became campaigns**
(76% never entered, `entry_band_left` being the logged refusal), and the
default shell is `touch_cancel_30s` — `rest_max_s 30.0`,
`cancel_on_touch_move True`.

Against L5's **5 fills and 2 switches per 5 minutes**, our clone gets at most
6 *decisions* — not fills — and produced 1 matched campaign in 29.

## NOT YET BUILT

The counterfactual continuous-inventory replay is **not built**, and no
verdict on it is offered. What exists in `SHELLS` today
(`rest_60s/120s/180s`, `reprice_180s`) is *longer rests*, which the owner
explicitly distinguished from continuous intention: the required unit of
persistence is the inventory INTENTION re-asked against refreshed
market/inventory state, not a longer-lived order. Building it on the existing
shells would answer a different question.
