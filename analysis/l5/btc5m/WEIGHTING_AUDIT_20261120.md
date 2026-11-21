# Weighting audit of BTC5m research, 2026-11-20

Every research script under `analysis/l5/btc5m/` and
`analysis/l5/bonereaper_recent/` was re-read for one defect: a headline
number that is the mean of per-window means (each window counted once
whatever it traded) instead of total proceeds minus total cost and fees.
Affected numbers were recomputed pooled (sum of P&L over sum of shares,
windows resampled for the interval). Recomputation scripts and outputs:
`weighting_audit/`. From now on results use `research_accounting.py`.

## Verdict by script

SOUND (point estimate is already a total or one entry per window):
favourite_band_weighting_check, favourite_band_one_entry,
entry_cell_validation, entry_filter_study Part B,
integrated_inventory_replay, one_sided_policy_replay,
late_favourite_recorder_study, recent_fills_by_price,
reconcile_platform_pnl, brti_model_vs_market, inventory_policies, and
the mechanics-only scripts (brti_calibration, early_window_entry,
opportunity_table, repeat_reach, rung_reachability).

MINOR (defect present, conclusion unchanged): adverse_selection_anatomy
(-3.7c/-3.4c becomes -3.8c/-3.1c), entry_band_evidence, entry_filter_study
Part A, taker_fee_adjusted (bands get more negative), late_favourite_tail_check,
brti_lead_lag (inputs not on disk, not recomputed). taker_longshot,
frozen_taker_longshot, unmatched_exit_value and book_relative_policy drop
the 3-4 windows whose final quote was undecided, which conditions on the
outcome.

AFFECTED: favourite_band_holdout, favourite_band_execution,
favourite_band_where, bonereaper_selection_test,
bonereaper_spot_discrepancy, one_sided_value, entry_research Part 1.

## Statements that are wrong as written, and what replaces them

| Where | As written | Pooled |
|---|---|---|
| FINDINGS, selection test | his underdog buys lose 3-7c here before fees | -0.017 [-0.044,+0.012] and +0.002: not supported |
| FINDINGS, selection test | his side minus favourite, discovery -0.017 (significant) | -0.004 [-0.016,+0.007] |
| FINDINGS, selection test | his moments minus every point, validation -0.009 (significant) | +0.001 [-0.012,+0.013] |
| FINDINGS correction | "paired results stand" | the paired differences were also window-averaged; pooled they are all indistinguishable from zero. "No transferable side or timing signal" still holds |
| FINDINGS, spot test | every taker earns +4 to +9c at 0.50-0.85 and loses 3-9c below 0.50 | every band interval spans zero |
| FINDINGS, spot test | buys where spot said cheap lose 3-6c; spot is a worse forecast | -0.005 to +0.014: no sign either way |
| FINDINGS, spot test | the other venue's print above our ask loses on both halves | after fee: discovery yes; validation -0.002 to -0.018, mostly spanning zero |
| FINDINGS, spot test | his taker buys earn 0.5c less than other takers (same window, band) | -0.0058 and -0.0070: stands |
| FINDINGS, favourite band | +0.013 / +0.037 after fee | -0.037 / -0.020 (already withdrawn) |
| FINDINGS correction | no price x time cell positive on hold-out | true only pooled (the committed table shows three small positives) |
| FINDINGS correction | maker worse than taker | stands; hold-out pooled: taker -0.037, maker -0.046 to -0.072, maker-then-taker worst |
| Packet, one-sided control | rule allows -1.9c, refuses -4.3c | allows +0.8c [-3.9,+5.6], refuses -4.75c [-6.5,-3.1]; NO PROMOTION unchanged (it rests on per-window sums: +$0.0004) |
| Packet, one-sided control | model worse than market (Brier +0.0069, significant) | +0.0048 [-0.002,+0.011]: not significant |
| Packet, entry research Part 1 | 0.55-0.70 +0.068, below 0.50 -0.043, first 60 s least bad | no price band distinguishable from zero; first 60 s is his worst bucket |

## What does not change

No decision changes. Every direction in which numbers moved removes an
apparent favourite-side edge or an apparent underdog-side loss. Still
standing on sound accounting: maker bids lose about 3-4c a share when
filled; the two-sided strategy loses about $0.025 a window; the one-sided
rule adds +$0.0004 a window; the late-favourite candidate is rejected;
nothing tested survives our taker fee.
