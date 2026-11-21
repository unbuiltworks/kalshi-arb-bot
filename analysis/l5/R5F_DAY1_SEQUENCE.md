# R5F day-1 sequence ledger (R5F-RUN-2026-09-07)

Markets 16 (11 with fills, 11 settled) | decisions 314 | orders 252 submitted / 45 filled | executed L5's draw on 0.7707 of decisions | holds by refusal {'entry_band_left': 33, 'no_legal_maker_price': 20, 'spend_cap_reached': 10}
Venue realized sum over settled markets with a venue number: -8.8243 (REALIZED markets only; SETTLED_REALIZED_UNCONFIRMED rows carry no venue number).

The question on every row: at this exact state, what would L5 do next (column L5 sampled/v2), and did we do it (column did L5)? If not, the engine's own reason is on the row.

## Loss partition totals (losing markets, residual loss by category)

- faithful_l5_behavior: 0.0000
- execution_queue_failure: -3.1844
- market_selection_transfer: -1.9240
- remaining_shell_distortion: -3.2609
- counterfactual saving, fill-assumed: execution 2.7801, shell 1.6284, transfer 2.8340

## Band quantification (item 5)

- bounds: {'legal_maker_price': '0.20-0.80 (q1 MIN_PRICE/MAX_PRICE, entry_ok)', 'observation_band': '0.35-0.65 (OPEN admission; EXTEND with zero inventory)'}
- no_legal_maker_price: 20 decisions refused; L5 action refused {'complete': 20}; 71.50 contracts / 63.8095 of L5-intended orders; counterfactual delta vs actual 7.6905 (20 settled rows, 3 with the paying side read from the venue's public settlement price, UNCONFIRMED); by market {'aec-setkamecz-tomvoj-ticjir-2026-09-07': 15, 'aec-setkameua-naiole-khaser-2026-09-07': 3, 'aec-setkamemd-scovla-otgand-2026-09-07': 1, 'tsc-mlb-laa-bos-2026-09-07-7pt5': 1}
- entry_band_left: 33 decisions refused; L5 action refused {'extend': 33}; 121.34 contracts / 14.2102 of L5-intended orders; counterfactual delta vs actual -2.7402 (33 settled rows, 6 with the paying side read from the venue's public settlement price, UNCONFIRMED); by market {'aec-setkamemd-scovla-otgand-2026-09-07': 12, 'tsc-mlb-laa-bos-2026-09-07-7pt5': 9, 'aec-setkamecz-tomvoj-ticjir-2026-09-07': 6, 'aec-setkameua-naiole-khaser-2026-09-07': 3, 'atc-ekst-psz-plo-2026-09-07-draw': 3}
- refused COMPLETEs: 20 (20 above-par pairs), delta 7.6905; refused EXTENDs: 33, delta -2.7402; net 4.9503
- recommendation: REMOVE the 0.20-0.80 legal-price refusal for COMPLETE (price L5's touch wherever the venue accepts it) and keep entry_band_left only as the OPEN/zero-inventory band. Numbers: 20 COMPLETE decisions L5 drew were refused by the legal-price range (63.8095 of intended orders, 20 above-par pairs); had every one filled at L5's touch the realized outcome would have moved by 7.6905 (fill-assumed, given the venue's actual resolutions). 33 EXTEND draws were refused by entry_band_left (14.2102 intended); had they filled and been held, the outcome would have moved by -2.7402. Net 4.9503. The COMPLETE refusals are pure shell distortion of an approved L5 action; the EXTEND refusals mostly stopped buying a side the venue then settled against, so they are the part of the band that protected the account -- which is a selection fact (the price ran through the range in minutes), not an L5 fidelity gain.

| market | idx | t | band | L5 | refused order | pair if filled | paying | delta vs actual (cf) |
|---|---|---|---|---|---|---|---|---|
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 23 | 19:59:01 | no_legal_maker_price | complete/complete | complete NO 3.96@0.81 (3.2076) | 1.1407 | NO | 0.7524 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 24 | 19:59:15 | no_legal_maker_price | complete/complete | complete NO 3.86@0.81 (3.1266) | 1.1407 | NO | 0.7334 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 25 | 19:59:17 | no_legal_maker_price | complete/complete | complete NO 3.86@0.81 (3.1266) | 1.1407 | NO | 0.7334 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 26 | 19:59:19 | entry_band_left | extend/complete | extend YES 3.86@0.1600 (0.6176) |  | NO | -0.6176 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 27 | 19:59:45 | no_legal_maker_price | complete/complete | complete NO 3.69@0.87 (3.2103) | 1.2007 | NO | 0.4797 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 28 | 19:59:47 | entry_band_left | extend/complete | extend YES 3.69@0.1200 (0.4428) |  | NO | -0.4428 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 29 | 20:00:19 | no_legal_maker_price | complete/complete | complete NO 3.42@0.94 (3.2148) | 1.2707 | NO | 0.2052 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 30 | 20:00:21 | no_legal_maker_price | complete/complete | complete NO 3.42@0.94 (3.2148) | 1.2707 | NO | 0.2052 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 31 | 20:00:23 | entry_band_left | extend/complete | extend YES 3.42@0.0500 (0.1710) |  | NO | -0.1710 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 32 | 20:00:50 | no_legal_maker_price | complete/complete | complete NO 3.35@0.96 (3.2160) | 1.2907 | NO | 0.1340 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 33 | 20:00:52 | no_legal_maker_price | complete/complete | complete NO 3.35@0.96 (3.2160) | 1.2907 | NO | 0.1340 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 34 | 20:00:54 | entry_band_left | extend/complete | extend YES 3.35@0.0300 (0.1005) |  | NO | -0.1005 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 35 | 20:01:21 | no_legal_maker_price | complete/complete | complete NO 3.28@0.97 (3.1816) | 1.3007 | NO | 0.0984 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 36 | 20:01:23 | no_legal_maker_price | complete/complete | complete NO 3.28@0.97 (3.1816) | 1.3007 | NO | 0.0984 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 37 | 20:01:25 | no_legal_maker_price | complete/complete | complete NO 3.28@0.97 (3.1816) | 1.3007 | NO | 0.0984 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 38 | 20:01:27 | no_legal_maker_price | complete/complete | complete NO 3.28@0.97 (3.1816) | 1.3007 | NO | 0.0984 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 39 | 20:01:29 | no_legal_maker_price | complete/complete | complete NO 3.28@0.97 (3.1816) | 1.3007 | NO | 0.0984 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 40 | 20:01:31 | no_legal_maker_price | complete/complete | complete NO 3.28@0.97 (3.1816) | 1.3007 | NO | 0.0984 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 41 | 20:01:50 | no_legal_maker_price | complete/complete | complete NO 3.38@0.92 (3.1096) | 1.2507 | NO | 0.2704 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 42 | 20:01:52 | entry_band_left | extend/complete | extend YES 3.38@0.0400 (0.1352) |  | NO | -0.1352 |
| aec-setkamecz-tomvoj-ticjir-2026-09-07 | 43 | 20:02:23 | entry_band_left | extend/complete | extend YES 3.38@0.0400 (0.1352) |  | NO | -0.1352 |
| aec-setkamemd-scovla-otgand-2026-09-07 | 12 | 20:46:13 | entry_band_left | extend/extend | extend YES 3.77@0.1400 (0.5278) |  | NO | -0.5278 |
| aec-setkamemd-scovla-otgand-2026-09-07 | 13 | 20:46:44 | entry_band_left | extend/extend | extend YES 3.53@0.0800 (0.2824) |  | NO | -0.2824 |
| aec-setkamemd-scovla-otgand-2026-09-07 | 14 | 20:47:16 | entry_band_left | extend/extend | extend YES 4.01@0.1900 (0.7619) |  | NO | -0.7619 |
| aec-setkamemd-scovla-otgand-2026-09-07 | 15 | 20:49:18 | entry_band_left | extend/extend | extend YES 4.01@0.1900 (0.7619) |  | NO | -0.7619 |
| aec-setkamemd-scovla-otgand-2026-09-07 | 16 | 20:49:50 | entry_band_left | extend/extend | extend YES 4.01@0.1900 (0.7619) |  | NO | -0.7619 |
| aec-setkamemd-scovla-otgand-2026-09-07 | 17 | 20:50:21 | entry_band_left | extend/extend | extend YES 3.73@0.1300 (0.4849) |  | NO | -0.4849 |
| aec-setkamemd-scovla-otgand-2026-09-07 | 18 | 20:51:23 | entry_band_left | extend/extend | extend YES 3.65@0.1100 (0.4015) |  | NO | -0.4015 |
| aec-setkamemd-scovla-otgand-2026-09-07 | 19 | 20:51:55 | entry_band_left | extend/extend | extend YES 3.77@0.1400 (0.5278) |  | NO | -0.5278 |
| aec-setkamemd-scovla-otgand-2026-09-07 | 20 | 20:52:26 | entry_band_left | extend/extend | extend YES 3.86@0.1600 (0.6176) |  | NO | -0.6176 |
| aec-setkamemd-scovla-otgand-2026-09-07 | 21 | 20:52:53 | no_legal_maker_price | complete/extend | complete NO 3.96@0.81 (3.2076) | 1.1700 | NO | 0.7524 |
| aec-setkamemd-scovla-otgand-2026-09-07 | 22 | 20:52:55 | entry_band_left | extend/extend | extend YES 3.96@0.1800 (0.7128) |  | NO | -0.7128 |
| aec-setkamemd-scovla-otgand-2026-09-07 | 23 | 20:53:26 | entry_band_left | extend/extend | extend YES 3.57@0.0900 (0.3213) |  | NO | -0.3213 |
| aec-setkamemd-scovla-otgand-2026-09-07 | 24 | 20:53:58 | entry_band_left | extend/extend | extend YES 3.69@0.1200 (0.4428) |  | NO | -0.4428 |
| aec-setkameua-naiole-khaser-2026-09-07 | 6 | 20:34:18 | entry_band_left | extend/extend | extend YES 3.91@0.1700 (0.6647) |  | NO (unconfirmed) | -0.6647 |
| aec-setkameua-naiole-khaser-2026-09-07 | 7 | 20:34:24 | entry_band_left | extend/extend | extend YES 3.77@0.1400 (0.5278) |  | NO (unconfirmed) | -0.5278 |
| aec-setkameua-naiole-khaser-2026-09-07 | 8 | 20:34:56 | no_legal_maker_price | complete/extend | complete NO 3.96@0.81 (3.2076) | 1.1700 | NO (unconfirmed) | 0.7524 |
| aec-setkameua-naiole-khaser-2026-09-07 | 9 | 20:34:58 | no_legal_maker_price | complete/extend | complete NO 3.96@0.81 (3.2076) | 1.1700 | NO (unconfirmed) | 0.7524 |
| aec-setkameua-naiole-khaser-2026-09-07 | 10 | 20:35:00 | no_legal_maker_price | complete/extend | complete NO 3.96@0.81 (3.2076) | 1.1700 | NO (unconfirmed) | 0.7524 |
| aec-setkameua-naiole-khaser-2026-09-07 | 11 | 20:35:02 | entry_band_left | extend/extend | extend YES 3.96@0.1800 (0.7128) |  | NO (unconfirmed) | -0.7128 |
| atc-ekst-psz-plo-2026-09-07-draw | 13 | 19:41:11 | entry_band_left | extend/complete | extend YES 3.96@0.1800 (0.7128) |  | YES (unconfirmed) | 3.2472 |
| atc-ekst-psz-plo-2026-09-07-draw | 14 | 19:41:43 | entry_band_left | extend/complete | extend YES 3.82@0.1500 (0.5730) |  | YES (unconfirmed) | 3.2470 |
| atc-ekst-psz-plo-2026-09-07-draw | 17 | 19:43:17 | entry_band_left | extend/complete | extend YES 3.69@0.1200 (0.4428) |  | YES (unconfirmed) | 3.2472 |
| tsc-mlb-laa-bos-2026-09-07-7pt5 | 18 | 20:17:36 | entry_band_left | extend/extend | extend YES 3.65@0.1100 (0.4015) |  | NO | -0.4015 |
| tsc-mlb-laa-bos-2026-09-07-7pt5 | 19 | 20:18:07 | entry_band_left | extend/extend | extend YES 3.61@0.1000 (0.3610) |  | NO | -0.3610 |
| tsc-mlb-laa-bos-2026-09-07-7pt5 | 20 | 20:18:39 | no_legal_maker_price | complete/extend | complete NO 3.69@0.88 (3.2472) | 1.2045 | NO | 0.4428 |
| tsc-mlb-laa-bos-2026-09-07-7pt5 | 21 | 20:18:41 | entry_band_left | extend/extend | extend YES 3.69@0.1200 (0.4428) |  | NO | -0.4428 |
| tsc-mlb-laa-bos-2026-09-07-7pt5 | 22 | 20:19:08 | entry_band_left | extend/extend | extend YES 3.61@0.1000 (0.3610) |  | NO | -0.3610 |
| tsc-mlb-laa-bos-2026-09-07-7pt5 | 23 | 20:19:39 | entry_band_left | extend/extend | extend YES 3.45@0.0600 (0.2070) |  | NO | -0.2070 |
| tsc-mlb-laa-bos-2026-09-07-7pt5 | 24 | 20:20:11 | entry_band_left | extend/extend | extend YES 3.42@0.0500 (0.1710) |  | NO | -0.1710 |
| tsc-mlb-laa-bos-2026-09-07-7pt5 | 25 | 20:20:42 | entry_band_left | extend/extend | extend YES 3.36@0.0350 (0.1176) |  | NO | -0.1176 |
| tsc-mlb-laa-bos-2026-09-07-7pt5 | 26 | 20:21:09 | entry_band_left | extend/extend | extend YES 3.35@0.0300 (0.1005) |  | NO | -0.1005 |
| tsc-mlb-laa-bos-2026-09-07-7pt5 | 27 | 20:21:40 | entry_band_left | extend/extend | extend YES 3.45@0.0600 (0.2070) |  | NO | -0.2070 |

## aec-atp-fracer-aleblo-2026-09-06

- decisions 12 (coverage [0, 11], missing 0); orders 12 / filled 2; did L5's draw 1.0; holds {}; outcomes {'rejected:rejected_at_non_crossing_price': 6, 'deadline': 3, 'filled': 2, 'repriced:touch_moved:0.405': 1}; session time {'sessions': 2, 'active_s': 65.8, 'wall_s': 180.5, 'first_decision_utc': '2026-09-07T20:26:52.564647Z', 'last_session_end_utc': '2026-09-07T20:29:53.029299Z'}

| idx | t | state | L5 sampled/v2 | acted | did L5 | order | outcome | rest_s/reeval_s | touch | move to next | inv after up/dn | pair econ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 20:26:52 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.09@0.375 | repriced:touch_moved:0.405 | 4.035/2.0 | NO 0.38 (ye 0.62) | -2.0c in 0.07m | 0/0 res 0 unm 0.0000 |  |
| 1 | 20:26:56 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.23@0.405 | deadline | 6.311/2.0 | NO 0.40 (ye 0.60) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 2 | 20:27:03 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.23@0.405 | deadline | 6.518/2.0 | NO 0.40 (ye 0.60) | 0.0c in 0.12m | 0/0 res 0 unm 0.0000 |  |
| 3 | 20:27:10 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.23@0.405 | deadline | 6.006/2.0 | NO 0.40 (ye 0.60) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 4 | 20:27:16 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.23@0.405 | filled | 3.67/2.0 | NO 0.40 (ye 0.60) | -1.0c in 0.07m | 0/4.23 res -4.23 unm 1.7132 |  |
| 5 | 20:27:20 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | YES 4.23@0.590 | filled | 0.876/30.0 | YES 0.59 (ye 0.59) | -1.0c in 2.41m | 0/0 res 0 unm 0.0000 | l5_touch pair 0.9950 par>N fee_safe N |
| 6 | 20:29:45 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.38@0.420 | rejected:rejected_at_non_crossing_price | 0.891/2.0 | NO 0.42 (ye 0.58) | 0.0c in 0.02m | 0/0 res 0 unm 0.0000 |  |
| 7 | 20:29:46 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.38@0.420 | rejected:rejected_at_non_crossing_price | 0.855/2.0 | NO 0.42 (ye 0.58) | 0.0c in 0.02m | 0/0 res 0 unm 0.0000 |  |
| 8 | 20:29:47 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.38@0.420 | rejected:rejected_at_non_crossing_price | 0.293/2.0 | NO 0.42 (ye 0.58) | 0.0c in 0.01m | 0/0 res 0 unm 0.0000 |  |
| 9 | 20:29:48 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.38@0.420 | rejected:rejected_at_non_crossing_price | 0.83/2.0 | NO 0.42 (ye 0.58) | 0.0c in 0.02m | 0/0 res 0 unm 0.0000 |  |
| 10 | 20:29:49 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.38@0.420 | rejected:rejected_at_non_crossing_price | 0.861/2.0 | NO 0.42 (ye 0.58) | 0.0c in 0.02m | 0/0 res 0 unm 0.0000 |  |
| 11 | 20:29:51 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.38@0.420 | rejected:rejected_at_non_crossing_price | 0.328/2.0 | NO 0.42 (ye 0.58) |  | 0/0 res 0 unm 0.0000 |  |

- venue trades (2): 20:27:21 4.2300@0.5900; 20:27:19 4.2300@0.5950
- settlement: settled True (SETTLED_REALIZED_UNCONFIRMED, venue_market_resolved_account_flat); resolution field None; paying side NO [venue public_settlement_px=0.0000 (YES terms) read as the paying side; UNCONFIRMED -- no position-resolution row because the account was flat]; venue realized None
- derived: bought YES 4.23 @ 0.590, NO 4.23 @ 0.405; matched pairs 4.23 at pair cost 0.995 -> 0.02115; residual 0.00 -> None; fees venue_unreported
- credited rebates: not present in the settlement rows (fees_usd=venue_unreported); rebates are only ever read from the incentive audit

## aec-cs2-ntr-cs-2026-09-07

- decisions 6 (coverage [0, 5], missing 0); orders 6 / filled 0; did L5's draw 1.0; holds {}; outcomes {'deadline': 4, 'repriced:touch_moved:0.39': 1, 'rejected:post_only_cross_within_1_tick_of_lagging_ask': 1}; session time {'sessions': 1, 'active_s': 32.4, 'wall_s': 32.4, 'first_decision_utc': '2026-09-07T20:30:08.400415Z', 'last_session_end_utc': '2026-09-07T20:30:40.845559Z'}

| idx | t | state | L5 sampled/v2 | acted | did L5 | order | outcome | rest_s/reeval_s | touch | move to next | inv after up/dn | pair econ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 20:30:08 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.24@0.38 | deadline | 5.84/2.0 | YES 0.38 (ye 0.38) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 1 | 20:30:14 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.24@0.38 | deadline | 5.561/2.0 | YES 0.38 (ye 0.38) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 2 | 20:30:20 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.24@0.38 | deadline | 5.743/2.0 | YES 0.38 (ye 0.38) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 3 | 20:30:26 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.24@0.38 | deadline | 6.77/2.0 | YES 0.38 (ye 0.38) | 0.0c in 0.12m | 0/0 res 0 unm 0.0000 |  |
| 4 | 20:30:33 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.24@0.38 | repriced:touch_moved:0.39 | 3.48/2.0 | YES 0.38 (ye 0.38) | 1.0c in 0.06m | 0/0 res 0 unm 0.0000 |  |
| 5 | 20:30:37 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.32@0.39 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 1.063/2.0 | YES 0.39 (ye 0.39) |  | 0/0 res 0 unm 0.0000 |  |

- venue trades (0): 
- settlement: settled False (None, None); resolution field None; paying side None [PENDING]; venue realized None
- derived: bought YES None @ None, NO None @ None; matched pairs None at pair cost None -> None; residual None -> None; fees None
- credited rebates: not present in the settlement rows (fees_usd=None); rebates are only ever read from the incentive audit

## aec-mlb-az-kc-2026-09-07

- decisions 12 (coverage [0, 11], missing 0); orders 11 / filled 6; did L5's draw 0.75; holds {'spend_cap_reached': 1}; outcomes {'filled': 6, 'deadline': 3, 'repriced:touch_moved:0.440': 1, 'hold': 1, 'repriced:touch_moved:0.495': 1}; session time {'sessions': 2, 'active_s': 137.8, 'wall_s': 169.3, 'first_decision_utc': '2026-09-07T20:28:46.953728Z', 'last_session_end_utc': '2026-09-07T20:31:36.220766Z'}

| idx | t | state | L5 sampled/v2 | acted | did L5 | order | outcome | rest_s/reeval_s | touch | move to next | inv after up/dn | pair econ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 20:28:46 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.11@0.365 | filled | 5.589/2.0 | YES 0.36 (ye 0.36) | 0.0c in 0.1m | 5.11/0 res 5.11 unm 1.8652 |  |
| 1 | 20:28:52 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.11@0.365 | repriced:touch_moved:0.440 | 10.546/30.0 | YES 0.36 (ye 0.36) | 8.0c in 0.18m | 5.11/0 res 5.11 unm 1.8652 |  |
| 2 | 20:29:03 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 5.11@0.555 | filled | 21.425/30.0 | NO 0.56 (ye 0.44) | 0.0c in 0.36m; rest max_touch 0.555 min_opp_ask 0.5600 | 5.11/5.11 res 0.00 unm 0.0000 | l5_touch pair 0.9200 par>N fee_safe Y |
| 3 | 20:29:25 | p55-75c|tte>10m|flat | open/open | open | did L5 | YES 5.68@0.440 | filled | 3.001/30.0 | YES 0.44 (ye 0.44) | 0.0c in 0.06m | 10.79/5.11 res 5.68 unm 2.2975 |  |
| 4 | 20:29:28 | p25-45c|tte>10m|imb10-50 | extend/extend | complete | NOT: engine executed complete on L5 draw extend: safety_adaptation:spend_cap->complete | NO 5.68@0.555 | filled | 11.691/30.0 | NO 0.56 (ye 0.44) | ; rest max_touch 0.555 min_opp_ask 0.5600 | 10.79/10.79 res 0.00 unm 0.0000 | l5_touch pair 0.9595 par>N fee_safe Y |
| 5 | 20:29:40 | p55-75c|tte>10m|flat | open/open | hold:spend_cap_reached | NOT: spend_cap_reached |  | hold | /30.0 |  |  | 0/0 res 0 unm 0.0000 |  |
| 6 | 20:30:42 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.10@0.490 | deadline | 6.126/2.0 | YES 0.49 (ye 0.49) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 7 | 20:30:48 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.10@0.490 | deadline | 5.845/2.0 | YES 0.49 (ye 0.49) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 8 | 20:30:54 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.10@0.490 | deadline | 6.701/2.0 | YES 0.49 (ye 0.49) | 0.0c in 0.12m | 0/0 res 0 unm 0.0000 |  |
| 9 | 20:31:01 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.10@0.490 | repriced:touch_moved:0.495 | 1.673/2.0 | YES 0.49 (ye 0.49) | 1.0c in 0.03m | 0/0 res 0 unm 0.0000 |  |
| 10 | 20:31:03 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.05@0.495 | filled | 1.961/2.0 | YES 0.50 (ye 0.50) | 0.0c in 0.04m | 5.05/0 res 5.05 unm 2.4998 |  |
| 11 | 20:31:06 | p45-55c|tte>10m|imb10-50 | extend/extend | complete | NOT: engine executed complete on L5 draw extend: safety_adaptation:spend_cap->complete | NO 5.05@0.500 | filled | 24.582/30.0 | NO 0.50 (ye 0.50) |  | 5.05/5.05 res 0.00 unm 0.0000 | l5_touch pair 0.9950 par>N fee_safe N |

- venue trades (6): 20:31:30 5.0500@0.5000; 20:31:04 5.0500@0.4950; 20:29:40 5.6800@0.4450; 20:29:28 5.6800@0.4400; 20:29:23 5.1100@0.4450; 20:28:52 5.1100@0.3650
- settlement: settled True (SETTLED_REALIZED_UNCONFIRMED, venue_market_resolved_account_flat); resolution field None; paying side YES [venue public_settlement_px=1.0000 (YES terms) read as the paying side; UNCONFIRMED -- no position-resolution row because the account was flat]; venue realized None
- derived: bought YES 15.84 @ 0.4333396464646464646464646465, NO 15.84 @ 0.5374652777777777777777777778; matched pairs 15.84 at pair cost 0.9708049242424242424242424243 -> 0.4624499999999999999999999991; residual 0.00 -> None; fees venue_unreported
- credited rebates: not present in the settlement rows (fees_usd=venue_unreported); rebates are only ever read from the incentive audit

## aec-setkamecz-tomvoj-ticjir-2026-09-07

- decisions 44 (coverage [0, 43], missing 0); orders 23 / filled 4; did L5's draw 0.5227; holds {'no_legal_maker_price': 15, 'entry_band_left': 6}; outcomes {'hold': 21, 'deadline': 10, 'rejected:rejected_at_non_crossing_price': 6, 'filled': 4, 'repriced:touch_moved:0.49': 1, 'repriced:touch_moved:0.54': 1, 'rejected:post_only_cross_within_1_tick_of_lagging_ask': 1}; session time {'sessions': 11, 'active_s': 309.7, 'wall_s': 531.3, 'first_decision_utc': '2026-09-07T19:53:42.486670Z', 'last_session_end_utc': '2026-09-07T20:02:33.790923Z'}

| idx | t | state | L5 sampled/v2 | acted | did L5 | order | outcome | rest_s/reeval_s | touch | move to next | inv after up/dn | pair econ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 19:53:42 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.68@0.44 | deadline | 6.393/2.0 | YES 0.44 (ye 0.44) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 1 | 19:53:49 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.68@0.44 | deadline | 5.922/2.0 | YES 0.44 (ye 0.44) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 2 | 19:53:55 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.68@0.44 | deadline | 5.634/2.0 | YES 0.44 (ye 0.44) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 3 | 19:54:01 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.68@0.44 | deadline | 6.723/2.0 | YES 0.44 (ye 0.44) | 0.0c in 0.12m | 0/0 res 0 unm 0.0000 |  |
| 4 | 19:54:08 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.68@0.44 | repriced:touch_moved:0.49 | 2.512/2.0 | YES 0.44 (ye 0.44) | 5.0c in 0.05m | 0/0 res 0 unm 0.0000 |  |
| 5 | 19:54:11 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.10@0.49 | deadline | 5.846/2.0 | YES 0.49 (ye 0.49) | 4.0c in 2.18m | 0/0 res 0 unm 0.0000 |  |
| 6 | 19:56:21 | p45-55c|tte>10m|flat | open/open | open | did L5 | NO 4.80@0.47 | deadline | 5.989/2.0 | NO 0.47 (ye 0.53) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 7 | 19:56:28 | p45-55c|tte>10m|flat | open/open | open | did L5 | NO 4.80@0.47 | deadline | 5.559/2.0 | NO 0.47 (ye 0.53) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 8 | 19:56:34 | p45-55c|tte>10m|flat | open/open | open | did L5 | NO 4.80@0.47 | deadline | 5.796/2.0 | NO 0.47 (ye 0.53) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 9 | 19:56:40 | p45-55c|tte>10m|flat | open/open | open | did L5 | NO 4.80@0.47 | repriced:touch_moved:0.54 | 2.9/2.0 | NO 0.47 (ye 0.53) | -9.0c in 0.05m | 0/0 res 0 unm 0.0000 |  |
| 10 | 19:56:43 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.68@0.44 | deadline | 5.483/2.0 | YES 0.44 (ye 0.44) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 11 | 19:56:49 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.68@0.44 | filled | 2.081/2.0 | YES 0.44 (ye 0.44) | 0.0c in 0.06m | 5.68/0 res 5.68 unm 2.4992 |  |
| 12 | 19:56:53 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.68@0.44 | rejected:rejected_at_non_crossing_price | 1.914/30.0 | YES 0.44 (ye 0.44) | 0.0c in 0.04m | 5.68/0 res 5.68 unm 2.4992 |  |
| 13 | 19:56:55 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.68@0.44 | rejected:rejected_at_non_crossing_price | 0.85/30.0 | YES 0.44 (ye 0.44) | 0.0c in 0.02m | 5.68/0 res 5.68 unm 2.4992 |  |
| 14 | 19:56:56 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.68@0.44 | rejected:rejected_at_non_crossing_price | 0.828/30.0 | YES 0.44 (ye 0.44) | 0.0c in 0.02m | 5.68/0 res 5.68 unm 2.4992 |  |
| 15 | 19:56:57 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.68@0.44 | rejected:rejected_at_non_crossing_price | 0.925/30.0 | YES 0.44 (ye 0.44) | 0.0c in 0.02m | 5.68/0 res 5.68 unm 2.4992 |  |
| 16 | 19:56:58 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.68@0.44 | rejected:rejected_at_non_crossing_price | 0.815/30.0 | YES 0.44 (ye 0.44) | 0.0c in 0.02m | 5.68/0 res 5.68 unm 2.4992 |  |
| 17 | 19:56:59 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.68@0.44 | rejected:rejected_at_non_crossing_price | 1.949/30.0 | YES 0.44 (ye 0.44) | -18.0c in 0.23m | 5.68/0 res 5.68 unm 2.4992 |  |
| 18 | 19:57:13 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.39@0.26 | filled | 6.254/30.0 | YES 0.26 (ye 0.26) | 0.0c in 0.11m | 10.07/0 res 10.07 unm 3.6406 |  |
| 19 | 19:57:19 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.39@0.26 | filled | 11.617/30.0 | YES 0.26 (ye 0.26) | 0.0c in 0.2m | 14.46/0 res 14.46 unm 4.7820 |  |
| 20 | 19:57:31 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.39@0.26 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 1.883/30.0 | YES 0.26 (ye 0.26) | -4.0c in 0.54m | 14.46/0 res 14.46 unm 4.7820 |  |
| 21 | 19:58:03 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 4.11@0.78 | deadline | 30.956/30.0 | NO 0.78 (ye 0.22) | 0.0c in 0.52m; rest max_touch 0.78 min_opp_ask 0.7900 | 14.46/0 res 14.46 unm 4.7820 | l5_touch pair 1.1107 par>Y fee_safe N |
| 22 | 19:58:35 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 4.11@0.78 | filled | 25.819/30.0 | NO 0.78 (ye 0.22) | -3.0c in 0.44m; rest max_touch 0.81 min_opp_ask 0.7900 | 14.46/4.11 res 10.35 unm 3.4228 | l5_touch pair 1.1107 par>Y fee_safe N |
| 23 | 19:59:01 | p75-90c|tte>10m|imb10-50 | complete/complete | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /2.0 | NO 0.81 (ye 0.19) | 0.0c in 0.23m | 14.46/4.11 res 10.35 unm 3.4228 | l5_touch pair 1.1407 par>Y fee_safe N |
| 24 | 19:59:15 | p75-90c|tte>10m|imb10-50 | complete/complete | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /10.0 | NO 0.81 (ye 0.19) | 0.0c in 0.03m | 14.46/4.11 res 10.35 unm 3.4228 | l5_touch pair 1.1407 par>Y fee_safe N |
| 25 | 19:59:17 | p75-90c|tte>10m|imb10-50 | complete/complete | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /10.0 | NO 0.81 (ye 0.19) | -3.0c in 0.04m | 14.46/4.11 res 10.35 unm 3.4228 | l5_touch pair 1.1407 par>Y fee_safe N |
| 26 | 19:59:19 | p75-90c|tte>10m|imb10-50 | extend/complete | hold:entry_band_left | NOT: entry_band_left:bid=0.1600:q1_entry_ok=False |  | hold | /10.0 | YES 0.1600 (ye 0.1600) | -3.0c in 0.44m | 14.46/4.11 res 10.35 unm 3.4228 |  |
| 27 | 19:59:45 | p75-90c|tte>10m|imb10-50 | complete/complete | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /10.0 | NO 0.87 (ye 0.13) | -1.0c in 0.03m | 14.46/4.11 res 10.35 unm 3.4228 | l5_touch pair 1.2007 par>Y fee_safe N |
| 28 | 19:59:47 | p75-90c|tte>10m|imb10-50 | extend/complete | hold:entry_band_left | NOT: entry_band_left:bid=0.1200:q1_entry_ok=False |  | hold | /10.0 | YES 0.1200 (ye 0.1200) | -6.0c in 0.53m | 14.46/4.11 res 10.35 unm 3.4228 |  |
| 29 | 20:00:19 | p75-90c|tte>10m|imb10-50 | complete/complete | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /10.0 | NO 0.94 (ye 0.06) | 0.0c in 0.04m | 14.46/4.11 res 10.35 unm 3.4228 | l5_touch pair 1.2707 par>Y fee_safe N |
| 30 | 20:00:21 | p75-90c|tte>10m|imb10-50 | complete/complete | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /10.0 | NO 0.94 (ye 0.06) | -1.0c in 0.04m | 14.46/4.11 res 10.35 unm 3.4228 | l5_touch pair 1.2707 par>Y fee_safe N |
| 31 | 20:00:23 | p75-90c|tte>10m|imb10-50 | extend/complete | hold:entry_band_left | NOT: entry_band_left:bid=0.0500:q1_entry_ok=False |  | hold | /10.0 | YES 0.0500 (ye 0.0500) | -1.0c in 0.44m | 14.46/4.11 res 10.35 unm 3.4228 |  |
| 32 | 20:00:50 | p75-90c|tte>10m|imb10-50 | complete/complete | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /10.0 | NO 0.96 (ye 0.04) | 0.0c in 0.04m | 14.46/4.11 res 10.35 unm 3.4228 | l5_touch pair 1.2907 par>Y fee_safe N |
| 33 | 20:00:52 | p75-90c|tte>10m|imb10-50 | complete/complete | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /10.0 | NO 0.96 (ye 0.04) | -1.0c in 0.04m | 14.46/4.11 res 10.35 unm 3.4228 | l5_touch pair 1.2907 par>Y fee_safe N |
| 34 | 20:00:54 | p75-90c|tte>10m|imb10-50 | extend/complete | hold:entry_band_left | NOT: entry_band_left:bid=0.0300:q1_entry_ok=False |  | hold | /10.0 | YES 0.0300 (ye 0.0300) | 0.0c in 0.44m | 14.46/4.11 res 10.35 unm 3.4228 |  |
| 35 | 20:01:21 | p75-90c|tte>10m|imb10-50 | complete/complete | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /10.0 | NO 0.97 (ye 0.03) | 0.0c in 0.03m | 14.46/4.11 res 10.35 unm 3.4228 | l5_touch pair 1.3007 par>Y fee_safe N |
| 36 | 20:01:23 | p75-90c|tte>10m|imb10-50 | complete/complete | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /10.0 | NO 0.97 (ye 0.03) | 0.0c in 0.03m | 14.46/4.11 res 10.35 unm 3.4228 | l5_touch pair 1.3007 par>Y fee_safe N |
| 37 | 20:01:25 | p75-90c|tte>10m|imb10-50 | complete/complete | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /10.0 | NO 0.97 (ye 0.03) | 0.0c in 0.04m | 14.46/4.11 res 10.35 unm 3.4228 | l5_touch pair 1.3007 par>Y fee_safe N |
| 38 | 20:01:27 | p75-90c|tte>10m|imb10-50 | complete/complete | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /10.0 | NO 0.97 (ye 0.03) | 0.0c in 0.04m | 14.46/4.11 res 10.35 unm 3.4228 | l5_touch pair 1.3007 par>Y fee_safe N |
| 39 | 20:01:29 | p75-90c|tte>10m|imb10-50 | complete/complete | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /10.0 | NO 0.97 (ye 0.03) | 0.0c in 0.04m | 14.46/4.11 res 10.35 unm 3.4228 | l5_touch pair 1.3007 par>Y fee_safe N |
| 40 | 20:01:31 | p75-90c|tte>10m|imb10-50 | complete/complete | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /10.0 | NO 0.97 (ye 0.03) | 5.0c in 0.31m | 14.46/4.11 res 10.35 unm 3.4228 | l5_touch pair 1.3007 par>Y fee_safe N |
| 41 | 20:01:50 | p75-90c|tte>10m|imb10-50 | complete/complete | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /10.0 | NO 0.92 (ye 0.08) | -4.0c in 0.04m | 14.46/4.11 res 10.35 unm 3.4228 | l5_touch pair 1.2507 par>Y fee_safe N |
| 42 | 20:01:52 | p75-90c|tte>10m|imb10-50 | extend/complete | hold:entry_band_left | NOT: entry_band_left:bid=0.0400:q1_entry_ok=False |  | hold | /10.0 | YES 0.0400 (ye 0.0400) | 0.0c in 0.53m | 14.46/4.11 res 10.35 unm 3.4228 |  |
| 43 | 20:02:23 | p75-90c|tte>10m|imb10-50 | extend/complete | hold:entry_band_left | NOT: entry_band_left:bid=0.0400:q1_entry_ok=False |  | hold | /10.0 | YES 0.0400 (ye 0.0400) |  | 14.46/4.11 res 10.35 unm 3.4228 |  |

- venue trades (4): 19:59:00 4.1100@0.2200; 19:57:29 4.3900@0.2600; 19:57:18 4.3900@0.2600; 19:56:50 5.6800@0.4400
- settlement: settled True (REALIZED, position_resolution_activity); resolution field POSITION_RESOLUTION_SIDE_SHORT; paying side NO [derived: venue realized_usd reproduced by ledger payout NO x 1.00 - cost_basis]; venue realized -3.8778
- derived: bought YES 14.46 @ 0.3307053941908713692946058091, NO 4.11 @ 0.78; matched pairs 4.11 at pair cost 1.110705394190871369294605809 -> -0.4549991701244813278008298750; residual 10.35 -> -3.422800829875518672199170125; fees venue_unreported
- credited rebates: not present in the settlement rows (fees_usd=venue_unreported); rebates are only ever read from the incentive audit

VERDICT aec-setkamecz-tomvoj-ticjir-2026-09-07: venue realized -3.8778 = matched pairs -0.4550 (pair cost 1.110705394190871369294605809) + residual -3.4228 (10.35 YES at avg 0.3307, paying side NO). Path (* = not L5's draw): open(YES 5.68@0.44 deadline) -> open(YES 5.68@0.44 deadline) -> open(YES 5.68@0.44 deadline) -> open(YES 5.68@0.44 deadline) -> open(YES 5.68@0.44 repriced:touch_moved:0.49) -> open(YES 5.10@0.49 deadline) -> open(NO 4.80@0.47 deadline) -> open(NO 4.80@0.47 deadline) -> open(NO 4.80@0.47 deadline) -> open(NO 4.80@0.47 repriced:touch_moved:0.54) -> open(YES 5.68@0.44 deadline) -> open(YES 5.68@0.44 filled) -> extend(YES 5.68@0.44 rejected:rejected_at_non_crossing_price) -> extend(YES 5.68@0.44 rejected:rejected_at_non_crossing_price) -> extend(YES 5.68@0.44 rejected:rejected_at_non_crossing_price) -> extend(YES 5.68@0.44 rejected:rejected_at_non_crossing_price) -> extend(YES 5.68@0.44 rejected:rejected_at_non_crossing_price) -> extend(YES 5.68@0.44 rejected:rejected_at_non_crossing_price) -> extend(YES 4.39@0.26 filled) -> extend(YES 4.39@0.26 filled) -> extend(YES 4.39@0.26 rejected:post_only_cross_within_1_tick_of_lagging_ask) -> complete(NO 4.11@0.78 deadline) -> complete(NO 4.11@0.78 filled) -> hold:no_legal_maker_price x3 -> hold:entry_band_left -> hold:no_legal_maker_price -> hold:entry_band_left -> hold:no_legal_maker_price x2 -> hold:entry_band_left -> hold:no_legal_maker_price x2 -> hold:entry_band_left -> hold:no_legal_maker_price x7 -> hold:entry_band_left x2.
- faithful L5 behavior: 0.0000 of residual loss on 0.00 contracts; plus the matched-pair contribution -0.4550 at L5's own above-par target where applicable, and 2.2828 of EXTEND fills L5 asked for on the residual side.
- execution/queue failure: -1.3592 on 4.11 contracts; 1 quoted COMPLETE(s) did not fill ({'sat_at_touch_unfilled:deadline': 1}: idx 21 4.11@0.78 rested 30.956s, touch during rest 0.78, opp ask 0.7900, 27 polls); counterfactual saving had they filled at their price: 0.9042 (fill-assumed). Also 7 EXTEND(s) L5 drew on the losing side were REJECTED by the venue ({'rejected:rejected_at_non_crossing_price': 6, 'rejected:post_only_cross_within_1_tick_of_lagging_ask': 1}), which avoided 16.1366 of further loss by accident.
- market-selection transfer: 0.0000 on 0.00 contracts. Price run: yes-equivalent touch 0.44 at the first fill -> min 0.03 / last 0.04 over 5.6 min; left the 0.20-0.80 range after 2.2 min (idx 23), 21 decisions outside it, 0 off-distribution.
- remaining shell distortion: -2.0636 on 6.24 contracts refused by {'complement_not_priceable:no_legal_maker_price': 2}; counterfactual saving had the refused COMPLETE(s) filled at L5's touch: 1.1856 (fill-assumed); the band also refused EXTEND draws on the losing side worth 1.6023 of avoided loss.
- partition check: -3.4228 vs derived residual -3.4228 (reconciles=True).

## aec-setkamemd-salvea-dupale-2026-09-07

- decisions 5 (coverage [0, 4], missing 0); orders 4 / filled 4; did L5's draw 0.8; holds {'spend_cap_reached': 1}; outcomes {'filled': 4, 'hold': 1}; session time {'sessions': 1, 'active_s': 63.3, 'wall_s': 63.3, 'first_decision_utc': '2026-09-07T20:24:16.464713Z', 'last_session_end_utc': '2026-09-07T20:25:19.797242Z'}

| idx | t | state | L5 sampled/v2 | acted | did L5 | order | outcome | rest_s/reeval_s | touch | move to next | inv after up/dn | pair econ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 20:24:16 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.41@0.40 | filled | 3.004/2.0 | YES 0.40 (ye 0.40) | 2.0c in 0.06m | 5.41/0 res 5.41 unm 2.1640 |  |
| 1 | 20:24:19 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 5.41@0.58 | filled | 10.793/30.0 | NO 0.58 (ye 0.42) | -2.0c in 0.19m; rest max_touch 0.58 min_opp_ask 0.6000 | 5.41/5.41 res 0.00 unm 0.0000 | l5_touch pair 0.9800 par>N fee_safe Y |
| 2 | 20:24:31 | p55-75c|tte>10m|flat | open/open | open | did L5 | YES 5.41@0.40 | filled | 7.361/30.0 | YES 0.40 (ye 0.40) | 2.0c in 0.13m | 10.82/5.41 res 5.41 unm 2.1640 |  |
| 3 | 20:24:38 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 5.41@0.58 | filled | 10.686/30.0 | NO 0.58 (ye 0.42) | ; rest max_touch 0.60 min_opp_ask 0.6000 | 10.82/10.82 res 0.00 unm 0.0000 | l5_touch pair 0.9800 par>N fee_safe Y |
| 4 | 20:24:49 | p55-75c|tte>10m|flat | open/open | hold:spend_cap_reached | NOT: spend_cap_reached |  | hold | /30.0 |  |  | 10.82/10.82 res 0.00 unm 0.0000 |  |

- venue trades (4): 20:24:48 5.4100@0.4200; 20:24:36 5.4100@0.4000; 20:24:30 5.4100@0.4200; 20:24:18 5.4100@0.4000
- settlement: settled True (SETTLED_REALIZED_UNCONFIRMED, venue_market_resolved_account_flat); resolution field None; paying side NO [venue public_settlement_px=0.0000 (YES terms) read as the paying side; UNCONFIRMED -- no position-resolution row because the account was flat]; venue realized None
- derived: bought YES 10.82 @ 0.40, NO 10.82 @ 0.58; matched pairs 10.82 at pair cost 0.98 -> 0.2164; residual 0.00 -> None; fees venue_unreported
- credited rebates: not present in the settlement rows (fees_usd=venue_unreported); rebates are only ever read from the incentive audit

## aec-setkamemd-scovla-otgand-2026-09-07

- decisions 25 (coverage [0, 24], missing 0); orders 12 / filled 1; did L5's draw 0.48; holds {'entry_band_left': 12, 'no_legal_maker_price': 1}; outcomes {'hold': 13, 'rejected:post_only_cross_within_1_tick_of_lagging_ask': 8, 'deadline': 3, 'filled': 1}; session time {'sessions': 14, 'active_s': 719.8, 'wall_s': 949.6, 'first_decision_utc': '2026-09-07T20:38:38.766299Z', 'last_session_end_utc': '2026-09-07T20:54:28.370264Z'}

| idx | t | state | L5 sampled/v2 | acted | did L5 | order | outcome | rest_s/reeval_s | touch | move to next | inv after up/dn | pair econ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 20:38:38 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.07@0.36 | filled | 2.989/2.0 | YES 0.36 (ye 0.36) | 0.0c in 0.06m | 5.07/0 res 5.07 unm 1.8252 |  |
| 1 | 20:38:42 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.07@0.36 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.844/30.0 | YES 0.36 (ye 0.36) | 0.0c in 0.52m | 5.07/0 res 5.07 unm 1.8252 |  |
| 2 | 20:39:13 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.07@0.36 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.295/30.0 | YES 0.36 (ye 0.36) | 1.0c in 0.51m | 5.07/0 res 5.07 unm 1.8252 |  |
| 3 | 20:39:43 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 5.07@0.63 | deadline | 30.741/30.0 | NO 0.63 (ye 0.37) | 0.0c in 0.52m; rest max_touch 0.63 min_opp_ask 0.6400 | 5.07/0 res 5.07 unm 1.8252 | l5_touch pair 0.9900 par>N fee_safe N |
| 4 | 20:40:14 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 5.07@0.63 | deadline | 30.851/30.0 | NO 0.63 (ye 0.37) | -1.0c in 0.52m; rest max_touch 0.63 min_opp_ask 0.6400 | 5.07/0 res 5.07 unm 1.8252 | l5_touch pair 0.9900 par>N fee_safe N |
| 5 | 20:40:46 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.07@0.36 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.827/30.0 | YES 0.36 (ye 0.36) | 0.0c in 0.54m | 5.07/0 res 5.07 unm 1.8252 |  |
| 6 | 20:41:18 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.07@0.36 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 1.883/30.0 | YES 0.36 (ye 0.36) | 1.0c in 0.54m | 5.07/0 res 5.07 unm 1.8252 |  |
| 7 | 20:41:50 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 5.07@0.63 | deadline | 30.774/30.0 | NO 0.63 (ye 0.37) | -1.0c in 0.52m; rest max_touch 0.63 min_opp_ask 0.6400 | 5.07/0 res 5.07 unm 1.8252 | l5_touch pair 0.9900 par>N fee_safe N |
| 8 | 20:42:21 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.07@0.36 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.864/30.0 | YES 0.36 (ye 0.36) | 0.0c in 0.52m | 5.07/0 res 5.07 unm 1.8252 |  |
| 9 | 20:42:53 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.07@0.36 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.839/30.0 | YES 0.36 (ye 0.36) | 0.0c in 0.52m | 5.07/0 res 5.07 unm 1.8252 |  |
| 10 | 20:43:24 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.07@0.36 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.35/30.0 | YES 0.36 (ye 0.36) | 0.0c in 0.51m | 5.07/0 res 5.07 unm 1.8252 |  |
| 11 | 20:43:54 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.07@0.36 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.872/30.0 | YES 0.36 (ye 0.36) | -22.0c in 2.31m | 5.07/0 res 5.07 unm 1.8252 |  |
| 12 | 20:46:13 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.1400:q1_entry_ok=False |  | hold | /30.0 | YES 0.1400 (ye 0.1400) | -6.0c in 0.53m | 5.07/0 res 5.07 unm 1.8252 |  |
| 13 | 20:46:44 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.0800:q1_entry_ok=False |  | hold | /30.0 | YES 0.0800 (ye 0.0800) | 11.0c in 0.53m | 5.07/0 res 5.07 unm 1.8252 |  |
| 14 | 20:47:16 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.1900:q1_entry_ok=False |  | hold | /30.0 | YES 0.1900 (ye 0.1900) | 0.0c in 2.03m | 5.07/0 res 5.07 unm 1.8252 |  |
| 15 | 20:49:18 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.1900:q1_entry_ok=False |  | hold | /30.0 | YES 0.1900 (ye 0.1900) | 0.0c in 0.52m | 5.07/0 res 5.07 unm 1.8252 |  |
| 16 | 20:49:50 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.1900:q1_entry_ok=False |  | hold | /30.0 | YES 0.1900 (ye 0.1900) | -6.0c in 0.53m | 5.07/0 res 5.07 unm 1.8252 |  |
| 17 | 20:50:21 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.1300:q1_entry_ok=False |  | hold | /30.0 | YES 0.1300 (ye 0.1300) | -2.0c in 1.03m | 5.07/0 res 5.07 unm 1.8252 |  |
| 18 | 20:51:23 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.1100:q1_entry_ok=False |  | hold | /30.0 | YES 0.1100 (ye 0.1100) | 3.0c in 0.53m | 5.07/0 res 5.07 unm 1.8252 |  |
| 19 | 20:51:55 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.1400:q1_entry_ok=False |  | hold | /30.0 | YES 0.1400 (ye 0.1400) | 2.0c in 0.53m | 5.07/0 res 5.07 unm 1.8252 |  |
| 20 | 20:52:26 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.1600:q1_entry_ok=False |  | hold | /30.0 | YES 0.1600 (ye 0.1600) | 3.0c in 0.44m | 5.07/0 res 5.07 unm 1.8252 |  |
| 21 | 20:52:53 | p25-45c|tte>10m|imb10-50 | complete/extend | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /30.0 | NO 0.81 (ye 0.19) | -1.0c in 0.04m | 5.07/0 res 5.07 unm 1.8252 | l5_touch pair 1.1700 par>Y fee_safe N |
| 22 | 20:52:55 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.1800:q1_entry_ok=False |  | hold | /30.0 | YES 0.1800 (ye 0.1800) | -9.0c in 0.53m | 5.07/0 res 5.07 unm 1.8252 |  |
| 23 | 20:53:26 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.0900:q1_entry_ok=False |  | hold | /30.0 | YES 0.0900 (ye 0.0900) | 3.0c in 0.52m | 5.07/0 res 5.07 unm 1.8252 |  |
| 24 | 20:53:58 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.1200:q1_entry_ok=False |  | hold | /30.0 | YES 0.1200 (ye 0.1200) |  | 5.07/0 res 5.07 unm 1.8252 |  |

- venue trades (1): 20:38:40 5.0700@0.3600
- settlement: settled True (REALIZED, position_resolution_activity); resolution field POSITION_RESOLUTION_SIDE_SHORT; paying side NO [derived: venue realized_usd reproduced by ledger payout NO x 1.00 - cost_basis]; venue realized -1.8252
- derived: bought YES 5.07 @ 0.36, NO 0 @ None; matched pairs 0 at pair cost None -> 0; residual 5.07 -> -1.8252; fees venue_unreported
- credited rebates: not present in the settlement rows (fees_usd=venue_unreported); rebates are only ever read from the incentive audit

VERDICT aec-setkamemd-scovla-otgand-2026-09-07: venue realized -1.8252 = matched pairs 0.0000 (pair cost None) + residual -1.8252 (5.07 YES at avg 0.3600, paying side NO). Path (* = not L5's draw): open(YES 5.07@0.36 filled) -> extend(YES 5.07@0.36 rejected:post_only_cross_within_1_tick_of_lagging_ask) -> extend(YES 5.07@0.36 rejected:post_only_cross_within_1_tick_of_lagging_ask) -> complete(NO 5.07@0.63 deadline) -> complete(NO 5.07@0.63 deadline) -> extend(YES 5.07@0.36 rejected:post_only_cross_within_1_tick_of_lagging_ask) -> extend(YES 5.07@0.36 rejected:post_only_cross_within_1_tick_of_lagging_ask) -> complete(NO 5.07@0.63 deadline) -> extend(YES 5.07@0.36 rejected:post_only_cross_within_1_tick_of_lagging_ask) -> extend(YES 5.07@0.36 rejected:post_only_cross_within_1_tick_of_lagging_ask) -> extend(YES 5.07@0.36 rejected:post_only_cross_within_1_tick_of_lagging_ask) -> extend(YES 5.07@0.36 rejected:post_only_cross_within_1_tick_of_lagging_ask) -> hold:entry_band_left x9 -> hold:no_legal_maker_price -> hold:entry_band_left x3.
- faithful L5 behavior: 0.0000 of residual loss on 0.00 contracts; plus the matched-pair contribution 0.0000 at L5's own above-par target where applicable, and 0.0000 of EXTEND fills L5 asked for on the residual side.
- execution/queue failure: -1.8252 on 5.07 contracts; 1 quoted COMPLETE(s) did not fill ({'sat_at_touch_unfilled:deadline': 1}: idx 3 5.07@0.63 rested 30.741s, touch during rest 0.63, opp ask 0.6400, 27 polls); counterfactual saving had they filled at their price: 1.8759 (fill-assumed). Also 8 EXTEND(s) L5 drew on the losing side were REJECTED by the venue ({'rejected:post_only_cross_within_1_tick_of_lagging_ask': 8}), which avoided 14.6016 of further loss by accident.
- market-selection transfer: 0.0000 on 0.00 contracts. Price run: yes-equivalent touch 0.36 at the first fill -> min 0.08 / last 0.12 over 15.3 min; left the 0.20-0.80 range after 7.6 min (idx 12), 13 decisions outside it, 0 off-distribution.
- remaining shell distortion: 0.0000 on 0.00 contracts refused by {}; counterfactual saving had the refused COMPLETE(s) filled at L5's touch: 0.0000 (fill-assumed); the band also refused EXTEND draws on the losing side worth 6.6046 of avoided loss.
- partition check: -1.8252 vs derived residual -1.8252 (reconciles=True).

## aec-setkamemd-turmax-dupale-2026-09-07

- decisions 12 (coverage [0, 11], missing 0); orders 12 / filled 0; did L5's draw 1.0; holds {}; outcomes {'deadline': 11, 'repriced:touch_moved:0.44': 1}; session time {'sessions': 2, 'active_s': 70.2, 'wall_s': 195.1, 'first_decision_utc': '2026-09-07T19:53:02.956564Z', 'last_session_end_utc': '2026-09-07T19:56:18.072101Z'}

| idx | t | state | L5 sampled/v2 | acted | did L5 | order | outcome | rest_s/reeval_s | touch | move to next | inv after up/dn | pair econ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 19:53:02 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.62@0.42 | deadline | 5.509/2.0 | NO 0.42 (ye 0.58) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 1 | 19:53:08 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.62@0.42 | deadline | 5.88/2.0 | NO 0.42 (ye 0.58) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 2 | 19:53:15 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.62@0.42 | deadline | 5.582/2.0 | NO 0.42 (ye 0.58) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 3 | 19:53:20 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.62@0.42 | deadline | 5.715/2.0 | NO 0.42 (ye 0.58) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 4 | 19:53:26 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.62@0.42 | deadline | 5.581/2.0 | NO 0.42 (ye 0.58) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 5 | 19:53:32 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.54@0.42 | deadline | 5.863/2.0 | NO 0.42 (ye 0.58) | 5.0c in 2.19m | 0/0 res 0 unm 0.0000 |  |
| 6 | 19:55:44 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.31@0.37 | deadline | 6.35/2.0 | NO 0.37 (ye 0.63) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 7 | 19:55:50 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.31@0.37 | deadline | 5.795/2.0 | NO 0.37 (ye 0.63) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 8 | 19:55:57 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.31@0.37 | deadline | 5.545/2.0 | NO 0.37 (ye 0.63) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 9 | 19:56:02 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.31@0.37 | repriced:touch_moved:0.44 | 2.607/2.0 | NO 0.37 (ye 0.63) | -7.0c in 0.05m | 0/0 res 0 unm 0.0000 |  |
| 10 | 19:56:05 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 5@0.44 | deadline | 5.994/2.0 | NO 0.44 (ye 0.56) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 11 | 19:56:12 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 5@0.44 | deadline | 5.578/2.0 | NO 0.44 (ye 0.56) |  | 0/0 res 0 unm 0.0000 |  |

- venue trades (0): 
- settlement: settled False (None, None); resolution field None; paying side None [PENDING]; venue realized None
- derived: bought YES None @ None, NO None @ None; matched pairs None at pair cost None -> None; residual None -> None; fees None
- credited rebates: not present in the settlement rows (fees_usd=None); rebates are only ever read from the incentive audit

## aec-setkameua-kuzgre-krovit-2026-09-07

- decisions 6 (coverage [0, 5], missing 0); orders 6 / filled 0; did L5's draw 1.0; holds {}; outcomes {'rejected:rejected_at_non_crossing_price': 6}; session time {'sessions': 1, 'active_s': 7.7, 'wall_s': 7.7, 'first_decision_utc': '2026-09-07T20:57:12.201598Z', 'last_session_end_utc': '2026-09-07T20:57:19.916740Z'}

| idx | t | state | L5 sampled/v2 | acted | did L5 | order | outcome | rest_s/reeval_s | touch | move to next | inv after up/dn | pair econ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 20:57:12 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.38@0.31 | rejected:rejected_at_non_crossing_price | 0.903/2.0 | NO 0.31 (ye 0.69) | 0.0c in 0.02m | 0/0 res 0 unm 0.0000 |  |
| 1 | 20:57:13 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.38@0.31 | rejected:rejected_at_non_crossing_price | 0.887/2.0 | NO 0.31 (ye 0.69) | 0.0c in 0.02m | 0/0 res 0 unm 0.0000 |  |
| 2 | 20:57:14 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.38@0.31 | rejected:rejected_at_non_crossing_price | 0.87/2.0 | NO 0.31 (ye 0.69) | 0.0c in 0.02m | 0/0 res 0 unm 0.0000 |  |
| 3 | 20:57:15 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.38@0.31 | rejected:rejected_at_non_crossing_price | 0.319/2.0 | NO 0.31 (ye 0.69) | 0.0c in 0.01m | 0/0 res 0 unm 0.0000 |  |
| 4 | 20:57:16 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.38@0.31 | rejected:rejected_at_non_crossing_price | 0.898/2.0 | NO 0.31 (ye 0.69) | 0.0c in 0.02m | 0/0 res 0 unm 0.0000 |  |
| 5 | 20:57:17 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.38@0.31 | rejected:rejected_at_non_crossing_price | 0.406/2.0 | NO 0.31 (ye 0.69) |  | 0/0 res 0 unm 0.0000 |  |

- venue trades (0): 
- settlement: settled False (None, None); resolution field None; paying side None [PENDING]; venue realized None
- derived: bought YES None @ None, NO None @ None; matched pairs None at pair cost None -> None; residual None -> None; fees None
- credited rebates: not present in the settlement rows (fees_usd=None); rebates are only ever read from the incentive audit

## aec-setkameua-malden-pytiva-2026-09-07

- decisions 24 (coverage [0, 23], missing 0); orders 20 / filled 6; did L5's draw 0.7917; holds {'spend_cap_reached': 4}; outcomes {'deadline': 7, 'filled': 6, 'rejected:rejected_at_non_crossing_price': 6, 'hold': 4, 'repriced:touch_moved:0.41': 1}; session time {'sessions': 6, 'active_s': 245.2, 'wall_s': 236.0, 'first_decision_utc': '2026-09-07T20:25:18.125971Z', 'last_session_end_utc': '2026-09-07T20:29:14.091140Z'}

| idx | t | state | L5 sampled/v2 | acted | did L5 | order | outcome | rest_s/reeval_s | touch | move to next | inv after up/dn | pair econ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 20:25:18 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.50@0.41 | deadline | 6.348/2.0 | YES 0.41 (ye 0.41) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 1 | 20:25:24 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.50@0.41 | deadline | 6.569/2.0 | YES 0.41 (ye 0.41) | 0.0c in 0.12m | 0/0 res 0 unm 0.0000 |  |
| 2 | 20:25:31 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.50@0.41 | filled | 1.986/2.0 | YES 0.41 (ye 0.41) | 0.0c in 0.04m | 5.5/0 res 5.5 unm 2.2550 |  |
| 3 | 20:25:34 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.50@0.41 | rejected:rejected_at_non_crossing_price | 0.37/30.0 | YES 0.41 (ye 0.41) | 0.0c in 0.01m | 5.5/0 res 5.5 unm 2.2550 |  |
| 4 | 20:25:34 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.50@0.41 | rejected:rejected_at_non_crossing_price | 0.859/30.0 | YES 0.41 (ye 0.41) | 0.0c in 0.02m | 5.5/0 res 5.5 unm 2.2550 |  |
| 5 | 20:25:36 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.50@0.41 | rejected:rejected_at_non_crossing_price | 0.293/30.0 | YES 0.41 (ye 0.41) | 0.0c in 0.03m | 5.5/0 res 5.5 unm 2.2550 |  |
| 6 | 20:25:38 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.50@0.41 | rejected:rejected_at_non_crossing_price | 0.84/30.0 | YES 0.41 (ye 0.41) | 0.0c in 0.02m | 5.5/0 res 5.5 unm 2.2550 |  |
| 7 | 20:25:39 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.50@0.41 | rejected:rejected_at_non_crossing_price | 0.354/30.0 | YES 0.41 (ye 0.41) | 0.0c in 0.01m | 5.5/0 res 5.5 unm 2.2550 |  |
| 8 | 20:25:39 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.50@0.41 | rejected:rejected_at_non_crossing_price | 0.828/30.0 | YES 0.41 (ye 0.41) | 2.0c in 0.02m | 5.5/0 res 5.5 unm 2.2550 |  |
| 9 | 20:25:41 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 5.5@0.57 | filled | 13.372/30.0 | NO 0.57 (ye 0.43) | -7.0c in 0.23m; rest max_touch 0.61 min_opp_ask 0.5900 | 5.5/5.5 res 0.0 unm 0.0000 | l5_touch pair 0.9800 par>N fee_safe Y |
| 10 | 20:25:54 | p55-75c|tte>10m|flat | open/open | open | did L5 | YES 5.07@0.36 | repriced:touch_moved:0.41 | 22.65/30.0 | YES 0.36 (ye 0.36) |  | 5.5/5.5 res 0.0 unm 0.0000 |  |
| 11 | 20:26:17 | p55-75c|tte>10m|flat | open/open | hold:spend_cap_reached | NOT: spend_cap_reached |  | hold | /30.0 |  |  | 0/0 res 0 unm 0.0000 |  |
| 12 | 20:27:23 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.60@0.42 | deadline | 6.012/2.0 | YES 0.42 (ye 0.42) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 13 | 20:27:29 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.60@0.42 | deadline | 5.835/2.0 | YES 0.42 (ye 0.42) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 14 | 20:27:35 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.60@0.42 | deadline | 5.995/2.0 | YES 0.42 (ye 0.42) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 15 | 20:27:42 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.60@0.42 | deadline | 6.359/2.0 | YES 0.42 (ye 0.42) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 16 | 20:27:48 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.60@0.42 | deadline | 5.6/2.0 | YES 0.42 (ye 0.42) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 17 | 20:27:54 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.60@0.42 | filled | 1.94/2.0 | YES 0.42 (ye 0.42) | 1.0c in 0.06m | 11.1/5.5 res 5.6 unm 2.3243 |  |
| 18 | 20:27:58 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 5.6@0.57 | filled | 12.861/30.0 | NO 0.57 (ye 0.43) | ; rest max_touch 0.57 min_opp_ask 0.5800 | 11.1/11.1 res 0.0 unm 0.0000 | l5_touch pair 0.9850 par>N fee_safe N |
| 19 | 20:28:11 | p55-75c|tte>10m|flat | open/open | hold:spend_cap_reached | NOT: spend_cap_reached |  | hold | /30.0 |  |  | 0/0 res 0 unm 0.0000 |  |
| 20 | 20:28:27 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.60@0.42 | filled | 2.014/2.0 | YES 0.42 (ye 0.42) | 2.0c in 0.04m | 5.6/0 res 5.6 unm 2.3520 |  |
| 21 | 20:28:29 | p25-45c|tte>10m|imb10-50 | extend/extend | complete | NOT: engine executed complete on L5 draw extend: safety_adaptation:spend_cap->complete | NO 5.6@0.56 | filled | 13.844/30.0 | NO 0.56 (ye 0.44) | ; rest max_touch 0.56 min_opp_ask 0.5800 | 5.6/5.6 res 0.0 unm 0.0000 | l5_touch pair 0.9800 par>N fee_safe Y |
| 22 | 20:28:44 | p55-75c|tte>10m|flat | open/open | hold:spend_cap_reached | NOT: spend_cap_reached |  | hold | /30.0 |  |  | 0/0 res 0 unm 0.0000 |  |
| 23 | 20:28:45 | p25-45c|tte>10m|flat | open/open | hold:spend_cap_reached | NOT: spend_cap_reached |  | hold | /2.0 |  |  | 0/0 res 0 unm 0.0000 |  |

- venue trades (6): 20:28:42 5.6000@0.4400; 20:28:28 5.6000@0.4200; 20:28:10 5.6000@0.4300; 20:27:56 5.6000@0.4200; 20:25:52 5.5000@0.4300; 20:25:33 5.5000@0.4100
- settlement: settled True (SETTLED_REALIZED_UNCONFIRMED, venue_market_resolved_account_flat); resolution field None; paying side NO [venue public_settlement_px=0.0000 (YES terms) read as the paying side; UNCONFIRMED -- no position-resolution row because the account was flat]; venue realized None
- derived: bought YES 16.7 @ 0.4167065868263473053892215569, NO 16.7 @ 0.5666467065868263473053892216; matched pairs 16.7 at pair cost 0.9833532934131736526946107785 -> 0.2779999999999999999999999990; residual 0.0 -> None; fees venue_unreported
- credited rebates: not present in the settlement rows (fees_usd=venue_unreported); rebates are only ever read from the incentive audit

## aec-setkameua-naiole-khaser-2026-09-07

- decisions 28 (coverage [0, 27], missing 0); orders 21 / filled 5; did L5's draw 0.6786; holds {'entry_band_left': 3, 'no_legal_maker_price': 3, 'spend_cap_reached': 1}; outcomes {'deadline': 9, 'hold': 7, 'filled': 5, 'rejected:post_only_cross_within_1_tick_of_lagging_ask': 5, 'repriced:touch_moved:0.60': 1, 'rejected:rejected_at_non_crossing_price': 1}; session time {'sessions': 7, 'active_s': 345.5, 'wall_s': 420.9, 'first_decision_utc': '2026-09-07T20:31:36.998078Z', 'last_session_end_utc': '2026-09-07T20:38:37.858157Z'}

| idx | t | state | L5 sampled/v2 | acted | did L5 | order | outcome | rest_s/reeval_s | touch | move to next | inv after up/dn | pair econ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 20:31:36 | p45-55c|tte>10m|flat | open/open | open | did L5 | NO 4.80@0.47 | deadline | 6.547/2.0 | NO 0.47 (ye 0.53) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 1 | 20:31:43 | p45-55c|tte>10m|flat | open/open | open | did L5 | NO 4.80@0.47 | repriced:touch_moved:0.60 | 5.375/2.0 | NO 0.47 (ye 0.53) | -17.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 2 | 20:31:49 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.07@0.36 | deadline | 5.741/2.0 | YES 0.36 (ye 0.36) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 3 | 20:31:55 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.07@0.36 | filled | 2.97/2.0 | YES 0.36 (ye 0.36) | 0.0c in 0.06m | 5.07/0 res 5.07 unm 1.8252 |  |
| 4 | 20:31:58 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.07@0.36 | rejected:rejected_at_non_crossing_price | 0.323/30.0 | YES 0.36 (ye 0.36) | 4.0c in 0.01m | 5.07/0 res 5.07 unm 1.8252 |  |
| 5 | 20:31:59 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 5.07@0.60 | deadline | 31.69/30.0 | NO 0.60 (ye 0.40) | -23.0c in 2.31m; rest max_touch 0.72 min_opp_ask 0.6400 | 5.07/0 res 5.07 unm 1.8252 | l5_touch pair 0.9600 par>N fee_safe Y |
| 6 | 20:34:18 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.1700:q1_entry_ok=False |  | hold | /30.0 | YES 0.1700 (ye 0.1700) | -3.0c in 0.11m | 5.07/0 res 5.07 unm 1.8252 |  |
| 7 | 20:34:24 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.1400:q1_entry_ok=False |  | hold | /30.0 | YES 0.1400 (ye 0.1400) | 5.0c in 0.53m | 5.07/0 res 5.07 unm 1.8252 |  |
| 8 | 20:34:56 | p25-45c|tte>10m|imb10-50 | complete/extend | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /30.0 | NO 0.81 (ye 0.19) | 0.0c in 0.03m | 5.07/0 res 5.07 unm 1.8252 | l5_touch pair 1.1700 par>Y fee_safe N |
| 9 | 20:34:58 | p25-45c|tte>10m|imb10-50 | complete/extend | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /30.0 | NO 0.81 (ye 0.19) | 0.0c in 0.04m | 5.07/0 res 5.07 unm 1.8252 | l5_touch pair 1.1700 par>Y fee_safe N |
| 10 | 20:35:00 | p25-45c|tte>10m|imb10-50 | complete/extend | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /30.0 | NO 0.81 (ye 0.19) | -1.0c in 0.03m | 5.07/0 res 5.07 unm 1.8252 | l5_touch pair 1.1700 par>Y fee_safe N |
| 11 | 20:35:02 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.1800:q1_entry_ok=False |  | hold | /30.0 | YES 0.1800 (ye 0.1800) | 10.0c in 0.37m | 5.07/0 res 5.07 unm 1.8252 |  |
| 12 | 20:35:24 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.51@0.28 | filled | 27.872/30.0 | YES 0.28 (ye 0.28) | 2.0c in 0.47m | 9.58/0 res 9.58 unm 3.0880 |  |
| 13 | 20:35:52 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 4.51@0.70 | filled | 13.582/30.0 | NO 0.70 (ye 0.30) | -1.0c in 0.23m; rest max_touch 0.71 min_opp_ask 0.7200 | 9.58/4.51 res 5.07 unm 1.6343 | l5_touch pair 1.0223 par>Y fee_safe N |
| 14 | 20:36:06 | p55-75c|tte>10m|imb10-50 | extend/complete | complete | NOT: engine executed complete on L5 draw extend: safety_adaptation:spend_cap->complete | NO 4.51@0.71 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.299/2.0 | NO 0.71 (ye 0.29) | 0.0c in 0.04m | 9.58/4.51 res 5.07 unm 1.6343 | l5_touch pair 1.0323 par>Y fee_safe N |
| 15 | 20:36:09 | p55-75c|tte>10m|imb10-50 | complete/complete | complete | did L5 | NO 4.51@0.71 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.861/2.0 | NO 0.71 (ye 0.29) | 0.0c in 0.05m | 9.58/4.51 res 5.07 unm 1.6343 | l5_touch pair 1.0323 par>Y fee_safe N |
| 16 | 20:36:12 | p55-75c|tte>10m|imb10-50 | complete/complete | complete | did L5 | NO 4.51@0.71 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.874/2.0 | NO 0.71 (ye 0.29) | 0.0c in 0.05m | 9.58/4.51 res 5.07 unm 1.6343 | l5_touch pair 1.0323 par>Y fee_safe N |
| 17 | 20:36:15 | p55-75c|tte>10m|imb10-50 | complete/complete | complete | did L5 | NO 4.51@0.71 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.291/2.0 | NO 0.71 (ye 0.29) | 6.0c in 0.16m | 9.58/4.51 res 5.07 unm 1.6343 | l5_touch pair 1.0323 par>Y fee_safe N |
| 18 | 20:36:25 | p55-75c|tte>10m|imb1-10 | complete/complete | complete | did L5 | NO 4.92@0.65 | filled | 9.66/30.0 | NO 0.65 (ye 0.35) | 0.0c in 0.17m; rest max_touch 0.65 min_opp_ask 0.6600 | 9.58/9.43 res 0.15 unm 0.0484 | l5_touch pair 0.9723 par>N fee_safe Y |
| 19 | 20:36:35 | p55-75c|tte>10m|imb1-10 | complete/complete | complete | did L5 | NO 0.15@0.65 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.859/30.0 | NO 0.65 (ye 0.35) | 2.0c in 0.52m | 9.58/9.43 res 0.15 unm 0.0484 | l5_touch pair 0.9723 par>N fee_safe Y |
| 20 | 20:37:06 | p55-75c|tte>10m|imb1-10 | extend/complete | complete | NOT: engine executed complete on L5 draw extend: safety_adaptation:spend_cap->complete | NO 0.15@0.63 | filled | 22.57/30.0 | NO 0.63 (ye 0.37) | ; rest max_touch 0.63 min_opp_ask 0.6400 | 9.58/9.58 res 0.00 unm 0.0000 | l5_touch pair 0.9523 par>N fee_safe Y |
| 21 | 20:37:29 | p55-75c|tte>10m|flat | open/open | hold:spend_cap_reached | NOT: spend_cap_reached |  | hold | /30.0 |  |  | 0/0 res 0 unm 0.0000 |  |
| 22 | 20:37:59 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.15@0.37 | deadline | 5.566/2.0 | YES 0.37 (ye 0.37) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 23 | 20:38:05 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.15@0.37 | deadline | 5.822/2.0 | YES 0.37 (ye 0.37) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 24 | 20:38:11 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.15@0.37 | deadline | 6.353/2.0 | YES 0.37 (ye 0.37) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 25 | 20:38:18 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.15@0.37 | deadline | 5.869/2.0 | YES 0.37 (ye 0.37) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 26 | 20:38:24 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.15@0.37 | deadline | 5.827/2.0 | YES 0.37 (ye 0.37) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 27 | 20:38:30 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.15@0.37 | deadline | 6.14/2.0 | YES 0.37 (ye 0.37) |  | 0/0 res 0 unm 0.0000 |  |

- venue trades (5): 20:37:27 0.1500@0.3700; 20:36:33 4.9200@0.3500; 20:36:05 4.5100@0.3000; 20:35:51 4.5100@0.2800; 20:31:57 5.0700@0.3600
- settlement: settled True (SETTLED_REALIZED_UNCONFIRMED, venue_market_resolved_account_flat); resolution field None; paying side NO [venue public_settlement_px=0.0000 (YES terms) read as the paying side; UNCONFIRMED -- no position-resolution row because the account was flat]; venue realized None
- derived: bought YES 9.58 @ 0.3223382045929018789144050104, NO 9.58 @ 0.6732254697286012526096033403; matched pairs 9.58 at pair cost 0.9955636743215031315240083507 -> 0.04250000000000000000000000029; residual 0.00 -> None; fees venue_unreported
- credited rebates: not present in the settlement rows (fees_usd=venue_unreported); rebates are only ever read from the incentive audit

## aec-wta-anatik-darsem-2026-09-07

- decisions 14 (coverage [0, 13], missing 0); orders 13 / filled 4; did L5's draw 0.8571; holds {'spend_cap_reached': 1}; outcomes {'filled': 4, 'rejected:post_only_cross_within_1_tick_of_lagging_ask': 3, 'deadline': 3, 'repriced:touch_moved:0.38': 1, 'repriced:touch_moved:0.47': 1, 'rejected:post_only_cross_after_book_move': 1, 'hold': 1}; session time {'sessions': 3, 'active_s': 290.0, 'wall_s': 373.8, 'first_decision_utc': '2026-09-07T19:24:46.627181Z', 'last_session_end_utc': '2026-09-07T19:31:00.443472Z'}

| idx | t | state | L5 sampled/v2 | acted | did L5 | order | outcome | rest_s/reeval_s | touch | move to next | inv after up/dn | pair econ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 19:24:46 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 3.96@0.36 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.31/2.0 | NO 0.36 (ye 0.64) | 1.0c in 0.05m | 0/0 res 0 unm 0.0000 |  |
| 1 | 19:24:49 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 3.90@0.35 | deadline | 6.086/2.0 | NO 0.35 (ye 0.65) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 2 | 19:24:55 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 3.90@0.35 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.896/2.0 | NO 0.35 (ye 0.65) | 0.0c in 0.05m | 0/0 res 0 unm 0.0000 |  |
| 3 | 19:24:59 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 3.90@0.35 | filled | 5.564/2.0 | NO 0.35 (ye 0.65) | 0.0c in 0.1m | 0/3.9 res -3.9 unm 1.3650 |  |
| 4 | 19:25:05 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | NO 3.90@0.35 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.929/30.0 | NO 0.35 (ye 0.65) | -6.0c in 0.52m | 0/3.9 res -3.9 unm 1.3650 |  |
| 5 | 19:25:36 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | YES 3.9@0.59 | deadline | 30.849/30.0 | YES 0.59 (ye 0.59) | 4.0c in 0.54m; rest max_touch 0.63 min_opp_ask 0.6000 | 0/3.9 res -3.9 unm 1.3650 | l5_touch pair 0.9400 par>N fee_safe Y |
| 6 | 19:26:08 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | YES 3.9@0.63 | filled | 12.26/30.0 | YES 0.63 (ye 0.63) | 2.0c in 0.21m; rest max_touch 0.63 min_opp_ask 0.6400 | 3.9/3.9 res 0.0 unm 0.0000 | l5_touch pair 0.9800 par>N fee_safe Y |
| 7 | 19:26:21 | p55-75c|tte>10m|flat | open/open | open | did L5 | NO 3.96@0.35 | repriced:touch_moved:0.38 | 28.731/30.0 | NO 0.35 (ye 0.65) | -3.0c in 0.48m | 3.9/3.9 res 0.0 unm 0.0000 |  |
| 8 | 19:26:50 | p55-75c|tte>10m|flat | open/open | open | did L5 | NO 4.23@0.38 | repriced:touch_moved:0.47 | 30.221/30.0 | NO 0.38 (ye 0.62) | -9.0c in 0.51m | 3.9/3.9 res 0.0 unm 0.0000 |  |
| 9 | 19:27:21 | p55-75c|tte>10m|flat | open/open | open | did L5 | NO 4.80@0.47 | filled | 26.345/30.0 | NO 0.47 (ye 0.53) | -1.0c in 0.45m | 3.9/8.7 res -4.8 unm 1.9978 |  |
| 10 | 19:27:47 | p45-55c|tte>10m|imb10-50 | complete/extend | complete | did L5 | YES 4.8@0.52 | deadline | 31.364/30.0 | YES 0.52 (ye 0.52) | 3.0c in 0.53m; rest max_touch 0.55 min_opp_ask 0.5300 | 3.9/8.7 res -4.8 unm 1.9978 | l5_touch pair 0.9362 par>N fee_safe Y |
| 11 | 19:28:19 | p45-55c|tte>10m|imb10-50 | extend/extend | complete | NOT: engine executed complete on L5 draw extend: safety_adaptation:spend_cap->complete | YES 4.54@0.55 | rejected:post_only_cross_after_book_move | 0.844/30.0 | YES 0.55 (ye 0.55) | -9.0c in 2.05m | 3.9/8.7 res -4.8 unm 1.9978 | l5_touch pair 0.9662 par>N fee_safe Y |
| 12 | 19:30:22 | p45-55c|tte>10m|imb10-50 | complete/extend | complete | did L5 | YES 4.8@0.46 | filled | 7.4/30.0 | YES 0.46 (ye 0.46) | ; rest max_touch 0.46 min_opp_ask 0.4800 | 8.7/8.7 res 0.0 unm 0.0000 | l5_touch pair 0.8762 par>N fee_safe Y |
| 13 | 19:30:30 | p45-55c|tte>10m|flat | extend/open | hold:spend_cap_reached | NOT: spend_cap_reached |  | hold | /30.0 |  |  | 8.7/8.7 res 0.0 unm 0.0000 |  |

- venue trades (6): 19:30:29 4.8000@0.4600; 19:27:47 4.3600@0.5300; 19:27:37 0.4400@0.5300; 19:26:20 3.9000@0.6300; 19:25:03 0.9000@0.6500; 19:25:00 3.0000@0.6500
- settlement: settled True (SETTLED_REALIZED_UNCONFIRMED, venue_market_resolved_account_flat); resolution field None; paying side YES [venue public_settlement_px=1.0000 (YES terms) read as the paying side; UNCONFIRMED -- no position-resolution row because the account was flat]; venue realized None
- derived: bought YES 8.7 @ 0.5362068965517241379310344828, NO 8.7 @ 0.4162068965517241379310344828; matched pairs 8.7 at pair cost 0.9524137931034482758620689656 -> 0.4139999999999999999999999993; residual 0.0 -> None; fees venue_unreported
- credited rebates: not present in the settlement rows (fees_usd=venue_unreported); rebates are only ever read from the incentive audit

## atc-arg2-chi-qui-2026-09-05-chi

- decisions 6 (coverage [0, 5], missing 0); orders 6 / filled 0; did L5's draw 1.0; holds {}; outcomes {'deadline': 6}; session time {'sessions': 1, 'active_s': 41.4, 'wall_s': 41.4, 'first_decision_utc': '2026-09-07T20:57:51.618656Z', 'last_session_end_utc': '2026-09-07T20:58:33.051090Z'}

| idx | t | state | L5 sampled/v2 | acted | did L5 | order | outcome | rest_s/reeval_s | touch | move to next | inv after up/dn | pair econ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 20:57:51 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 3.96@0.36 | deadline | 5.885/2.0 | NO 0.36 (ye 0.64) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 1 | 20:57:57 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 3.96@0.36 | deadline | 6.53/2.0 | NO 0.36 (ye 0.64) | 0.0c in 0.12m | 0/0 res 0 unm 0.0000 |  |
| 2 | 20:58:04 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 3.96@0.36 | deadline | 5.852/2.0 | NO 0.36 (ye 0.64) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 3 | 20:58:10 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 3.96@0.36 | deadline | 8.766/2.0 | NO 0.36 (ye 0.64) | 0.0c in 0.15m | 0/0 res 0 unm 0.0000 |  |
| 4 | 20:58:20 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 3.96@0.36 | deadline | 6.411/2.0 | NO 0.36 (ye 0.64) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 5 | 20:58:26 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 3.96@0.36 | deadline | 5.876/2.0 | NO 0.36 (ye 0.64) |  | 0/0 res 0 unm 0.0000 |  |

- venue trades (0): 
- settlement: settled False (None, None); resolution field None; paying side None [PENDING]; venue realized None
- derived: bought YES None @ None, NO None @ None; matched pairs None at pair cost None -> None; residual None -> None; fees None
- credited rebates: not present in the settlement rows (fees_usd=None); rebates are only ever read from the incentive audit

## atc-ekst-psz-plo-2026-09-07-draw

- decisions 68 (coverage [0, 67], missing 0); orders 65 / filled 6; did L5's draw 0.9265; holds {'entry_band_left': 3, 'spend_cap_reached': 1}; outcomes {'deadline': 42, 'rejected:rejected_at_non_crossing_price': 7, 'filled': 6, 'rejected:post_only_cross_within_1_tick_of_lagging_ask': 6, 'hold': 4, 'repriced:touch_moved:0.39': 1, 'repriced:touch_moved:0.40': 1, 'repriced:touch_moved:0.22': 1}; session time {'sessions': 14, 'active_s': 760.8, 'wall_s': 1118.3, 'first_decision_utc': '2026-09-07T19:37:04.476760Z', 'last_session_end_utc': '2026-09-07T19:55:42.775254Z'}

| idx | t | state | L5 sampled/v2 | acted | did L5 | order | outcome | rest_s/reeval_s | touch | move to next | inv after up/dn | pair econ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 19:37:04 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.32@0.39 | deadline | 5.66/2.0 | YES 0.39 (ye 0.39) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 1 | 19:37:10 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.32@0.39 | deadline | 5.662/2.0 | YES 0.39 (ye 0.39) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 2 | 19:37:16 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.32@0.39 | deadline | 6.643/2.0 | YES 0.39 (ye 0.39) | 0.0c in 0.12m | 0/0 res 0 unm 0.0000 |  |
| 3 | 19:37:23 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.32@0.39 | deadline | 5.719/2.0 | YES 0.39 (ye 0.39) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 4 | 19:37:29 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.32@0.39 | deadline | 6.508/2.0 | YES 0.39 (ye 0.39) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 5 | 19:37:36 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.32@0.39 | filled | 3.037/2.0 | YES 0.39 (ye 0.39) | 0.0c in 0.08m | 5.32/0 res 5.32 unm 2.0748 |  |
| 6 | 19:37:41 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.32@0.39 | deadline | 30.737/30.0 | YES 0.39 (ye 0.39) | 1.0c in 0.52m | 5.32/0 res 5.32 unm 2.0748 |  |
| 7 | 19:38:12 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 5.32@0.60 | deadline | 30.875/30.0 | NO 0.60 (ye 0.40) | 0.0c in 0.52m; rest max_touch 0.60 min_opp_ask 0.6100 | 5.32/0 res 5.32 unm 2.0748 | l5_touch pair 0.9900 par>N fee_safe N |
| 8 | 19:38:43 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 5.32@0.60 | deadline | 31.495/30.0 | NO 0.60 (ye 0.40) | 0.0c in 0.53m; rest max_touch 0.60 min_opp_ask 0.6100 | 5.32/0 res 5.32 unm 2.0748 | l5_touch pair 0.9900 par>N fee_safe N |
| 9 | 19:39:15 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 5.15@0.60 | filled | 20.4/30.0 | NO 0.60 (ye 0.40) | -3.0c in 0.35m; rest max_touch 0.60 min_opp_ask 0.6300 | 5.32/5.15 res 0.17 unm 0.0663 | l5_touch pair 0.9900 par>N fee_safe N |
| 10 | 19:39:36 | p55-75c|tte>10m|flat | open/open | open | did L5 | YES 5.15@0.37 | repriced:touch_moved:0.39 | 2.103/30.0 | YES 0.37 (ye 0.37) | 2.0c in 0.04m | 5.32/5.15 res 0.17 unm 0.0663 |  |
| 11 | 19:39:38 | p55-75c|tte>10m|flat | open/open | open | did L5 | YES 5.32@0.39 | repriced:touch_moved:0.40 | 29.729/30.0 | YES 0.39 (ye 0.39) | 2.0c in 1.03m | 5.32/5.15 res 0.17 unm 0.0663 |  |
| 12 | 19:40:40 | p55-75c|tte>10m|imb1-10 | complete/complete | complete | did L5 | NO 0.17@0.59 | deadline | 30.942/30.0 | NO 0.59 (ye 0.41) | -23.0c in 0.52m; rest max_touch 0.77 min_opp_ask 0.6300 | 5.32/5.15 res 0.17 unm 0.0663 | l5_touch pair 0.9800 par>N fee_safe Y |
| 13 | 19:41:11 | p55-75c|tte>10m|imb1-10 | extend/complete | hold:entry_band_left | NOT: entry_band_left:bid=0.1800:q1_entry_ok=False |  | hold | /30.0 | YES 0.1800 (ye 0.1800) | -3.0c in 0.53m | 5.32/5.15 res 0.17 unm 0.0663 |  |
| 14 | 19:41:43 | p55-75c|tte>10m|imb1-10 | extend/complete | hold:entry_band_left | NOT: entry_band_left:bid=0.1500:q1_entry_ok=False |  | hold | /30.0 | YES 0.1500 (ye 0.1500) | 6.0c in 0.53m | 5.32/5.15 res 0.17 unm 0.0663 |  |
| 15 | 19:42:14 | p55-75c|tte>10m|imb1-10 | complete/complete | complete | did L5 | NO 0.17@0.79 | deadline | 31.529/30.0 | NO 0.79 (ye 0.21) | 0.0c in 0.53m; rest max_touch 0.79 min_opp_ask 0.8000 | 5.32/5.15 res 0.17 unm 0.0663 | l5_touch pair 1.1800 par>Y fee_safe N |
| 16 | 19:42:46 | p55-75c|tte>10m|imb1-10 | complete/complete | complete | did L5 | NO 0.17@0.79 | deadline | 30.751/30.0 | NO 0.79 (ye 0.21) | -9.0c in 0.52m; rest max_touch 0.79 min_opp_ask 0.8000 | 5.32/5.15 res 0.17 unm 0.0663 | l5_touch pair 1.1800 par>Y fee_safe N |
| 17 | 19:43:17 | p55-75c|tte>10m|imb1-10 | extend/complete | hold:entry_band_left | NOT: entry_band_left:bid=0.1200:q1_entry_ok=False |  | hold | /30.0 | YES 0.1200 (ye 0.1200) | 9.0c in 0.44m | 5.32/5.15 res 0.17 unm 0.0663 |  |
| 18 | 19:43:44 | p55-75c|tte>10m|imb1-10 | extend/complete | extend | did L5 | YES 4.11@0.21 | deadline | 32.22/30.0 | YES 0.21 (ye 0.21) | 1.0c in 0.54m | 5.32/5.15 res 0.17 unm 0.0663 |  |
| 19 | 19:44:16 | p55-75c|tte>10m|imb1-10 | complete/complete | complete | did L5 | NO 0.17@0.78 | deadline | 30.712/30.0 | NO 0.78 (ye 0.22) | 0.0c in 0.52m; rest max_touch 0.78 min_opp_ask 0.7900 | 5.32/5.15 res 0.17 unm 0.0663 | l5_touch pair 1.1700 par>Y fee_safe N |
| 20 | 19:44:47 | p55-75c|tte>10m|imb1-10 | complete/complete | complete | did L5 | NO 0.17@0.78 | deadline | 31.644/30.0 | NO 0.78 (ye 0.22) | -1.0c in 0.53m; rest max_touch 0.78 min_opp_ask 0.7900 | 5.32/5.15 res 0.17 unm 0.0663 | l5_touch pair 1.1700 par>Y fee_safe N |
| 21 | 19:45:19 | p55-75c|tte>10m|imb1-10 | extend/complete | extend | did L5 | YES 4.11@0.21 | repriced:touch_moved:0.22 | 24.784/30.0 | YES 0.21 (ye 0.21) | 1.0c in 0.42m | 5.32/5.15 res 0.17 unm 0.0663 |  |
| 22 | 19:45:44 | p55-75c|tte>10m|imb1-10 | extend/complete | extend | did L5 | YES 4.16@0.22 | filled | 6.258/30.0 | YES 0.22 (ye 0.22) | 5.0c in 0.11m | 9.48/5.15 res 4.33 unm 1.3657 |  |
| 23 | 19:45:50 | p10-25c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 4.16@0.73 | deadline | 6.132/2.0 | NO 0.73 (ye 0.27) | -5.0c in 0.47m; rest max_touch 0.73 min_opp_ask 0.7800 | 9.48/5.15 res 4.33 unm 1.3657 | l5_touch pair 1.0454 par>Y fee_safe N |
| 24 | 19:46:19 | p10-25c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.16@0.22 | deadline | 6.241/2.0 | YES 0.22 (ye 0.22) | 0.0c in 0.11m | 9.48/5.15 res 4.33 unm 1.3657 |  |
| 25 | 19:46:25 | p10-25c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.16@0.22 | deadline | 5.929/2.0 | YES 0.22 (ye 0.22) | 0.0c in 0.1m | 9.48/5.15 res 4.33 unm 1.3657 |  |
| 26 | 19:46:31 | p10-25c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.16@0.22 | deadline | 6.413/2.0 | YES 0.22 (ye 0.22) | 0.0c in 0.11m | 9.48/5.15 res 4.33 unm 1.3657 |  |
| 27 | 19:46:38 | p10-25c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.16@0.22 | deadline | 5.822/2.0 | YES 0.22 (ye 0.22) | 4.0c in 0.1m | 9.48/5.15 res 4.33 unm 1.3657 |  |
| 28 | 19:46:44 | p10-25c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 4.16@0.74 | deadline | 5.579/2.0 | NO 0.74 (ye 0.26) | -4.0c in 0.1m; rest max_touch 0.74 min_opp_ask 0.7800 | 9.48/5.15 res 4.33 unm 1.3657 | l5_touch pair 1.0554 par>Y fee_safe N |
| 29 | 19:46:50 | p10-25c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.16@0.22 | deadline | 6.074/2.0 | YES 0.22 (ye 0.22) | 0.0c in 0.47m | 9.48/5.15 res 4.33 unm 1.3657 |  |
| 30 | 19:47:18 | p10-25c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.16@0.22 | filled | 1.947/2.0 | YES 0.22 (ye 0.22) | 5.0c in 0.04m | 13.64/5.15 res 8.49 unm 2.4307 |  |
| 31 | 19:47:20 | p10-25c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 4.16@0.73 | deadline | 5.619/2.0 | NO 0.73 (ye 0.27) | 0.0c in 0.1m; rest max_touch 0.73 min_opp_ask 0.7800 | 13.64/5.15 res 8.49 unm 2.4307 | l5_touch pair 1.0163 par>Y fee_safe N |
| 32 | 19:47:26 | p10-25c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 4.16@0.73 | deadline | 5.646/2.0 | NO 0.73 (ye 0.27) | 0.0c in 0.1m; rest max_touch 0.73 min_opp_ask 0.7800 | 13.64/5.15 res 8.49 unm 2.4307 | l5_touch pair 1.0163 par>Y fee_safe N |
| 33 | 19:47:32 | p10-25c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 4.16@0.73 | deadline | 5.576/2.0 | NO 0.73 (ye 0.27) | -5.0c in 0.1m; rest max_touch 0.73 min_opp_ask 0.7800 | 13.64/5.15 res 8.49 unm 2.4307 | l5_touch pair 1.0163 par>Y fee_safe N |
| 34 | 19:47:38 | p10-25c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.16@0.22 | rejected:rejected_at_non_crossing_price | 0.871/2.0 | YES 0.22 (ye 0.22) | 0.0c in 0.02m | 13.64/5.15 res 8.49 unm 2.4307 |  |
| 35 | 19:47:39 | p10-25c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.16@0.22 | rejected:rejected_at_non_crossing_price | 0.425/2.0 | YES 0.22 (ye 0.22) | -1.0c in 0.12m | 13.64/5.15 res 8.49 unm 2.4307 |  |
| 36 | 19:47:46 | p10-25c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.11@0.21 | deadline | 6.49/2.0 | YES 0.21 (ye 0.21) | 0.0c in 0.11m | 13.64/5.15 res 8.49 unm 2.4307 |  |
| 37 | 19:47:53 | p10-25c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.11@0.21 | deadline | 5.632/2.0 | YES 0.21 (ye 0.21) | 0.0c in 0.1m | 13.64/5.15 res 8.49 unm 2.4307 |  |
| 38 | 19:47:59 | p10-25c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.11@0.21 | deadline | 5.615/2.0 | YES 0.21 (ye 0.21) | 0.0c in 0.1m | 13.64/5.15 res 8.49 unm 2.4307 |  |
| 39 | 19:48:05 | p10-25c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.11@0.21 | deadline | 5.937/2.0 | YES 0.21 (ye 0.21) | 1.0c in 0.1m | 13.64/5.15 res 8.49 unm 2.4307 |  |
| 40 | 19:48:11 | p10-25c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 4.11@0.78 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.849/2.0 | NO 0.78 (ye 0.22) | 0.0c in 0.05m | 13.64/5.15 res 8.49 unm 2.4307 | l5_touch pair 1.0663 par>Y fee_safe N |
| 41 | 19:48:14 | p10-25c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.16@0.22 | deadline | 6.53/2.0 | YES 0.22 (ye 0.22) | 11.0c in 0.56m | 13.64/5.15 res 8.49 unm 2.4307 |  |
| 42 | 19:48:48 | p10-25c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 4.22@0.67 | filled | 5.297/2.0 | NO 0.67 (ye 0.33) | 0.0c in 0.09m; rest max_touch 0.67 min_opp_ask 0.7700 | 13.64/9.37 res 4.27 unm 1.2225 | l5_touch pair 0.9563 par>N fee_safe Y |
| 43 | 19:48:53 | p55-75c|tte>10m|imb10-50 | complete/complete | complete | did L5 | NO 4.22@0.67 | rejected:rejected_at_non_crossing_price | 0.871/2.0 | NO 0.67 (ye 0.33) | 0.0c in 0.02m | 13.64/9.37 res 4.27 unm 1.2225 | l5_touch pair 0.9563 par>N fee_safe Y |
| 44 | 19:48:54 | p55-75c|tte>10m|imb10-50 | complete/complete | complete | did L5 | NO 4.22@0.67 | rejected:rejected_at_non_crossing_price | 1.907/2.0 | NO 0.67 (ye 0.33) | 0.0c in 0.04m | 13.64/9.37 res 4.27 unm 1.2225 | l5_touch pair 0.9563 par>N fee_safe Y |
| 45 | 19:48:57 | p55-75c|tte>10m|imb10-50 | extend/complete | complete | NOT: engine executed complete on L5 draw extend: safety_adaptation:spend_cap->complete | NO 4.22@0.67 | rejected:rejected_at_non_crossing_price | 0.842/2.0 | NO 0.67 (ye 0.33) | 0.0c in 0.02m | 13.64/9.37 res 4.27 unm 1.2225 | l5_touch pair 0.9563 par>N fee_safe Y |
| 46 | 19:48:58 | p55-75c|tte>10m|imb10-50 | complete/complete | complete | did L5 | NO 4.22@0.67 | rejected:rejected_at_non_crossing_price | 0.86/2.0 | NO 0.67 (ye 0.33) | 0.0c in 0.02m | 13.64/9.37 res 4.27 unm 1.2225 | l5_touch pair 0.9563 par>N fee_safe Y |
| 47 | 19:48:59 | p55-75c|tte>10m|imb10-50 | complete/complete | complete | did L5 | NO 4.22@0.67 | rejected:rejected_at_non_crossing_price | 0.874/2.0 | NO 0.67 (ye 0.33) | 15.0c in 0.33m | 13.64/9.37 res 4.27 unm 1.2225 | l5_touch pair 0.9563 par>N fee_safe Y |
| 48 | 19:49:19 | p55-75c|tte>10m|imb1-10 | complete/complete | complete | did L5 | NO 4.27@0.52 | filled | 20.419/30.0 | NO 0.52 (ye 0.48) | ; rest max_touch 0.52 min_opp_ask 0.6200 | 13.64/13.64 res 0.00 unm 0.0000 | l5_touch pair 0.8063 par>N fee_safe Y |
| 49 | 19:49:40 | p45-55c|tte>10m|flat | open/open | hold:spend_cap_reached | NOT: spend_cap_reached |  | hold | /30.0 |  |  | 0/0 res 0 unm 0.0000 |  |
| 50 | 19:49:54 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.55@0.45 | deadline | 6.139/2.0 | YES 0.45 (ye 0.45) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 51 | 19:50:00 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.55@0.45 | deadline | 6.557/2.0 | YES 0.45 (ye 0.45) | 0.0c in 0.12m | 0/0 res 0 unm 0.0000 |  |
| 52 | 19:50:07 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.55@0.45 | deadline | 5.637/2.0 | YES 0.45 (ye 0.45) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 53 | 19:50:13 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.55@0.45 | deadline | 5.582/2.0 | YES 0.45 (ye 0.45) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 54 | 19:50:19 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.55@0.45 | deadline | 5.65/2.0 | YES 0.45 (ye 0.45) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 55 | 19:50:25 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.55@0.45 | deadline | 5.84/2.0 | YES 0.45 (ye 0.45) | 4.0c in 2.22m | 0/0 res 0 unm 0.0000 |  |
| 56 | 19:52:38 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.10@0.49 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.85/2.0 | YES 0.49 (ye 0.49) | 0.0c in 0.05m | 0/0 res 0 unm 0.0000 |  |
| 57 | 19:52:41 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.10@0.49 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.828/2.0 | YES 0.49 (ye 0.49) | 0.0c in 0.05m | 0/0 res 0 unm 0.0000 |  |
| 58 | 19:52:44 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.10@0.49 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 1.919/2.0 | YES 0.49 (ye 0.49) | 0.0c in 0.07m | 0/0 res 0 unm 0.0000 |  |
| 59 | 19:52:49 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.10@0.49 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.848/2.0 | YES 0.49 (ye 0.49) | 0.0c in 0.05m | 0/0 res 0 unm 0.0000 |  |
| 60 | 19:52:52 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.10@0.49 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.82/2.0 | YES 0.49 (ye 0.49) | -1.0c in 0.05m | 0/0 res 0 unm 0.0000 |  |
| 61 | 19:52:55 | p45-55c|tte>10m|flat | open/open | open | did L5 | YES 5.20@0.48 | deadline | 5.588/2.0 | YES 0.48 (ye 0.48) | -11.0c in 2.17m | 0/0 res 0 unm 0.0000 |  |
| 62 | 19:55:05 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.15@0.37 | deadline | 6.437/2.0 | YES 0.37 (ye 0.37) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 63 | 19:55:12 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.15@0.37 | deadline | 5.727/2.0 | YES 0.37 (ye 0.37) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 64 | 19:55:18 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.15@0.37 | deadline | 5.715/2.0 | YES 0.37 (ye 0.37) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 65 | 19:55:24 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.15@0.37 | deadline | 5.543/2.0 | YES 0.37 (ye 0.37) | 14.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 66 | 19:55:30 | p45-55c|tte>10m|flat | open/open | open | did L5 | NO 5@0.49 | deadline | 5.803/2.0 | NO 0.49 (ye 0.51) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 67 | 19:55:36 | p45-55c|tte>10m|flat | open/open | open | did L5 | NO 5@0.49 | deadline | 5.802/2.0 | NO 0.49 (ye 0.51) |  | 0/0 res 0 unm 0.0000 |  |

- venue trades (9): 19:49:38 4.2700@0.4800; 19:48:52 4.2200@0.3300; 19:47:19 0.1600@0.2200; 19:47:19 4.0000@0.2200; 19:45:49 4.1600@0.2200; 19:39:34 5.1500@0.4000; 19:37:38 0.3200@0.3900; 19:37:38 4.0000@0.3900; 19:37:37 1.0000@0.3900
- settlement: settled True (SETTLED_REALIZED_UNCONFIRMED, venue_market_resolved_account_flat); resolution field None; paying side YES [venue public_settlement_px=1.0000 (YES terms) read as the paying side; UNCONFIRMED -- no position-resolution row because the account was flat]; venue realized None
- derived: bought YES 13.64 @ 0.2863049853372434017595307918, NO 13.64 @ 0.5966129032258064516129032258; matched pairs 13.64 at pair cost 0.8829178885630498533724340176 -> 1.597000000000000000000000000; residual 0.00 -> None; fees venue_unreported
- credited rebates: not present in the settlement rows (fees_usd=venue_unreported); rebates are only ever read from the incentive audit

## atc-sea-udi-laz-2026-09-07-udi

- decisions 18 (coverage [0, 17], missing 0); orders 17 / filled 5; did L5's draw 0.8333; holds {'spend_cap_reached': 1}; outcomes {'deadline': 9, 'filled': 5, 'rejected:post_only_cross_within_1_tick_of_lagging_ask': 3, 'hold': 1}; session time {'sessions': 3, 'active_s': 452.5, 'wall_s': 460.5, 'first_decision_utc': '2026-09-07T20:02:52.706420Z', 'last_session_end_utc': '2026-09-07T20:10:33.191099Z'}

| idx | t | state | L5 sampled/v2 | acted | did L5 | order | outcome | rest_s/reeval_s | touch | move to next | inv after up/dn | pair econ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 20:02:52 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.16@0.39 | deadline | 6.505/2.0 | NO 0.39 (ye 0.61) | 0.0c in 0.11m | 0/0 res 0 unm 0.0000 |  |
| 1 | 20:02:59 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.16@0.39 | deadline | 5.616/2.0 | NO 0.39 (ye 0.61) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 2 | 20:03:05 | p25-45c|tte>10m|flat | open/open | open | did L5 | NO 4.16@0.39 | filled | 3.005/2.0 | NO 0.39 (ye 0.61) | 0.0c in 0.06m | 0/4.16 res -4.16 unm 1.6224 |  |
| 3 | 20:03:08 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | NO 4.16@0.39 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 1.913/30.0 | NO 0.39 (ye 0.61) | 0.0c in 0.54m | 0/4.16 res -4.16 unm 1.6224 |  |
| 4 | 20:03:41 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | YES 4.09@0.61 | deadline | 31.974/30.0 | YES 0.61 (ye 0.61) | 1.0c in 0.54m; rest max_touch 0.62 min_opp_ask 0.6200 | 0/4.16 res -4.16 unm 1.6224 | l5_touch pair 1.0000 par>N fee_safe N |
| 5 | 20:04:13 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | YES 4.03@0.62 | filled | 2.994/30.0 | YES 0.62 (ye 0.62) | 0.0c in 0.08m; rest max_touch 0.62 min_opp_ask 0.6300 | 4.03/4.16 res -0.13 unm 0.0507 | l5_touch pair 1.0100 par>Y fee_safe N |
| 6 | 20:04:18 | p55-75c|tte>10m|imb1-10 | complete/complete | complete | did L5 | YES 0.13@0.62 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.846/30.0 | YES 0.62 (ye 0.62) | -1.0c in 0.52m | 4.03/4.16 res -0.13 unm 0.0507 | l5_touch pair 1.0100 par>Y fee_safe N |
| 7 | 20:04:49 | p55-75c|tte>10m|imb1-10 | complete/complete | complete | did L5 | YES 0.13@0.61 | deadline | 31.298/30.0 | YES 0.61 (ye 0.61) | 1.0c in 0.53m; rest max_touch 0.61 min_opp_ask 0.6200 | 4.03/4.16 res -0.13 unm 0.0507 | l5_touch pair 1.0000 par>N fee_safe N |
| 8 | 20:05:20 | p55-75c|tte>10m|imb1-10 | extend/complete | extend | did L5 | NO 4.09@0.38 | deadline | 31.12/30.0 | NO 0.38 (ye 0.62) | -1.0c in 0.52m | 4.03/4.16 res -0.13 unm 0.0507 |  |
| 9 | 20:05:52 | p55-75c|tte>10m|imb1-10 | complete/complete | complete | did L5 | YES 0.13@0.61 | deadline | 30.933/30.0 | YES 0.61 (ye 0.61) | 0.0c in 0.52m; rest max_touch 0.61 min_opp_ask 0.6200 | 4.03/4.16 res -0.13 unm 0.0507 | l5_touch pair 1.0000 par>N fee_safe N |
| 10 | 20:06:23 | p55-75c|tte>10m|imb1-10 | complete/complete | complete | did L5 | YES 0.13@0.61 | deadline | 31.491/30.0 | YES 0.61 (ye 0.61) | 1.0c in 0.53m; rest max_touch 0.61 min_opp_ask 0.6200 | 4.03/4.16 res -0.13 unm 0.0507 | l5_touch pair 1.0000 par>N fee_safe N |
| 11 | 20:06:55 | p55-75c|tte>10m|imb1-10 | extend/complete | extend | did L5 | NO 4.09@0.38 | deadline | 30.606/30.0 | NO 0.38 (ye 0.62) | -1.0c in 1.13m | 4.03/4.16 res -0.13 unm 0.0507 |  |
| 12 | 20:08:03 | p55-75c|tte>10m|imb1-10 | extend/complete | extend | did L5 | NO 4.16@0.39 | filled | 24.929/30.0 | NO 0.39 (ye 0.61) | -1.0c in 0.42m | 4.03/8.32 res -4.29 unm 1.6731 |  |
| 13 | 20:08:28 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | YES 4.16@0.60 | filled | 11.735/30.0 | YES 0.60 (ye 0.60) | 0.0c in 0.2m; rest max_touch 0.60 min_opp_ask 0.6100 | 8.19/8.32 res -0.13 unm 0.0507 | l5_touch pair 0.9900 par>N fee_safe N |
| 14 | 20:08:40 | p55-75c|tte>10m|imb1-10 | complete/complete | complete | did L5 | YES 0.13@0.60 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.846/30.0 | YES 0.60 (ye 0.60) | 1.0c in 0.52m | 8.19/8.32 res -0.13 unm 0.0507 | l5_touch pair 0.9900 par>N fee_safe N |
| 15 | 20:09:11 | p55-75c|tte>10m|imb1-10 | extend/complete | complete | NOT: engine executed complete on L5 draw extend: safety_adaptation:spend_cap->complete | YES 0.13@0.61 | deadline | 31.491/30.0 | YES 0.61 (ye 0.61) | 0.0c in 0.53m; rest max_touch 0.61 min_opp_ask 0.6200 | 8.19/8.32 res -0.13 unm 0.0507 | l5_touch pair 1.0000 par>N fee_safe N |
| 16 | 20:09:43 | p55-75c|tte>10m|imb1-10 | extend/complete | complete | NOT: engine executed complete on L5 draw extend: safety_adaptation:spend_cap->complete | YES 0.13@0.61 | filled | 19.35/30.0 | YES 0.61 (ye 0.61) | ; rest max_touch 0.61 min_opp_ask 0.6200 | 8.32/8.32 res 0.00 unm 0.0000 | l5_touch pair 1.0000 par>N fee_safe N |
| 17 | 20:10:03 | p55-75c|tte>10m|flat | open/open | hold:spend_cap_reached | NOT: spend_cap_reached |  | hold | /30.0 |  |  | 8.32/8.32 res 0.00 unm 0.0000 |  |

- venue trades (5): 20:10:02 0.1300@0.6100; 20:08:39 4.1600@0.6000; 20:08:26 4.1600@0.6100; 20:04:15 4.0300@0.6200; 20:03:07 4.1600@0.6100
- settlement: settled True (SETTLED_REALIZED_UNCONFIRMED, venue_market_resolved_account_flat); resolution field None; paying side NO [venue public_settlement_px=0.0000 (YES terms) read as the paying side; UNCONFIRMED -- no position-resolution row because the account was flat]; venue realized None
- derived: bought YES 8.32 @ 0.60984375, NO 8.32 @ 0.39; matched pairs 8.32 at pair cost 0.99984375 -> 0.0013000000; residual 0.00 -> None; fees venue_unreported
- credited rebates: not present in the settlement rows (fees_usd=venue_unreported); rebates are only ever read from the incentive audit

## tsc-mlb-laa-bos-2026-09-07-7pt5

- decisions 28 (coverage [0, 27], missing 0); orders 18 / filled 2; did L5's draw 0.6429; holds {'entry_band_left': 9, 'no_legal_maker_price': 1}; outcomes {'deadline': 10, 'hold': 10, 'rejected:rejected_at_non_crossing_price': 4, 'filled': 2, 'rejected:post_only_cross_within_1_tick_of_lagging_ask': 2}; session time {'sessions': 12, 'active_s': 654.5, 'wall_s': 692.8, 'first_decision_utc': '2026-09-07T20:10:38.113540Z', 'last_session_end_utc': '2026-09-07T20:22:10.907662Z'}

| idx | t | state | L5 sampled/v2 | acted | did L5 | order | outcome | rest_s/reeval_s | touch | move to next | inv after up/dn | pair econ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 20:10:38 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.2@0.375 | deadline | 6.412/2.0 | YES 0.38 (ye 0.38) | 0.0c in 0.12m | 0/0 res 0 unm 0.0000 |  |
| 1 | 20:10:45 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.2@0.375 | deadline | 5.841/2.0 | YES 0.38 (ye 0.38) | 0.0c in 0.1m | 0/0 res 0 unm 0.0000 |  |
| 2 | 20:10:51 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.2@0.375 | filled | 3.018/2.0 | YES 0.38 (ye 0.38) | 0.0c in 0.06m | 5.2/0 res 5.2 unm 1.9500 |  |
| 3 | 20:10:55 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.2@0.375 | rejected:rejected_at_non_crossing_price | 1.882/30.0 | YES 0.38 (ye 0.38) | 0.0c in 0.04m | 5.2/0 res 5.2 unm 1.9500 |  |
| 4 | 20:10:57 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.2@0.375 | rejected:rejected_at_non_crossing_price | 0.869/30.0 | YES 0.38 (ye 0.38) | 0.0c in 0.02m | 5.2/0 res 5.2 unm 1.9500 |  |
| 5 | 20:10:58 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.2@0.375 | rejected:rejected_at_non_crossing_price | 1.09/30.0 | YES 0.38 (ye 0.38) | 0.0c in 0.05m | 5.2/0 res 5.2 unm 1.9500 |  |
| 6 | 20:11:01 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 5.2@0.375 | rejected:rejected_at_non_crossing_price | 0.863/30.0 | YES 0.38 (ye 0.38) | 16.0c in 0.02m | 5.2/0 res 5.2 unm 1.9500 |  |
| 7 | 20:11:02 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 5.2@0.455 | deadline | 31.522/30.0 | NO 0.46 (ye 0.54) | -25.0c in 0.53m; rest max_touch 0.705 min_opp_ask 0.6250 | 5.2/0 res 5.2 unm 1.9500 | l5_touch pair 0.8300 par>N fee_safe Y |
| 8 | 20:11:34 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.57@0.290 | deadline | 30.796/30.0 | YES 0.29 (ye 0.29) | 0.0c in 0.52m | 5.2/0 res 5.2 unm 1.9500 |  |
| 9 | 20:12:05 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.57@0.290 | deadline | 31.746/30.0 | YES 0.29 (ye 0.29) | 0.0c in 0.53m | 5.2/0 res 5.2 unm 1.9500 |  |
| 10 | 20:12:37 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.57@0.290 | deadline | 30.828/30.0 | YES 0.29 (ye 0.29) | 1.0c in 0.52m | 5.2/0 res 5.2 unm 1.9500 |  |
| 11 | 20:13:08 | p25-45c|tte>10m|imb10-50 | complete/extend | complete | did L5 | NO 4.57@0.705 | deadline | 30.763/30.0 | NO 0.70 (ye 0.30) | -4.0c in 1.21m; rest max_touch 0.705 min_opp_ask 0.7100 | 5.2/0 res 5.2 unm 1.9500 | l5_touch pair 1.0800 par>Y fee_safe N |
| 12 | 20:14:21 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.42@0.265 | filled | 11.727/30.0 | YES 0.26 (ye 0.26) | 0.0c in 0.2m | 9.62/0 res 9.62 unm 3.1213 |  |
| 13 | 20:14:33 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.42@0.265 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.854/30.0 | YES 0.26 (ye 0.26) | -6.0c in 0.52m | 9.62/0 res 9.62 unm 3.1213 |  |
| 14 | 20:15:04 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.06@0.200 | deadline | 31.401/30.0 | YES 0.20 (ye 0.20) | 0.0c in 0.53m | 9.62/0 res 9.62 unm 3.1213 |  |
| 15 | 20:15:36 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.06@0.200 | deadline | 30.713/30.0 | YES 0.20 (ye 0.20) | 0.0c in 0.52m | 9.62/0 res 9.62 unm 3.1213 |  |
| 16 | 20:16:07 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.06@0.200 | deadline | 30.715/30.0 | YES 0.20 (ye 0.20) | 0.0c in 0.52m | 9.62/0 res 9.62 unm 3.1213 |  |
| 17 | 20:16:38 | p25-45c|tte>10m|imb10-50 | extend/extend | extend | did L5 | YES 4.06@0.200 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.844/30.0 | YES 0.20 (ye 0.20) | -9.0c in 0.96m | 9.62/0 res 9.62 unm 3.1213 |  |
| 18 | 20:17:36 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.1100:q1_entry_ok=False |  | hold | /30.0 | YES 0.1100 (ye 0.1100) | -1.0c in 0.53m | 9.62/0 res 9.62 unm 3.1213 |  |
| 19 | 20:18:07 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.1000:q1_entry_ok=False |  | hold | /30.0 | YES 0.1000 (ye 0.1000) | 2.0c in 0.52m | 9.62/0 res 9.62 unm 3.1213 |  |
| 20 | 20:18:39 | p25-45c|tte>10m|imb10-50 | complete/extend | hold:no_legal_maker_price | NOT: complement_not_priceable:no_legal_maker_price |  | hold | /30.0 | NO 0.88 (ye 0.12) | 0.0c in 0.03m | 9.62/0 res 9.62 unm 3.1213 | l5_touch pair 1.1995 par>Y fee_safe N |
| 21 | 20:18:41 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.1200:q1_entry_ok=False |  | hold | /30.0 | YES 0.1200 (ye 0.1200) | -2.0c in 0.44m | 9.62/0 res 9.62 unm 3.1213 |  |
| 22 | 20:19:08 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.1000:q1_entry_ok=False |  | hold | /30.0 | YES 0.1000 (ye 0.1000) | -4.0c in 0.53m | 9.62/0 res 9.62 unm 3.1213 |  |
| 23 | 20:19:39 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.0600:q1_entry_ok=False |  | hold | /30.0 | YES 0.0600 (ye 0.0600) | -1.0c in 0.53m | 9.62/0 res 9.62 unm 3.1213 |  |
| 24 | 20:20:11 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.0500:q1_entry_ok=False |  | hold | /30.0 | YES 0.0500 (ye 0.0500) | -1.5c in 0.53m | 9.62/0 res 9.62 unm 3.1213 |  |
| 25 | 20:20:42 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.0350:q1_entry_ok=False |  | hold | /30.0 | YES 0.0350 (ye 0.0350) | -0.5c in 0.44m | 9.62/0 res 9.62 unm 3.1213 |  |
| 26 | 20:21:09 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.0300:q1_entry_ok=False |  | hold | /30.0 | YES 0.0300 (ye 0.0300) | 3.0c in 0.53m | 9.62/0 res 9.62 unm 3.1213 |  |
| 27 | 20:21:40 | p25-45c|tte>10m|imb10-50 | extend/extend | hold:entry_band_left | NOT: entry_band_left:bid=0.0600:q1_entry_ok=False |  | hold | /30.0 | YES 0.0600 (ye 0.0600) |  | 9.62/0 res 9.62 unm 3.1213 |  |

- venue trades (6): 20:14:31 2.4200@0.2650; 20:14:26 1.0000@0.2650; 20:14:25 1.0000@0.2650; 20:10:53 0.2000@0.3750; 20:10:53 4.0000@0.3750; 20:10:52 1.0000@0.3750
- settlement: settled True (REALIZED, position_resolution_activity); resolution field POSITION_RESOLUTION_SIDE_SHORT; paying side NO [derived: venue realized_usd reproduced by ledger payout NO x 1.00 - cost_basis]; venue realized -3.1213
- derived: bought YES 9.62 @ 0.3244594594594594594594594595, NO 0 @ None; matched pairs 0 at pair cost None -> 0; residual 9.62 -> -3.1213; fees venue_unreported
- credited rebates: not present in the settlement rows (fees_usd=venue_unreported); rebates are only ever read from the incentive audit

VERDICT tsc-mlb-laa-bos-2026-09-07-7pt5: venue realized -3.1213 = matched pairs 0.0000 (pair cost None) + residual -3.1213 (9.62 YES at avg 0.3245, paying side NO). Path (* = not L5's draw): open(YES 5.2@0.375 deadline) -> open(YES 5.2@0.375 deadline) -> open(YES 5.2@0.375 filled) -> extend(YES 5.2@0.375 rejected:rejected_at_non_crossing_price) -> extend(YES 5.2@0.375 rejected:rejected_at_non_crossing_price) -> extend(YES 5.2@0.375 rejected:rejected_at_non_crossing_price) -> extend(YES 5.2@0.375 rejected:rejected_at_non_crossing_price) -> complete(NO 5.2@0.455 deadline) -> extend(YES 4.57@0.290 deadline) -> extend(YES 4.57@0.290 deadline) -> extend(YES 4.57@0.290 deadline) -> complete(NO 4.57@0.705 deadline) -> extend(YES 4.42@0.265 filled) -> extend(YES 4.42@0.265 rejected:post_only_cross_within_1_tick_of_lagging_ask) -> extend(YES 4.06@0.200 deadline) -> extend(YES 4.06@0.200 deadline) -> extend(YES 4.06@0.200 deadline) -> extend(YES 4.06@0.200 rejected:post_only_cross_within_1_tick_of_lagging_ask) -> hold:entry_band_left x2 -> hold:no_legal_maker_price -> hold:entry_band_left x7.
- faithful L5 behavior: 0.0000 of residual loss on 0.00 contracts; plus the matched-pair contribution 0.0000 at L5's own above-par target where applicable, and 1.1713 of EXTEND fills L5 asked for on the residual side.
- execution/queue failure: 0.0000 on 0.00 contracts; 0 quoted COMPLETE(s) did not fill ({}: ); counterfactual saving had they filled at their price: 0.0000 (fill-assumed). Also 6 EXTEND(s) L5 drew on the losing side were REJECTED by the venue ({'rejected:rejected_at_non_crossing_price': 4, 'rejected:post_only_cross_within_1_tick_of_lagging_ask': 2}), which avoided 9.7833 of further loss by accident.
- market-selection transfer: -1.9240 on 5.93 contracts; idx 7 COMPLETE 5.20@0.46 rested 31.522s while the touch ran to 0.705 (opp ask 0.6250): the price ran through the complement during the rest, cf saving 2.8340 (the market left the legal price range / distribution while L5 was still drawing hold or extend; no complete was ever drawn for these contracts; tail yes-equiv ['0.12', '0.10', '0.06', '0.05', '0.04', '0.03', '0.06']). Price run: yes-equivalent touch 0.38 at the first fill -> min 0.03 / last 0.06 over 10.8 min; left the 0.20-0.80 range after 6.7 min (idx 18), 10 decisions outside it, 0 off-distribution.
- remaining shell distortion: -1.1973 on 3.69 contracts refused by {'complement_not_priceable:no_legal_maker_price': 1}; counterfactual saving had the refused COMPLETE(s) filled at L5's touch: 0.4428 (fill-assumed); the band also refused EXTEND draws on the losing side worth 2.3694 of avoided loss.
- partition check: -3.1213 vs derived residual -3.1213 (reconciles=True).

## tsc-sea-udi-laz-2026-09-07-2pt5

- decisions 6 (coverage [0, 5], missing 0); orders 6 / filled 0; did L5's draw 1.0; holds {}; outcomes {'rejected:post_only_cross_within_1_tick_of_lagging_ask': 5, 'rejected:post_only_cross_after_book_move': 1}; session time {'sessions': 1, 'active_s': 21.7, 'wall_s': 21.7, 'first_decision_utc': '2026-09-07T20:24:52.335396Z', 'last_session_end_utc': '2026-09-07T20:25:14.054366Z'}

| idx | t | state | L5 sampled/v2 | acted | did L5 | order | outcome | rest_s/reeval_s | touch | move to next | inv after up/dn | pair econ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 20:24:52 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.60@0.42 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.951/2.0 | YES 0.42 (ye 0.42) | 0.0c in 0.06m | 0/0 res 0 unm 0.0000 |  |
| 1 | 20:24:55 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.60@0.42 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.842/2.0 | YES 0.42 (ye 0.42) | 0.0c in 0.05m | 0/0 res 0 unm 0.0000 |  |
| 2 | 20:24:58 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.60@0.42 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 0.905/2.0 | YES 0.42 (ye 0.42) | 0.0c in 0.05m | 0/0 res 0 unm 0.0000 |  |
| 3 | 20:25:02 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.60@0.42 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 1.902/2.0 | YES 0.42 (ye 0.42) | 0.0c in 0.07m | 0/0 res 0 unm 0.0000 |  |
| 4 | 20:25:06 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.60@0.42 | rejected:post_only_cross_within_1_tick_of_lagging_ask | 1.916/2.0 | YES 0.42 (ye 0.42) | 0.0c in 0.07m | 0/0 res 0 unm 0.0000 |  |
| 5 | 20:25:10 | p25-45c|tte>10m|flat | open/open | open | did L5 | YES 5.60@0.42 | rejected:post_only_cross_after_book_move | 0.853/2.0 | YES 0.42 (ye 0.42) |  | 0/0 res 0 unm 0.0000 |  |

- venue trades (0): 
- settlement: settled False (None, None); resolution field None; paying side None [PENDING]; venue realized None
- derived: bought YES None @ None, NO None @ None; matched pairs None at pair cost None -> None; residual None -> None; fees None
- credited rebates: not present in the settlement rows (fees_usd=None); rebates are only ever read from the incentive audit

Labels: realized = the venue's own number (settlement rows only); derived = arithmetic on ledger fills / venue rows; counterfactual = did not happen; fill-assumed at the quoted or touch price, queue position and depth not modelled; partition = residual contracts x their own avg cost, attributed to the first opportunity that could have removed them (FIFO in decision order); sums to the derived residual contribution
