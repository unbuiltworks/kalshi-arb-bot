# CF_MATCH purchase attribution — the losing residual is predictable, and avoiding it destroys the profit

Read-only. CF_MATCH and PM_V1 are unmodified, no controller is proposed, no
threshold is selected. All 387 CF_MATCH purchases (3,120.8 shares, $386.46)
are classified at the end of the accounting chain under **two lot
conventions**, because the classification is a convention and the portfolio
economics are not.

## Where every CF_MATCH purchased share ended up

| convention | MATCHED shares | WINNING residual | LOSING residual | matched cost | win-resid cost | lose-resid cost |
|---|---|---|---|---|---|---|
| FIFO | 635.3 (20.4%) | 45.3 (1.5%) | 2440.2 (78.2%) | $309.47 | $9.72 | $67.27 |
| PRORATA | 635.3 (20.4%) | 45.3 (1.5%) | 2440.2 (78.2%) | $255.00 | $10.39 | $121.08 |

### Economics by class (FIFO)

| class | shares | cost | settlement value | **P&L before fees** |
|---|---|---|---|---|
| MATCHED (pairs) | 635.3 | $309.47 | $317.66 | **+8.188** |
| WINNING residual | 45.3 | $9.72 | $45.28 | **+35.561** |
| LOSING residual | 2440.2 | $67.27 | $0.00 | **-67.271** |
| fees | | | | **-12.167** |
| **total** | 3120.8 | $386.46 | | **-35.690** |

*(MATCHED shares come in pairs: 635.3 shares = 317.7 pairs paying $1.00 each.)*

### Concentration of the residual loss
## A correction that matters: the +$62.66 "matched economics" was an average-cost artifact

Both conventions classify the **same shares** — 635.3 matched, 45.3 winning
residual, 2,440.2 losing residual — but they assign different *costs* to
them, and the whole apparent story changes:

| | FIFO (earliest shares matched) | PRO-RATA (= average cost, what I reported before) |
|---|---|---|
| cost of the matched 635.3 shares | **$309.47** | $255.00 |
| implied pair cost | **$0.974** | $0.803 |
| **matched-pair economics** | **+$8.19** | **+$62.66** |
| residual economics | **−$31.71** | −$86.18 |
| **net (identical)** | **−$35.69** | **−$35.69** |

My earlier reports of "+$62.66 of matched economics at a median pair cost
of $0.8694" used average cost. Under FIFO the matched pairs earn **+$8.19**
and the cheap inventory sits in the residual line instead. **The portfolio
economics are unchanged; the attribution is not.** Every conclusion that
rested on "matched economics is large and good" needs reading as "the cheap
shares are good, and whether they are called matched or residual is a
bookkeeping choice."

## Where the loss comes from

- **79 of 387 purchases (20.4%) ever become losing residual**, costing
  **$67.27** in total — these are cheap shares: 2,440 of them for $67.
- The loss is **moderately concentrated**: the worst 34 purchases (8.8% of
  all) carry half of it, the worst 54 (14.0%) carry 80%.
- It is **broadly distributed across windows**: 24 of 28 affected, worst
  window $8.73, median $2.41, top-3 windows only 32% of the total.

So this is not a tail accident — it is a routine, repeated cost.

## Descriptive buckets (FIFO, purchase-time information only)

**position in the same-side run**

| bucket | purchases | shares | % of shares MATCHED | % WINNING resid | % LOSING resid | P&L before fees | P&L per $ spent |
|---|---|---|---|---|---|---|---|
| 1st | 300 | 1538.2 | 36.0% | 2.9% | 61.0% | +22.949 | +0.0766 |
| 2nd | 48 | 603.3 | 8.9% | 0.0% | 91.1% | -21.256 | -0.4428 |
| 3-5th | 31 | 674.3 | 3.3% | 0.0% | 96.7% | -19.741 | -0.6368 |
| 6+ | 8 | 305.1 | 1.7% | 0.0% | 98.3% | -5.475 | -0.6843 |

**seconds since the last reversal**

| bucket | purchases | shares | % of shares MATCHED | % WINNING resid | % LOSING resid | P&L before fees | P&L per $ spent |
|---|---|---|---|---|---|---|---|
| <30s | 60 | 343.7 | 34.3% | 2.3% | 63.4% | +7.093 | +0.1185 |
| 30-90s | 222 | 1337.6 | 29.9% | 2.8% | 67.3% | +15.413 | +0.0696 |
| 90-300s | 62 | 1078.4 | 5.3% | 0.0% | 94.7% | -33.565 | -0.5414 |
| >=300s | 43 | 361.1 | 16.9% | 0.0% | 83.1% | -12.464 | -0.2899 |

**residual already present (shares)**

| bucket | purchases | shares | % of shares MATCHED | % WINNING resid | % LOSING resid | P&L before fees | P&L per $ spent |
|---|---|---|---|---|---|---|---|
| 0-5 | 230 | 833.0 | 58.2% | 1.0% | 40.9% | +20.864 | +0.0909 |
| 5-25 | 97 | 817.2 | 13.5% | 4.6% | 81.9% | -4.551 | -0.0469 |
| 25-100 | 45 | 762.6 | 4.2% | 0.0% | 95.8% | -28.876 | -0.6417 |
| 100+ | 15 | 708.1 | 1.1% | 0.0% | 98.9% | -10.960 | -0.7306 |

**extension price**

| bucket | purchases | shares | % of shares MATCHED | % WINNING resid | % LOSING resid | P&L before fees | P&L per $ spent |
|---|---|---|---|---|---|---|---|
| <0.20 | 74 | 2549.8 | 4.3% | 1.1% | 94.6% | +8.693 | +0.1181 |
| 0.20-0.40 | 55 | 193.9 | 83.5% | 4.7% | 11.8% | +35.075 | +0.6377 |
| 0.40-0.60 | 80 | 157.6 | 94.0% | 3.9% | 2.1% | +0.359 | +0.0045 |
| 0.60-0.80 | 76 | 110.8 | 98.6% | 0.3% | 1.2% | -21.124 | -0.2779 |
| >=0.80 | 102 | 108.8 | 98.0% | 2.0% | 0.0% | -46.527 | -0.4561 |

**seconds remaining**

| bucket | purchases | shares | % of shares MATCHED | % WINNING resid | % LOSING resid | P&L before fees | P&L per $ spent |
|---|---|---|---|---|---|---|---|
| >600 | 120 | 282.4 | 93.4% | 0.0% | 6.6% | +11.915 | +0.0993 |
| 300-600 | 142 | 866.0 | 27.3% | 2.9% | 69.8% | +1.625 | +0.0115 |
| 120-300 | 80 | 1039.3 | 8.6% | 0.8% | 90.5% | -26.413 | -0.3302 |
| <=120 | 45 | 933.1 | 4.9% | 1.2% | 93.9% | -10.650 | -0.2367 |

**buying the favourite?**

| bucket | purchases | shares | % of shares MATCHED | % WINNING resid | % LOSING resid | P&L before fees | P&L per $ spent |
|---|---|---|---|---|---|---|---|
| favourite | 231 | 315.8 | 97.5% | 1.9% | 0.6% | -70.843 | -0.3069 |
| underdog | 156 | 2805.0 | 11.7% | 1.4% | 86.9% | +47.320 | +0.3041 |

**10s movement of this side at purchase**

| bucket | purchases | shares | % of shares MATCHED | % WINNING resid | % LOSING resid | P&L before fees | P&L per $ spent |
|---|---|---|---|---|---|---|---|
| rising | 144 | 330.7 | 69.4% | 0.7% | 29.8% | -26.735 | -0.1857 |
| flat | 111 | 1806.8 | 8.6% | 0.0% | 91.4% | -32.862 | -0.2971 |
| falling | 132 | 983.4 | 25.4% | 4.4% | 70.2% | +36.074 | +0.2736 |

## OOS: can a losing-residual purchase be spotted at purchase time?

Window-grouped chronological 70/30 split; no window in both. train 261 / test 126 purchases.

| | |
|---|---|
| base rate (majority class) | 0.825 |
| best single feature on TRAIN | `px`, OOS AUC **0.018** |
| all-feature logit | OOS AUC **0.982**, accuracy 0.944 |
The patterns are strong, monotone, and heavily collinear — underdog ≈ cheap
≈ falling ≈ first-in-run:

- **first purchase in a same-side run +$0.077 per dollar; 2nd −$0.443;
  3rd–5th −$0.637; 6th+ −$0.684.** Continuing to extend the same side is
  where the money goes.
- **buying the underdog +$0.304/$ against buying the favourite −$0.307/$.**
  The buy-the-favourite premise of this whole family is inverted at the
  purchase level. This is the same direction as the independent L5 finding
  that L5 is an underdog accumulator.
- **buying a side that has just fallen +$0.274/$; rising −$0.186/$; flat
  −$0.297/$.**
- extension price: $0.20–$0.40 is **+$0.638/$**; ≥$0.80 is **−$0.456/$**.
- pre-existing residual 0–5 shares **+$0.091/$**; 100+ shares **−$0.731/$**.
- more than 600 s remaining **+$0.099/$**; under 300 s **−$0.33/$**.

## OOS: can a losing-residual purchase be spotted at purchase time?

Window-grouped chronological 70/30 split; no window in both. train 261 / test 126 purchases.

| | |
|---|---|
| base rate (majority class) | 0.825 |
| best single feature on TRAIN | `px`, OOS AUC **0.018** |
| all-feature logit | OOS AUC **0.982**, accuracy 0.944 |
| top standardized coefficients | `px` -1.814, `fav_px` +1.267, `resid_same_side` +1.135, `matched_before` +0.862, `locked_matched_profit` +0.581 |

| OOS tercile by predicted loss probability | n | spend | P&L before fees | P&L per $ |
|---|---|---|---|---|
| lowest (predicted safe) | 42 | $41.86 | -8.349 | -0.1994 |
| middle | 42 | $42.00 | -3.763 | -0.0896 |
| highest (predicted losing) | 42 | $42.00 | +19.404 | +0.4620 |

## The answer, and it is the opposite of what the question assumes

**Yes — losing-residual purchases are almost perfectly predictable at
purchase time. OOS AUC 0.982, accuracy 0.944, on a window-grouped split.**
The dominant feature is simply the **price paid** (`px`, standardized
coefficient −1.814).

**And acting on that prediction would destroy the strategy.** Sorted by the
model's own OOS predicted probability of becoming losing residual:

| tercile | spend | P&L before fees | **P&L per $** |
|---|---|---|---|
| predicted safest | $41.86 | −$8.35 | **−$0.199** |
| middle | $42.00 | −$3.76 | −$0.090 |
| **predicted most likely to lose** | $42.00 | **+$19.40** | **+$0.462** |

The classifier is near-perfect and economically **inverted**. The reason is
arithmetic: a $1 purchase at $0.05 buys 20 shares that usually expire
worthless and occasionally pay $20. Those purchases are 78% of all shares
and only 17% of all dollars, and they are where the positive expectancy
lives.

This is precisely the distinction you set up. The objective is
`matched + residual − fees`, and **this residual's expectancy is positive**:
the winning residual returns +$35.56 on $9.72 of cost while the losing
residual costs $67.27, so residual economics is −$31.71 — but the
*underdog* purchases that generate it are +$47.32 overall. CF_MATCH does not
fail because it carries directional residual. It fails because it spends
**$231** of its $386 on favourite-side purchases that return **−$70.84**,
and only **$155** on the underdog purchases that return **+$47.32**.

## What this does and does not license

It does **not** license a controller. Every pattern above is purchase-time
observable, but they are near-collinear with price, the sample is 387
purchases in 28 windows, and the one model I fitted is a cautionary tale
rather than a candidate — it predicts the label with 0.98 AUC and loses
money on it.

What it does establish, for whatever comes next: in this data the expectancy
sits in **cheap, recently-fallen, early-in-run purchases**, and the family's
defining action — repeatedly extending an expensive favourite — is the
single largest identified loss, at **−$0.307 per dollar spent across 231
purchases**.

`historical replay`. No live orders, no scaling, no controller changes,
no threshold selected. Verified compensation $0.0000.
