# L5 Strategy Findings

- Subject wallet: `0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68` (profile `l5zn1bwom8etsk`)
- Scope: BTC and ETH 5-minute and 15-minute Up/Down markets only
- Generated: 2026-08-29T21:02:10.475692Z
- Data source: public Polymarket APIs, read-only; raw responses cached under `data/l5/raw`
- Fetch status: **synthetic-fixture**

## 1. Coverage

- Fills in scope: **5**
- Windows in scope: **2**
- By asset: {'BTC': 1, 'ETH': 1}
- By window length: {'5': 1, '15': 1}
- Window-classification provenance: {'both_agree': 2}
- Windows with a known resolution: 2
- Windows with any P&L figure: 2

> **Small sample.** 2 windows is enough to describe behaviour but not to establish it. Treat every rate below as provisional.

## 2. Candidate behavioural rules

| Rule | Holds | n | Share | 95% CI | Counterexamples | Confidence |
|---|---|---|---|---|---|---|
| `R1_opens_cheaper_side` | The first buy in a window is on the side priced below $0.50. | 2 | 100.0% | 34–100% | 0 | insufficient-data |
| `R2_always_buys_both_sides` | Both Up and Down are bought before the window closes. | 2 | 100.0% | 34–100% | 0 | insufficient-data |
| `R3_pair_cost_target` | Matched Up+Down acquisition cost finishes at or below $1.0217 (median + $0.01). | 2 | 50.0% | 9–91% | 1 | insufficient-data |
| `R4_finishes_balanced` | The window ends with matched Up/Down inventory (no material residual). | 2 | 50.0% | 9–91% | 1 | insufficient-data |
| `R5_pays_up_to_high_prices` | At least one buy occurs at $0.80 or above. | 2 | 50.0% | 9–91% | 1 | insufficient-data |
| `R6_second_leg_latency` | The opposite side is bought within 195s of the first buy (observed p75). | 2 | 50.0% | 9–91% | 1 | insufficient-data |

**Counterexamples for `R3_pair_cost_target`** (1 total, first 5):

| window_id | slug | asset | min | first_price | pair_cost | residual |
|---|---|---|---|---|---|---|
| `0xbtc5m` | bitcoin-up-or-down-5m-a | BTC | 5 | 0.45 | 1.073333 | 50.0 |

**Counterexamples for `R4_finishes_balanced`** (1 total, first 5):

| window_id | slug | asset | min | first_price | pair_cost | residual |
|---|---|---|---|---|---|---|
| `0xbtc5m` | bitcoin-up-or-down-5m-a | BTC | 5 | 0.45 | 1.073333 | 50.0 |

**Counterexamples for `R5_pays_up_to_high_prices`** (1 total, first 5):

| window_id | slug | asset | min | first_price | pair_cost | residual |
|---|---|---|---|---|---|---|
| `0xeth15m` | ethereum-up-or-down-15m-a | ETH | 15 | 0.4 | 0.95 | 0.0 |

**Counterexamples for `R6_second_leg_latency`** (1 total, first 5):

| window_id | slug | asset | min | first_price | pair_cost | residual |
|---|---|---|---|---|---|---|
| `0xeth15m` | ethereum-up-or-down-15m-a | ETH | 15 | 0.4 | 0.95 | 0.0 |

## 3. Question-by-question results

### Q1 — Does position size increase at 70c / 80c / 90c?

Buy fills analysed: 5
Spearman(price, shares): rho=-0.5270, p=0.28275, n=5

| Price band | fills | median shares | median $ |
|---|---|---|---|
| lt50 | 2 | 150.0000 | 62.5000 |
| 50_59 | 2 | 150.0000 | 80.0000 |
| 60_69 | 0 | n/a | n/a |
| 70_79 | 0 | n/a | n/a |
| 80_89 | 1 | 50.0000 | 41.0000 |
| 90_plus | 0 | n/a | n/a |

- **ge_70c**: median shares 50.0000 at/above vs 150.0000 below (n=1/4), p=0.13604 — insufficient-data
- **ge_80c**: median shares 50.0000 at/above vs 150.0000 below (n=1/4), p=0.13604 — insufficient-data
- **ge_90c**: median shares n/a at/above vs 100.0000 below (n=0/5), p=n/a — insufficient-data

### Q2 — 5-minute vs 15-minute windows

Groups: 5m (n=1) vs 15m (n=1)

| Metric | median A | median B | p | confidence |
|---|---|---|---|---|
| total_trades | 3.0000 | 2.0000 | 0.31731 | insufficient-data |
| total_dollars_bought | 136.0000 | 190.0000 | 0.31731 | insufficient-data |
| max_price_bought | 0.8200 | 0.5500 | 0.31731 | insufficient-data |
| matched_qty | 100.0000 | 200.0000 | 0.31731 | insufficient-data |
| matched_pair_unit_cost | 1.0733 | 0.9500 | 0.31731 | insufficient-data |
| residual_qty_abs | 50.0000 | 0.0000 | 0.31731 | insufficient-data |
| seconds_first_to_opposite_side | 60.0000 | 240.0000 | 0.31731 | insufficient-data |
| first_price | 0.4500 | 0.4000 | 0.31731 | insufficient-data |
| first_trade_seconds_from_start | 30.0000 | 60.0000 | 0.31731 | insufficient-data |

| Rate | A | B | p | confidence |
|---|---|---|---|---|
| balanced_at_end | 0.0% | 100.0% | 0.15730 | insufficient-data |
| cheaper_side_first | 100.0% | 100.0% | n/a | insufficient-data |
| any_buy_ge_80c | 100.0% | 0.0% | 0.15730 | insufficient-data |
| any_buy_ge_90c | 0.0% | 0.0% | n/a | insufficient-data |
| opposite_side_ever_bought | 100.0% | 100.0% | n/a | insufficient-data |

### Q3 — BTC vs ETH

Groups: BTC (n=1) vs ETH (n=1)

| Metric | median A | median B | p | confidence |
|---|---|---|---|---|
| total_trades | 3.0000 | 2.0000 | 0.31731 | insufficient-data |
| total_dollars_bought | 136.0000 | 190.0000 | 0.31731 | insufficient-data |
| max_price_bought | 0.8200 | 0.5500 | 0.31731 | insufficient-data |
| matched_qty | 100.0000 | 200.0000 | 0.31731 | insufficient-data |
| matched_pair_unit_cost | 1.0733 | 0.9500 | 0.31731 | insufficient-data |
| residual_qty_abs | 50.0000 | 0.0000 | 0.31731 | insufficient-data |
| seconds_first_to_opposite_side | 60.0000 | 240.0000 | 0.31731 | insufficient-data |
| first_price | 0.4500 | 0.4000 | 0.31731 | insufficient-data |
| first_trade_seconds_from_start | 30.0000 | 60.0000 | 0.31731 | insufficient-data |

| Rate | A | B | p | confidence |
|---|---|---|---|---|
| balanced_at_end | 0.0% | 100.0% | 0.15730 | insufficient-data |
| cheaper_side_first | 100.0% | 100.0% | n/a | insufficient-data |
| any_buy_ge_80c | 100.0% | 0.0% | 0.15730 | insufficient-data |
| any_buy_ge_90c | 0.0% | 0.0% | n/a | insufficient-data |
| opposite_side_ever_bought | 100.0% | 100.0% | n/a | insufficient-data |

### Q8 — Profitable vs losing windows

Groups: profitable (n=2) vs losing (n=0)

Windows with P&L: 2 (profitable 2, losing 0, flat 0); P&L sources: {'derived_resolution': 2}

> Trading P&L only. Maker rebates, liquidity-incentive payouts and gas are NOT included and must not be assumed to be.

| Metric | median A | median B | p | confidence |
|---|---|---|---|---|
| total_trades | 2.5000 | n/a | n/a | insufficient-data |
| total_dollars_bought | 163.0000 | n/a | n/a | insufficient-data |
| max_price_bought | 0.6850 | n/a | n/a | insufficient-data |
| matched_qty | 150.0000 | n/a | n/a | insufficient-data |
| matched_pair_unit_cost | 1.0117 | n/a | n/a | insufficient-data |
| residual_qty_abs | 25.0000 | n/a | n/a | insufficient-data |
| seconds_first_to_opposite_side | 150.0000 | n/a | n/a | insufficient-data |
| first_price | 0.4250 | n/a | n/a | insufficient-data |
| first_trade_seconds_from_start | 45.0000 | n/a | n/a | insufficient-data |

| Rate | A | B | p | confidence |
|---|---|---|---|---|
| balanced_at_end | 50.0% | n/a | n/a | insufficient-data |
| cheaper_side_first | 100.0% | n/a | n/a | insufficient-data |
| any_buy_ge_80c | 50.0% | n/a | n/a | insufficient-data |
| any_buy_ge_90c | 0.0% | n/a | n/a | insufficient-data |
| opposite_side_ever_bought | 100.0% | n/a | n/a | insufficient-data |

### Q4 — Does he systematically buy the cheaper side?

_On a binary Up/Down market the two sides price to ~$1.00, so 'cheaper side' is equivalent to a fill below $0.50 at that moment._

- First buy on the sub-$0.50 side: 2/2 = 100.0%, p vs coin flip=0.15730 — insufficient-data
- All buy fills below $0.50: 2/5 = 40.0%, p=0.65472 — insufficient-data

### Q5 — Latency from first leg to opposite side

- Windows where the opposite side is ever bought: 2/2 (100.0%)

| Group | n | median | p25 | p75 | mean |
|---|---|---|---|---|---|
| all (seconds) | 2 | 150.0000 | 105.0000 | 195.0000 | 150.0000 |
| 5m (seconds) | 1 | 60.0000 | 60.0000 | 60.0000 | 60.0000 |
| 15m (seconds) | 1 | 240.0000 | 240.0000 | 240.0000 | 240.0000 |
| BTC (seconds) | 1 | 60.0000 | 60.0000 | 60.0000 | 60.0000 |
| ETH (seconds) | 1 | 240.0000 | 240.0000 | 240.0000 | 240.0000 |
| fraction of window | 2 | 0.2333 | 0.2167 | 0.2500 | 0.2333 |

### Q6 — Combined Up+Down acquisition-cost threshold

_A matched Up+Down pair pays exactly $1.00 at resolution. A unit cost clustered just below $1.00 is consistent with a pair-cost target; a wide, unimodal-at-nothing spread is not._

- Windows holding matched pairs: 2
- Pair unit cost: median 1.0117, IQR width 0.0617, range 0.9500–1.0733
- Share within $0.01 of median: 0.0%
- Share within $0.02 of median: 0.0%
- Share below $1.00 (locked profit before fees/rebates): 50.0%

### Q7 — Balanced vs directional finishes

_balanced = residual <= 1.0 share or <= 1% of the larger leg_
- Balanced at expiry: 1/2 (50.0%) — insufficient-data
- Residual (shares): {'n': 2, 'mean': 25.0, 'median': 25.0, 'p25': 12.5, 'p75': 37.5, 'min': 0.0, 'max': 50.0, 'stdev': 35.35533905932738}
- By group: {'window_minutes=5': {'n': 1, 'balanced': 0, 'share': 0.0}, 'window_minutes=15': {'n': 1, 'balanced': 1, 'share': 1.0}, 'asset=BTC': {'n': 1, 'balanced': 0, 'share': 0.0}, 'asset=ETH': {'n': 1, 'balanced': 1, 'share': 1.0}}

## 4. Implementable pseudocode

Reconstruction of the behaviour the rules above describe. It is a *description of
observed fills*, not a validated strategy, and it deliberately encodes only what
the data can support.

```python
PAIR_COST_TARGET      = 1.0117
SECOND_LEG_MAX_SECONDS = 195
MAX_SECOND_LEG_PRICE   = PAIR_COST_TARGET - wavg_first_leg_price

def on_window_open(window):
    # R1: enter on whichever side is trading below $0.50.
    side = 'Up' if best_ask('Up') < best_ask('Down') else 'Down'
    fill = buy(side, size=BASE_SIZE)
    state.first_leg = fill

def on_book_update(window):
    # R3 + R6: complete the pair while the combined cost still clears the target.
    other = opposite(state.first_leg.side)
    budget = PAIR_COST_TARGET - state.wavg_price(state.first_leg.side)
    if best_ask(other) <= budget:
        buy(other, size=state.qty(state.first_leg.side) - state.qty(other))
    elif now() - state.first_leg.ts > SECOND_LEG_MAX_SECONDS:
        # R5: observed behaviour pays up rather than staying legged.
        buy(other, size=..., limit=best_ask(other))

def before_expiry(window):
    # R4: finish matched; residual is directional risk this trader mostly avoids.
    flatten(abs(state.qty('Up') - state.qty('Down')))
```

## 5. What this analysis cannot tell you

- Public activity shows **executed fills only**. Resting orders, cancels, quote
  updates and queue position are invisible; a maker strategy will look like a
  sequence of unexplained fills.
- Historical order-book state at fill time is not reconstructed. Statements like
  "he crossed the spread" are not derivable from this data and are not made.
- P&L here is **trading** P&L. Maker rebates and liquidity-incentive payouts do
  not appear in fill or position data; a window that looks flat or negative may
  still be profitable after incentives, and vice versa.
- 'Cheaper side' is inferred from the fill price against the $1.00 binary
  complement, not from a snapshot of both books.
- Window classification uses market duration metadata first and text second;
  markets that could only be classified from text are excluded and listed in
  `l5_excluded_markets.csv`.
- Correlation across windows is not independence: consecutive windows in the
  same session share regime, so p-values are optimistic.

