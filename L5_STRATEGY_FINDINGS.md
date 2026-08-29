# L5 Strategy Findings

- Subject wallet: `0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68` (profile `l5zn1bwom8etsk`)
- Scope: BTC and ETH 5-minute and 15-minute Up/Down markets only
- Generated: 2026-08-29T21:02:10.551038Z
- Data source: public Polymarket APIs, read-only; raw responses cached under `data/l5/raw`
- Fetch status: **failed**

## STATUS: NO DATA — no conclusions drawn

The exporter produced zero in-scope fills, so **every question below is unanswered**. Nothing in this file should be read as a finding about the trader. Re-run `python -m src.polymarket_l5.cli all` from an environment with network access to Polymarket's public APIs and this document will regenerate with real numbers.

Reason recorded by the last fetch attempt: /activity unreachable: http_0: transport error: Tunnel connection failed: 403 Forbidden

The pre-registered analysis plan (what will be computed, and how) is in
`README_FOR_ANALYSIS.md`. The rule templates below are hypotheses only.

## 1. Coverage

- Fills in scope: **0**
- Windows in scope: **0**
- By asset: {}
- By window length: {}
- Window-classification provenance: {}
- Windows with a known resolution: 0
- Windows with any P&L figure: 0

## 2. Candidate behavioural rules

| Rule | Holds | n | Share | 95% CI | Counterexamples | Confidence |
|---|---|---|---|---|---|---|
| `R1_opens_cheaper_side` | The first buy in a window is on the side priced below $0.50. | 0 | n/a | n/a | 0 | insufficient-data |
| `R2_always_buys_both_sides` | Both Up and Down are bought before the window closes. | 0 | n/a | n/a | 0 | insufficient-data |
| `R3_pair_cost_target` | Matched Up+Down acquisition cost finishes at or below the median target (median + $0.01). | 0 | n/a | n/a | 0 | insufficient-data |
| `R4_finishes_balanced` | The window ends with matched Up/Down inventory (no material residual). | 0 | n/a | n/a | 0 | insufficient-data |
| `R5_pays_up_to_high_prices` | At least one buy occurs at $0.80 or above. | 0 | n/a | n/a | 0 | insufficient-data |
| `R6_second_leg_latency` | The opposite side is bought within the observed p75 of the first buy (observed p75). | 0 | n/a | n/a | 0 | insufficient-data |

## 3. Question-by-question results

### Q1 — Does position size increase at 70c / 80c / 90c?

Buy fills analysed: 0
Spearman(price, shares): rho=n/a, p=n/a, n=0

| Price band | fills | median shares | median $ |
|---|---|---|---|
| lt50 | 0 | n/a | n/a |
| 50_59 | 0 | n/a | n/a |
| 60_69 | 0 | n/a | n/a |
| 70_79 | 0 | n/a | n/a |
| 80_89 | 0 | n/a | n/a |
| 90_plus | 0 | n/a | n/a |

- **ge_70c**: median shares n/a at/above vs n/a below (n=0/0), p=n/a — insufficient-data
- **ge_80c**: median shares n/a at/above vs n/a below (n=0/0), p=n/a — insufficient-data
- **ge_90c**: median shares n/a at/above vs n/a below (n=0/0), p=n/a — insufficient-data

### Q2 — 5-minute vs 15-minute windows

Groups: 5m (n=0) vs 15m (n=0)

| Metric | median A | median B | p | confidence |
|---|---|---|---|---|
| first_price | n/a | n/a | n/a | insufficient-data |
| first_trade_seconds_from_start | n/a | n/a | n/a | insufficient-data |
| matched_pair_unit_cost | n/a | n/a | n/a | insufficient-data |
| matched_qty | n/a | n/a | n/a | insufficient-data |
| max_price_bought | n/a | n/a | n/a | insufficient-data |
| residual_qty_abs | n/a | n/a | n/a | insufficient-data |
| seconds_first_to_opposite_side | n/a | n/a | n/a | insufficient-data |
| total_dollars_bought | n/a | n/a | n/a | insufficient-data |
| total_trades | n/a | n/a | n/a | insufficient-data |

| Rate | A | B | p | confidence |
|---|---|---|---|---|
| any_buy_ge_80c | n/a | n/a | n/a | insufficient-data |
| any_buy_ge_90c | n/a | n/a | n/a | insufficient-data |
| balanced_at_end | n/a | n/a | n/a | insufficient-data |
| cheaper_side_first | n/a | n/a | n/a | insufficient-data |
| opposite_side_ever_bought | n/a | n/a | n/a | insufficient-data |

### Q3 — BTC vs ETH

Groups: BTC (n=0) vs ETH (n=0)

| Metric | median A | median B | p | confidence |
|---|---|---|---|---|
| first_price | n/a | n/a | n/a | insufficient-data |
| first_trade_seconds_from_start | n/a | n/a | n/a | insufficient-data |
| matched_pair_unit_cost | n/a | n/a | n/a | insufficient-data |
| matched_qty | n/a | n/a | n/a | insufficient-data |
| max_price_bought | n/a | n/a | n/a | insufficient-data |
| residual_qty_abs | n/a | n/a | n/a | insufficient-data |
| seconds_first_to_opposite_side | n/a | n/a | n/a | insufficient-data |
| total_dollars_bought | n/a | n/a | n/a | insufficient-data |
| total_trades | n/a | n/a | n/a | insufficient-data |

| Rate | A | B | p | confidence |
|---|---|---|---|---|
| any_buy_ge_80c | n/a | n/a | n/a | insufficient-data |
| any_buy_ge_90c | n/a | n/a | n/a | insufficient-data |
| balanced_at_end | n/a | n/a | n/a | insufficient-data |
| cheaper_side_first | n/a | n/a | n/a | insufficient-data |
| opposite_side_ever_bought | n/a | n/a | n/a | insufficient-data |

### Q8 — Profitable vs losing windows

Groups: profitable (n=0) vs losing (n=0)

Windows with P&L: 0 (profitable 0, losing 0, flat 0); P&L sources: {}

> Trading P&L only. Maker rebates, liquidity-incentive payouts and gas are NOT included and must not be assumed to be.

| Metric | median A | median B | p | confidence |
|---|---|---|---|---|
| first_price | n/a | n/a | n/a | insufficient-data |
| first_trade_seconds_from_start | n/a | n/a | n/a | insufficient-data |
| matched_pair_unit_cost | n/a | n/a | n/a | insufficient-data |
| matched_qty | n/a | n/a | n/a | insufficient-data |
| max_price_bought | n/a | n/a | n/a | insufficient-data |
| residual_qty_abs | n/a | n/a | n/a | insufficient-data |
| seconds_first_to_opposite_side | n/a | n/a | n/a | insufficient-data |
| total_dollars_bought | n/a | n/a | n/a | insufficient-data |
| total_trades | n/a | n/a | n/a | insufficient-data |

| Rate | A | B | p | confidence |
|---|---|---|---|---|
| any_buy_ge_80c | n/a | n/a | n/a | insufficient-data |
| any_buy_ge_90c | n/a | n/a | n/a | insufficient-data |
| balanced_at_end | n/a | n/a | n/a | insufficient-data |
| cheaper_side_first | n/a | n/a | n/a | insufficient-data |
| opposite_side_ever_bought | n/a | n/a | n/a | insufficient-data |

### Q4 — Does he systematically buy the cheaper side?

_On a binary Up/Down market the two sides price to ~$1.00, so 'cheaper side' is equivalent to a fill below $0.50 at that moment._

- First buy on the sub-$0.50 side: 0/0 = n/a, p vs coin flip=n/a — insufficient-data
- All buy fills below $0.50: 0/0 = n/a, p=n/a — insufficient-data

### Q5 — Latency from first leg to opposite side

- Windows where the opposite side is ever bought: 0/0 (n/a)

| Group | n | median | p25 | p75 | mean |
|---|---|---|---|---|---|
| all (seconds) | 0 | n/a | n/a | n/a | n/a |
| 15m (seconds) | 0 | n/a | n/a | n/a | n/a |
| 5m (seconds) | 0 | n/a | n/a | n/a | n/a |
| BTC (seconds) | 0 | n/a | n/a | n/a | n/a |
| ETH (seconds) | 0 | n/a | n/a | n/a | n/a |
| fraction of window | 0 | n/a | n/a | n/a | n/a |

### Q6 — Combined Up+Down acquisition-cost threshold

_A matched Up+Down pair pays exactly $1.00 at resolution. A unit cost clustered just below $1.00 is consistent with a pair-cost target; a wide, unimodal-at-nothing spread is not._

- Windows holding matched pairs: 0
- Pair unit cost: median n/a, IQR width n/a, range n/a–n/a
- Share within $0.01 of median: n/a
- Share within $0.02 of median: n/a
- Share below $1.00 (locked profit before fees/rebates): n/a

### Q7 — Balanced vs directional finishes

_balanced = residual <= 1.0 share or <= 1% of the larger leg_
- Balanced at expiry: 0/0 (n/a) — insufficient-data
- Residual (shares): {'max': None, 'mean': None, 'median': None, 'min': None, 'n': 0, 'p25': None, 'p75': None, 'stdev': None}
- By group: {'asset=BTC': {'balanced': 0, 'n': 0, 'share': None}, 'asset=ETH': {'balanced': 0, 'n': 0, 'share': None}, 'window_minutes=15': {'balanced': 0, 'n': 0, 'share': None}, 'window_minutes=5': {'balanced': 0, 'n': 0, 'share': None}}

## 4. Implementable pseudocode

Reconstruction of the behaviour the rules above describe. It is a *description of
observed fills*, not a validated strategy, and it deliberately encodes only what
the data can support.

```python
PAIR_COST_TARGET      = None  # not measurable
SECOND_LEG_MAX_SECONDS = None  # not measurable
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

