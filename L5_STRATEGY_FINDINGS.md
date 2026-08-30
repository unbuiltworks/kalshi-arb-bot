# L5 Strategy Findings

- Subject wallet: `0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68` (profile `l5zn1bwom8etsk`)
- Scope: BTC and ETH 5-minute and 15-minute Up/Down markets only
- Generated: 2026-08-30T21:14:37.547341Z
- Data source: public Polymarket APIs, read-only; raw responses cached under `data/l5/raw`
- Fetch status: **ok**

## 1. Coverage

- Fills in scope: **53926**
- Windows in scope: **1671**
- By asset: {'BTC': 1151, 'ETH': 520}
- By window length: {'15': 454, '5': 1217}
- Window-classification provenance: {'both_agree': 1671}
- Windows with a known resolution: 1668
- Windows with any P&L figure: 1668

## 2. Candidate behavioural rules

| Rule | Holds | n | Share | 95% CI | Counterexamples | Confidence |
|---|---|---|---|---|---|---|
| `R1_opens_cheaper_side` | The first buy in a window is on the side priced below $0.50. | 1632 | 36.5% | 34–39% | 1036 | strong |
| `R2_always_buys_both_sides` | Both Up and Down are bought before the window closes. | 1671 | 73.5% | 71–76% | 443 | strong |
| `R3_pair_cost_target` | Matched Up+Down acquisition cost finishes at or below $0.9814 (median + $0.01). | 1228 | 54.2% | 51–57% | 562 | strong |
| `R4_finishes_balanced` | The window ends with matched Up/Down inventory (no material residual). | 1671 | 2.5% | 2–3% | 1629 | strong |
| `R5_pays_up_to_high_prices` | At least one buy occurs at $0.80 or above. | 1671 | 67.7% | 65–70% | 539 | strong |
| `R6_second_leg_latency` | The opposite side is bought within 109s of the first buy (observed p75). | 1228 | 74.2% | 72–77% | 317 | strong |

**Counterexamples for `R1_opens_cheaper_side`** (1036 total, first 5):

| window_id | slug | asset | min | first_price | pair_cost | residual |
|---|---|---|---|---|---|---|
| `0xfedd60f56f5dcf4c` | btc-updown-5m-1773914700 | BTC | 5 | 0.8099999757 |  | 0.014563 |
| `0x232dad50a354595f` | btc-updown-5m-1773915600 | BTC | 5 | 0.64 |  | 0.0225 |
| `0xd306952642278c0c` | btc-updown-15m-1774440000 | BTC | 15 | 0.95 |  | 1.05262 |
| `0xd5923966f323cb67` | btc-updown-15m-1774441800 | BTC | 15 | 0.7299998102 |  | -1.369862 |
| `0x69762ddeaa39ccea` | btc-updown-15m-1774450800 | BTC | 15 | 0.98 |  | -1.0204 |

**Counterexamples for `R2_always_buys_both_sides`** (443 total, first 5):

| window_id | slug | asset | min | first_price | pair_cost | residual |
|---|---|---|---|---|---|---|
| `0xfedd60f56f5dcf4c` | btc-updown-5m-1773914700 | BTC | 5 | 0.8099999757 |  | 0.014563 |
| `0x232dad50a354595f` | btc-updown-5m-1773915600 | BTC | 5 | 0.64 |  | 0.0225 |
| `0x9a0c102629d17c2a` | btc-updown-15m-1773917100 | BTC | 15 | 0.01 |  | 5.0 |
| `0xd306952642278c0c` | btc-updown-15m-1774440000 | BTC | 15 | 0.95 |  | 1.05262 |
| `0xd5923966f323cb67` | btc-updown-15m-1774441800 | BTC | 15 | 0.7299998102 |  | -1.369862 |

**Counterexamples for `R3_pair_cost_target`** (562 total, first 5):

| window_id | slug | asset | min | first_price | pair_cost | residual |
|---|---|---|---|---|---|---|
| `0x33bdf50adf607892` | btc-updown-15m-1774605600 | BTC | 15 | 0.35 | 1.011746 | -25.401802 |
| `0x27d464fa15cc60be` | btc-updown-15m-1774607400 | BTC | 15 | 0.5 | 1.248162 | -10.280859 |
| `0xa2964ea349efb37a` | btc-updown-15m-1774624500 | BTC | 15 | 0.4099999218 | 1.119288 | 88.698385 |
| `0x8aef7c25db9b4992` | btc-updown-15m-1774628100 | BTC | 15 | 0.78 | 0.998466 | 32.902313 |
| `0x7d3cb808247f3563` | btc-updown-15m-1774630800 | BTC | 15 | 0.45 | 1.073832 | 23.980213 |

**Counterexamples for `R4_finishes_balanced`** (1629 total, first 5):

| window_id | slug | asset | min | first_price | pair_cost | residual |
|---|---|---|---|---|---|---|
| `0x9a0c102629d17c2a` | btc-updown-15m-1773917100 | BTC | 15 | 0.01 |  | 5.0 |
| `0xd306952642278c0c` | btc-updown-15m-1774440000 | BTC | 15 | 0.95 |  | 1.05262 |
| `0xd5923966f323cb67` | btc-updown-15m-1774441800 | BTC | 15 | 0.7299998102 |  | -1.369862 |
| `0x084d90b4e10238a2` | btc-updown-15m-1774442700 | BTC | 15 | 0.2599998648 |  | 3.846152 |
| `0x69762ddeaa39ccea` | btc-updown-15m-1774450800 | BTC | 15 | 0.98 |  | -1.0204 |

**Counterexamples for `R5_pays_up_to_high_prices`** (539 total, first 5):

| window_id | slug | asset | min | first_price | pair_cost | residual |
|---|---|---|---|---|---|---|
| `0x232dad50a354595f` | btc-updown-5m-1773915600 | BTC | 5 | 0.64 |  | 0.0225 |
| `0x9a0c102629d17c2a` | btc-updown-15m-1773917100 | BTC | 15 | 0.01 |  | 5.0 |
| `0xd5923966f323cb67` | btc-updown-15m-1774441800 | BTC | 15 | 0.7299998102 |  | -1.369862 |
| `0x084d90b4e10238a2` | btc-updown-15m-1774442700 | BTC | 15 | 0.2599998648 |  | 3.846152 |
| `0x3dbfdd115ab680e6` | btc-updown-15m-1774526400 | BTC | 15 | 0.5 |  | 2.0 |

**Counterexamples for `R6_second_leg_latency`** (317 total, first 5):

| window_id | slug | asset | min | first_price | pair_cost | residual |
|---|---|---|---|---|---|---|
| `0x13df315b2ea770e7` | btc-updown-15m-1774540800 | BTC | 15 | 0.41 | 0.953571 | 65.0 |
| `0x2bbfdb90e98ec256` | btc-updown-15m-1774541700 | BTC | 15 | 0.44 | 0.965204 | 27.950802 |
| `0x668cb0784666b3ef` | btc-updown-15m-1774614600 | BTC | 15 | 0.53 | 0.835533 | -53.348052 |
| `0xa2964ea349efb37a` | btc-updown-15m-1774624500 | BTC | 15 | 0.4099999218 | 1.119288 | 88.698385 |
| `0xfcb602d4347a7fee` | btc-updown-15m-1774647000 | BTC | 15 | 0.4 | 1.0025 | -70.0 |

## 2b. Incentive income vs trading P&L (the headline)

_Incentive income is reported by Polymarket as separate activity rows and is NOT included in any trading P&L figure in this study. Coverage is limited to the harvested eras; incentives outside them are unobserved._

| Activity type | rows | total USDC |
|---|---|---|
| MAKER_REBATE | 3 | 2610.3916 |
| REDEEM | 1540 | 540510.6282 |
| REWARD | 2 | 4000.3765 |
| TAKER_REBATE | 2 | 384.3151 |

Observed coverage eras (gaps mean unharvested history, not inactivity):

| Era start | Era end | days | fills | incentive USDC | ~USDC/day |
|---|---|---|---|---|---|
| 2026-03-19T10:09:37Z | 2026-03-19T10:58:39Z | 0.03 | 5 | 0 | n/a |
| 2026-03-25T12:07:21Z | 2026-03-28T18:28:55Z | 3.26 | 1281 | 0 | 0.0 |
| 2026-03-30T16:06:23Z | 2026-04-01T10:10:15Z | 1.75 | 730 | 0 | 0.0 |
| 2026-04-02T17:00:13Z | 2026-04-02T19:43:37Z | 0.11 | 150 | 0 | n/a |
| 2026-04-06T20:20:41Z | 2026-04-06T21:16:39Z | 0.04 | 41 | 0 | n/a |
| 2026-04-09T13:20:29Z | 2026-04-10T18:53:51Z | 1.23 | 405 | 0 | 0.0 |
| 2026-04-13T08:48:49Z | 2026-04-15T18:58:40Z | 2.42 | 882 | 0 | 0.0 |
| 2026-04-17T11:47:50Z | 2026-04-17T20:26:38Z | 0.36 | 118 | 0 | n/a |
| 2026-04-20T12:53:44Z | 2026-04-21T08:14:10Z | 0.81 | 1657 | 29.9379 | 36.96 |
| 2026-08-28T07:45:27Z | 2026-08-30T21:09:24Z | 2.56 | 48657 | 6965.1453 | 2720.76 |

## 3. Question-by-question results

### Q1 — Does position size increase at 70c / 80c / 90c?

Buy fills analysed: 53924
Spearman(price, shares): rho=0.1645, p=0.00000, n=53924

| Price band | fills | median shares | median $ |
|---|---|---|---|
| lt50 | 30527 | 10.0000 | 1.8658 |
| 50_59 | 5159 | 14.0000 | 7.5600 |
| 60_69 | 5142 | 15.0000 | 9.5474 |
| 70_79 | 4940 | 17.9808 | 13.3100 |
| 80_89 | 4473 | 20.0000 | 17.0000 |
| 90_plus | 3683 | 24.7525 | 22.9900 |

- **ge_70c**: median shares 20.0000 at/above vs 10.2734 below (n=13096/40828), p=0.00000 — strong
- **ge_80c**: median shares 20.3215 at/above vs 10.8989 below (n=8156/45768), p=0.00000 — strong
- **ge_90c**: median shares 24.7525 at/above vs 11.5000 below (n=3683/50241), p=0.00000 — strong

### Q2 — 5-minute vs 15-minute windows

Groups: 5m (n=1217) vs 15m (n=454)

| Metric | median A | median B | p | confidence |
|---|---|---|---|---|
| first_price | 0.6000 | 0.5100 | 0.00000 | strong |
| first_trade_seconds_from_start | 73.0000 | 18.5000 | 0.00000 | strong |
| matched_pair_unit_cost | 0.9871 | 0.9659 | 0.03735 | moderate |
| matched_qty | 35.7200 | 644.0419 | 0.00000 | strong |
| max_price_bought | 0.8500 | 0.9600 | 0.00000 | strong |
| residual_qty_abs | 50.4500 | 70.1850 | 0.00000 | strong |
| seconds_first_to_opposite_side | 66.0000 | 28.0000 | 0.00000 | strong |
| total_dollars_bought | 76.4518 | 712.3491 | 0.00000 | strong |
| total_trades | 4.0000 | 81.0000 | 0.00000 | strong |

| Rate | A | B | p | confidence |
|---|---|---|---|---|
| any_buy_ge_80c | 61.0% | 85.9% | 0.00000 | strong |
| any_buy_ge_90c | 39.5% | 74.0% | 0.00000 | strong |
| balanced_at_end | 2.2% | 3.3% | 0.20736 | no-evidence |
| cheaper_side_first | 32.7% | 43.6% | 0.00003 | strong |
| opposite_side_ever_bought | 65.9% | 93.8% | 0.00000 | strong |

### Q3 — BTC vs ETH

Groups: BTC (n=1151) vs ETH (n=520)

| Metric | median A | median B | p | confidence |
|---|---|---|---|---|
| first_price | 0.5300 | 0.6300 | 0.00000 | strong |
| first_trade_seconds_from_start | 39.0000 | 118.0000 | 0.00000 | strong |
| matched_pair_unit_cost | 0.9696 | 0.9930 | 0.16831 | no-evidence |
| matched_qty | 102.5478 | 0.0000 | 0.00000 | strong |
| max_price_bought | 0.9300 | 0.7500 | 0.00000 | strong |
| residual_qty_abs | 84.5882 | 30.0000 | 0.00000 | strong |
| seconds_first_to_opposite_side | 48.0000 | 70.0000 | 0.00015 | strong |
| total_dollars_bought | 152.1592 | 28.0893 | 0.00000 | strong |
| total_trades | 9.0000 | 2.0000 | 0.00000 | strong |

| Rate | A | B | p | confidence |
|---|---|---|---|---|
| any_buy_ge_80c | 81.0% | 38.5% | 0.00000 | strong |
| any_buy_ge_90c | 63.5% | 16.5% | 0.00000 | strong |
| balanced_at_end | 3.0% | 1.3% | 0.04047 | moderate |
| cheaper_side_first | 39.3% | 27.7% | 0.00000 | strong |
| opposite_side_ever_bought | 87.6% | 42.3% | 0.00000 | strong |

### Q8 — Profitable vs losing windows

Groups: profitable (n=937) vs losing (n=729)

Windows with P&L: 1668 (profitable 937, losing 729, flat 2); P&L sources: {'derived_resolution': 1517, 'polymarket_positions': 151}

> Trading P&L only. Maker rebates, liquidity-incentive payouts and gas are NOT included and must not be assumed to be.

| Metric | median A | median B | p | confidence |
|---|---|---|---|---|
| first_price | 0.6000 | 0.5100 | 0.00000 | strong |
| first_trade_seconds_from_start | 60.0000 | 54.0000 | 0.37350 | no-evidence |
| matched_pair_unit_cost | 0.9364 | 1.0269 | 0.00000 | strong |
| matched_qty | 50.0000 | 75.0000 | 0.00004 | strong |
| max_price_bought | 0.8900 | 0.8900 | 0.00299 | no-evidence |
| residual_qty_abs | 50.0000 | 62.3000 | 0.00000 | strong |
| seconds_first_to_opposite_side | 52.0000 | 51.0000 | 0.78925 | no-evidence |
| total_dollars_bought | 93.2912 | 108.9621 | 0.31255 | no-evidence |
| total_trades | 5.0000 | 7.0000 | 0.00000 | strong |

| Rate | A | B | p | confidence |
|---|---|---|---|---|
| any_buy_ge_80c | 71.2% | 63.4% | 0.00072 | strong |
| any_buy_ge_90c | 49.4% | 48.1% | 0.60839 | no-evidence |
| balanced_at_end | 2.8% | 2.2% | 0.45373 | no-evidence |
| cheaper_side_first | 27.1% | 46.6% | 0.00000 | strong |
| opposite_side_ever_bought | 68.9% | 79.1% | 0.00000 | strong |

### Q4 — Does he systematically buy the cheaper side?

_On a binary Up/Down market the two sides price to ~$1.00, so 'cheaper side' is equivalent to a fill below $0.50 at that moment._

- First buy on the sub-$0.50 side: 596/1632 = 36.5%, p vs coin flip=0.00000 — strong
- All buy fills below $0.50: 30527/53924 = 56.6%, p=0.00000 — strong

### Q5 — Latency from first leg to opposite side

- Windows where the opposite side is ever bought: 1228/1671 (73.5%)

| Group | n | median | p25 | p75 | mean |
|---|---|---|---|---|---|
| all (seconds) | 1228 | 52.0000 | 20.0000 | 109.0000 | 72.3721 |
| 15m (seconds) | 426 | 28.0000 | 12.0000 | 62.7500 | 55.0822 |
| 5m (seconds) | 802 | 66.0000 | 30.0000 | 126.0000 | 81.5561 |
| BTC (seconds) | 1008 | 48.0000 | 19.0000 | 102.5000 | 68.0982 |
| ETH (seconds) | 220 | 70.0000 | 25.0000 | 132.2500 | 91.9545 |
| fraction of window | 1228 | 0.1250 | 0.0400 | 0.3067 | 0.1988 |

### Q6 — Combined Up+Down acquisition-cost threshold

_A matched Up+Down pair pays exactly $1.00 at resolution. A unit cost clustered just below $1.00 is consistent with a pair-cost target; a wide, unimodal-at-nothing spread is not._

- Windows holding matched pairs: 1228
- Pair unit cost: median 0.9714, IQR width 0.1582, range 0.1806–1.7633
- Share within $0.01 of median: 8.8%
- Share within $0.02 of median: 17.2%
- Share below $1.00 (locked profit before fees/rebates): 60.9%

### Q7 — Balanced vs directional finishes

_balanced = residual <= 1.0 share or <= 1% of the larger leg_
- Balanced at expiry: 42/1671 (2.5%) — strong
- Residual (shares): {'max': 1168.43298, 'mean': 101.17423440933582, 'median': 55.0, 'min': 0.0, 'n': 1671, 'p25': 21.613302, 'p75': 126.81605200000001, 'stdev': 132.96810737348872}
- By group: {'asset=BTC': {'balanced': 35, 'n': 1151, 'share': 0.030408340573414423}, 'asset=ETH': {'balanced': 7, 'n': 520, 'share': 0.013461538461538462}, 'window_minutes=15': {'balanced': 15, 'n': 454, 'share': 0.03303964757709251}, 'window_minutes=5': {'balanced': 27, 'n': 1217, 'share': 0.02218570254724733}}

## 4. Implementable pseudocode

Reconstruction of the behaviour the rules above describe. It is a *description of
observed fills*, not a validated strategy, and it deliberately encodes only what
the data can support.

```python
PAIR_COST_TARGET      = 0.9714
SECOND_LEG_MAX_SECONDS = 109
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

