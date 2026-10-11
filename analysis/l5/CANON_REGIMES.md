# CANON_REGIMES -- L5 Sections 2 & 4

Generated 2026-10-05T22:31:23.489271+00:00 by `scripts/l5_regime_detect.py`. RESEARCH ONLY.

> Public activity of Polymarket trader l5zn1bwom8etsk. No figure here is realized profit for our account.

## 0. Corpus and the identity rule

- envelopes read: **1570** (activity 157, gap 1413); wrong-user 0, unparseable 0, no payload 1
- payload records seen: **784500**
- records carrying `name`/`proxyWallet`: 78000; carrying NEITHER: **706500** -- all kept on provenance
- records dropped for a *contradicting* identity: **0**
- duplicates dropped across/within files: **7751** ({'same_file': 4464, 'cross_file': 3287})
- records with an EMPTY eventSlug (duration/asset UNRESOLVED): **203**; records with a non-hex `conditionId`: **204**. These are counted, not dropped; 0 of them are TRADE records.
- malformed/unresolved: malformed 0, TRADE with unresolved slug 0, unresolved window epoch 0, out-of-range price/size 0
- post-dedupe type counts: `{'TRADE': 760817, 'REDEEM': 15729, 'MAKER_REBATE': 110, 'TAKER_REBATE': 73, 'REWARD': 20}`
- **760817 TRADE fills / 17046 independent window-events**, span 2026-03-19 -> 2026-08-31

**Coverage caveat.** These two files stop at 2026-08-31. Nothing here supports any claim about September L5 behaviour; September is a separate harvest under `data/l5/raw/sep/` and was not read by this script.

Dedupe key: `(transactionHash, conditionId, outcomeIndex, timestamp, str(price), str(size), type)` -- the Section-1-validated key, measured to produce ZERO conflicts. The task originally named `asset`, but the gap file has no `asset` field, so that key degenerates to `(txh, None, ts)` for all 706,500 gap records and silently collapses ~33k records differing in price/size/side/outcome/type. `(conditionId, outcomeIndex)` names the identical outcome token; `(conditionId, outcomeIndex)` names the identical outcome token. `price`/`size` are in the key because one `transactionHash` demonstrably contains several distinct fills at different prices.

### UNAVAILABLE

- **maker_taker_role**: The /activity endpoint carries no liquidity/role flag. Maker vs taker is NOT estimated anywhere here.
- **order_placements_and_cancels**: Only fills are public; quoting behaviour and patience-in-book are UNAVAILABLE.
- **L5_pnl**: No cost basis beyond these fills; settlement credit for events outside the fetch span is UNAVAILABLE.

## 2. Regime detection

- grid: calendar day, UTC; algorithm: multivariate binary segmentation, L2 mean-shift cost on sqrt(event-count)-weighted z-scores
- constraints: >= 7 days and >= 300 independent events per side
- significance: day-label permutation test, 400 draws, alpha=0.01
- **no month boundary was imposed**; segment edges are whatever the observable vector supports.

Observables on the daily grid:

- `events` -- independent window-events that day
- `trades_per_event` -- fills per window-event
- `trades_per_min` -- fills per minute of active window span
- `first_price` -- price of the first acquisition
- `first_favored_frac` -- fraction of events whose first buy is the favorite (p>0.5)
- `first_tte_s` -- seconds from first acquisition to window CLOSE (epoch+duration)
- `extend_per_event` -- same-side EXTEND actions per event
- `switch_per_event` -- outcome-switch actions per event
- `complement_per_event` -- complement actions per event
- `completion_rate` -- fraction of events reaching any matched inventory
- `pair_cost` -- effective matched pair cost (VWAP_up + VWAP_dn)
- `t_to_match_s` -- seconds from first acquisition to first matched inventory
- `peak_resid` -- peak |up-down| share residual
- `term_resid_frac` -- terminal residual / gross shares
- `mean_size` -- mean fill size (shares)
- `notional_per_event` -- USDC notional per event
- `concurrency` -- max distinct windows open simultaneously that day

### Change-point trace

| candidate day | gain | p(perm) | accepted | events left | events right |
|---|---|---|---|---|---|
| 2026-06-23 | 6805.529 | 0.00249 | True | 2070 | 14976 |
| 2026-04-25 | 1412.736 | 0.00249 | True | 386 | 1684 |
| 2026-08-19 | 929.336 | 0.00249 | True | 8235 | 6741 |
| 2026-08-08 | 245.268 | 0.01995 | False | 5218 | 3017 |

All statistically significant change points: **['2026-04-25', '2026-06-23', '2026-08-19']** (accepted gains: `{'2026-04-25': 1412.736, '2026-06-23': 6805.529, '2026-08-19': 929.336}`)

Dominance filter -- keep a change point only if its cost reduction is >= 10% of the largest accepted reduction -- leaves the PRIMARY change points: **['2026-04-25', '2026-06-23', '2026-08-19']**. Sections 2 and 4 below are reported on this primary partition; the finer significant partition is kept in `canon_regimes.json` under `regimes_fine_partition` and is summarised at the end. The splits the dominance filter removes are driven by the volume ramp (fills per event rising) rather than by a change in the action policy.

### Regime table

**Evidence-weight caveat.** The first segment (R1) rests on far fewer independent window-events than the others -- read its boundary and its rates as the weakest in the table. The 2026-06-23 boundary carries ~5x the cost reduction of either other boundary and is the one the data insists on.

| metric | R1 | R2 | R3 | R4 |
|---|---|---|---|---|
| start_day | 2026-03-19 | 2026-04-25 | 2026-06-23 | 2026-08-19 |
| end_day | 2026-04-24 | 2026-06-22 | 2026-08-18 | 2026-08-31 |
| n_days | 21 | 49 | 46 | 12 |
| independent_events | 386 | 1684 | 8235 | 6741 |
| fills | 15671 | 162174 | 349166 | 233806 |
| trades_per_event | 40.5984 | 96.3029 | 42.4002 | 34.6842 |
| mean_fill_size | 14.76669 | 17.94791 | 30.27587 | 34.13681 |
| notional_per_event | 316.19069 | 794.90994 | 427.09634 | 389.78219 |
| first_price_mean | 0.50212 | 0.49609 | 0.54509 | 0.53229 |
| first_favored_frac | 0.43264 | 0.44656 | 0.61651 | 0.63047 |
| first_tte_s_mean | 797.98187 | 814.77316 | 403.13018 | 354.97834 |
| extend_per_event | 24.2254 | 69.5802 | 30.6006 | 24.611 |
| switch_per_event | 5.6736 | 10.4501 | 4.1392 | 3.5306 |
| complement_per_event | 9.6995 | 15.2726 | 6.6605 | 5.5425 |
| completion_rate | 0.93264 | 0.99406 | 0.7292 | 0.73253 |
| t_to_match_s_mean | 72.35 | 45.43429 | 68.37868 | 87.27623 |
| pair_cost_median | 0.95915 | 0.96666 | 0.98437 | 0.97052 |
| pair_cost_frac_le_0_96 | 0.50556 | 0.44624 | 0.37469 | 0.45443 |
| pair_cost_frac_lt_1_00 | 0.72222 | 0.74373 | 0.58085 | 0.60976 |
| peak_resid_mean | 108.745 | 274.60735 | 166.36228 | 158.52642 |
| term_resid_frac_mean | 0.22023 | 0.1319 | 0.44477 | 0.48871 |
| rebate_events_n | 4 | 44 | 91 | 22 |
| reward_events_n | 0 | 0 | 7 | 11 |
| sells | 2 | 0 | 0 | 0 |

Maker/taker split per regime: **UNAVAILABLE** -- `/activity` records carry no liquidity-role flag, so no maker share is estimated.

## 4. Sequential decision process by regime

Action alphabet, per window-event, in arrival order of fills:

- `OPEN` -- first acquisition in the window
- `EXTEND` -- buy the SAME outcome as the immediately preceding fill
- `COMPLEMENT` -- buy the OTHER outcome while holding less of it (reduces imbalance; this is COMPLETE)
- `SWITCH` -- buy the other outcome when that does NOT reduce imbalance (i.e. the bought outcome was already the larger / equal leg)
- `STOP` -- no further fill in the window

L5 is essentially buy-only, so a `SWITCH` is never a sell; it is a change in WHICH OUTCOME IS BOUGHT. Sells in corpus per regime are in the table above.

Every conditioning feature is computed from the state *after* the current fill and *before* the next one, so no transition probability uses future information. `independent_events` beside each probability is the number of distinct window-events contributing; fills inside one window are not independent trials.

### R1 (2026-03-19 -> 2026-04-24, 386 events)

**overall**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| ALL | 15671 | 386 | 0.5967 | 0.1397 | 0.2389 | 0.0246 |

**by imbalance**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| balanced<=5% | 4130 | 289 | 0.5835 | 0.1676 | 0.2208 | 0.0281 |
| heavy 35-80% | 1860 | 325 | 0.5726 | 0.1258 | 0.2753 | 0.0263 |
| mild 5-35% | 8214 | 343 | 0.5906 | 0.1516 | 0.2346 | 0.0233 |
| one-sided>80% | 1467 | 386 | 0.6987 | 0.013 | 0.2679 | 0.0204 |

**by tte**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| 0-30s | 720 | 104 | 0.625 | 0.0764 | 0.1722 | 0.1264 |
| 120-300s | 3288 | 285 | 0.5499 | 0.1658 | 0.2561 | 0.0283 |
| 30-60s | 711 | 131 | 0.6273 | 0.1027 | 0.2152 | 0.0549 |
| 60-120s | 1343 | 189 | 0.6009 | 0.1206 | 0.2375 | 0.041 |
| >300s | 9561 | 375 | 0.6088 | 0.1412 | 0.2404 | 0.0096 |
| past-close | 48 | 16 | 0.3958 | 0.1042 | 0.1667 | 0.3333 |

**by favored**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| favored | 6836 | 373 | 0.568 | 0.164 | 0.2387 | 0.0293 |
| underdog | 8835 | 371 | 0.6189 | 0.121 | 0.239 | 0.0211 |

**by match_state**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| has_matched | 14365 | 360 | 0.5869 | 0.1525 | 0.2356 | 0.0251 |
| no_matched | 1306 | 386 | 0.7044 | 0.0 | 0.2757 | 0.0199 |

**by step**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| step0 | 386 | 386 | 0.6995 | 0.0 | 0.2617 | 0.0389 |
| step1-4 | 1453 | 371 | 0.6435 | 0.1032 | 0.2388 | 0.0145 |
| step20+ | 9214 | 255 | 0.6008 | 0.1428 | 0.2287 | 0.0277 |
| step5-19 | 4618 | 350 | 0.5652 | 0.1568 | 0.2575 | 0.0206 |

Motifs: `{'OPEN_EXTEND_only': 11, 'OPEN_only_or_switch': 15, 'OPEN_EXTEND_COMPLETE': 359, 'repeated_compression': 334, 'OPEN_COMPLETE_only': 1}`

### R2 (2026-04-25 -> 2026-06-22, 1684 events)

**overall**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| ALL | 162174 | 1684 | 0.7225 | 0.1085 | 0.1586 | 0.0104 |

**by imbalance**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| balanced<=5% | 35682 | 1462 | 0.7142 | 0.1329 | 0.1388 | 0.0141 |
| heavy 35-80% | 19386 | 1622 | 0.7251 | 0.0857 | 0.1845 | 0.0047 |
| mild 5-35% | 98238 | 1647 | 0.7205 | 0.113 | 0.1554 | 0.011 |
| one-sided>80% | 8868 | 1684 | 0.7722 | 0.0103 | 0.2163 | 0.0012 |

**by tte**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| 0-30s | 4841 | 535 | 0.7127 | 0.0832 | 0.1039 | 0.1002 |
| 120-300s | 39455 | 1501 | 0.7254 | 0.1119 | 0.1515 | 0.0112 |
| 30-60s | 7735 | 755 | 0.7352 | 0.1017 | 0.131 | 0.0321 |
| 60-120s | 13532 | 1032 | 0.7414 | 0.1029 | 0.1355 | 0.0202 |
| >300s | 96133 | 1661 | 0.7186 | 0.1098 | 0.1699 | 0.0017 |
| past-close | 478 | 76 | 0.6381 | 0.0858 | 0.1172 | 0.159 |

**by favored**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| favored | 70787 | 1679 | 0.6953 | 0.1266 | 0.1691 | 0.009 |
| underdog | 91387 | 1680 | 0.7436 | 0.0945 | 0.1505 | 0.0115 |

**by match_state**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| has_matched | 154763 | 1674 | 0.7201 | 0.1137 | 0.1554 | 0.0108 |
| no_matched | 7411 | 1684 | 0.7728 | 0.0 | 0.2259 | 0.0013 |

**by step**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| step0 | 1684 | 1684 | 0.772 | 0.0 | 0.228 | 0.0 |
| step1-4 | 6711 | 1684 | 0.7358 | 0.063 | 0.1992 | 0.0019 |
| step20+ | 128902 | 1642 | 0.7217 | 0.1133 | 0.1523 | 0.0127 |
| step5-19 | 24877 | 1671 | 0.7199 | 0.1035 | 0.1754 | 0.0012 |

Motifs: `{'OPEN_EXTEND_COMPLETE': 1671, 'repeated_compression': 1658, 'OPEN_EXTEND_only': 10, 'OPEN_COMPLETE_only': 3}`

### R3 (2026-06-23 -> 2026-08-18, 8235 events)

**overall**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| ALL | 349166 | 8235 | 0.7217 | 0.0976 | 0.1571 | 0.0236 |

**by imbalance**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| balanced<=5% | 97303 | 3642 | 0.7369 | 0.1182 | 0.1309 | 0.014 |
| heavy 35-80% | 33355 | 4983 | 0.6889 | 0.0878 | 0.1816 | 0.0418 |
| mild 5-35% | 188911 | 5020 | 0.7262 | 0.1023 | 0.1553 | 0.0162 |
| one-sided>80% | 29597 | 8235 | 0.6805 | 0.0109 | 0.2271 | 0.0815 |

**by tte**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| 0-30s | 10039 | 2102 | 0.6251 | 0.07 | 0.0959 | 0.209 |
| 120-300s | 93189 | 6829 | 0.7112 | 0.0992 | 0.1668 | 0.0228 |
| 30-60s | 19795 | 3390 | 0.6723 | 0.0938 | 0.1429 | 0.091 |
| 60-120s | 35189 | 4870 | 0.6885 | 0.102 | 0.152 | 0.0575 |
| >300s | 190948 | 2474 | 0.7432 | 0.0979 | 0.158 | 0.0009 |
| past-close | 6 | 4 | 0.3333 | 0.0 | 0.0 | 0.6667 |

**by favored**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| favored | 165456 | 7564 | 0.7011 | 0.0831 | 0.1891 | 0.0267 |
| underdog | 183710 | 6684 | 0.7402 | 0.1107 | 0.1283 | 0.0208 |

**by match_state**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| has_matched | 323642 | 6005 | 0.7252 | 0.1053 | 0.1509 | 0.0186 |
| no_matched | 25524 | 8235 | 0.6774 | 0.0 | 0.2353 | 0.0874 |

**by step**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| step0 | 8235 | 8235 | 0.635 | 0.0 | 0.2537 | 0.1114 |
| step1-4 | 24895 | 7318 | 0.6298 | 0.068 | 0.2002 | 0.102 |
| step20+ | 265830 | 2666 | 0.7379 | 0.1039 | 0.1482 | 0.01 |
| step5-19 | 50206 | 4778 | 0.6957 | 0.0951 | 0.1671 | 0.0421 |

Motifs: `{'OPEN_EXTEND_COMPLETE': 5578, 'repeated_compression': 3926, 'OPEN_only_or_switch': 917, 'OPEN_EXTEND_only': 1313, 'OPEN_COMPLETE_only': 427}`

### R4 (2026-08-19 -> 2026-08-31, 6741 events)

**overall**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| ALL | 233806 | 6741 | 0.7096 | 0.1018 | 0.1598 | 0.0288 |

**by imbalance**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| balanced<=5% | 76270 | 2228 | 0.7421 | 0.1182 | 0.1301 | 0.0095 |
| heavy 35-80% | 18580 | 3857 | 0.6214 | 0.0992 | 0.1964 | 0.083 |
| mild 5-35% | 119146 | 3816 | 0.7201 | 0.1063 | 0.1534 | 0.0202 |
| one-sided>80% | 19810 | 6741 | 0.6037 | 0.0135 | 0.2782 | 0.1045 |

**by tte**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| 0-30s | 2616 | 780 | 0.5233 | 0.0707 | 0.1086 | 0.2974 |
| 120-300s | 61345 | 5794 | 0.6888 | 0.0986 | 0.1793 | 0.0333 |
| 30-60s | 11341 | 2394 | 0.6445 | 0.0804 | 0.1257 | 0.1495 |
| 60-120s | 22718 | 4026 | 0.6291 | 0.1094 | 0.1744 | 0.0871 |
| >300s | 135784 | 1500 | 0.7415 | 0.1044 | 0.1524 | 0.0018 |
| past-close | 2 | 2 | 0.0 | 0.0 | 0.0 | 1.0 |

**by favored**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| favored | 104964 | 6110 | 0.6731 | 0.1196 | 0.1775 | 0.0298 |
| underdog | 128842 | 5506 | 0.7393 | 0.0873 | 0.1454 | 0.028 |

**by match_state**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| has_matched | 216881 | 4938 | 0.718 | 0.1097 | 0.1495 | 0.0228 |
| no_matched | 16925 | 6741 | 0.6017 | 0.0 | 0.2918 | 0.1065 |

**by step**

| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |
|---|---|---|---|---|---|---|
| step0 | 6741 | 6741 | 0.5803 | 0.0 | 0.3075 | 0.1121 |
| step1-4 | 19432 | 5985 | 0.5376 | 0.0866 | 0.2361 | 0.1398 |
| step20+ | 182713 | 1020 | 0.7456 | 0.1062 | 0.1426 | 0.0056 |
| step5-19 | 24920 | 3269 | 0.6144 | 0.109 | 0.1863 | 0.0902 |

Motifs: `{'OPEN_EXTEND_COMPLETE': 4470, 'repeated_compression': 2727, 'OPEN_COMPLETE_only': 468, 'OPEN_EXTEND_only': 1047, 'OPEN_only_or_switch': 756}`

## Verdict: OPEN->EXTEND->COMPLETE vs compensated liquidity provision

| regime | span | independent_events | frac_OPEN_EXTEND_COMPLETE | frac_repeated_compression | completion_rate | pair_cost_median | pair_cost_frac_le_0_96 | trades_per_event | extend_per_event | complement_per_event | rebate_events_n | reward_events_n | rebate_plus_reward_usdc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R1 | ['2026-03-19', '2026-04-24'] | 386 | 0.9301 | 0.8653 | 0.93264 | 0.95915 | 0.50556 | 40.5984 | 24.2254 | 9.6995 | 4 | 0 | 327.8072 |
| R2 | ['2026-04-25', '2026-06-22'] | 1684 | 0.9923 | 0.9846 | 0.99406 | 0.96666 | 0.44624 | 96.3029 | 69.5802 | 15.2726 | 44 | 0 | 3344.1569 |
| R3 | ['2026-06-23', '2026-08-18'] | 8235 | 0.6774 | 0.4767 | 0.7292 | 0.98437 | 0.37469 | 42.4002 | 30.6006 | 6.6605 | 91 | 7 | 22577.6503 |
| R4 | ['2026-08-19', '2026-08-31'] | 6741 | 0.6631 | 0.4045 | 0.73253 | 0.97052 | 0.45443 | 34.6842 | 24.611 | 5.5425 | 22 | 11 | 40700.0373 |

**Call.** SUPPORTED. OPEN -> EXTEND -> COMPLETE -> repeated inventory compression belongs primarily to the EARLY regimes. Across the dominant change point the OPEN/EXTEND half of the motif is unchanged (P(EXTEND) ~constant) while the COMPLETE/compression half collapses: completion rate and the repeated-compression motif fall sharply, terminal residual rises several-fold, and median matched pair cost moves toward $1. Late behaviour is better described as persistent two-sided quoting that is NOT reliably compressed into matched pairs -- i.e. compensated liquidity provision -- and the first TAKER_REBATE and REWARD records in the corpus appear in the late regimes only.

Early-vs-late compression signature (event-count weighted; `[early, late]`):

- `early_regimes`: ['R1', 'R2']
- `late_regimes`: ['R3', 'R4']
- `completion_rate`: [0.98261, 0.7307]
- `pair_cost_median`: [0.96526, 0.97814]
- `pair_cost_frac_le_0_96`: [0.4573, 0.41058]
- `terminal_residual_frac`: [0.14837, 0.46455]
- `frac_repeated_compression`: [0.96232, 0.44424]
- `p_EXTEND_overall`: [0.69904, 0.71625]
- `p_COMPLEMENT_overall`: [0.17357, 0.15832]
- `taker_rebate_records`: [0, 64]
- `reward_records`: [0, 18]
- `maker_rebate_records`: [48, 49]

Discriminating battery:

- `completion_falls`: **True**
- `compression_motif_falls`: **True**
- `terminal_residual_rises`: **True**
- `pair_cost_worsens`: **True**
- `extend_rate_unchanged`: **True**
- `incentive_records_appear_only_late`: **True**

**Caveat.** Rebate/reward cashflows in /activity are sparse (183 rebate + 20 reward records in 760,817 fills). Absence of rebate records is NOT proof of absence of rebates; the endpoint may simply not surface them. Maker/taker role is UNAVAILABLE, so 'liquidity provision' is inferred from price placement and pair economics only.

## Appendix: finer significant partition

| segment | start_day | end_day | independent_events | trades_per_event | completion_rate | pair_cost_median | pair_cost_frac_le_0_96 | first_favored_frac | extend_per_event | complement_per_event | mean_fill_size |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 | 2026-03-19 | 2026-04-24 | 386 | 40.5984 | 0.93264 | 0.95915 | 0.50556 | 0.43264 | 24.2254 | 9.6995 | 14.76669 |
| F2 | 2026-04-25 | 2026-06-22 | 1684 | 96.3029 | 0.99406 | 0.96666 | 0.44624 | 0.44656 | 69.5802 | 15.2726 | 17.94791 |
| F3 | 2026-06-23 | 2026-08-18 | 8235 | 42.4002 | 0.7292 | 0.98437 | 0.37469 | 0.61651 | 30.6006 | 6.6605 | 30.27587 |
| F4 | 2026-08-19 | 2026-08-31 | 6741 | 34.6842 | 0.73253 | 0.97052 | 0.45443 | 0.63047 | 24.611 | 5.5425 | 34.13681 |

