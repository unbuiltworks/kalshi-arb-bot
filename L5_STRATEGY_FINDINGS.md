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
| Trades reconstructed | 2,444 |
| Windows reconstructed | 58 |
| Windows classified from market timestamps | 58 |
| Windows classified from title text only (weaker) | 0 |
| Windows where timestamps and title disagree | 0 |
| Windows with reconstructed start bounds | 0 |
| Windows with realized P&L available | 42 |
| Windows flagged oversold (a sell exceeded visible inventory) | 0 |

Windows per cell: {'BTC_5m': 31, 'BTC_15m': 13, 'ETH_5m': 14}

## 2. Does position size increase at 70c / 80c / 90c?

Pooled across all 2,444 buys, Spearman(price, shares) =
0.183
(p=0.0000); Spearman(price, dollars) =
0.652
(p=0.0000).

Pooling confounds "bigger at high prices" with "bigger in busy windows", so the
controlled reading is the within-window one: in each window separately, does size rise
with price? Of 37 windows with enough distinct prices to test,
20 had a positive rank correlation
(rate 54.1%,
sign-test p=0.7423) — **no evidence**.
Median within-window rho 0.101
[95% CI -0.049,
0.182].

Size by price band:

| band | buys | median shares | median $ |
| --- | --- | --- | --- |
| lt70 | 1,806 | 10.0 | 2.65 |
| 70to80 | 318 | 15.2 | 11.61 |
| 80to90 | 201 | 23.0 | 19.29 |
| gte90 | 119 | 30.7 | 28.83 |

Threshold splits (shares bought below vs at/above each level):

**70c threshold — shares** — strong evidence (Mann-Whitney p=0.0000, effect r=-0.202)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| price<0.7 | 1,806 | 10.0 | 3.3 | 38.2 | 19.5 | 0.0 | 125.0 |
| price>=0.7 | 638 | 20.0 | 7.5 | 46.1 | 24.7 | 0.0 | 53.8 |

**80c threshold — shares** — strong evidence (Mann-Whitney p=0.0000, effect r=-0.273)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| price<0.8 | 2,124 | 10.5 | 4.0 | 38.2 | 19.8 | 0.0 | 125.0 |
| price>=0.8 | 320 | 25.8 | 9.7 | 50.0 | 27.9 | 0.0 | 51.8 |

**90c threshold — shares** — strong evidence (Mann-Whitney p=0.0000, effect r=-0.319)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| price<0.9 | 2,325 | 11.4 | 4.8 | 40.0 | 20.4 | 0.0 | 125.0 |
| price>=0.9 | 119 | 30.7 | 14.3 | 50.0 | 30.6 | 0.3 | 50.6 |

## 3. 5-minute vs 15-minute windows

40 vs 7 windows.

**total_trades** — insufficient data (Mann-Whitney p=0.0000, effect r=-1.000)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5m | 40 | 4.500 | 2.000 | 9.000 | 5.600 | 1.000 | 16.000 |
| 15m | 7 | 205.000 | 186.500 | 256.000 | 213.714 | 143.000 | 263.000 |

**total_dollars_bought** — insufficient data (Mann-Whitney p=0.0000, effect r=-1.000)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5m | 40 | 105.831 | 32.300 | 170.204 | 109.160 | 0.746 | 324.682 |
| 15m | 7 | 1,934.791 | 1,606.742 | 2,262.710 | 1,976.915 | 1,417.544 | 2,747.166 |

**matched_pair_cost_per_share** — insufficient data (Mann-Whitney p=0.4213, effect r=-0.204)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5m | 28 | 0.933 | 0.888 | 1.016 | 0.949 | 0.725 | 1.285 |
| 15m | 7 | 0.975 | 0.944 | 0.987 | 0.964 | 0.911 | 0.998 |

**seconds_to_opposite_side** — insufficient data (Mann-Whitney p=0.0728, effect r=0.449)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5m | 28 | 54.000 | 21.750 | 84.500 | 64.500 | 1.000 | 212.000 |
| 15m | 7 | 26.000 | 6.500 | 42.000 | 26.143 | 2.000 | 58.000 |

**max_price_bought** — insufficient data (Mann-Whitney p=0.0014, effect r=-0.764)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5m | 40 | 0.900 | 0.762 | 0.940 | 0.778 | 0.030 | 0.980 |
| 15m | 7 | 0.970 | 0.955 | 0.975 | 0.959 | 0.890 | 0.990 |

**finished_balanced rate** — 5m 7.5% vs 15m 28.6% (p=0.0953) — insufficient data

**bought_at_90c_plus rate** — 5m 52.5% vs 15m 85.7% (p=0.1011) — insufficient data

## 4. BTC vs ETH

33 vs 14 windows.

**total_trades** — insufficient data (Mann-Whitney p=0.0000, effect r=0.946)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTC | 33 | 9.000 | 5.000 | 15.000 | 51.364 | 2.000 | 263.000 |
| ETH | 14 | 2.000 | 1.000 | 2.000 | 1.786 | 1.000 | 3.000 |

**total_dollars_bought** — insufficient data (Mann-Whitney p=0.0000, effect r=0.987)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTC | 33 | 183.697 | 110.978 | 322.226 | 544.210 | 38.122 | 2,747.166 |
| ETH | 14 | 7.917 | 2.191 | 32.300 | 17.564 | 0.746 | 46.402 |

**matched_pair_cost_per_share** — insufficient data (Mann-Whitney p=0.3467, effect r=0.253)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTC | 29 | 0.956 | 0.917 | 0.998 | 0.957 | 0.725 | 1.285 |
| ETH | 6 | 0.926 | 0.824 | 0.954 | 0.929 | 0.758 | 1.208 |

**seconds_to_opposite_side** — insufficient data (Mann-Whitney p=0.1425, effect r=0.391)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTC | 29 | 51.000 | 23.000 | 81.000 | 62.414 | 2.000 | 212.000 |
| ETH | 6 | 16.000 | 9.500 | 52.500 | 29.833 | 1.000 | 74.000 |

**max_price_bought** — insufficient data (Mann-Whitney p=0.0001, effect r=0.745)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTC | 33 | 0.940 | 0.900 | 0.960 | 0.898 | 0.370 | 0.990 |
| ETH | 14 | 0.685 | 0.250 | 0.853 | 0.585 | 0.030 | 0.940 |

**finished_balanced rate** — BTC 12.1% vs ETH 7.1% (p=0.6127) — insufficient data

**bought_at_90c_plus rate** — BTC 75.8% vs ETH 14.3% (p=0.0001) — insufficient data

## 5. Does he systematically buy the cheaper side?

Two independent readings, because the naive one leans on an assumption.

**Opening trade below 50c:** 15 of 58
windows (25.9%, p=0.0004) — **strong evidence**.
_Treats 50c as the neutral point of a two-outcome market. It does not observe the opposite side's contemporaneous price._

**Against his own contemporaneous opposite-side fill:** of 2,268
buys with an opposite-side fill within 30s,
1,302 were on the cheaper side (57.4%,
p=0.0000) — **strong evidence**. Median price gap to the opposite
side: 0.220.
_Both prices are this trader's own executed fills within 30s of each other. No resting book is inferred._

## 6. How quickly does the opposite side follow?

35 of 47 windows
(74.5%) saw both sides traded.

Median delay from the first fill to the first opposite-side fill:
**48.0s** [95% CI 23.0,
62.0], p25 20.0s,
p75 76.0s. That is
9.7% of the window length at the median.
Median trades on the opening side before switching:
2.0.

| window | n | median seconds | p75 seconds |
| --- | --- | --- | --- |
| 5m | 28 | 54.0 | 84.5 |
| 15m | 7 | 26.0 | 42.0 |

## 7. Is there a combined Up+Down acquisition-cost target?

Across 35 windows that ended holding both sides, the matched-pair
acquisition cost (weighted-average Up price + weighted-average Down price) had median
**$0.954** [95% CI 0.918,
0.977], IQR
0.906–0.997,
sd 0.119.

27 of 35 paired windows
(77.1%) finished with a pair cost under $1.00 — a locked settlement
profit before fees, rebates and gas.

Concentration around the median:

| tolerance | windows | share |
| --- | --- | --- |
| within 0.005 of median | 3 | 8.6% |
| within 0.01 of median | 4 | 11.4% |
| within 0.02 of median | 6 | 17.1% |
| within 0.05 of median | 18 | 51.4% |

Final pair cost was the window's running minimum in 10 of 29 windows (34.5%). _A high rate is consistent with stopping once the pair is cheap enough, but is also what mechanically happens when the cheap side is bought last._

## 8. Balanced or directional at the close?

Balanced = residual |Up-Down| <= 1.0 shares or <= 5% of the larger leg.

**5 of 47 windows (10.6%) finished balanced.**
Median residual as a fraction of the larger leg:
45.2%. Residual side counts:
{'DOWN': 23, 'UP': 24}. Windows that only ever traded one side:
12.

| cell | windows | balanced | rate |
| --- | --- | --- | --- |
| BTC_5m | 26 | 2 | 7.7% |
| BTC_15m | 7 | 2 | 28.6% |
| ETH_5m | 14 | 1 | 7.1% |
| ETH_15m | 0 | 0 | n/a |

## 9. Profitable vs losing windows

Realized P&L was available for 42 windows:
29 profitable, 13 losing, 0 flat.
Total realized P&L across those windows: 803.69.
Median per window: 7.51.

> Realized P&L is Polymarket's own position accounting. It excludes liquidity rebates and maker incentives, which for a two-sided quoting strategy may be a large share of true economics. A negative trading P&L is therefore not proof the strategy loses money.

**matched_pair_cost_per_share** — insufficient data (Mann-Whitney p=0.0066, effect r=-0.559)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| profitable | 22 | 0.922 | 0.899 | 0.961 | 0.910 | 0.725 | 1.049 |
| losing | 13 | 1.005 | 0.954 | 1.104 | 1.023 | 0.776 | 1.285 |

**total_dollars_bought** — insufficient data (Mann-Whitney p=0.7441, effect r=-0.066)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| profitable | 29 | 141.735 | 42.770 | 231.293 | 464.283 | 3.960 | 2,747.166 |
| losing | 13 | 132.746 | 66.100 | 265.921 | 363.942 | 33.640 | 1,749.250 |

**max_price_bought** — insufficient data (Mann-Whitney p=0.7129, effect r=0.074)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| profitable | 29 | 0.920 | 0.830 | 0.950 | 0.878 | 0.370 | 0.980 |
| losing | 13 | 0.910 | 0.860 | 0.940 | 0.876 | 0.620 | 0.990 |

**seconds_to_opposite_side** — insufficient data (Mann-Whitney p=0.2456, effect r=-0.241)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| profitable | 22 | 29.500 | 21.250 | 61.000 | 46.500 | 2.000 | 212.000 |
| losing | 13 | 63.000 | 19.000 | 101.000 | 74.308 | 1.000 | 207.000 |

**residual_frac_of_larger_leg** — insufficient data (Mann-Whitney p=0.7026, effect r=0.077)

| group | n | median | p25 | p75 | mean | min | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| profitable | 29 | 0.484 | 0.060 | 0.938 | 0.477 | 0.005 | 1.000 |
| losing | 13 | 0.265 | 0.175 | 0.452 | 0.365 | 0.083 | 0.802 |

**finished_balanced rate** — profitable 17.2% vs losing 0.0% (p=0.1107) — insufficient data

## 10. Candidate behavioural rules

Each rule states its support, its counterexamples, and a confidence level. The confidence
column is capped at "moderate" throughout: this is a single trader, observed through fills
only, over one sample period.

### R1 — Opens on the side priced below 50c

- **Support:** 15/58 windows (25.9%), p=0.0004
- **Confidence:** strong evidence
- **Counterexamples (43 total):**

- `btc-updown-15m-1788024600` — opened DOWN at 0.530 (2026-08-29T17:30:00Z)
- `btc-updown-5m-1788024600` — opened DOWN at 0.560 (2026-08-29T17:30:00Z)
- `eth-updown-5m-1788024600` — opened DOWN at 0.910 (2026-08-29T17:30:00Z)
- `eth-updown-5m-1788024900` — opened UP at 0.940 (2026-08-29T17:35:00Z)
- `btc-updown-5m-1788025200` — opened UP at 0.890 (2026-08-29T17:40:00Z)
- `eth-updown-5m-1788025200` — opened UP at 0.640 (2026-08-29T17:40:00Z)
- `btc-updown-15m-1788025500` — opened DOWN at 0.520 (2026-08-29T17:45:00Z)
- `btc-updown-5m-1788025500` — opened UP at 0.720 (2026-08-29T17:45:00Z)

### R2 — Takes the opposite side shortly after the first

- **Support:** both sides traded in 35/47 windows (74.5%); median delay 48.0s
- **Confidence:** descriptive — no null model is being tested here
- **Counterexamples — never traded the second side (12 total):**

- `eth-updown-5m-1788024600` — only ever traded DOWN, 2 trades, 28.28 bought
- `btc-updown-5m-1788025500` — only ever traded UP, 5 trades, 165.71 bought
- `eth-updown-5m-1788025800` — only ever traded UP, 1 trades, 1.60 bought
- `eth-updown-5m-1788027000` — only ever traded UP, 1 trades, 1.53 bought
- `eth-updown-5m-1788033000` — only ever traded UP, 1 trades, 0.75 bought
- `eth-updown-5m-1788033900` — only ever traded UP, 1 trades, 3.96 bought
- `btc-updown-5m-1788034800` — only ever traded DOWN, 2 trades, 38.12 bought
- `eth-updown-5m-1788035700` — only ever traded UP, 3 trades, 42.77 bought

- **Counterexamples — unusually slow to cross (> p75 = 76.0s):**

- `btc-updown-5m-1788025800` — 95s before the opposite side
- `btc-updown-5m-1788026100` — 81s before the opposite side
- `btc-updown-5m-1788027000` — 78s before the opposite side
- `btc-updown-5m-1788032700` — 109s before the opposite side
- `btc-updown-5m-1788033900` — 162s before the opposite side
- `btc-updown-5m-1788034200` — 212s before the opposite side
- `btc-updown-5m-1788034500` — 101s before the opposite side
- `btc-updown-5m-1788035100` — 119s before the opposite side

### R3 — Finishes with a matched two-sided book

- **Support:** 5/47 windows (10.6%) finished balanced
- **Confidence:** descriptive
- **Counterexamples (42 total):**

- `btc-updown-15m-1788023700` — residual 266.1 shares UP (61.5% of larger leg)
- `btc-updown-5m-1788024300` — residual 27.0 shares UP (81.0% of larger leg)
- `btc-updown-15m-1788024600` — residual -148.4 shares DOWN (6.0% of larger leg)
- `eth-updown-5m-1788024600` — residual -30.6 shares DOWN (100.0% of larger leg)
- `eth-updown-5m-1788024900` — residual -61.0 shares DOWN (61.0% of larger leg)
- `btc-updown-5m-1788024900` — residual -191.6 shares DOWN (55.6% of larger leg)
- `btc-updown-5m-1788025200` — residual 125.3 shares UP (83.4% of larger leg)
- `eth-updown-5m-1788025200` — residual 5.0 shares UP (33.3% of larger leg)

### R4 — Accumulates toward a combined pair cost near $0.954

- **Support:** 35 paired windows, median pair cost $0.954, IQR 0.906–0.997; 77.1% finished under $1.00
- **Confidence:** moderate — a tight distribution is *consistent with* a target but does not prove one; the same shape arises if he simply stops when the second leg gets expensive
- **Counterexamples — pair cost at or above $1.00:**

- `btc-updown-5m-1788025200` — pair cost $1.049, matched 25.0 shares
- `btc-updown-5m-1788025800` — pair cost $1.005, matched 165.0 shares
- `btc-updown-5m-1788027600` — pair cost $1.298, matched 50.0 shares
- `eth-updown-5m-1788033300` — pair cost $1.208, matched 10.0 shares
- `btc-updown-5m-1788033600` — pair cost $1.104, matched 153.1 shares
- `btc-updown-5m-1788033900` — pair cost $1.107, matched 262.3 shares
- `btc-updown-5m-1788034500` — pair cost $1.088, matched 100.0 shares
- `btc-updown-5m-1788035100` — pair cost $1.285, matched 100.9 shares

### R5 — Buys larger as price rises

- **Support:** within-window sign test 20/37 positive, p=0.7423
- **Confidence:** no evidence
- **Counterexample rate:** 17 windows showed the opposite sign

## 11. Implementable pseudocode

This is a *description of observed behaviour*, not a recommendation, and not a strategy
that has been validated for profitability. It reproduces the shape of the fills only. The
parameters are medians from this sample and will drift.

```python
# Observed-behaviour sketch. Read-only research artifact.
#
# Fitted parameters (medians from this sample -- refit before any use):
PAIR_COST_TARGET      = 0.954      # combined Up+Down acquisition cost
OPPOSITE_SIDE_DELAY_S = 48.0    # median seconds before crossing to side 2
BALANCED_TOLERANCE    = 0.05

def on_window_open(window):
    # R1: open on whichever side is quoted below the midpoint.
    side = cheaper_side(window)          # observed: opened <50c in 25.9% of windows
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
    # R3: finish matched. Observed balanced finish rate: 10.6%.
    residual = state.qty(UP) - state.qty(DOWN)
    if abs(residual) > BALANCED_TOLERANCE * max(state.qty(UP), state.qty(DOWN)):
        pass  # observed: he did NOT always flatten -- 42 windows ended directional
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
