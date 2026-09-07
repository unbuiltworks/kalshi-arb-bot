# R4 parallel research (live engine untouched)

Owner direction (chat, 2026-09-07 ~11:00Z): do nothing to the live R4
parameters mid-cohort; in parallel measure L5's concurrency/switching,
explain why the $2.50 residual cap overrides L5 EXTEND decisions, keep the
sports universe ranked, and prepare the R4 action-by-action comparison.
Everything below is read-only analysis on canonical history, the venue's
public gateway, and the probe's own log rows. No cap, ceiling, size,
concurrency, gate, selection or recorder change.

Artifacts: `l5_cap_override_study.json`, `l5_concurrency_switching.json`
(new `idle_gaps` / `flat_time`), `pmus_universe_rank.json`,
`r4_action_comparison.json` + `R4_ACTION_COMPARISON.md`, and the compact
harvest `r4_decisions.jsonl` / `r4_orders.jsonl` / `r4_sessions.jsonl` /
`r4_selections.jsonl` / `r4_settlements.json`.

## 1. Why the $2.50 residual cap overrides L5's EXTENDs (`src/l5_cap_override_study.py`)

Mechanism. R4 sizes every action at ~$2.50 and caps unmatched exposure at
$2.50, so the cap is ONE action's notional. An EXTEND adds an action to an
already-unmatched residual, so any EXTEND drawn with more than a few cents
unmatched previews over the cap and is refused. Live decisions 13-17 on
market 2 were refused with $0.0517 unmatched (after-if-filled 2.54-2.61).

L5 measured on its own history (2,216 windows; relation derived from the
inventory columns: BUY from flat = open, BUY on the residual side = extend,
BUY on the other side = complete):

| | value |
|---|---|
| EXTEND share of L5 actions | 49.4% (35,576 of 71,982) |
| window action unit (median BUY notional) p25 / p50 / p75 | $4.25 / $8.47 / $19.63 |
| unmatched AFTER an extend, in action units p25 / p50 / p90 | 5.9 / 15.1 / 48.7 |
| peak unmatched per window, in action units p50 / p75 / p90 | 3.5 / 7.7 / 38.6 |
| windows that extend beyond one unit | 78.5% |
| of those, brought back under one unit before settlement | 46.9% |
| L5 extends refused by a cap of 1 / 1.5 / 2 / 3 / 4 action units | 98.4% / 96.2% / 93.1% / 87.7% / 82.8% |

By lane, refused at k = 1 / 2 / 4: BTC-15m 98.7 / 95.5 / 88.3%; BTC-5m
95.4 / 74.2 / 35.5%; ETH-5m 95.2 / 64.0 / 33.9%; ETH-15m 96.4 / 69.1 / 18.2%.
By window spend: <$25 97.1 / 48.6 / 21.4%; $25-100 95.4 / 72.0 / 35.4%;
$100-500 95.5 / 76.4 / 41.1%; >=$500 98.9 / 96.9 / 91.4%.

Live R4 (144 decisions, two markets): 54 EXTEND draws, 44 refused
(81.5%); refusals are 30.6% of all decisions; unmatched before refusal p50
$2.4509 (p10 $0.0517), after-if-filled p50 $4.963 = 1.99 action units; by
state imb1-10 35, imb10-50 9. The historical implied share at k = 1 is
0.494 x 0.984 = 48.6% of actions; live is lower only because slots are
also spent on completes and other holds.

Reading. L5's residual is normally several actions deep (median peak 3.5
units, p75 7.7). A dollar cap that admitted L5's MEDIAN window would be
~3.5 action units = ~$8.75 at R4 size, i.e. above the $7.50 per-market
spend cap; at this action size "a cap sized to L5's envelope" is the same
thing as "no residual cap below the spend cap". The other way to make the
cap several units deep is a smaller action, which is a size change. Both
are reported for the owner's decision after three markets; neither is
applied. The refusals stay classified `safety_adaptation:unmatched_usd_cap`.

## 2. Turnover between markets (`src/l5_concurrency_study.py`, `idle_gaps` / `flat_time`)

Per lane, settlement of one episode to the first fill of the next:

| lane | transitions | entered next BEFORE this settled | p50 wait after settlement | within 60 s | within 300 s |
|---|---|---|---|---|---|
| BTC-15m | 517 | 0 | 15 s | 77.0% | 88.2% |
| BTC-5m | 955 | 0 | 60 s | 50.1% | 96.4% |
| ETH-5m | 702 | 0 | 168 s | 23.9% | 71.7% |
| ETH-15m | 27 | 0 | 463 s | 14.8% | 44.4% |

L5 never entered the next market of a lane before the current one
settled, and in its main lane re-entered within a minute three times out
of four. Flat time (capital reading, covered days only): 76% overall,
BTC-15m 77%, BTC-5m 49%. "Start the next market immediately after
settlement" is L5's own pattern; waiting 45 minutes with nothing admitted
is not.

## 3. Ranked sports universe vs the probe's pool (`src/pmus_universe_rank.py`)

Read-only, public gateway, scored with the campaign's own pair_score and
frozen model view (recorder stream features are unavailable there and
score 0; the strict-evidence admission gate is NOT evaluated). Run
2026-09-07 ~11:05Z: 50 live sports events + 336 pregame events within 6 h
= 1,021 markets; 352 read (BBO cap 400, 48 one-sided); 257 in-distribution.

The probe's pool at 09:50:52Z was 80 rows: 73 refused
`strict_evidence_market_closed` (resolved 09-06 markets), 7 thin flow. Of
the top 25 in-distribution live markets by the campaign's own scorer, 17
are absent from that pool (ITF/ATP-qualifying tennis in S1-S3, CS2, LoL,
R6 -- all rebate-eligible). That is a recorder-coverage gap, not an
absence of markets. Caveat: many of these sit in thin-evidence states
(state_n 1-7), so admission would still depend on the recorder's stream
evidence. No recorder change is made here; the diff is for the owner.

## 4. R4 action-by-action comparison (`src/l5_r4_action_comparison.py`)

Harvested from the probe's session_end log rows (deduped by decision_id /
order_id; every order paired with the decision in its action slot, 45/45).

Campaign (2 markets, both settled): 144 decisions, 45 submitted, 9 filled
(9/9 maker). Sampled relation {complete .368, extend .382, open .250} vs
L5 expected {.376, .396, .228}: L1 0.044 -- the behavioral layer is
faithful. Acted excluding holds {complete .711, extend .022, open .267}:
L1 0.748 -- the executable layer is not, and the reason is itemised:
holds by refusal `unmatched_usd_cap` 44, `off_distribution_hold` 24
(market 1, the ledger bug fixed in 082e29a9), `complement_not_priceable`
21, `entry_band_left` 8, `spend_cap_reached` 1, `complete_without_residual`
1. Unfilled orders: deadline 23, post-only reject 10, repriced 3.

Completions filled 5; 1 unlocked by the 1.00 bound. Pair cost on three
bases for that one (market 2, NO 5.01 @0.51): engine 0.9992 (average of
ALL YES ever bought + 0.51, inside par); open lots 1.0191 (average of the
still-unmatched YES lots + 0.51, above par); marginal 0.98 for 0.11 @0.47
and 1.02 for 4.90 @0.51. The engine basis is what ran; the other two are
the owner's open question (#167 5568536360). Every other completion is
<= 0.98 on all three bases.

Economics: venue realized +1.9955 (market 1, POSITION_RESOLUTION LONG);
derived matched-pair cash +1.0279 (market 1) + 0.0082 (market 2, flat
account, no venue P&L row possible); rebates venue-credited $0.14 (from
the incentive audits). Size executed / L5 p50: ~0.46-0.54. Decision
cadence p50 10-30 s.

Rerun after market 3: `python3 -m src.l5_r4_action_comparison --logs <dumps>`.

The `par_band` block (OWNER 5569709706 #5) tracks every COMPLETE decision
whose L5 touch pair was above 0.98: in band (0.98-1.00, legal only under
R4) or above par and priced DOWN to par by the ceiling. Markets 1-2: 17
opportunities, all submitted (6 in band, 11 priced down to par), 1 filled
(in band, decision 63, rested 2.9 s); the 16 unfilled rested to their 30 s
deadline (p50 31.5 s), one was a post-only reject. So far the widened
ceiling is "available" 16 times and "causal" once.

## 5. Residual-cap tradeoff curve (`src/l5_cap_tradeoff_curve.py`, OWNER 5569709706 #6)

Same refusal rule as R4 (exposure-increasing fills above the cap refused,
exposure-reducing always allowed), cap in action units, each L5 window
settled at its actual outcome; incentive dollars ESTIMATED at the
published maker rate with per-fill cent rounding (the estimator gives
$0.15 on our R4 fills against $0.14 venue-credited).

L5's 2,216 windows:

| cap (action units) | actions kept | relation-mix L1 vs uncapped | pair $ | rebate $ (est) | residual P&L | total | peak unmatched p50 / p90 / max | income per $ of peak residual |
|---|---|---|---|---|---|---|---|---|
| uncapped | 100% | 0 | 21,442 | 3,072 | -18,703 | +5,811 | 43.8 / 180.8 / 465 | 0.160 |
| 1 (= R4) | 31.0% | 0.498 | 3,882 | 431 | -981 | +3,331 | 6.0 / 26.0 / 77 | 0.186 |
| 1.5 | 39.6% | 0.450 | 4,084 | 641 | -1,161 | +3,564 | 8.7 / 34.5 / 82 | 0.153 |
| 2 | 46.4% | 0.399 | 4,809 | 866 | -1,235 | +4,440 | 12.9 / 47.1 / 107 | 0.125 |
| 3 | 57.6% | 0.342 | 5,561 | 1,283 | -1,087 | +5,757 | 16.8 / 65.7 / 157 | 0.114 |
| 4 | 65.6% | 0.297 | 6,415 | 1,606 | -1,345 | +6,676 | 20.8 / 81.5 / 223 | 0.110 |

Reading. The one-unit cap has the best income per dollar of peak residual
(0.186) and the smallest worst case, at the price of keeping 31% of L5's
actions and a relation mix 0.50 (L1) away from L5's. Total dollars rise
monotonically with the cap and pass the uncapped total at 4 units,
because the residual loss falls far faster than the pair income does.
"Appropriately translated from L5" and "best risk-adjusted" are therefore
different answers: 1 unit is the risk-adjusted optimum on this history, 3
units keeps ~58% of L5's actions at ~uncapped total with a third of the
peak exposure. Nothing here is applied; the owner chooses after market 3.

Our own fills (R3L + R4, 5 markets, cap = k x $2.50): uncapped pair
+5.51, rebate est +0.43, residual -5.32 (wisc-nd -6.29, market 1 +0.97),
total +0.62, worst peak $7.49; at 1 unit kept 68%, total +1.89, worst
$2.50; 1.5 units +1.12; 2 units +0.85; 3 units and above = uncapped. Five
markets with one pure loser: a shape, not a curve.
