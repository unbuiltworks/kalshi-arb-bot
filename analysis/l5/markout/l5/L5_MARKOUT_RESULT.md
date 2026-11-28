# What happened to price after our fills - sports bot, 14-18 Nov and 22 Nov 2026

Read-only. Files: `l5_markout.py`, `l5_markout_report.py`, `l5_markout_fills.csv` (one row per fill), `l5_markout_summary.json` (every table, all variants). Cents per contract, before fees.
- **MARKOUT(h)** = value of the side we bought at fill time + h, minus our fill price. YES is worth the mid, NO is worth 1 - mid.
- **SETTLED** = what the side finally paid (1 or 0) minus our fill price. The two are never mixed below.
- Role, from the position before the fill: **OPEN** = no unpaired contracts (YES = NO; pairs are netted and pay $1); **EXTEND** = adds to
  the heavier side; **COMPLETE** = adds to the lighter side. (If "flat" means 0/0 only, the 331 OPEN fills made at YES = NO > 0 would be
  "EXTEND with 0 unpaired"; the CSV keeps them apart as `OPEN_REOPEN`.) Mean = contract-weighted; [a,b] = 95% bootstrap interval
  resampling whole markets; n = fills with a usable value (coverage %).

## MEASURED
### M1. Sanity checks (14-18 Nov file)
- 10,295 rows = 9,279 ORDER + 1,016 FILL. FILL rows: **1900.97 contracts, $953.91 cost** (expected 1,900.97 / $953.91: exact match).
  173 markets with fills, 240 with orders. Duplicate fills: 0. Fills with no matching order: 0. `pos` agrees with a running sum on every fill.
- The 57 rows without a side are NOT cancels: they are ORDER rows with side and price missing (47 NHL, 16 Nov 00h; 10 with qty "?").
  The file has no cancel rows. Odd but harmless: 91 partial-fill rows (20 orders have 2-3 fill rows), 92 fills under 1 contract,
  3 fills at a better price than the order (all taker exits), rows not in time order.
- Stage, orders with a side / fills: open 3727 / 310; open_rewarded 2546 / 191; complete 2380 / 303; open_rewarded_cheap_first 342 / 21; exit 181 / 184; extend 45 / 7; open_cheap_first 1 / 0.
- **Fill times are the bot's log times, not venue times.** The 184 taker `exit` fills are logged 5.3-31.1 s after their order (median 6.1 s), never sooner; on 22 Nov two fills were logged ~30 s late. The 5 s horizon is therefore not trustworthy.

### M2. MARKOUT, 22 Nov, against the recorded book (EXACT; 8 fills, 2 markets - too few for any interval)
| Fill (UTC, log time) | Role (unpaired before) | at fill | 5 s | 30 s | 2 min | 5 min | 15 min |
|---|---|---|---|---|---|---|---|
| 14:09:27 Bologna NO 0.47 x2 | OPEN (0) | +1.5 | +1.5 | -1.5 | +0.5 | +2.5 | +6.5 |
| 14:12:31 Genoa NO 0.66 x2 | OPEN (0) | +1.5 | +1.5 | +1.5 | +3.5 | gap | +6.5 |
| 14:14:33 Bologna YES 0.49 x2 | COMPLETE (2) | +1.5 | +1.5 | -0.5 | gap | gap | -6.5 |
| 14:24:26 Bologna YES 0.45 x2 | OPEN (0) | +1.5 | +1.5 | -0.5 | -0.5 | -2.5 | -4.5 |
| 14:28:39 Bologna YES 0.43 x2 | EXTEND (2) | +0.5 | +0.5 | +0.5 | -0.5 | +2.5 | -2.5 |
| 14:28:43 Genoa NO 0.68 x2 | EXTEND (2) | -21.5 | -21.5 | -21.5 | -21.5 | -21.5 | -49.5 |
| 14:40:52 Bologna NO 0.51 x2 | COMPLETE (4) | +6.5 | +6.5 | +7.5 | +9.5 | -22.5 | -22.5 |
| 14:44:08 Bologna NO 0.51 x2 | COMPLETE (2) | +8.5 | +8.5 | -17.5 | -22.5 | -22.5 | -22.5 |
| **mean OPEN (3 fills)** | | | +1.5 (n=3) | -0.2 (n=3) | +1.2 (n=3) | +0.0 (n=2) | +2.8 (n=3) |
| **mean EXTEND (2 fills)** | | | -10.5 (n=2) | -10.5 (n=2) | -11.0 (n=2) | -9.5 (n=2) | -26.0 (n=2) |
| **mean COMPLETE (3 fills)** | | | +5.5 (n=3) | -3.5 (n=3) | -6.5 (n=2) | -22.5 (n=2) | -17.2 (n=3) |
- "gap" = the recording hole 14:16-14:20Z (3 values lost). Recording began 14:07:34Z, before the first fill. The two 14:28 fills were
  logged ~30 s late: from the venue time (~14:28:07Z) the Genoa NO 0.68 was +2.0c at the fill and -21.5c 30 s later (the goal).
  The 14:40 and 14:44 completions turn to -22.5c only because Bologna scored at ~14:44:30; both were already paired.
- SETTLED, 22 Nov: 16 contracts, -$2.40 = -15.0c per contract (6 pairs +$0.28; 4 unpaired Genoa NO, cost $2.68, paid 0).

### M3. SETTLED result, 14-18 Nov (exact arithmetic on fills; no mid estimate involved)
- All 173 markets: -40.71 USD on 1900.97 contracts = **-2.14c per contract** [-3.4,-1.1]. Fully paired (126 markets): -0.43c [-1.2,+0.4].
  Left with unpaired contracts (47 markets): -7.91c [-12.5,-5.0]; in all 47 the unpaired side paid 0.
- By cycle (an OPEN fill until YES = NO again; 504 cycles, total -40.71 USD = the reconciliation's -40.71):

| Time from OPEN fill to paired | Cycles | Contracts | Mean pair cost | SETTLED c/contract [95%] | $ |
|---|---|---|---|---|---|
| <30s | 59 | 163 | 0.968 | +1.61 [+1.2,+2.0] | +2.61 |
| 30s-2min | 214 | 838 | 1.009 | -0.47 [-0.8,-0.1] | -3.96 |
| 2-5min | 81 | 407 | 1.035 | -1.75 [-2.8,-0.7] | -7.12 |
| 5-15min | 55 | 231 | 1.017 | -0.84 [-2.0,+0.4] | -1.95 |
| >15min | 48 | 182 | 0.950 | +2.53 [-0.3,+4.5] | +4.60 |
| never paired (held to settlement) | 47 | 80 | - | -43.54 [-50.3,-35.6] | -34.90 |
- Mean pair cost by day: 14 Nov 0.975, 15 Nov 0.993, 16 Nov 0.999, 17 Nov 1.022, 18 Nov 1.041. Median time to pair 88 s; 47 of 504 OPEN fills never paired.
- SETTLED per fill needs the winner, known only for the 47 markets left unpaired (366 fills, 435 contracts). **Biased sample**:
  these are exactly the markets where the last open leg lost. **OPEN -13.9 [-23.0,-5.1] (n=201); COMPLETE +1.1 [-8.9,+9.5] (n=162); EXTEND -43.7 (n=3, 1 market).**
  By entry price: 0.25-0.40 +4.0 [-26.2,+28.0]; 0.40-0.60 -2.8 [-8.7,+2.2]; 0.60-0.75 -39.5 [-56.5,-1.4]. By stage: open -4.9 [-13.9,+2.0]; open_rewarded -35.1 [-50.9,-27.6]; complete +2.6 [-13.1,+12.7]; exit +0.7 [-11.1,+14.1]. By time rested: <10s -7.5 [-17.0,+0.0]; 10-60s -7.0 [-14.5,-3.2]; 1-5min -11.5 [-29.6,+7.5]. By sport and unpaired bucket: in the JSON (most cells are 1-4 markets).

### M4. How good is the own-quote mid proxy (measured where a real price exists)
- 22 Nov, proxy mid minus recorded mid: two-sided quotes n=5: mean +0.9c, mean abs 1.3c, worst 2.0c. One-sided orders, adjusted,
  n=35: median 0.0c, mean abs 1.7c, 86% within 2c, worst -22c (a completion bid left far behind after the Genoa goal).
  Proxy markout minus exact markout on the 22 Nov fills (n=15): mean +2.5c, mean abs 3.5c, worst +22c - the proxy flatters. A weak
  test: 5 of 12 two-sided quotes fell inside the recording, and the 22 Nov controller differs from 14-18 Nov.
- 14-15 Nov, in-sample: a taker exit paid the real far touch. Touch minus the two-sided proxy mid placed seconds later, n=134:
  median +0.25c, 80% within half a cent, 88% within 1c. So the two-sided proxy is good to about half a cent on the
  tight college-football quotes of the older controller. No such check exists for the wider 17-18 Nov quotes.
- Hard limit: the true mid lies between our two resting quotes, so a two-sided estimate is off by at most its half-width
  (median 2.50c, 10th-90th percentile 1.25-4.50c over 3,033 two-sided quotes).

## ESTIMATED (14-18 Nov; everything here rests on the own-quote proxy)
Value at t+h = first two-sided quote at or after t+h within max(h, 30 s); if none, the first one-sided order, moved by the
market's usual half-width. No interpolation. Variants in the JSON: two-sided only (A), this one (AB_adj), unadjusted (AB_raw).
### E1. MARKOUT by role and by controller era (central variant AB_adj)
| | 5 s | 30 s | 2 min | 5 min | 15 min |
|---|---|---|---|---|---|
| All fills (1,016) | -1.4 [-2.0,-0.8] n=554 (55%) | -1.3 [-2.0,-0.5] n=519 (51%) | -0.7 [-1.4,+0.1] n=642 (63%) | +0.6 [-0.4,+1.6] n=692 (68%) | +0.4 [-0.2,+0.9] n=581 (57%) |
| OPEN (504) | -1.5 [-2.2,-0.7] n=280 (56%) | -1.1 [-2.1,+0.0] n=271 (54%) | +0.2 [-1.3,+1.7] n=328 (65%) | +1.7 [-0.3,+3.7] n=363 (72%) | +2.4 [+0.5,+4.0] n=306 (61%) |
| EXTEND (6) | -1.2 [n/a] n=4 (67%) | -5.5 [n/a] n=1 (17%) | -1.2 [n/a] n=2 (33%) | -4.2 [n/a] n=5 (83%) | -3.4 [n/a] n=4 (67%) |
| COMPLETE (506) | -1.4 [-2.3,-0.4] n=270 (53%) | -1.5 [-2.9,-0.1] n=247 (49%) | -1.7 [-2.9,-0.5] n=312 (62%) | -0.8 [-1.7,+0.2] n=324 (64%) | -1.9 [-3.5,-0.1] n=271 (54%) |
| OPEN, 14-16 Nov (395) | +1.4 [+1.0,+1.7] n=192 (49%) | +1.3 [+0.6,+1.9] n=214 (54%) | +1.7 [+0.6,+2.8] n=281 (71%) | +2.7 [+0.9,+4.6] n=322 (82%) | +2.4 [+0.7,+4.1] n=282 (71%) |
| OPEN, 17-18 Nov (109) | -3.6 [-4.4,-2.9] n=88 (81%) | -4.2 [-5.7,-2.6] n=57 (52%) | -2.7 [-6.4,+0.6] n=47 (43%) | -1.6 [-5.5,+2.8] n=41 (38%) | +2.3 [-5.0,+7.9] n=24 (22%) |
| COMPLETE, 14-16 Nov (392) | -0.8 [-1.8,+0.4] n=234 (60%) | -1.2 [-2.8,+0.3] n=216 (55%) | -1.7 [-2.8,-0.8] n=267 (68%) | -0.8 [-1.9,+0.2] n=290 (74%) | -1.4 [-3.0,+0.2] n=249 (64%) |
| COMPLETE, 17-18 Nov (114) | -2.9 [-4.3,-1.8] n=36 (32%) | -2.4 [-5.5,+0.8] n=31 (27%) | -1.5 [-4.8,+2.0] n=45 (40%) | -0.7 [-2.9,+1.9] n=34 (30%) | -4.5 [-7.9,+2.4] n=22 (19%) |
- 14-16 Nov = older controller (1 contract, bids 1.7c below mid on average, taker exits). 17-18 Nov = reward-first (5 contracts, 3.2c below mid).

### E2. The two requested numbers: adverse selection on a resting bid
| OPEN fills | 2 min | 5 min |
|---|---|---|
| (a) MARKOUT, central | +0.2 [-1.3,+1.7] n=328 (65%) | +1.7 [-0.3,+3.7] n=363 (72%) |
| (a) two-sided quotes only | +1.1 [-0.3,+2.7] n=185 (37%) | +1.6 [+0.2,+3.4] n=198 (39%) |
| (a) unadjusted one-sided | +1.0 [-0.4,+2.4] n=328 (65%) | +2.2 [+0.4,+4.1] n=363 (72%) |
| mid move since the order was placed* | -1.8 [-3.3,-0.3] n=320 (67%) | -0.2 [-2.2,+1.9] n=357 (74%) |
| mid move*, 14-16 Nov | 0.0 [-1.2,+1.2] n=280 (71%) | +1.1 [-0.7,+3.0] n=320 (81%) |
| mid move*, 17-18 Nov | -5.7 [-9.5,-2.7] n=40 (46%) | -4.7 [-8.5,-0.4] n=37 (42%) |
| (a) cfb (217 OPEN fills) | +0.9 [+0.2,+1.6] n=163 (75%) | +1.3 [+0.6,+1.9] n=198 (91%) |
| (a) atp (52 OPEN fills) | +1.2 [-2.3,+4.8] n=34 (65%) | +1.4 [-1.5,+6.1] n=32 (62%) |
| (a) cs2 (47 OPEN fills) | +1.3 [-4.0,+4.7] n=32 (68%) | +3.4 [-4.4,+7.7] n=32 (68%) |
| (a) itfwo (38 OPEN fills) | -3.7 [-6.5,+1.3] n=17 (45%) | -3.1 [-6.1,+3.4] n=18 (47%) |
- *mid move = MARKOUT minus the 2.3c (mean) that the bid sat below the mid when placed; it is the pure price move against the fill.
- **Every OPEN figure is biased upward; read it as an upper bound.** A still-unpaired fill is valued from our own completion bid; the older controller parked that bid exactly at the pair target in 136 of 267 cases, where it cannot show an adverse move.
  Two-sided quotes return only once the leg has paired (survivors). The 47 never-paired OPEN legs (M3) are the adverse tail the proxy does not see.
- **(b) EXTEND: not measurable.** Only 6 EXTEND fills in 14-18 Nov, all with 1 unpaired contract before (bucket 1-5): 2 min -1.2c
  (n=2), 5 min -4.2c (n=5), no interval. 22 Nov adds 2 exact ones (2 unpaired before): -0.5c and -21.5c at 2 min. No sport has n >= 30.

### E3. MARKOUT breakdowns (AB_adj; 30 s / 2 min / 5 min; 5 s and 15 min in the JSON)
| Group (fills) | 30 s | 2 min | 5 min |
|---|---|---|---|
| sport cfb (440) | +0.5 [+0.1,+0.9] n=248 (56%) | +0.2 [-0.3,+0.7] n=335 (76%) | +0.5 [+0.2,+0.9] n=390 (89%) |
| sport atp (106) | -2.1 [-3.4,-0.1] n=62 (58%) | -0.3 [-2.4,+1.9] n=64 (60%) | -0.1 [-2.1,+2.8] n=61 (57%) |
| sport cs2 (96) | +0.5 [-2.0,+3.5] n=36 (38%) | +0.0 [-2.3,+1.3] n=60 (62%) | +1.9 [-1.8,+3.7] n=56 (58%) |
| sport itfwo (74) | -2.7 [-5.1,-1.2] n=26 (35%) | -3.0 [-5.7,+0.0] n=31 (42%) | -0.9 [-2.6,+1.9] n=32 (43%) |
| sport itfme (54) | -1.4 [-4.7,+0.1] n=21 (39%) | -3.9 [-9.3,-0.1] n=30 (56%) | -0.4 [n/a] n=28 (52%) |
| sport setkameua (52) | -3.0 [-6.8,-0.6] n=21 (40%) | +2.7 [-2.9,+11.2] n=18 (35%) | -4.3 [-13.5,+4.8] n=12 (23%) |
| sport dota2 (46) | -3.9 [-5.3,-1.4] n=19 (41%) | -2.9 [-8.3,-0.6] n=23 (50%) | +1.7 [-4.1,+9.9] n=29 (63%) |
| sport nba (45) | -0.1 [-1.1,+1.0] n=29 (64%) | +0.3 [-1.3,+1.5] n=27 (60%) | -1.1 [-2.1,+0.3] n=37 (82%) |
| unpaired before 0 (504) | -1.1 [-2.1,+0.0] n=271 (54%) | +0.2 [-1.3,+1.7] n=328 (65%) | +1.7 [-0.3,+3.7] n=363 (72%) |
| unpaired before 1-5 (499) | -1.5 [-2.6,-0.6] n=243 (49%) | -1.4 [-2.6,-0.3] n=308 (62%) | -0.5 [-1.4,+0.4] n=324 (65%) |
| unpaired before 6-10 (13) | -1.4 [n/a] n=5 (38%) | -3.5 [n/a] n=6 (46%) | -3.6 [n/a] n=5 (38%) |
| entry price 0.25-0.40 (195) | -2.4 [-4.2,-0.4] n=94 (48%) | +1.0 [-1.8,+4.2] n=107 (55%) | +3.3 [-1.3,+7.4] n=121 (62%) |
| entry price 0.40-0.60 (609) | -0.8 [-1.8,+0.2] n=333 (55%) | -0.9 [-2.0,0.0] n=408 (67%) | +0.8 [+0.0,+1.6] n=459 (75%) |
| entry price 0.60-0.75 (195) | -1.6 [-3.0,-0.2] n=90 (46%) | -1.3 [-3.5,+0.8] n=120 (62%) | -2.9 [-5.2,-0.4] n=107 (55%) |
| stage open (310) | +1.4 [+1.0,+1.7] n=176 (57%) | +1.2 [+0.7,+1.7] n=232 (75%) | +1.4 [+0.8,+1.9] n=278 (90%) |
| stage open_rewarded (191) | -1.7 [-3.2,-0.1] n=100 (52%) | -0.5 [-3.2,+1.8] n=103 (54%) | +2.0 [-1.9,+5.6] n=91 (48%) |
| stage open_rewarded_cheap_first (21) | -6.2 [-8.9,-3.5] n=9 (43%) | -2.1 [-11.7,+14.6] n=7 (33%) | +2.5 [n/a] n=4 (19%) |
| stage complete (303) | -2.1 [-3.6,-0.7] n=137 (45%) | -2.0 [-3.7,-0.2] n=160 (53%) | -1.2 [-2.8,+0.6] n=159 (52%) |
| stage exit (184) | -0.7 [-3.5,+2.1] n=95 (52%) | -1.4 [-2.6,-0.1] n=138 (75%) | -0.5 [-2.0,+1.1] n=154 (84%) |
| rested <10s (369) | -0.5 [-1.8,+0.8] n=198 (54%) | +0.2 [-1.0,+1.7] n=262 (71%) | +1.2 [-0.1,+2.8] n=288 (78%) |
| rested 10-60s (468) | -2.1 [-3.1,-1.2] n=246 (53%) | -1.6 [-2.8,-0.3] n=277 (59%) | +0.0 [-1.6,+1.6] n=298 (64%) |
| rested 1-5min (163) | +0.0 [-1.4,+1.2] n=71 (44%) | +0.0 [-2.3,+2.2] n=100 (61%) | +0.2 [-1.9,+2.1] n=101 (62%) |
| rested >5min (16) | +0.6 [n/a] n=4 (25%) | +1.6 [n/a] n=3 (19%) | +11.7 [-10.2,+20.1] n=5 (31%) |
- No fill had more than 10 unpaired contracts before it; 13 fills above 0.75 and 4 below 0.25 are too few to value; `exit` = taker, traded ~6 s before the logged time.

### E4. SETTLED result by 5-minute MARKOUT tercile (47 resolved markets only; markout from the proxy)
| Tercile of 5-min MARKOUT | Fills | Mean MARKOUT | Mean SETTLED [95%] | Share that settled as winner |
|---|---|---|---|---|
| lowest | 108 | -6.4 | -12.4 [-30.6,+3.7] | 50% |
| middle | 119 | +0.3 | -11.8 [-24.7,+0.2] | 40% |
| highest | 118 | +6.8 | +7.3 [-6.2,+21.9] | 47% |
- The intervals overlap: a 5-minute markout of a few cents says little about a result decided tens of cents later.

## NOT AVAILABLE
- A book recording for 14-18 Nov. Every 14-18 Nov markout is an estimate; none is exact.
- Venue fill times (horizons run from the bot's log time, which lags by seconds, ~30 s seen on 22 Nov) and cancel times (no check
  that a quote was still resting; no fill rate per unit of resting time).
- A value for 32-49% of fills, depending on the horizon (the bot placed no order in the window). Coverage is in every cell.
- The winner of the 126 fully paired markets (a pair pays $1 whoever wins): no unbiased SETTLED result per fill or per role.
- EXTEND adverse selection (6 + 2 fills; nothing by sport; no bucket above 10 unpaired). An unbiased OPEN markout: the proxy gives an
  upper bound only; the solid OPEN evidence is M3 (what the cycle finally paid).
- Maker or taker per fill (only the `exit` stage is known taker). Fees and rewards are in no figure here.
