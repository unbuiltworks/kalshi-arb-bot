# L5 Strategy Findings

Behavioural reconstruction of Polymarket wallet `0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68`
(profile `l5zn1bwom8etsk`) across BTC and ETH 5-minute and 15-minute
Up/Down markets, built from public API data only.

> **Read this first.** Everything below is inferred from *executed fills*. Polymarket's
> public endpoints do not expose resting orders, cancellations, or historical book state,
> so nothing here can distinguish "he chose not to buy" from "his order did not fill".
> No order-book state is reconstructed anywhere in this analysis. Realized P&L, where
> shown, is Polymarket's own position accounting and **excludes liquidity rebates and
> maker incentives** — for a two-sided quoting strategy those may dominate the economics,
> so a negative trading P&L here is not evidence the strategy loses money.
>
> These are exploratory comparisons across many hypotheses with no multiple-comparison
> correction. Treat p-values as ranking signals, not as confirmatory tests.

## 1. Coverage

| | |
| --- | --- |
| Trades reconstructed | 4,155 |
| Windows reconstructed | 96 |
| Windows classified from market timestamps | 96 |
| Windows classified from title text only (weaker) | 0 |
| Windows where timestamps and title disagree | 0 |
| Windows with reconstructed start bounds | 0 |
| Windows with realized P&L available | 79 |
| Windows flagged oversold (a sell exceeded visible inventory) | 0 |

Windows per cell: {'BTC_5m': 50, 'BTC_15m': 18, 'ETH_5m': 28}

## 2. Does position size increase at 70c / 80c / 90c?

Pooled across all 4,155 buys, Spearman(price, shares) =
0.175
(p=0.0000); Spearman(price, dollars) =
0.668
(p=0.0000).

Pooling confounds "bigger at high prices" with "bigger in busy windows", so the
controlled reading is the within-window one: in each window separately, does size rise
with price? Of 64 windows with enough distinct prices to test,
34 had a positive rank correlation
(rate 53.1%,
sign-test p=0.7077) — **no evidence**.
Median within-window rho 0.044
[95% CI -0.046,
0.153].

Size by price band:

| band | buys | median shares | median $ |
| --- | --- | --- | --- |
| lt70 | 3,152 | 11.2 | 2.86 |
| 70to80 | 465 | 18.7 | 14.00 |
| 80to90 | 335 | 24.0 | 19.44 |
| gte90 | 203 | 31.1 | 29.68 |

Threshold splits (shares bought below vs at/above each level):

**70c threshold — shares** — strong evidence (Mann-Whitney p=0.0000, effect r=-0.211)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| price<0.7 | 3,152 | 11.2 | 4.4 | 41.0 | 20.6 | 0.0 | 125.0 |
| price>=0.7 | 1,003 | 22.5 | 8.9 | 50.0 | 26.2 | 0.0 | 53.8 |

**80c threshold — shares** — strong evidence (Mann-Whitney p=0.0000, effect r=-0.254)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| price<0.8 | 3,617 | 12.0 | 5.0 | 42.0 | 21.0 | 0.0 | 125.0 |
| price>=0.8 | 538 | 26.3 | 10.0 | 50.0 | 28.3 | 0.0 | 51.8 |

**90c threshold — shares** — strong evidence (Mann-Whitney p=0.0000, effect r=-0.297)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| price<0.9 | 3,952 | 12.8 | 5.0 | 43.0 | 21.5 | 0.0 | 125.0 |
| price>=0.9 | 203 | 31.1 | 13.8 | 50.0 | 30.8 | 0.3 | 50.6 |

## 3. 5-minute vs 15-minute windows

76 vs 16 windows.

**total_trades** — insufficient data (Mann-Whitney p=0.0000, effect r=-1.000)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5m | 76 | 4.000 | 2.000 | 9.000 | 5.684 | 1.000 | 16.000 |
| 15m | 16 | 205.000 | 176.500 | 259.250 | 221.812 | 143.000 | 361.000 |

**total_dollars_bought** — insufficient data (Mann-Whitney p=0.0000, effect r=-1.000)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5m | 76 | 86.280 | 26.192 | 162.404 | 99.561 | 0.428 | 324.682 |
| 15m | 16 | 2,063.767 | 1,641.481 | 2,350.939 | 2,135.506 | 1,278.888 | 4,167.587 |

**matched_pair_cost_per_share** — insufficient data (Mann-Whitney p=0.2007, effect r=-0.214)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5m | 52 | 0.928 | 0.834 | 1.048 | 0.939 | 0.519 | 1.343 |
| 15m | 16 | 0.966 | 0.946 | 0.980 | 0.965 | 0.911 | 1.033 |

**seconds_to_opposite_side** — insufficient data (Mann-Whitney p=0.0015, effect r=0.529)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5m | 52 | 57.000 | 23.750 | 95.750 | 68.962 | 1.000 | 234.000 |
| 15m | 16 | 12.000 | 7.000 | 31.750 | 29.375 | 0.000 | 179.000 |

**max_price_bought** — insufficient data (Mann-Whitney p=0.0000, effect r=-0.767)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5m | 76 | 0.860 | 0.660 | 0.932 | 0.737 | 0.010 | 0.980 |
| 15m | 16 | 0.970 | 0.950 | 0.980 | 0.955 | 0.840 | 0.990 |

**finished_balanced rate** — 5m 3.9% vs 15m 18.8% (p=0.0293) — insufficient data

**bought_at_90c_plus rate** — 5m 43.4% vs 15m 87.5% (p=0.0013) — insufficient data

## 4. BTC vs ETH

64 vs 28 windows.

**total_trades** — strong evidence (Mann-Whitney p=0.0000, effect r=0.911)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTC | 64 | 10.000 | 5.000 | 47.750 | 61.297 | 1.000 | 361.000 |
| ETH | 28 | 2.000 | 1.000 | 3.000 | 2.071 | 1.000 | 4.000 |

**total_dollars_bought** — strong evidence (Mann-Whitney p=0.0000, effect r=0.939)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTC | 64 | 165.436 | 107.603 | 563.234 | 642.683 | 10.039 | 4,167.587 |
| ETH | 28 | 10.834 | 1.599 | 34.701 | 21.535 | 0.428 | 91.141 |

**matched_pair_cost_per_share** — insufficient data (Mann-Whitney p=0.8286, effect r=-0.043)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTC | 57 | 0.948 | 0.901 | 0.997 | 0.938 | 0.519 | 1.285 |
| ETH | 11 | 0.954 | 0.836 | 1.108 | 0.982 | 0.758 | 1.343 |

**seconds_to_opposite_side** — insufficient data (Mann-Whitney p=0.7453, effect r=-0.064)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTC | 57 | 42.000 | 18.000 | 78.000 | 58.526 | 0.000 | 224.000 |
| ETH | 11 | 63.000 | 16.000 | 70.500 | 65.455 | 1.000 | 234.000 |

**max_price_bought** — strong evidence (Mann-Whitney p=0.0000, effect r=0.736)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTC | 64 | 0.930 | 0.875 | 0.960 | 0.873 | 0.190 | 0.990 |
| ETH | 28 | 0.715 | 0.107 | 0.823 | 0.551 | 0.010 | 0.940 |

**finished_balanced rate** — BTC 7.8% vs ETH 3.6% (p=0.4484) — no evidence

**bought_at_90c_plus rate** — BTC 68.8% vs ETH 10.7% (p=0.0000) — strong evidence

## 5. Does he systematically buy the cheaper side?

Two independent readings, because the naive one leans on an assumption.

**Opening trade below 50c:** 33 of 96
windows (34.4%, p=0.0031) — **moderate evidence**.
_Treats 50c as the neutral point of a two-outcome market. It does not observe the opposite side's contemporaneous price._

**Against his own contemporaneous opposite-side fill:** of 3,807
buys with an opposite-side fill within 30s,
2,194 were on the cheaper side (57.6%,
p=0.0000) — **strong evidence**. Median price gap to the opposite
side: 0.190.
_Both prices are this trader's own executed fills within 30s of each other. No resting book is inferred._

## 6. How quickly does the opposite side follow?

68 of 92 windows
(73.9%) saw both sides traded.

Median delay from the first fill to the first opposite-side fill:
**48.0s** [95% CI 27.5,
58.0], p25 17.2s,
p75 75.0s. That is
14.0% of the window length at the median.
Median trades on the opening side before switching:
2.0.

| window | n | median seconds | p75 seconds |
| --- | --- | --- | --- |
| 5m | 52 | 57.0 | 95.8 |
| 15m | 16 | 12.0 | 31.8 |

## 7. Is there a combined Up+Down acquisition-cost target?

Across 68 windows that ended holding both sides, the matched-pair
acquisition cost (weighted-average Up price + weighted-average Down price) had median
**$0.951** [95% CI 0.921,
0.967], IQR
0.887–1.000,
sd 0.144.

51 of 68 paired windows
(75.0%) finished with a pair cost under $1.00 — a locked settlement
profit before fees, rebates and gas.

Concentration around the median:

| tolerance | windows | share |
| --- | --- | --- |
| within 0.005 of median | 4 | 5.9% |
| within 0.01 of median | 5 | 7.4% |
| within 0.02 of median | 14 | 20.6% |
| within 0.05 of median | 32 | 47.1% |

Final pair cost was the window's running minimum in 21 of 58 windows (36.2%). _A high rate is consistent with stopping once the pair is cheap enough, but is also what mechanically happens when the cheap side is bought last._

## 8. Balanced or directional at the close?

Balanced = residual |Up-Down| <= 1.0 shares or <= 5% of the larger leg.

**6 of 92 windows (6.5%) finished balanced.**
Median residual as a fraction of the larger leg:
52.5%. Residual side counts:
{'DOWN': 49, 'UP': 43}. Windows that only ever traded one side:
24.

| cell | windows | balanced | rate |
| --- | --- | --- | --- |
| BTC_5m | 48 | 2 | 4.2% |
| BTC_15m | 16 | 3 | 18.8% |
| ETH_5m | 28 | 1 | 3.6% |
| ETH_15m | 0 | 0 | n/a |

## 9. Profitable vs losing windows

Realized P&L was available for 79 windows:
47 profitable, 32 losing, 0 flat.
Total realized P&L across those windows: 593.57.
Median per window: 3.63.

> Realized P&L is Polymarket's own position accounting. It excludes liquidity rebates and maker incentives, which for a two-sided quoting strategy may be a large share of true economics. A negative trading P&L is therefore not proof the strategy loses money.

**matched_pair_cost_per_share** — moderate evidence (Mann-Whitney p=0.0037, effect r=-0.411)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| profitable | 36 | 0.919 | 0.834 | 0.962 | 0.906 | 0.609 | 1.257 |
| losing | 32 | 0.979 | 0.938 | 1.077 | 0.989 | 0.519 | 1.343 |

**total_dollars_bought** — no evidence (Mann-Whitney p=0.2137, effect r=-0.166)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| profitable | 47 | 114.111 | 43.391 | 227.608 | 452.594 | 3.960 | 2,747.166 |
| losing | 32 | 159.155 | 76.933 | 563.234 | 636.990 | 33.434 | 4,167.587 |

**max_price_bought** — no evidence (Mann-Whitney p=0.7301, effect r=-0.047)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| profitable | 47 | 0.900 | 0.830 | 0.950 | 0.873 | 0.370 | 0.990 |
| losing | 32 | 0.925 | 0.837 | 0.960 | 0.872 | 0.600 | 0.990 |

**seconds_to_opposite_side** — no evidence (Mann-Whitney p=0.4498, effect r=0.108)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| profitable | 36 | 54.000 | 21.750 | 75.000 | 64.361 | 2.000 | 234.000 |
| losing | 32 | 41.500 | 12.000 | 72.000 | 54.344 | 0.000 | 207.000 |

**residual_frac_of_larger_leg** — no evidence (Mann-Whitney p=0.6491, effect r=0.061)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| profitable | 47 | 0.484 | 0.113 | 0.886 | 0.483 | 0.005 | 1.000 |
| losing | 32 | 0.308 | 0.174 | 0.618 | 0.394 | 0.066 | 0.949 |

**finished_balanced rate** — profitable 12.8% vs losing 0.0% (p=0.0355) — weak evidence

## 10. Candidate behavioural rules

Each rule states its support, its counterexamples, and a confidence level. The confidence
column is capped at "moderate" throughout: this is a single trader, observed through fills
only, over one sample period.

### R1 — Opens on the side priced below 50c

- **Support:** 33/96 windows (34.4%), p=0.0031
- **Confidence:** moderate evidence
- **Counterexamples (63 total):**

- `btc-updown-15m-1788024600` — opened DOWN at 0.530 (2026-08-29T17:30:00Z)
- `btc-updown-5m-1788024600` — opened DOWN at 0.560 (2026-08-29T17:30:00Z)
- `eth-updown-5m-1788024600` — opened DOWN at 0.910 (2026-08-29T17:30:00Z)
- `eth-updown-5m-1788024900` — opened UP at 0.940 (2026-08-29T17:35:00Z)
- `btc-updown-5m-1788025200` — opened UP at 0.890 (2026-08-29T17:40:00Z)
- `eth-updown-5m-1788025200` — opened UP at 0.640 (2026-08-29T17:40:00Z)
- `btc-updown-15m-1788025500` — opened DOWN at 0.520 (2026-08-29T17:45:00Z)
- `btc-updown-5m-1788025500` — opened UP at 0.720 (2026-08-29T17:45:00Z)

### R2 — Takes the opposite side shortly after the first

- **Support:** both sides traded in 68/92 windows (73.9%); median delay 48.0s
- **Confidence:** descriptive — no null model is being tested here
- **Counterexamples — never traded the second side (24 total):**

- `eth-updown-5m-1788024600` — only ever traded DOWN, 2 trades, 28.28 bought
- `btc-updown-5m-1788025500` — only ever traded UP, 5 trades, 165.71 bought
- `eth-updown-5m-1788025800` — only ever traded UP, 1 trades, 1.60 bought
- `eth-updown-5m-1788027000` — only ever traded UP, 1 trades, 1.53 bought
- `eth-updown-5m-1788027600` — only ever traded DOWN, 1 trades, 1.07 bought
- `eth-updown-5m-1788028200` — only ever traded UP, 2 trades, 10.77 bought
- `btc-updown-5m-1788028500` — only ever traded DOWN, 1 trades, 10.04 bought
- `btc-updown-5m-1788029100` — only ever traded UP, 2 trades, 87.35 bought

- **Counterexamples — unusually slow to cross (> p75 = 75.0s):**

- `btc-updown-5m-1788025800` — 95s before the opposite side
- `btc-updown-5m-1788026100` — 81s before the opposite side
- `btc-updown-5m-1788027000` — 78s before the opposite side
- `btc-updown-5m-1788028200` — 155s before the opposite side
- `eth-updown-5m-1788028800` — 234s before the opposite side
- `btc-updown-5m-1788028800` — 111s before the opposite side
- `btc-updown-5m-1788029400` — 120s before the opposite side
- `btc-updown-5m-1788032700` — 109s before the opposite side

### R3 — Finishes with a matched two-sided book

- **Support:** 6/92 windows (6.5%) finished balanced
- **Confidence:** descriptive
- **Counterexamples (86 total):**

- `btc-updown-15m-1788023700` — residual 266.1 shares UP (61.5% of larger leg)
- `btc-updown-5m-1788024300` — residual 27.0 shares UP (81.0% of larger leg)
- `btc-updown-15m-1788024600` — residual -148.4 shares DOWN (6.0% of larger leg)
- `eth-updown-5m-1788024600` — residual -30.6 shares DOWN (100.0% of larger leg)
- `eth-updown-5m-1788024900` — residual -61.0 shares DOWN (61.0% of larger leg)
- `btc-updown-5m-1788024900` — residual -191.6 shares DOWN (55.6% of larger leg)
- `btc-updown-5m-1788025200` — residual 125.3 shares UP (83.4% of larger leg)
- `eth-updown-5m-1788025200` — residual 5.0 shares UP (33.3% of larger leg)

### R4 — Accumulates toward a combined pair cost near $0.951

- **Support:** 68 paired windows, median pair cost $0.951, IQR 0.887–1.000; 75.0% finished under $1.00
- **Confidence:** moderate — a tight distribution is *consistent with* a target but does not prove one; the same shape arises if he simply stops when the second leg gets expensive
- **Counterexamples — pair cost at or above $1.00:**

- `btc-updown-5m-1788025200` — pair cost $1.049, matched 25.0 shares
- `btc-updown-5m-1788025800` — pair cost $1.005, matched 165.0 shares
- `btc-updown-15m-1788028200` — pair cost $1.033, matched 2,351.7 shares
- `eth-updown-5m-1788028800` — pair cost $1.126, matched 20.0 shares
- `btc-updown-5m-1788028800` — pair cost $1.257, matched 55.0 shares
- `btc-updown-5m-1788029700` — pair cost $1.074, matched 100.0 shares
- `btc-updown-5m-1788030300` — pair cost $1.172, matched 55.0 shares
- `btc-updown-5m-1788031800` — pair cost $1.069, matched 267.5 shares

### R5 — Buys larger as price rises

- **Support:** within-window sign test 34/64 positive, p=0.7077
- **Confidence:** no evidence
- **Counterexample rate:** 30 windows showed the opposite sign

## 11. Implementable pseudocode

This is a *description of observed behaviour*, not a recommendation, and not a strategy
that has been validated for profitability. It reproduces the shape of the fills only. The
parameters are medians from this sample and will drift.

```python
# Observed-behaviour sketch. Read-only research artifact.
#
# Fitted parameters (medians from this sample -- refit before any use):
PAIR_COST_TARGET      = 0.951      # combined Up+Down acquisition cost
OPPOSITE_SIDE_DELAY_S = 48.0    # median seconds before crossing to side 2
BALANCED_TOLERANCE    = 0.05

def on_window_open(window):
    # R1: open on whichever side is quoted below the midpoint.
    side = cheaper_side(window)          # observed: opened <50c in 34.4% of windows
    buy(side, size=base_size(window))

def on_fill(window, state):
    # R5: size scales with price -- larger clips as the leg gets expensive.
    #     Direction of the effect: no evidence.
    #
    # R2/R4: cross to the other side, then keep adding while the *combined*
    #        acquisition cost still leaves the matched pair under target.
    other = opposite(state.first_side)
    projected = state.avg_price(state.first_side) + current_price(other)
    if projected <= PAIR_COST_TARGET:
        buy(other, size=size_for_price(current_price(other)))

def on_window_close(window, state):
    # R3: finish matched. Observed balanced finish rate: 6.5%.
    residual = state.qty(UP) - state.qty(DOWN)
    if abs(residual) > BALANCED_TOLERANCE * max(state.qty(UP), state.qty(DOWN)):
        pass  # observed: he did NOT always flatten -- 86 windows ended directional
```

## 12. What this analysis cannot tell you

- **Order intent.** Only fills are public. A window with one side traded may be a
  directional bet, or a two-sided quote where the other side never filled. These are
  indistinguishable in the data and are never assumed apart.
- **Book context.** No historical order book is available, so "he bought the cheaper
  side" is measured against his own fills, not against the market's quotes.
- **True economics.** Rebates, maker incentives, and gas are outside the position
  accounting. Section 9's P&L is trading P&L only.
- **Causality.** Section 2's price/size relationship is observational. Prices rise late in
  a window and sizes may rise late for unrelated reasons; the within-window test reduces
  but does not remove that confound.
- **Sample period.** Only history the public endpoints still serve is included; see the
  coverage table and any truncation note above.
