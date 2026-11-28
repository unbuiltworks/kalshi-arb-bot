# RN1 — price movement after its fills ("markout"), sports moneyline markets

2026-11-22. Wallet `0x2005d16a84ceefa912d4e380cd32e7ff827875ea`, Polymarket international. Public data only, read-only.
**HISTORICAL observation of another trader on another venue. Nothing here is our P&L, and markout is not profit.**

Words: *markout(h)* = price of the same token h after a BUY minus the price paid, per share (positive = moved the buyer's
way). *Maker fill* = his resting order was hit; *taker fill* = he hit someone else's order. *Bid-side print* = a public
trade where someone sold into a resting buy order. *Game* = one event with all its markets. CI = 95%, bootstrap by game.

## What was done
- Fills: every fill (11,064: 10,240 maker, 824 taker, $2.96M) in all 842 settled two-outcome moneyline conditions whose
  last fill is in the final 3 days of the activity file (11-18 12:18 → 11-21 21:19 UTC, 578 games).
- Trade tape: `data-api.polymarket.com/v2/trades?condition_id=…&limit=1000` serves closed markets; newest first, cursor
  only, no time filter (933 pages, 313,599 prints, none truncated). A print in the other token at q is used as 1−q.
- 1-minute series: `clob.polymarket.com/prices-history?market=<token>&fidelity=1` serves closed markets, also 30 days
  back. `data-api …/trades` (v1) is retired (410); `clob …/trades` needs a key (401).
- Check: all 824 taker and 9,400 of 10,240 maker fills were found on the tape (same transaction, price and expected
  side); the other 840 show another price on the taker row. Activity and tape times are identical (1 s resolution).

## MEASURED
A value counts only if a price was observed after the fill and no later than t+h (tape: a print with t < time ≤ t+h;
history: a point in that window no older than 90 s). Coverage = share of fills with such a value. Nothing is filled in.

**A. Markout by horizon, cents per share** (% = percent of price paid; the mean % is inflated by tokens under 20c)

| who | horizon | price source | n | coverage | mean c | 95% CI | median c | mean % | median % |
|---|---|---|---|---|---|---|---|---|---|
| maker | 5 s | tape last print | 5109 | 50% | +0.24 | [+0.17, +0.31] | 0.00 | +1.3% | 0.0% |
| maker | 30 s | tape last print | 8163 | 80% | +0.15 | [+0.04, +0.26] | 0.00 | +1.3% | 0.0% |
| maker | 2 min | 1-min history | 9738 | 95% | +0.33 | [+0.14, +0.52] | +0.50 | +6.5% | +0.8% |
| maker | 5 min | 1-min history | 9727 | 95% | +0.24 | [−0.16, +0.68] | +0.40 | +4.1% | +0.6% |
| maker | 15 min | 1-min history | 9704 | 95% | +0.39 | [−0.44, +1.21] | +0.35 | +6.9% | +0.6% |
| taker | 5 s | tape last print | 515 | 62% | +1.20 | [+0.83, +1.62] | +1.00 | +3.4% | +1.9% |
| taker | 30 s | tape last print | 742 | 90% | +1.44 | [+1.06, +1.90] | +1.00 | +4.2% | +2.2% |
| taker | 2 min | 1-min history | 738 | 90% | +1.67 | [+1.31, +2.06] | +1.24 | +6.5% | +2.0% |
| taker | 5 min | 1-min history | 759 | 92% | +1.71 | [+1.23, +2.21] | +1.50 | +7.6% | +2.0% |
| taker | 15 min | 1-min history | 762 | 92% | +1.54 | [+0.32, +2.84] | +1.50 | +9.1% | +2.6% |

- Short horizons are only partly supported: half of maker fills (5,131 of 10,240) have no later print within 5 s, 20%
  none within 30 s; those rows describe busy moments only and contain bid/ask bounce (D has the like-for-like version).
- Tape last print at 2 / 5 / 15 min agrees with the history rows: maker +0.29 [+0.04, +0.53] / +0.06 [−0.37, +0.50] /
  +0.41 [−0.40, +1.25]; taker +1.69 / +1.67 / +1.46 (coverage 93–99%).
- Taker fee averaged 0.90c a share (2–5 min taker markout after fee about +0.8c). Share-weighted means, 2 / 5 / 15 min:
  maker +0.57 / +0.31 / +1.05c; taker +1.50 / +1.11 / +1.58c.
- Maker markout is a small difference between two wide tails: p5 −6.2c, p95 +6.5c at 2 min; −29.5c, +32.5c at 15 min.

**B. Breakdowns, 1-min history price** (mean c per share [95% CI], n)

| maker fills | 2 min | 5 min |
|---|---|---|
| before game start (140 fills, 58 games) | +0.73 [+0.43, +1.01] n 137 | +0.74 [+0.36, +1.05] n 138 |
| in game (10,100 fills) | +0.32 [+0.12, +0.52] n 9601 | +0.24 [−0.18, +0.68] n 9589 |
| price <0.20 | +0.23 [−0.15, +0.68] n 2328 | +0.01 [−0.74, +0.94] n 2330 |
| price 0.20–0.40 | +0.17 [−0.31, +0.68] n 1996 | +0.13 [−1.10, +1.50] n 2006 |
| price 0.40–0.60 | +0.44 [+0.07, +0.82] n 2413 | +0.13 [−1.05, +1.19] n 2378 |
| price 0.60–0.80 | +0.28 [−0.23, +0.84] n 1725 | +0.17 [−0.87, +1.20] n 1746 |
| price >0.80 | +0.63 [+0.43, +0.85] n 1276 | +1.18 [+0.53, +1.79] n 1267 |
| OPEN: first buy in the market | +0.47 [+0.18, +0.81] n 730 | +0.48 [−0.07, +1.00] n 739 |
| EXTEND: same-side add, other side not yet held | +0.58 [+0.28, +0.88] n 1596 | +0.93 [+0.11, +1.78] n 1586 |
| COMPLETE: first buy of the opposite side | +0.14 [−0.28, +0.56] n 411 | −0.44 [−1.30, +0.45] n 405 |
| later buys once both sides are held | +0.27 [+0.01, +0.52] n 7001 | +0.10 [−0.49, +0.70] n 6997 |

- Maker by sport, 2 min then 5 min: tennis (179 games) +0.29 [−0.01, +0.62], −0.17 [−0.87, +0.55]; basketball (90)
  −0.01 [−0.59, +0.64], +0.89 [−0.21, +1.81]; American football (52) +0.46 [−0.19, +0.94], +0.93 [−0.51, +2.10]; soccer
  and other (226) +0.39 [+0.14, +0.66], +0.27 [−0.37, +0.95]. Hockey (7 games), esports (6): too few. Leagues: `tables.txt`.
- Maker by fill size, 2 min: under $20 +0.30 [+0.06, +0.53] n 4248; $20–100 +0.32 [0.00, +0.66] n 2167; $100–500 +0.27
  [−0.05, +0.58] n 2037; $500 and over +0.55 [+0.13, +0.96] n 1286. At 5 min all four intervals include zero.
- Taker fills (823 of 824 in game): positive at 2 min in every price band (+1.1c to +2.1c) and size band (+1.2c to
  +2.1c). Tennis +2.12 [+1.26, +2.79] n 186; basketball +1.33 [+0.94, +1.72] n 512 from only 26 games. By role: OPEN
  +1.57 (n 60), EXTEND +2.14 (n 33), COMPLETE +1.50 (n 25), later buys +1.67 (n 620); the first three are small.

**C. Settlement next to markout** (settled result = final outcome price − price paid, c per share)

| fills | n | settled mean | 95% CI | settled median |
|---|---|---|---|---|
| maker, all | 10240 | +1.58 | [−1.08, +3.96] | −2.00 |
| taker, all, after fee | 824 | −0.35 | [−3.23, +2.70] | +2.85 |
| all, lowest third of 5-min markout (mean −7.97c) | 3495 | −8.60 | [−11.34, −5.63] | −15.0 |
| all, middle third (mean +0.18c) | 3495 | +1.83 | [−1.50, +5.32] | −1.0 |
| all, highest third (mean +8.85c) | 3496 | +11.72 | [+8.10, +15.35] | +17.0 |

Correlation of 5-min markout with settled result: 0.26; the settled result varies four times as much (sd 41.5c against
10.6c), so one fill's markout says little about what it finally paid. Three days cannot separate either row from zero.

**D. Maker fills: worse price right after the fill? Recovery?** Later bid-side prints against the fill price (like for like):

| horizon | n | lower | equal | higher | mean c | 95% CI |
|---|---|---|---|---|---|---|
| 5 s | 3743 | 32.7% | 42.7% | 24.6% | −0.18 | [−0.25, −0.12] |
| 30 s | 7125 | 41.5% | 28.3% | 30.2% | −0.31 | [−0.42, −0.20] |
| 2 min | 8883 | 47.1% | 15.7% | 37.2% | −0.26 | [−0.49, −0.01] |
| 5 min | 9590 | 49.7% | 9.3% | 41.0% | −0.51 | [−0.95, −0.04] |
| 15 min | 9997 | 50.4% | 4.0% | 45.6% | −0.08 | [−0.89, +0.75] |

- 8,536 maker fills (83%) with a history price before the fill and at 2, 5, 15 min: filled 0.63c [+0.48, +0.79] below
  the last pre-fill price. That price then moved −0.26c [−0.50, −0.02], −0.31c [−0.84, +0.21], −0.10c [−1.07, +0.86].
  Markout against his fill: +0.37c [+0.18, +0.54], +0.32c [−0.13, +0.82], +0.53c [−0.37, +1.47]; medians +0.50, +0.45, +0.50.
- **Answer: yes, a small adverse move, and it does not deepen.** Later sellers hit bids on average 0.2–0.5c lower for the
  first five minutes, and about 40% of his 0.63c entry advantage is gone by 2 min. By 15 min the bid-side level is back
  at the fill price on average. What is left (+0.3c to +0.5c) is distinguishable from zero only at 2 min.
- Taker fills, same construction (n 606): paid 1.57c above the last history price; price 3.3c above it at 2 and 5 min.

## ESTIMATED (rests on an assumption)
- The 1-min history price is treated as the middle of the best buy and sell quotes. Not confirmed here: at 5 min it lay
  between the last bid-side and ask-side prints (±0.5c) in 6,174 of 9,185 fills where both existed.
- "0.63c below the pre-fill price" includes any move in the up-to-90 s before the fill. `gameStartTime` can be a scheduled time.
- Taker markout may partly be his own follow-on buying: 25% of taker fills are followed by another within 2 min. For the
  487 with none in the next 5 min: +1.31c [+0.88, +1.77] at 2 min, +0.43c [−0.18, +1.12] at 5 min (a selected subset).

## NOT AVAILABLE from public data
- Best bid and ask at or after the fill (no true mid or spread at 5 s / 30 s); ordering of prints inside one second.
- His resting orders: size, price versus the book, cancels, fill rate, orders that never filled. Rebates and rewards
  per fill. Hedges across the three conditions of one soccer game (COMPLETE is measured per condition only).
- Stability across windows: a first draw of 300 of these conditions (plus a taker top-up) gave maker 5-min −0.59c
  [−1.63, +0.30] (`tables_run1_300markets.txt`) against +0.24c [−0.16, +0.68] for all 842. The sign is not settled.

## Files (this directory) and re-run
`select_sample.py 3 9999 7` → `fetch_tape.py` → `fetch_hist.py` → `analyse.py > tables.txt` (writes
`rn1_markout_results.json`); `check_taker_bursts.py`; `common.py`. Caches: `sample.json`, `gamma_refresh.json`, `tape/`, `hist/`.
