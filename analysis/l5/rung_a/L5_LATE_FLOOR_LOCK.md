# Time-conditioned profit-floor protection

**VERDICT: NO PRODUCTION CHANGE. The apparent gain is three outlier windows
and its sign flips out-of-sample. It also never fires on R5.**

Analysis and shadow only. **R6 untouched.**

## Method

304 BTC-15m windows with real venue settlement, replayed **sequentially**.
At each state, `floor = min(q_up, q_down) − cash` — guaranteed, outcome-free.
When `s_left ≤ T` and `floor ≥ F` the lock triggers and **every subsequent
fill in that window is dropped**; the book as it stands is settled. Nothing
is added to the final book after the fact. Settlement scores only.

Variant **floor-preserving**: on trigger, keep accepting fills, but only
those leaving the post-trade floor at or above the locked floor.

## Controls

| policy | total | mean | median | losing | worst |
|---|---|---|---|---|---|
| **carry to settlement** | **$911.52** | **$2.998** | $4.720 | 126 | −$224.56 |
| frozen PROFIT_LOCK_5 | $396.58 | $1.305 | $5.881 | 120 | −$224.56 |

## Conditional lock (stop on trigger)

| T | floor | trig | total | mean | median | losing | worst | Δ carry | Δ frozen5 |
|---|---|---|---|---|---|---|---|---|---|
| 180 | ≥0 | 47 | 1071.55 | 3.525 | 5.069 | 119 | −224.56 | +160.03 | +674.97 |
| 180 | ≥0.5 | 43 | 1065.20 | 3.504 | 5.069 | 122 | −224.56 | +153.68 | +668.62 |
| 180 | ≥2 | 40 | 1015.19 | 3.339 | 5.069 | 123 | −224.56 | +103.67 | +618.61 |
| 180 | ≥5 | 37 | 923.66 | 3.038 | 5.268 | 124 | −224.56 | +12.13 | +527.08 |
| **120** | **≥2** | **34** | **1137.39** | **3.741** | 5.069 | 124 | −224.56 | **+225.86** | +740.81 |
| 120 | ≥0 | 36 | 1132.45 | 3.725 | 5.069 | 122 | −224.56 | +220.93 | +735.87 |
| 120 | ≥5 | 32 | 1046.84 | 3.444 | 5.268 | 125 | −224.56 | +135.31 | +650.26 |
| 90 | ≥0 | 30 | 943.09 | 3.102 | 4.933 | 124 | −224.56 | +31.57 | +546.51 |
| 60 | ≥0 | 20 | 899.85 | 2.960 | 4.933 | 125 | −224.56 | **−11.67** | +503.27 |
| 45 | any | 8 | 889.17 | 2.925 | 4.720 | 126 | −224.56 | **−22.35** | +492.59 |
| 30 | any | 1 | 911.74 | 2.999 | 4.720 | 126 | −224.56 | +0.22 | +515.16 |

Floor-preserving is similar, best at T=180/F≥0 (+$189.06) and T=120/F≥0
(+$156.74); it is worse than plain locking at T=120/F≥2.

Two things are already suspicious: the **worst window never moves**
(−$224.56 in every cell), and the **losing count goes UP** (126 → 124 at
best, 119 at T=180). The rule is not reducing tail risk.

## Robustness — this is where it fails

| config | trig | total | median Δ | improved | worsened | excl. best | excl. best 3 |
|---|---|---|---|---|---|---|---|
| T=120, F≥2 | 34 | **+225.86** | **+0.000** | 14 | **16** | +98.45 | **−77.95** |
| T=120, F≥0 | 36 | +220.93 | +0.000 | 16 | 16 | +93.52 | **−84.25** |
| T=180, F≥0 | 47 | +160.03 | +0.000 | 18 | **20** | +23.98 | **−149.43** |

**Median per-window delta is exactly $0.000, more windows are made worse
than better, and removing the best three windows turns every configuration
negative.** The top three contributors are +127.4, +110.5 and +65.9.

Split-half by window epoch:

| half | config | n | carry | lock | Δ |
|---|---|---|---|---|---|
| first | T=120 F≥2 | 12 | +48.78 | +23.54 | **−25.24** |
| first | T=180 F≥0 | 19 | +48.78 | −6.39 | **−55.17** |
| second | T=120 F≥2 | 22 | +862.74 | +1113.84 | +251.10 |
| second | T=180 F≥0 | 28 | +862.74 | +1077.94 | +215.20 |

**The sign flips between halves.** The rule loses money on the first half
and its entire measured benefit lives in a handful of second-half windows.
That is the definition of not surviving out-of-sample.

## R5 reproduced

| # | time | s_left | q_up | q_dn | cash | floor |
|---|---|---|---|---|---|---|
| 1 | 11:15:50.75 | 849.25 | 10 | 0 | 5.00 | −5.00 |
| 2 | 11:15:52.98 | 847.02 | 10 | 10 | 9.90 | +0.10 |
| 3 | 11:15:59.29 | 840.71 | 20 | 10 | 14.40 | −4.40 |
| 4 | 11:16:08.77 | 831.23 | 30 | 10 | 18.30 | −8.30 |
| 5 | 11:16:46.68 | 793.32 | 30 | 20 | 23.70 | −3.70 |
| **6** | **11:16:54.78** | **785.22** | 30 | 30 | 28.70 | **+1.30** |
| 7 | 11:17:00.54 | 779.46 | 30 | 40 | 33.60 | −3.60 |
| 8 | 11:17:11.61 | 768.39 | 30 | 50 | 38.30 | −8.30 |

**The +$1.30 guaranteed state occurred at 11:16:54.78, with 785.22 seconds
remaining** — not late at all, but 13 minutes from settlement. R5's entire
trading life ran from 849 s to 768 s remaining.

**No tested threshold (180/120/90/60/45/30 s) comes close. R5 P&L is
unchanged at −$8.30 under every configuration in this study.**

The hypothesis as posed — "stop reopening residual *late*" — simply does not
reach R5. R5 built and destroyed its floor in the first two minutes and then
sat idle for thirteen. Fixing R5 would need an *early* floor rule, which is
the global PROFIT_LOCK question we already settled: locking at ≥$1 with no
time condition yields −$660.64 against carry's +$911.52.

## Conclusion

1. The headline +$225.86 at T=120/F≥2 is **three windows**. Remove them and
   it is −$77.95.
2. Median per-window effect is **zero**; more windows are harmed than helped.
3. **Split-half sign flip** — negative on the first half, positive on the
   second.
4. Tail risk is untouched: the worst window is −$224.56 in every cell.
5. **It never fires on R5**, the case that motivated the question.

**Recommend no production change.** This does not beat frozen carry across
enough windows or out-of-sample, which was the stated bar. Frozen LOCK5,
PROFIT_LOCK_5 and R6 are untouched.
