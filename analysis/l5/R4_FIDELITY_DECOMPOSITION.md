# R4 fidelity decomposition: L5 decisions vs executed, and what the $2.50 unmatched cap suppressed

Shell: `R4/` rows only. Markets 3 (2 settled) | decisions 174 (deduped) | campaign rows 211, truncated 0 | active session time 2942.7s

Labels: **realized** = venue number only; **derived** = arithmetic on ledger / venue rows; **counterfactual** = did not happen, fill-assumed, fee-free; **PENDING** = not settled.

## Verdict

The $2.50 unmatched-dollar cap overrode 54 of 174 R4 decisions (31%), every one of them an L5 EXTEND, i.e. 54 of the 80 extends L5 sampled (68%); by market -- aec-atp-petbru-alecan: 10/30 decisions, 10/25 sampled extends, 302s of 901s live; aec-atpcq-aleher-tundur: 9/79 decisions, 9/18 sampled extends, 270s of 876s live; aec-atpcq-norgom-ollwal: 35/65 decisions, 35/37 sampled extends, 282s of 1166s live. It withheld 124.6736 USD of orders across 17 consecutive runs (39.9943 USD counting one order per run), 283.86 YES / 0.00 NO contracts never built, and held the engine in cap-refusal for 855s of 2943s of live session time (29%). On the 2 settled market(s), had every refused order filled and been held to the venue's actual settlement the extra inventory would have made -62.5205 USD (-21.2231 USD on the one-fill-per-run reading) (the paying side of aec-atpcq-norgom-ollwal is read from the venue's public settlement price and is unconfirmed; had the opposite side paid the held figure would be 82.7595 USD all-fill / 29.1269 USD per run); paired instead at the complement touch L5 later saw it would have made 10.0029 USD (3.6831 USD per run). Those are counterfactual, fill-assumed, fee-free numbers, not realized ones; aec-atp-petbru-alecan is still open, so its settlement counterfactual is PENDING. L5's OPEN and COMPLETE decisions were never touched by this cap; its EXTEND leg was mostly suppressed, so the R4 sequence is L5's opens and completes with L5's extends largely removed.

## Campaign: L5 relation (sampled) x executed outcome

| L5 sampled | acted:complete | acted:extend | acted:open | hold:complement_not_priceable | hold:complete_without_residual | hold:entry_band_left | hold:off_distribution_hold | hold:spend_cap_reached | hold:unmatched_usd_cap | n |
|---|---|---|---|---|---|---|---|---|---|---|
| complete | 35 | 0 | 0 | 21 | 1 | 0 | 0 | 0 | 0 | 57 |
| extend | 1 | 1 | 0 | 0 | 0 | 23 | 1 | 0 | 54 | 80 |
| open | 0 | 0 | 13 | 0 | 0 | 0 | 23 | 1 | 0 | 37 |

## Campaign: L5 v2_recommended x executed outcome

| L5 v2 | acted:complete | acted:extend | acted:open | hold:complement_not_priceable | hold:complete_without_residual | hold:entry_band_left | hold:off_distribution_hold | hold:spend_cap_reached | hold:unmatched_usd_cap | n |
|---|---|---|---|---|---|---|---|---|---|---|
| complete | 7 | 0 | 0 | 21 | 1 | 8 | 0 | 0 | 0 | 37 |
| extend | 29 | 1 | 0 | 0 | 0 | 15 | 0 | 0 | 54 | 99 |
| open | 0 | 0 | 13 | 0 | 0 | 0 | 24 | 1 | 0 | 38 |

## Campaign: refusals in proportion

| refusal | decisions | share | runs | strict s | inclusive s | share of live time | USD refused (all) | USD refused (1/run) | unmatched increment (all) | YES / NO contracts (all) | YES / NO (1/run) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| unmatched_usd_cap | 54 | 31.0% | 17 | 596.7 | 854.7 | 29.0% | 124.6736 | 39.9943 | 126.9738 | 283.86 / 0.00 | 90.65 / 0.00 |
| off_distribution_hold | 24 | 13.8% | 1 | 305.1 | 315.1 | 10.7% | 59.9495 | 2.4966 | 0.0000 | 116.47 / 0.00 | 4.38 / 0.00 |
| complement_not_priceable | 21 | 12.1% | 6 | 47.5 | 59.5 | 2.0% | 52.3397 | 14.9550 | 0.0000 | 0.00 / 56.98 | 0.00 / 16.35 |
| entry_band_left | 23 | 13.2% | 10 | 534.5 | 666.5 | 22.7% | 25.8175 | 16.7759 | 0.0000 | 76.27 / 0.00 | 31.05 / 0.00 |
| spend_cap_reached | 1 | 0.6% | 1 | 0.0 | 30.0 | 1.0% | 2.4957 | 2.4957 | 0.0000 | 0.00 / 0.00 | 0.00 / 0.00 |
| complete_without_residual | 1 | 0.6% | 1 | 0.0 | 30.0 | 1.0% | 2.4990 | 2.4990 | 0.0000 | 0.00 / 0.00 | 0.00 / 0.00 |

USD / contracts for a refusal are the planned safe size of the order it withheld. For `off_distribution_hold` L5 itself had no action (would_act = hold), so that row is a venue-side hold, not a suppressed L5 intention; `complement_not_priceable` and `entry_band_left` are price/band refusals of an L5 action, also not the dollar cap.

Cap vs L5 EXTEND: sampled 54/80 (67.5%); v2_recommended 54/99 (54.5%).

## Campaign: counterfactual settlement economics of the cap refusals (settled markets only)

Settled: aec-atpcq-aleher-tundur-2026-09-07, aec-atpcq-norgom-ollwal-2026-09-07; PENDING: aec-atp-petbru-alecan-2026-09-07

| reading | orders | held to venue settlement (USD) | held if the opposite side had paid (USD) | paired at later L5 touch (USD) |
|---|---|---|---|---|
| all_refusals_fill | 44 | -62.5205 | 82.7595 | 10.0029 |
| first_of_each_run_fills | 15 | -21.2231 | 29.1269 | 3.6831 |

Paying side UNCONFIRMED (read from the venue's public settlement price, no position-resolution row): aec-atpcq-norgom-ollwal-2026-09-07

Realized (venue): {'aec-atpcq-aleher-tundur-2026-09-07': '1.9955'} -> sum 1.9955

## aec-atp-petbru-alecan-2026-09-07

- decisions 30 idx [0, 62]; missing from dumps: [[12, 44]] (33)
- outcomes {'hold:entry_band_left': 15, 'hold:unmatched_usd_cap': 10, 'acted:complete': 4, 'acted:open': 1}
- faithful share: sampled relation executed 0.1667, v2 executed 0.0333
- live: 17 sessions, active 900.6s, wall 2606.3s (2026-09-07T17:42:56.527116Z -> 2026-09-07T18:26:22.781202Z)
- orders 5 submitted / 1 filled: open YES 5.24@0.38
- final ledger inventory: {'up': '5.24', 'dn': '0', 'matched': '0', 'residual': '5.24', 'avg_up': '0.38', 'avg_dn': None, 'spent': '1.9912', 'unmatched_usd': '1.9912'}
- settlement: PENDING; status None; paying side None (PENDING); venue realized None

### L5 sampled x outcome

| L5 sampled | acted:complete | acted:open | hold:entry_band_left | hold:unmatched_usd_cap | n |
|---|---|---|---|---|---|
| complete | 4 | 0 | 0 | 0 | 4 |
| extend | 0 | 0 | 15 | 10 | 25 |
| open | 0 | 1 | 0 | 0 | 1 |

### L5 v2_recommended x outcome

| L5 v2 | acted:complete | acted:open | hold:entry_band_left | hold:unmatched_usd_cap | n |
|---|---|---|---|---|---|
| extend | 4 | 0 | 15 | 10 | 29 |
| open | 0 | 1 | 0 | 0 | 1 |

### unmatched_usd_cap overrides

- 10 refusals (33.3% of decisions), all L5 {'extend': 10}; sampled extends suppressed 10/25, v2 extends 10/29
- unmatched USD at refusal: before 1.9912-1.9912, after-if-filled 3.7412-3.9824 vs cap 2.50
- dollars refused: 18.2131 USD (qty x px, all) / 3.7412 (1 per run); unmatched increment 18.2131 (all) / 3.7412 (1 per run)
- inventory not built: YES 50.70 / NO 0.00 (all); YES 10.24 / NO 0.00 (1 per run)
- time: 2 runs (lengths [6, 4]), strict 241.9s, inclusive 301.9s = 33.5% of active session time

| run idx | first | last | strict s | inclusive s |
|---|---|---|---|---|
| 1-6 | 17:43:10 | 17:45:41 | 151.7 | 181.7 |
| 8-11 | 17:46:43 | 17:48:13 | 90.2 | 120.2 |

Counterfactual settlement economics: **PENDING**
market still open; the pair-completion branch is outcome-independent but is withheld with the settlement branch until the venue settles

### other refusals

| refusal | decisions | share | runs | strict s | inclusive s | share of live time | USD withheld (all) | YES / NO (all) | L5 sampled at refusal |
|---|---|---|---|---|---|---|---|---|---|
| entry_band_left | 15 | 50.0% | 4 | 482.1 | 602.1 | 66.9% | 5.8790 | 54.58 / 0.00 | {'extend': 15} |

## aec-atpcq-aleher-tundur-2026-09-07

- decisions 79 idx [0, 78]; missing from dumps: none (0)
- outcomes {'hold:off_distribution_hold': 24, 'hold:complement_not_priceable': 21, 'acted:complete': 10, 'hold:unmatched_usd_cap': 9, 'hold:entry_band_left': 8, 'acted:open': 6, 'hold:complete_without_residual': 1}
- faithful share: sampled relation executed 0.2025, v2 executed 0.1646
- live: 18 sessions, active 876.5s, wall 2895.8s (2026-09-07T08:07:17.513110Z -> 2026-09-07T08:55:33.277585Z)
- orders 16 submitted / 5 filled: open YES 5.31@0.47; complete NO 5.2@0.51; complete NO 0.11@0.50; open YES 5.1@0.49; complete NO 3.24@0.22
- final ledger inventory: {'up': '10.41', 'dn': '8.55', 'matched': '8.55', 'residual': '1.86', 'avg_up': '0.4797982708933717579250720461', 'avg_dn': '0.3999766081871345029239766082', 'spent': '8.4145', 'unmatched_usd': '0.8924'}
- settlement: settled; status REALIZED; paying side YES (derived: venue realized_usd reproduced by ledger payout YES x 1.00 - cost_basis); venue realized 1.9955

### L5 sampled x outcome

| L5 sampled | acted:complete | acted:open | hold:complement_not_priceable | hold:complete_without_residual | hold:entry_band_left | hold:off_distribution_hold | hold:unmatched_usd_cap | n |
|---|---|---|---|---|---|---|---|---|
| complete | 10 | 0 | 21 | 1 | 0 | 0 | 0 | 32 |
| extend | 0 | 0 | 0 | 0 | 8 | 1 | 9 | 18 |
| open | 0 | 6 | 0 | 0 | 0 | 23 | 0 | 29 |

### L5 v2_recommended x outcome

| L5 v2 | acted:complete | acted:open | hold:complement_not_priceable | hold:complete_without_residual | hold:entry_band_left | hold:off_distribution_hold | hold:unmatched_usd_cap | n |
|---|---|---|---|---|---|---|---|---|
| complete | 7 | 0 | 21 | 1 | 8 | 0 | 0 | 37 |
| extend | 3 | 0 | 0 | 0 | 0 | 0 | 9 | 12 |
| open | 0 | 6 | 0 | 0 | 0 | 24 | 0 | 30 |

### unmatched_usd_cap overrides

- 9 refusals (11.4% of decisions), all L5 {'extend': 9}; sampled extends suppressed 9/18, v2 extends 9/12
- unmatched USD at refusal: before 2.4470-2.4990, after-if-filled 4.6994-4.9990 vs cap 2.50
- dollars refused: 22.4728 USD (qty x px, all) / 7.4895 (1 per run); unmatched increment 22.0148 (all) / 7.3912 (1 per run)
- inventory not built: YES 43.94 / NO 0.00 (all); YES 15.03 / NO 0.00 (1 per run)
- time: 3 runs (lengths [2, 3, 4]), strict 180.4s, inclusive 270.4s = 30.9% of active session time

| run idx | first | last | strict s | inclusive s |
|---|---|---|---|---|
| 3-4 | 08:08:32 | 08:09:02 | 30.1 | 60.1 |
| 11-13 | 08:11:45 | 08:12:45 | 60.1 | 90.1 |
| 38-41 | 08:48:25 | 08:49:55 | 90.2 | 120.2 |

Counterfactual settlement economics: **COUNTERFACTUAL**
paying side YES; all refusals fill: held 21.4672 USD (opposite side paid: -22.4728), paired at later touch 8.6510 USD (9/9 rows had a later touch); first of each run fills: held 7.5405 USD (opposite: -7.4895), paired 2.6940 USD

| idx | side | qty | px | held to settlement | later touch (idx) | pair | paired P&L |
|---|---|---|---|---|---|---|---|
| 3 | YES | 5.31 | 0.47 | 2.8143 | 0.51 (5) | 0.9800 | 0.1062 |
| 4 | YES | 5.55 | 0.45 | 3.0525 | 0.51 (5) | 0.9600 | 0.2220 |
| 11 | YES | 5.10 | 0.49 | 2.6010 | 0.22 (42) | 0.7100 | 1.4790 |
| 12 | YES | 5.00 | 0.50 | 2.5000 | 0.22 (42) | 0.7200 | 1.4000 |
| 13 | YES | 4.80 | 0.52 | 2.3040 | 0.22 (42) | 0.7400 | 1.2480 |
| 38 | YES | 4.62 | 0.54 | 2.1252 | 0.22 (42) | 0.7600 | 1.1088 |
| 39 | YES | 4.62 | 0.54 | 2.1252 | 0.22 (42) | 0.7600 | 1.1088 |
| 40 | YES | 5.10 | 0.49 | 2.6010 | 0.22 (42) | 0.7100 | 1.4790 |
| 41 | YES | 3.84 | 0.65 | 1.3440 | 0.22 (42) | 0.8700 | 0.4992 |

### other refusals

| refusal | decisions | share | runs | strict s | inclusive s | share of live time | USD withheld (all) | YES / NO (all) | L5 sampled at refusal |
|---|---|---|---|---|---|---|---|---|---|
| off_distribution_hold | 24 | 30.4% | 1 | 305.1 | 315.1 | 35.9% | 59.9495 | 116.47 / 0.00 | {'open': 23, 'extend': 1} |
| complement_not_priceable | 21 | 26.6% | 6 | 47.5 | 59.5 | 6.8% | 52.3397 | 0.00 / 56.98 | {'complete': 21} |
| entry_band_left | 8 | 10.1% | 6 | 52.4 | 64.4 | 7.3% | 19.9385 | 21.69 / 0.00 | {'extend': 8} |
| complete_without_residual | 1 | 1.3% | 1 | 0.0 | 30.0 | 3.4% | 2.4990 | 0.00 / 0.00 | {'complete': 1} |

## aec-atpcq-norgom-ollwal-2026-09-07

- decisions 65 idx [0, 64]; missing from dumps: none (0)
- outcomes {'hold:unmatched_usd_cap': 35, 'acted:complete': 22, 'acted:open': 6, 'acted:extend': 1, 'hold:spend_cap_reached': 1}
- faithful share: sampled relation executed 0.4308, v2 executed 0.1077
- live: 11 sessions, active 1165.6s, wall 1310.8s (2026-09-07T09:03:42.030307Z -> 2026-09-07T09:25:32.815545Z)
- orders 29 submitted / 4 filled: open YES 5.31@0.47; complete NO 5.2@0.51; extend YES 4.9@0.51; complete NO 5.01@0.51
- final ledger inventory: {'up': '10.21', 'dn': '10.21', 'matched': '10.21', 'residual': '0.00', 'avg_up': '0.4891968658178256611165523996', 'avg_dn': '0.5100', 'spent': '10.201800', 'unmatched_usd': '0.0000'}
- settlement: settled; status SETTLED_REALIZED_UNCONFIRMED; paying side NO (venue public_settlement_px=0.0000 (YES terms) read as the paying side; UNCONFIRMED -- no position-resolution row because the account was flat); venue realized None

### L5 sampled x outcome

| L5 sampled | acted:complete | acted:extend | acted:open | hold:spend_cap_reached | hold:unmatched_usd_cap | n |
|---|---|---|---|---|---|---|
| complete | 21 | 0 | 0 | 0 | 0 | 21 |
| extend | 1 | 1 | 0 | 0 | 35 | 37 |
| open | 0 | 0 | 6 | 1 | 0 | 7 |

### L5 v2_recommended x outcome

| L5 v2 | acted:complete | acted:extend | acted:open | hold:spend_cap_reached | hold:unmatched_usd_cap | n |
|---|---|---|---|---|---|---|
| extend | 22 | 1 | 0 | 0 | 35 | 58 |
| open | 0 | 0 | 6 | 1 | 0 | 7 |

### unmatched_usd_cap overrides

- 35 refusals (53.8% of decisions), all L5 {'extend': 35}; sampled extends suppressed 35/37, v2 extends 35/58
- unmatched USD at refusal: before 0.0517-2.4509, after-if-filled 2.5372-5.0202 vs cap 2.50
- dollars refused: 83.9877 USD (qty x px, all) / 28.7636 (1 per run); unmatched increment 86.7459 (all) / 29.8013 (1 per run)
- inventory not built: YES 189.22 / NO 0.00 (all); YES 65.38 / NO 0.00 (1 per run)
- time: 12 runs (lengths [2, 2, 2, 1, 3, 1, 3, 5, 8, 2, 1, 5]), strict 174.4s, inclusive 282.4s = 24.2% of active session time

| run idx | first | last | strict s | inclusive s |
|---|---|---|---|---|
| 13-14 | 09:10:22 | 09:10:52 | 30.1 | 60.1 |
| 16-17 | 09:11:53 | 09:12:24 | 30.1 | 60.1 |
| 19-20 | 09:13:57 | 09:14:27 | 30.1 | 60.1 |
| 24-24 | 09:16:18 | 09:16:18 | 0.0 | 2.0 |
| 26-28 | 09:16:51 | 09:16:55 | 4.1 | 6.1 |
| 30-30 | 09:17:46 | 09:17:46 | 0.0 | 2.0 |
| 32-34 | 09:18:21 | 09:18:25 | 4.1 | 6.1 |
| 37-41 | 09:19:51 | 09:20:00 | 8.3 | 10.3 |
| 43-50 | 09:21:20 | 09:21:56 | 35.8 | 37.8 |
| 52-53 | 09:22:30 | 09:22:32 | 2.1 | 4.1 |
| 56-56 | 09:23:53 | 09:23:53 | 0.0 | 2.0 |
| 58-62 | 09:24:27 | 09:24:57 | 29.7 | 31.7 |

Counterfactual settlement economics: **COUNTERFACTUAL**
paying side NO (UNCONFIRMED); all refusals fill: held -83.9877 USD (opposite side paid: 105.2323), paired at later touch 1.3519 USD (35/35 rows had a later touch); first of each run fills: held -28.7636 USD (opposite: 36.6164), paired 0.9891 USD

| idx | side | qty | px | held to settlement | later touch (idx) | pair | paired P&L |
|---|---|---|---|---|---|---|---|
| 13 | YES | 5.70 | 0.43 | -2.4510 | 0.61 (15) | 1.0400 | -0.2280 |
| 14 | YES | 5.70 | 0.43 | -2.4510 | 0.61 (15) | 1.0400 | -0.2280 |
| 16 | YES | 5.60 | 0.42 | -2.3520 | 0.52 (18) | 0.9400 | 0.3360 |
| 17 | YES | 5.43 | 0.46 | -2.4978 | 0.52 (18) | 0.9800 | 0.1086 |
| 19 | YES | 5.31 | 0.47 | -2.4957 | 0.48 (22) | 0.9500 | 0.2655 |
| 20 | YES | 5.31 | 0.47 | -2.4957 | 0.48 (22) | 0.9500 | 0.2655 |
| 24 | YES | 5.70 | 0.43 | -2.4510 | 0.56 (25) | 0.9900 | 0.0570 |
| 26 | YES | 5.50 | 0.41 | -2.2550 | 0.58 (29) | 0.9900 | 0.0550 |
| 27 | YES | 5.50 | 0.41 | -2.2550 | 0.58 (29) | 0.9900 | 0.0550 |
| 28 | YES | 5.50 | 0.41 | -2.2550 | 0.58 (29) | 0.9900 | 0.0550 |
| 30 | YES | 5.70 | 0.43 | -2.4510 | 0.56 (31) | 0.9900 | 0.0570 |
| 32 | YES | 5.43 | 0.46 | -2.4978 | 0.53 (35) | 0.9900 | 0.0543 |
| 33 | YES | 5.43 | 0.46 | -2.4978 | 0.53 (35) | 0.9900 | 0.0543 |
| 34 | YES | 5.43 | 0.46 | -2.4978 | 0.53 (35) | 0.9900 | 0.0543 |
| 37 | YES | 5.20 | 0.48 | -2.4960 | 0.53 (42) | 1.0100 | -0.0520 |
| 38 | YES | 5.20 | 0.48 | -2.4960 | 0.53 (42) | 1.0100 | -0.0520 |
| 39 | YES | 5.20 | 0.48 | -2.4960 | 0.53 (42) | 1.0100 | -0.0520 |
| 40 | YES | 5.20 | 0.48 | -2.4960 | 0.53 (42) | 1.0100 | -0.0520 |
| 41 | YES | 5.20 | 0.48 | -2.4960 | 0.53 (42) | 1.0100 | -0.0520 |
| 43 | YES | 5.55 | 0.45 | -2.4975 | 0.59 (51) | 1.0400 | -0.2220 |
| 44 | YES | 5.55 | 0.45 | -2.4975 | 0.59 (51) | 1.0400 | -0.2220 |
| 45 | YES | 5.55 | 0.45 | -2.4975 | 0.59 (51) | 1.0400 | -0.2220 |
| 46 | YES | 5.55 | 0.45 | -2.4975 | 0.59 (51) | 1.0400 | -0.2220 |
| 47 | YES | 5.55 | 0.45 | -2.4975 | 0.59 (51) | 1.0400 | -0.2220 |
| 48 | YES | 5.41 | 0.40 | -2.1640 | 0.59 (51) | 0.9900 | 0.0541 |
| 49 | YES | 5.41 | 0.40 | -2.1640 | 0.59 (51) | 0.9900 | 0.0541 |
| 50 | YES | 5.41 | 0.40 | -2.1640 | 0.59 (51) | 0.9900 | 0.0541 |
| 52 | YES | 5.07 | 0.36 | -1.8252 | 0.54 (54) | 0.9000 | 0.5070 |
| 53 | YES | 5.07 | 0.36 | -1.8252 | 0.54 (54) | 0.9000 | 0.5070 |
| 56 | YES | 5.31 | 0.47 | -2.4957 | 0.52 (57) | 0.9900 | 0.0531 |
| 58 | YES | 5.31 | 0.47 | -2.4957 | 0.51 (63) | 0.9800 | 0.1062 |
| 59 | YES | 5.31 | 0.47 | -2.4957 | 0.51 (63) | 0.9800 | 0.1062 |
| 60 | YES | 5.31 | 0.47 | -2.4957 | 0.51 (63) | 0.9800 | 0.1062 |
| 61 | YES | 5.31 | 0.47 | -2.4957 | 0.51 (63) | 0.9800 | 0.1062 |
| 62 | YES | 5.31 | 0.47 | -2.4957 | 0.51 (63) | 0.9800 | 0.1062 |

### other refusals

| refusal | decisions | share | runs | strict s | inclusive s | share of live time | USD withheld (all) | YES / NO (all) | L5 sampled at refusal |
|---|---|---|---|---|---|---|---|---|---|
| spend_cap_reached | 1 | 1.5% | 1 | 0.0 | 30.0 | 2.6% | 2.4957 | 0.00 / 0.00 | {'open': 1} |

## Inputs and limitations

- files read: 21; campaign rows 211, parsed 211, truncated (json failed, regex-scanned, not merged) 0
- coverage gaps: aec-atp-petbru-alecan-2026-09-07: [[12, 44]]; aec-atpcq-aleher-tundur-2026-09-07: none; aec-atpcq-norgom-ollwal-2026-09-07: none
- refused orders are reconstructed from planned_safe_size (px = usd / contracts) and the residual side; consecutive refusals re-attempt one L5 intention, hence the two fill readings
- nothing here changes the engine; realized money is the venue's number only
