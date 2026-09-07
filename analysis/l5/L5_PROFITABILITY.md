# L5 profitability (canonical report)

**Invariant (OWNER 5570474405):** L5 DECIDES -> EXECUTE WELL -> RECYCLE WHEN L5 COMPLETES -> MEASURE TOTAL ECONOMICS -> SCALE WHAT WORKS (pairing is an outcome of an L5 COMPLETE decision, never a substitute for an L5 OPEN or EXTEND; every safety override classified separately; profitability judged across the full L5 sequence; see L5_INVARIANT.md)

Owner mandate #167 5569845340. `realized expectancy = matched-pair economics + venue-credited maker rebates + venue-credited liquidity rewards - residual losses - fees`. Every number below is filled/settled/venue-credited evidence or L5's own canonical history. Nothing here changes a live R4 parameter, authorizes a rung, or relabels a bound. Machine-readable: `l5_profitability.json`, `l5_economic_scoreboard.json`, `l5_cap_tradeoff_curve.json`.

## Verdict first

* **Positive realized expectancy is not established.** Across every market this account has settled (25 markets, 10 with a venue-realized number) the net is $0.5222 on $54.07 filled: pair contribution +$5.5644, venue-credited rebates +$0.42, residual losses $-5.4813, fees $0.00, liquidity rewards $0.00 (none credited). At size (R3L + R4, 5 markets, $53.26 filled) the net is $0.6135: mean $0.1227 per market with standard error $1.6824. One market (wisc-nd, $-6.29 residual) dominates; the sign of the mean is not determined.
* **Break-even (observed):** pairs are profitable before incentives below 1.00; at exactly 1.00 only the observed rebate (0.00789 per filled dollar, $0.42 credited on $53.26) makes them positive; above 1.0080 nothing observed pays. R4 stays hard-capped at 1.00.

* **Largest executable mismatch:** the $2.50 residual cap refuses L5's EXTENDs (44 of 54 live EXTEND draws refused (81.5%) live). Second: idle time between markets. Third: action size and reprice cadence (L5 reprices within seconds; we hold one price for the 30 s deadline).

* **Gates:** 6 of 7 proposed profitability gates met; G1 (realized net >= 0 over >= 5 at-size markets with a sign on the mean) is NOT met. No rung is promoted.

## A. Economic scoreboard (every R1 -> R4 market)

Coverage: 25 markets; 23 with the probe's settlement breakdown row; aec-mlb-wsh-lad-2026-09-05, asc-cfb-ntx-ind-2026-09-05-pos-39pt5 carry only the venue's realized number (their breakdown rows predate the log retention window; turnover unknown, not zero).

| cohort | markets | venue-realized | filled $ | pair $ | rebates $ (credited) | residual $ | net $ | net / filled $ | capital-hours (bound) | net / capital-hour | matched share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| R1 | 11 | 4 | 0.4406 | 0.0316 | 0.0 | -0.0622 | -0.0115 | -0.0261 | 0.394 | -0.02919 | 0.8454 |
| R2C | 6 | 2 | 0.2118 | 0.0132 | 0.0 | -0.035 | -0.0218 | -0.10293 | 0.206 | -0.10583 | 0.7917 |
| R2C2 | 3 | 2 | 0.158 | 0.0082 | 0.0 | -0.0662 | -0.058 | -0.36709 | 0.156 | -0.37179 | 0.5 |
| R3L | 3 | 1 | 34.6402 | 4.4753 | 0.28 | -6.2855 | -1.5302 | -0.04417 | 80.411 | -0.01903 | 0.7731 |
| R4 | 2 | 1 | 18.6163 | 1.0361 | 0.14 | 0.9676 | 2.1437 | 0.11515 | 14.351 | 0.14938 | 0.9528 |

Per-market rows (all terms, both capital-hour definitions, price band, shape, league, cohort provenance) are in `l5_economic_scoreboard.json`. Net basis is the venue's realized number where the venue emitted a POSITION_RESOLUTION, else the DERIVED matched contribution on a flat account, labelled per row. Rebates are the venue's own per-print credits (25 prints, $0.42, all in R3L/R4; earlier prints were below the one-cent floor). R4 market-level: aleher-tundur venue +1.9955 (pair +1.0279, residual +0.9676 on the LONG side, rebate 0.06); norgom-ollwal flat, derived +0.0082, rebate 0.08.

## B. Profitable operating envelope (fail closed)

regimes ranked by net per filled dollar; a regime is RANKABLE only with >= 5 at-size markets and >= $10 turnover, otherwise INSUFFICIENT and not ranked; se_net_per_market is the standard error of the per-market net (n-1). At-size markets: 5. 20 markets at $0.05-$0.50 per market: prints below the venue's rebate floor, turnover $0.81, net $-0.0913 -- shape evidence only, not economics at size

**by cohort**

| cohort | markets | filled $ | net $ | net / filled $ | mean net / market | s.e. | worst market | status |
|---|---|---|---|---|---|---|---|---|
| R4 | 2 | 18.6163 | 2.1437 | 0.11515 | 1.0718 | 0.9836 | 0.0882 | INSUFFICIENT |
| R1 | 11 | 0.4406 | -0.0115 | -0.0261 | -0.001 | 0.0053 | -0.048 | INSUFFICIENT |
| R3L | 3 | 34.6402 | -1.5302 | -0.04417 | -0.5101 | 2.9345 | -6.2255 | INSUFFICIENT |
| R2C | 6 | 0.2118 | -0.0218 | -0.10293 | -0.0036 | 0.0038 | -0.0174 | INSUFFICIENT |
| R2C2 | 3 | 0.158 | -0.058 | -0.36709 | -0.0193 | 0.0146 | -0.0416 | INSUFFICIENT |

**by shape**

| shape | markets | filled $ | net $ | net / filled $ | mean net / market | s.e. | worst market | status |
|---|---|---|---|---|---|---|---|---|
| total | 1 | 12.8972 | 3.5028 | 0.27159 | 3.5028 |  | 3.5028 | INSUFFICIENT |
| match_winner | 2 | 18.6163 | 2.1437 | 0.11515 | 1.0718 | 0.9836 | 0.0882 | INSUFFICIENT |
| spread | 2 | 21.743 | -5.033 | -0.23148 | -2.5165 | 3.709 | -6.2255 | INSUFFICIENT |

**by category**

| category | markets | filled $ | net $ | net / filled $ | mean net / market | s.e. | worst market | status |
|---|---|---|---|---|---|---|---|---|
| tennis | 2 | 18.6163 | 2.1437 | 0.11515 | 1.0718 | 0.9836 | 0.0882 | INSUFFICIENT |
| football | 3 | 34.6402 | -1.5302 | -0.04417 | -0.5101 | 2.9345 | -6.2255 | INSUFFICIENT |

**by price_band**

| price_band | markets | filled $ | net $ | net / filled $ | mean net / market | s.e. | worst market | status |
|---|---|---|---|---|---|---|---|---|
| p0.35-0.45 | 2 | 21.3117 | 5.5583 | 0.26081 | 2.7792 | 0.7237 | 2.0555 | INSUFFICIENT |
| p0.45-0.55 | 2 | 25.6593 | 1.2807 | 0.04991 | 0.6403 | 0.5521 | 0.0882 | INSUFFICIENT |
| p<0.35 | 1 | 6.2855 | -6.2255 | -0.99045 | -6.2255 |  | -6.2255 | INSUFFICIENT |

Rankable regimes: none. Reading: no regime meets the sample bar. The only at-size structure visible: the one market whose residual resolved against it (wisc-nd, R3L spread, $19.27 unmatched at $0.33) lost $6.29; the one whose residual resolved for it (aleher-tundur, R4) gained $0.97; the three flat markets earned their pair contribution plus rebates. That is a description of five markets, not an envelope.

## C. Selector

not a directional model; the campaign's own pair_score + frozen model view rank L5-process suitability; the read-only universe pre-rank (pmus_universe_rank.json) found 17 of the top-25 in-distribution live markets absent from the probe's pool; R4 keeps one active market.

## D. Residual-cap counterfactual curves

| cap (action units) | L5 actions kept % | pair $ | rebate $ (est) | residual $ | total $ | peak unmatched p50 | max | income / $ peak residual |
|---|---|---|---|---|---|---|---|---|
| uncapped | 100.0 | 21442.28 | 3071.80 | -18702.71 | 5811.0 | 43.75 | 464.96 | 0.1604 |
| 1.0_units | 31.0 | 3881.89 | 430.66 | -981.40 | 3331.0 | 6.0 | 76.8 | 0.1859 |
| 1.5_units | 39.6 | 4084.43 | 641.17 | -1161.11 | 3564.0 | 8.68 | 82.3 | 0.1529 |
| 2.0_units | 46.4 | 4808.88 | 866.00 | -1235.37 | 4440.0 | 12.9 | 106.95 | 0.1253 |
| 3.0_units | 57.6 | 5561.27 | 1282.86 | -1087.48 | 5757.0 | 16.81 | 156.97 | 0.1142 |
| 4.0_units | 65.6 | 6414.54 | 1605.91 | -1344.80 | 6676.0 | 20.83 | 223.25 | 0.1097 |

at $2.50 per action the per-market spend cap ($7.50) is 3 units; 1 unit maximizes income per dollar of peak residual (0.186), 3 units keeps ~58% of L5's actions at ~99% of the uncapped total with a third of the peak. Capital-hours per cap and the expected-net-per-window columns are in the curve file. Our own five at-size markets: uncapped +0.62 (worst peak $7.49), 1 unit +1.89 (worst $2.50), 1.5 units +1.12, 2 units +0.85.

## E. Turnover mechanics: L5 vs ours

1. **residual cap refuses L5's EXTENDs.** L5: EXTEND is 49.4% of L5 actions; a 1-action-unit cap refuses 98.4% of them; L5 keeps a residual of median 3.5 units peak. Ours: 44 of 54 live EXTEND draws refused (81.5%); acted extend share 0.022 vs L5 0.396. Executable lever: a cap in action units > 1 or a smaller action; both are rung parameters, owner's call.

2. **time between markets (capital idle).** L5: BTC-15m: p50 15s after settlement, 77% within 60 s, never before settlement; BTC-5m: p50 60s after settlement, 50% within 60 s, never before settlement; ETH-15m: p50 463s after settlement, 15% within 60 s, never before settlement; ETH-5m: p50 168s after settlement, 24% within 60 s, never before settlement. Ours: R4 market 2 selected ~58 min after market 1's first fill; after market 2 settled (09:46Z) no candidate admitted for > 2 h (pool 80 rows: 73 resolved 09-06 markets, 7 thin-flow) while 257 in-distribution live markets existed on the public gateway. Executable lever: recorder coverage / pool refresh (no rung parameter); universe pre-rank exists read-only.

3. **action size and reprice cadence.** L5: action unit p50 $8.47 (p25 $4.25); L5 is a fast repricer, not a long rester: 75.1% of its price clusters are one print, a same-level follow-on arrives p50 1 s / p90 7 s later, only 1.8% of follow-ons come after 30 s, and its next print at a different level comes after p50 6 s (83.5% inside 30 s) with a 2 c median step (l5_patience_study.json). Ours: $2.50 per action (0.46-0.54 of L5 p50); one price held for the 30 s deadline, 23 of 45 orders cancelled untouched, 3 reprices; live decision cadence p50 10.1, 30.1 s. Executable lever: a touch-following reprice inside the rest window (the repriced:touch_moved path exists); size is a rung parameter (owner); the 30 s deadline is a frozen rung parameter.

## F. Observed break-even table (pair cost 0.95 -> 1.01)

| pair cost | pair $ / pair | observed rebate $ / pair | published-rate rebate at 5 sh @0.50 | net after observed rebate | class | executable under R4 |
|---|---|---|---|---|---|---|
| 0.95 | 0.05 | 0.0075 | 0.008 | 0.0575 | profitable before incentives | yes |
| 0.96 | 0.04 | 0.00757 | 0.008 | 0.04757 | profitable before incentives | yes |
| 0.97 | 0.03 | 0.00765 | 0.008 | 0.03765 | profitable before incentives | yes |
| 0.98 | 0.02 | 0.00773 | 0.008 | 0.02773 | profitable before incentives | yes |
| 0.99 | 0.01 | 0.00781 | 0.008 | 0.01781 | profitable before incentives | yes |
| 1.0 | 0.0 | 0.00789 | 0.008 | 0.00789 | profitable only after the observed maker rebate | yes |
| 1.01 | -0.01 | 0.00797 | 0.008 | -0.00203 | negative after the observed rebate; would need unproven liquidity rewards of 0.002 per pair -- NOT EXECUTABLE | NO (above the 1.00 ceiling) |

pair contribution = 1 - pair cost per matched pair (cash, netted at match); rebate = venue-credited dollars per filled dollar x pair cost; liquidity rewards zero (none credited); fees zero (venue charged 0.00); R4 stays hard-capped at 1.00 -- the 1.01 row is informational, not authorized. Observed average matched pair costs by market: asc-cfb-lou-miss 0.9346 (16.54 pairs); tsc-cfb-lou-miss 0.7917 (16.29 pairs); aec-atpcq-aleher-tundur 0.8798 (8.55 pairs); aec-atpcq-norgom-ollwal 0.9992 (10.21 pairs).

## G. Smallest next rung

ESTIMATE from 5 at-size markets with one $6.29 loser; the standard error is the point: at-size mean net per market $0.1227, s.e. $1.6824, net per filled dollar 0.01152 (R4 alone 0.11515, R3L alone -0.04417).

* **repetitions at frozen R4 parameters (3 more markets)** (moves a risk parameter: no). adds sample without moving any bound; smallest rung that can raise expected dollars only if the per-market mean is positive, which 5 markets cannot establish. Worst case: -7.5.

* **action size $2.50 -> $5.00 (10 shares, toward L5 p50 $8.47), cap unchanged $2.50** (moves a risk parameter: yes). doubles turnover and rebate dollars per market; with the cap held at $2.50 the cap falls to 0.5 action units and refuses an even larger share of EXTENDs (the L5 refusal share rises above the 98.4% measured at 1 unit); the relation mix moves further from L5. Worst case: -2.5.

* **residual cap $2.50 -> $3.75 (1.5 action units), size unchanged** (moves a risk parameter: yes). on L5's history +7% total dollars for +50% worst case and a lower income-per-residual ratio; our own fills: total +1.12 vs +1.89 at 1 unit (five markets). Worst case: -3.75.

* **residual cap -> $5.00 (2 units)** (moves a risk parameter: yes). +33% total on L5's history, income per residual dollar 0.125 vs 0.186. Worst case: -5.0.

* **concurrency 1 -> 2** (moves a risk parameter: yes). no change in per-market expectancy; doubles capital at risk and capital-hours; L5 never ran two markets of one lane at once (0 of 2,201 transitions) -- last lever by the owner's order. Worst case: -5.0.

none is established: the at-size mean net per market is 0.1227 +/- 1.6824 on 5 markets; the only lever that raises expected dollars without a sign on the mean is more repetitions, and it is the only one that moves no bound. Nothing is promoted here.

## H. Profitability gates (proposed)

| gate | have | status |
|---|---|---|
| G1 realized net >= 0 over >= 5 at-size markets AND the per-market mean exceeds its standard error (a sign on the mean), venue numbers only | {"markets": 5, "net_usd": 0.6135, "mean_net_per_market": 0.1227, "se": 1.6824} | NOT met |
| G2 pair + credited rebate income >= residual losses over the rung | {"pair_plus_rebate": 5.9314, "residual": -5.3179} | met |
| G3 credited rebate per filled dollar >= 0.006 and every credit reconciled to the venue balance | {"rate": 0.00789, "balance_reconciliation": "exact 108.9985 -> 109.194 -> 111.1422"} | met |
| G4 matched share of bought shares >= 0.75 per rung | {"at_size": 0.9528, "r3l": 0.7731} | met |
| G5 no market's residual loss exceeds the per-market cap ($2.50 under R4) | {"worst_at_size_market_net": -6.2255, "r4_worst": 0.0882} | met |
| G6 zero legging, zero accounting mismatches, zero taker prints in the rung; campaign stops reported | {"taker_prints": 0, "legging": 0, "accounting_mismatches": 0, "campaign_stops": "R4: 1 (08:21Z venue 429 on a read-only preflight GET with a leg held; root-caused and bounded in 43777804)"} | met |
| G7 the rung's daily stop (-$15) never touched | {"r4_net": 2.1437} | met |

proposed gates for any promotion; G1 stays NOT met until the at-size per-market mean is positive by more than its standard error; the owner decides, nothing is promoted by this file

## I. Capacity model (conditional)

CONDITIONAL -- the owner's precondition (positive realized expectancy measured) is NOT met on 5 at-size markets; this is the model's structure with observed inputs, not a plan. Inputs: {"r4_turnover_per_market": 9.308, "r4_capital_hours_per_market_first_fill_bound": 7.175, "market_hours_per_market_ESTIMATE": 0.77, "universe_in_distribution_markets_at_11_09Z": 257, "universe_two_sided_in_band": 100, "bankroll_usd": 109.0}

| $ / day | markets / day at R4 size | or size x at 3 markets / day | sequential hours needed | feasible in 24 h | worst-case residual / day | expected net at R4 ratio (EST) | expected net at all-at-size ratio (EST) |
|---|---|---|---|---|---|---|---|
| 25 | 2.7 | 0.9 | 2.1 | True | 6.71 | 2.88 | 0.29 |
| 100 | 10.7 | 3.6 | 8.3 | True | 26.86 | 11.52 | 1.15 |
| 480 | 51.6 | 17.2 | 39.8 | False | 128.92 | 55.27 | 5.53 |

## J. opportunity_utilization, capital recycling, early ramp, next rung (OWNER 5570456323 / 5570474405)

Companion reports on this branch: `L5_OPPORTUNITY_UTILIZATION.md` (active 23% of the R4 window; 2.51 h idle is a recorder-coverage bottleneck, 0.36 h a flat-account settlement wait), `l5_capital_recycling.json` (88% of deployed dollars released at the complement in both L5 and ours; L5 turns 85.6 dollars per capital-hour, we turn 4.8, because of market duration and the settlement wait), `L5_RAMP_COMPARISON.md` (L5's first week $1,113/day at a $3.25 action; ours $53 on 2026-09-07 at $2.50), and `L5_NEXT_RUNG.md` (levers in the owner's units; the single change to test after R4 is environment selection + rotation, everything else frozen; nothing promoted).

Rerun: `python3 -m src.l5_economic_scoreboard --logs <dumps>` then `python3 -m src.l5_profitability`.
