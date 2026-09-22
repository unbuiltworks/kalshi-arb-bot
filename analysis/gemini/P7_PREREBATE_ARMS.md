# P7 — Pre-rebate arms across a frozen split, and the imported prior fails calibration

**Status: OFFLINE REPLAY ONLY. No live orders, nothing armed, no rebate term
anywhere.** The objective scored throughout is

```
matched P&L + directional residual P&L - confirmed fees
```

with rebates held at exactly $0. A test asserts by AST scan that no rebate or
reward symbol exists in either policy module.

**Dataset.** 3,181 Gemini contracts pulled from the venue's own public
endpoints, 2026-09-16 .. 09-22 — retention was probed first (09-16 returns
trades, 09-14 empty, 09-10 an unknown symbol), so **seven days is the entire
usable history, not a choice**. 3,146 carry a `resolutionSide`; 2,406 traded.
BTC 5m 672 contracts, BTC 15m 655, BTC 1h 1,854 (all 8 strikes of every
hourly event). Split fixed **before any arm was scored**:

```
dev  2026-09-16..18     val  2026-09-19..20     OOS  2026-09-21..22 (untouched)
```

---

## 1. The finding that governs everything else: the imported prior does not transfer

`p_win` is L5's residual-return curve, imported wholesale from his Polymarket
history and **not fitted to Gemini at all**. Scoring it against Gemini's own
resolutions — every print makes one leg buyable at a price; did that leg win?

| band | dev actual | val actual | **OOS actual** | L5 model |
|---|---|---|---|---|
| <0.35 | 25.5% (n=3650) | 23.9% (n=2356) | **16.4%** (n=2512) | **4.1%** |
| 0.35-0.50 | 41.4% | 41.1% | **39.5%** | **18.7%** |
| 0.50-0.65 | 50.4% | 56.1% | **51.5%** | 47.3% |
| 0.65-0.80 | 62.1% | 67.5% | **72.1%** | **87.6%** |
| >0.80 | 76.0% | 87.0% | **86.7%** | **97.6%** |

**The curve is badly miscalibrated, and wrong in opposite directions at the
two ends.** Gemini's cheap legs win far *more* than L5's (16-26% against a
model saying 4%), and Gemini's dear legs win far *less* (62-72% against 88%,
76-87% against 98%). The favourite-longshot bias that dominates L5's history
is much weaker here — Gemini's win rates sit far closer to the raw price.

Only the middle band (0.50-0.65) is well calibrated. **The whole basis of the
EV completion rule — that a cheap leg is nearly worthless and a dear leg is
nearly certain — is not a Gemini property.**

This is reported, not repaired. Recalibrating is a tuning round and the
instruction was to report the complete pre-rebate results first. **It is also
the single most promising open lever**, because every arm below runs on a
prior now known to be wrong.

---

## 2. The five arms, pre-rebate

Nothing was tuned. The EV policy has no fitted parameters — `p_win` is
imported, and `max_residual`/`max_legs` were fixed at the frozen controller's
own values (4 and 12). **So the dev/val/OOS differences are sampling, not
overfitting**, and dev carries no special status.

### capture = 1.00 (UPPER BOUND — assumes we win every qualifying print)

| arm | split | mkts | fills | turnover | matched $ | residual $ | **NET $** | /mkt | RoC | maxDD |
|---|---|---|---|---|---|---|---|---|---|---|
| 1. frozen L5 (always, CAP4) | dev | 228 | 2035 | 1078.35 | −21.57 | −29.78 | **−51.35** | −0.225 | −4.76% | 79.53 |
| | val | 187 | 1612 | 820.60 | −17.38 | −3.22 | **−20.60** | −0.110 | −2.51% | 37.49 |
| | **oos** | 165 | 1164 | 602.72 | −7.50 | +5.78 | **−1.72** | −0.010 | −0.29% | 19.73 |
| 2. BTC5m EV | dev | 250 | 725 | 271.62 | +34.03 | −4.65 | **+29.38** | +0.118 | 10.82% | 5.67 |
| | val | 174 | 427 | 145.33 | +23.03 | −22.36 | **+0.67** | +0.004 | 0.46% | 6.56 |
| | **oos** | 182 | 622 | 238.27 | +31.61 | −18.88 | **+12.73** | +0.070 | 5.34% | 7.03 |
| 3. BTC15m EV | dev | 228 | 1080 | 449.03 | +21.05 | −6.08 | **+14.97** | +0.066 | 3.33% | 18.37 |
| | val | 187 | 907 | 384.11 | +17.53 | −10.64 | **+6.89** | +0.037 | 1.79% | 12.73 |
| | **oos** | 165 | 610 | 244.60 | +13.83 | −18.43 | **−4.60** | −0.028 | −1.88% | 14.39 |
| 4. BTC1h EV nearest-money | dev | 70 | 500 | 210.67 | +17.07 | −21.74 | **−4.67** | −0.067 | −2.22% | 10.66 |
| | val | 48 | 314 | 134.57 | +11.49 | −17.06 | **−5.57** | −0.116 | −4.14% | 7.57 |
| | **oos** | 43 | 213 | 90.68 | +5.90 | −4.58 | **+1.32** | +0.031 | 1.46% | 6.43 |
| 5. combined 5m+15m+1h | dev | 548 | 2305 | 931.32 | +72.14 | −32.46 | **+39.68** | +0.072 | 4.26% | 22.03 |
| | val | 409 | 1648 | 664.01 | +52.06 | −50.07 | **+1.99** | +0.005 | 0.30% | 19.43 |
| | **oos** | 390 | 1445 | 573.55 | +51.34 | −41.89 | **+9.45** | +0.024 | 1.65% | 16.28 |

### net P&L at every capture level

| arm | capture | dev | val | **OOS** |
|---|---|---|---|---|
| 1. frozen L5 | 1.00 / 0.50 / 0.33 / 0.25 | −51.35 / −39.12 / −25.18 / −30.47 | −20.60 / −12.20 / −9.38 / — | −1.72 / +4.52 / −7.28 / — |
| **2. BTC5m EV** | 1.00 / 0.50 / 0.33 / 0.25 | **+29.38 / +13.56 / +14.23 / —** | **+0.67 / +2.47 / +0.91 / −1.53** | **+12.73 / +13.44 / +5.33 / +5.20** |
| 3. BTC15m EV | 1.00 / 0.50 / 0.33 / 0.25 | +14.97 / +12.84 / +18.13 / +6.51 | +6.89 / +6.10 / +5.05 / +5.02 | **−4.60 / −0.50 / −5.09 / −2.61** |
| 4. BTC1h | 1.00 / 0.50 / 0.33 / 0.25 | −4.67 / +0.70 / −4.41 / +1.85 | −5.57 / −2.75 / −1.21 / +2.11 | +1.32 / +1.46 / +0.54 / +1.19 |
| **5. combined** | 1.00 / 0.50 / 0.33 / 0.25 | **+39.68 / +27.10 / +27.95 / +22.71** | **+1.99 / +5.82 / +4.75 / +5.60** | **+9.45 / +14.40 / +0.78 / +3.78** |

**Two arms are positive on every split at every capture level** — BTC5m EV
(one exception: val at capture 0.25, −1.53) and the combined selector
(positive in all twelve cells). **BTC15m EV is negative on OOS at every
capture level** despite being positive on dev and val, and is the clearest
failure. The frozen L5 controller is negative almost everywhere, which is the
baseline this programme already established.

---

## 3. The tail

| arm | capture | split | worst market | p05 | median | % losing | worst streak | maxDD |
|---|---|---|---|---|---|---|---|---|
| 1. frozen L5 | 1.00 | dev | **−8.33** | −6.00 | −0.12 | 52.6% | 8 | **79.53** |
| 1. frozen L5 | 1.00 | oos | −5.27 | −2.59 | 0.00 | 49.7% | 6 | 19.73 |
| 2. BTC5m EV | 1.00 | oos | −1.37 | −0.94 | 0.00 | 42.9% | 6 | 7.03 |
| 3. BTC15m EV | 1.00 | oos | −1.41 | −0.92 | −0.06 | 57.6% | 6 | 14.39 |
| 4. BTC1h | 1.00 | oos | −1.27 | −0.81 | 0.00 | 46.5% | 7 | 6.43 |
| 5. combined | 1.00 | oos | −1.41 | −0.92 | 0.00 | 49.5% | 7 | 16.28 |
| 5. combined | 0.33 | oos | −1.12 | −0.51 | 0.00 | 36.4% | 5 | 11.32 |

**The EV arms have a far better tail than the frozen controller**: worst
single market −1.4 against −8.3, and max drawdown 7-16 against 80. The
`max_legs=12` bound caps single-market loss structurally. But **the median
market is 0.00 and 36-50% of markets lose money** — these are thin,
high-frequency economics, not a few good trades.

**Capital.** Turnover is reported above; peak concurrent capital is not
separately computed and I will not state one I have not measured. The
structural bound is `max_legs x price` ≈ **$6 per market** at these
parameters, times the number of simultaneously live markets (5m and 15m
overlap; the hourly ladder adds one nearest-money strike per event).

---

## 4. The WAIT diagnostic — a third action has material value

For every EV completion decision, recorded post-hoc and never fed to the
policy:

| lane | split | decisions | completed | **complement cheaper later** | median gain if waited | completing beat holding (realised) |
|---|---|---|---|---|---|---|
| 5m | dev | 264 | 214 | 41.1% | $0.250 | 44.4% |
| 5m | val | 150 | 119 | 38.7% | $0.280 | 52.9% |
| 5m | **oos** | 255 | 218 | **47.7%** | $0.260 | 57.3% |
| 15m | dev | 385 | 357 | 48.2% | $0.200 | 63.9% |
| 15m | **oos** | 224 | 206 | **46.1%** | $0.160 | 68.0% |
| 1h | dev | 857 | 800 | **60.4%** | $0.240 | 71.0% |
| 1h | **oos** | 667 | 613 | **65.1%** | $0.250 | 63.0% |

**In 39-65% of completions the complement was available cheaper afterwards,
at a median saving of $0.16-0.28 per unit.** Against a matched P&L that runs
a few cents per pair, that is not a rounding error — it is larger than the
edge being captured. The effect is strongest on the hourly lane (60-65%),
which is exactly where the long life gives the price most room to move.

Separately, completing beat holding **only 44-71% of the time** once outcomes
are known. The current binary COMPLETE-or-HOLD choice is leaving value on
both sides.

**This is a diagnostic, not a policy change.** A WAIT action needs a rule for
*how long* to wait and what to do if the complement never returns, and
building one from these numbers would be fitting to the same data. It is
recorded as the second promising lever.

---

## 5. What I am not claiming

- **Capture 1.00 is an upper bound**, established in P3: it assumes we win
  every qualifying print. The 0.50/0.33/0.25 columns exist because of that
  and were not optimized — they are fixed levels reported for sensitivity.
- **Seven days.** That is the entire retention window, and OOS is two days.
  Two arms surviving twelve cells is encouraging and is not an expectancy
  result.
- **The prior is wrong.** Section 1 shows `p_win` badly miscalibrated on
  Gemini. Every positive arm here is running on it. That the arms are
  positive *anyway* is interesting but it means the mechanism producing the
  profit is not the one the model thinks it is.
- **No fees are modelled** — none has ever been observed on Gemini
  (`fee_amount` 0 on every COHORT-2 fill). If fees exist, every arm is worse.
- **No rebate anywhere.**

## 6. The two levers this opens, in order

1. **Recalibrate `p_win` on Gemini, fitting on dev only and reporting
   val/OOS.** The imported curve is demonstrably wrong; the completion rule
   derives entirely from it. This is the highest-value next step and it is a
   tuning round, deliberately not taken here.
2. **Evaluate a WAIT action**, given 39-65% of completions could have been
   made cheaper later.

Neither changes the standing position: **no live orders, nothing armed, and
no rebate term until a trading candidate is frozen.**
