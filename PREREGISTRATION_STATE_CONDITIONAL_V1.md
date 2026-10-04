# PREREGISTRATION — STATE_CONDITIONAL_PAIR_V1

Frozen before the state table is built and before any probability model is
fitted. DEVELOPMENT only; the OOS tape has not been retrieved or inspected.

## Hypothesis

> BUY only when one outcome has independently positive all-in estimated EV ->
> WAIT -> if the opposite outcome later independently qualifies, BUY it rather
> than selling the first leg -> selectively EXTEND only on independently
> qualifying states -> SETTLE.

The first purchase must stand on its own. **No action is ever credited with a
hypothetical future complement.** Complement completion is an outcome to be
measured, never an assumption.

## Reference data — venue-native only

BRTI via `GET /v1/asset-prices/history` (1-second buckets), and
`priceToBeat` / `settlementPrice` / window bounds via
`GET /v1/markets?slug=...`. Semantics and the 28/28 exact reconstruction are in
`VENUE_REFERENCE_SEMANTICS.md`; helpers in `src/pmus_brti.py`.

**Coinbase does not enter the probability model, the state table, or any
feature.** `data/btc_ref/*` is untouched by this experiment.

## Timestamp causality — the hard rules

1. A BRTI bucket with start `s` is usable at decision time `t` only if
   `s <= t - 1`. A one-second bucket starting at `s` is not complete, and so
   not published, until `s + 1`.
2. `priceToBeat` is the open statistic over `[ws-60, ws-1]`, which the venue
   fixes "a few seconds after the window starts" and only then publishes.
   It is treated as **unavailable before `ws + 15 s`** — deliberately
   conservative, since the exact publication instant is not observable from the
   historical endpoint. **No action may be taken before `ws + 15 s`**; the
   controller can only WAIT.
3. PMUS book state at `t` is the most recent book update with timestamp
   `<= t`. No touch=fill; executable prices and sizes only.
4. `settlementPrice` and the outcome label are **label-only**. They are joined
   after the full decision pass and are asserted absent from the feature
   matrix.

Each of 1-4 is asserted in code; a violation aborts the run.

## Decision grid

One evaluation per second of the window (BRTI's own publication cadence),
`ws + 15` through `we - 1`, with the latest book at or before each second.

## State table columns

Per (window, second):

- `t`, `seconds_remaining = we - t`
- `price_to_beat` (strike, 2 dp), `brti_now` (last close with `s <= t-1`)
- `raw_distance = brti_now - price_to_beat`
- `sigma_1s` — realized vol from 1-second BRTI log returns over the trailing
  300 s, using only buckets with `s <= t-1`
- `n_published` — settlement-minute observations already published, i.e.
  buckets in `[we-60, we-1]` with `s <= t-1` (0 until the final minute)
- `settle_cum_sum`, `settle_avg_published` — their sum and average
- `n_remaining = 60 - n_published`
- `required_remaining_avg` — the average the remaining settlement
  observations must reach for UP to settle:
  `(60 * price_to_beat - settle_cum_sum) / n_remaining`
- `gap_to_required = brti_now - required_remaining_avg`
- `sigma_remaining_avg` — uncertainty of that remaining average under a
  driftless random walk in 1-second steps. For `n` remaining steps beginning
  `d` seconds ahead,
  `Var = sigma_1s^2 * (d + (n+1)(2n+1)/(6n))`, with `d = max(0, (we-60) - t)`.
- `mom_60s` — BRTI return over the trailing 60 s, normalized by `sigma_1s`
- `up_ask`, `up_ask_qty` — executable UP price and size
- `down_px = 1 - bid`, `down_qty = bid_qty` — executable DOWN price and size
- `fee_up_cent`, `fee_down_cent` — venue-cent banker-rounded taker fee at the
  evaluated size; `fee_up_4dp`, `fee_down_4dp` reported alongside
- `spread`, `book_age_s`, and the PMUS flow variables already recorded
  (trade count and signed volume over the trailing 60 s) where
  contemporaneously available
- `outcome` — **label only**

## Probability model — simple and explainable, fixed now

Logistic regression on **exactly three** features plus intercept:

1. `z = gap_to_required / sigma_remaining_avg` (the normalized distance, built
   to respect the settlement statistic rather than ordinary spot distance)
2. `locked_frac = n_published / 60`
3. `mom_60s`

Target: `P(close >= open)` — **ties settle UP**, so the label is
`settlementPrice >= priceToBeat`.

No feature search, no interaction terms, no threshold tuning, no alternative
model families. If this model is poorly calibrated, that is a reported result,
not a prompt to try another.

`z` is undefined when `n_remaining = 0` (at `t = we-1` the outcome is fully
determined); those rows are excluded from fitting and from trading, because
trading on a determined outcome is not a strategy.

## Validation inside DEVELOPMENT

Two schemes, both reported:

- **Leave-one-window-out**, grouped by window (never by row — rows within a
  window share one binary outcome).
- **Chronological**: fit on the 09-30 windows, test on the 10-02 windows.

Reported: Brier score, log loss, reliability table in 10 predicted-probability
bins, and the base rate. Economic results are reported **separately from and
after** calibration.

Out-of-fold predictions are used for every economic figure, so no window is
ever traded on a model that saw its own outcome.

## EV and the decision rule

```
EV_UP   =    q    - executable_UP   - all_in_fee_per_share_UP
EV_DOWN = (1 - q) - executable_DOWN - all_in_fee_per_share_DOWN
EV_WAIT = 0
```

`all_in_fee_per_share` is the venue-cent-rounded taker fee at the evaluated
size divided by that size, so the gate pays the fee the venue would actually
charge. The 4-decimal rate is reported beside it.

Candidate actions: **BUY UP, BUY DOWN, WAIT, SETTLE. There is no SELL.** A
change in the estimated winner is never a reason to sell.

- Before `ws + 15 s`: WAIT only.
- **First leg** requires `EV > 0` strictly, on its own, with no complement
  credit. If both sides qualify, take the larger EV.
- **After inventory exists**, the same test is re-applied every second.
  An opposite-side BUY is allowed **only if independently `EV > 0` at that
  timestamp**. A same-side BUY (EXTEND) likewise.
- **Spacing**: at most one purchase per side per 60-second period. This reuses
  the cadence already frozen in `scripts/pmus_favorite_dca.py`; it is not a
  newly chosen parameter, and it bounds a window at 15 purchases per side.
- **Size**: unit — 1 share, capped by displayed depth. Sizing is evaluated only
  after edge and completion behavior are established.

## Primary output — the full decomposition

1. standalone first-leg predicted EV and realized P&L
2. first-leg calibration by predicted-edge bucket
3. complement completion rate
4. time-to-complement distribution
5. all-in matched pair costs
6. locked pair gains
7. first legs never complemented (count and share)
8. P&L of those unpaired legs
9. EXTEND purchases, separately
10. residual directional settlement P&L
11. fees (both treatments)
12. total portfolio cashflow, reconciling to the five-term identity

Completed pairs are never reported alone. Unpaired legs and their losses are
reported with equal prominence.

## The sub-$1 pair question, answered empirically

For every second of every window: does
`up_ask + down_px + fee_up + fee_down < $1.00` hold? Reported as

- frequency of the state overall, and **frequency conditional on occurring
  after a qualifying first leg** (the sequential case that actually matters);
- the **size available** at that state, from displayed depth on both sides;
- **persistence** — the distribution of how long the state lasts once entered;
- the distribution of all-in pair cost when it does occur.

The 30c + 25c illustration is treated as unverified until this table exists.
If the state is rare, small, or fleeting, that is the finding.

## Success criterion

```
completed-pair gains + directional gains - unpaired losses - fees > 0
```

robustly on unseen windows. Only if that holds on OOS do we ask whether the
average per traded window can reach $5 at executable size, against displayed
depth and a $200/window ceiling.

## Freeze and stop

Model, features, EV calculation and controller are frozen by this document.
Once the development specification is committed, work **stops** and the
specification is reported before OOS is accessed.

No OOS tuning. No live orders. No maker assumptions — taker execution only for
this falsification test.
