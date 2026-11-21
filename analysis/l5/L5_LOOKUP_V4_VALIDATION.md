# L5 LOOKUP v4 -- strict forward validation (frozen v3 vs candidate v4)

_STRICT FORWARD VALIDATION of the shadow loop's lookup-v4 candidate; read-only; the frozen model/lookup/spec are never written; the verdict is a RECOMMENDATION -- promotion requires an owner decision_

Generated 2026-09-08T01:03:13Z by `src/l5_lookup_v4_validation.py` (issue #167 comment 5577286614 item 4). **Verdict: `NOT_YET`** -- no material forward improvement: the gain is below the bar or its CI includes zero. Promotion: `REQUIRES_OWNER_DECISION`.

## Identities

- frozen model v2 / lookup v3: `analysis/l5/l5_action_model_v2.json` sha256_16 `bbb55bf90638d2bd` (n_transitions 69646, full states 988, generated 2026-09-03T04:19:16.377699+00:00); frozen spec `analysis/l5/l5_fidelity_gate.json` sha `fd03cb21a66dd025` frozen 2026-09-06T02:10:00+00:00
- reproduced loop proposal: `lookup_v4_proposal_20260908T004257Z.json` verdict `CANDIDATE_NOT_BETTER`, candidate sha `e86f907507fdec84`, train 2654 / holdout 1140, cut 2026-09-07T23:50:03Z (time_quantile_0.7 (window too short/lopsided for the day rule)), rows in window 3794; frozen agreement 0.7175 / mean P 0.1682 vs candidate 0.7035 / 0.1678

## Cohort

- network fetch worked: **True**; loop day-file fills 3911, windowed-fetch fills 109485, merged unique fills 109485 (fetch: {"attempted": true, "windows": 25, "pages": 225, "items": 112500, "oldest_ts": 1788201483, "newest_ts": 1788829066, "stopped_because": "reached_stop_ts", "elapsed_s": 122.1})
- normalized decision rows 102372 (of 106063 ingested) over 2658 settlement windows, span ['2026-09-01T01:12:06Z', '2026-09-08T00:57:46Z']; clean-cohort rule: windows starting at/after 2026-09-01T00:00Z (the frozen model's canonical history ends 2026-08-31T23:11Z); rows dropped as overlapping the frozen history: 3691

## What "materially improves" means here

On the STRICT forward holdout (>= 200 rows, >= 200 training rows, >= 30 holdout windows): candidate relation top-1 agreement >= frozen + 2.0 pp with the 95% paired-bootstrap CI lower bound > 0, OR mean P(observed action) >= frozen + 0.01 with CI lower bound > 0; AND no regression: relation L1 not worse by > 0.02, off-distribution not worse, post-fill COMPLETE |gap| not worse by > 2.0 pp, no new FAIL under the frozen fidelity spec. REJECT when the candidate is worse by >= 1.0 pp agreement (CI upper < 0) or >= 0.005 mean P (CI upper < 0) or adds a spec FAIL. Otherwise NOT_YET.

## Cut `loop` -- loop proposal lookup_v4_proposal_20260908T004257Z.json: time_quantile_0.7 (window too short/lopsided for the day rule); that cut TIME applied to this deeper cohort (the loop's own candidate saw only 2654 train / 1140 holdout rows of its collector window)

- split `row` at 2026-09-07T23:50:03Z: train 101163 rows / 2644 windows ['2026-09-01T01:12:06Z', '2026-09-07T23:49:57Z']; holdout 1209 rows / 15 windows ['2026-09-07T23:50:03Z', '2026-09-08T00:57:46Z']; straddling rows dropped 0
- candidate v4 rebuilt on the training cohort only: n_transitions 170809, full states 1062, states touched 1021, new 74, modal changes 340, count-table sha `94a8a1155c586005`
- **verdict `NOT_YET`** -- insufficient forward evidence

| dimension | frozen v3 | candidate v4 | delta |
|---|---|---|---|
| relation top-1 agreement | 0.7171 | 0.7022 | -0.0149 |
| exact action agreement | 0.3052 | 0.3052 | 0.0 |
| mean P(observed action) | 0.1688 | 0.1686 | -0.0002 |
| mean P(observed relation) | 0.586 | 0.585 | -0.001 |
| relation L1 observed vs expected | 0.0125 | 0.0058 | -0.0067 |
| state-weighted relation L1 | 0.1369 | 0.139 | 0.0021 |
| off-distribution % | 0.0 | 0.0 | 0.0 |
| side: open cheaper-touch agreement (model-free) | 0.6667 | 0.6667 | 0.0 |
| side: post-fill side agreement | 0.7164 | 0.7015 | -0.0149 |
| size bucket L1 observed vs expected | 0.294 | 0.2788 | -0.0152 |
| size bucket top-1 agreement | 0.3879 | 0.3755 | -0.0124 |
| timing: cadence_modal agreement | 0.6237 | 0.6237 | 0.0 |
| inventory: resid-after bucket L1 | 0.0065 | 0.0061 | -0.0004 |
| inventory: imbalanced-after expected (obs 0.9975) | 0.9986 | 0.9987 | 0.0001 |
| COMPLETE: P(complete|post-fill) expected (obs 0.4818, n 1206) | 0.4878 | 0.4846 | -0.0032 |
| COMPLETE: gap pp (observed - expected) | -0.61 | -0.28 | 0.33 |
| persistence P(extend|last=extend) expected (obs 0.7313, n 614) | 0.6707 | 0.6696 | -0.0011 |
| persistence P(complete|last=complete) expected (obs 0.7183, n 575) | 0.6597 | 0.6526 | -0.0071 |

- observed relation freq {'open': 0.0025, 'extend': 0.517, 'complete': 0.4806, 'sell': 0.0}; expected v3 {'open': 0.0022, 'extend': 0.511, 'complete': 0.4868, 'sell': 0.0}; expected v4 {'open': 0.0024, 'extend': 0.5142, 'complete': 0.4835, 'sell': 0.0}
- size: observed {'q<5': 0.1447, 'q5-15': 0.2399, 'q15-40': 0.2341, 'q>40': 0.3813}; expected v3 {'q<5': 0.2204, 'q5-15': 0.3112, 'q15-40': 0.2083, 'q>40': 0.2601}; v4 {'q<5': 0.2119, 'q5-15': 0.3121, 'q15-40': 0.2045, 'q>40': 0.2715}
- timing: observed gap freq {'gap<2s': 0.7146, 'gap2-10': 0.2341, 'gap10-30': 0.0347, 'gap>30': 0.0165} (cadence_modal is copied unchanged from frozen v3 into the candidate by the loop's builder, so timing is identical under both lookups)
- inventory: residual-after buckets observed {'flat': 0.0025, 'imb1-10': 0.0182, 'imb10-50': 0.0604, 'imb>50': 0.9189}; expected v3 {'flat': 0.0014, 'imb1-10': 0.0193, 'imb10-50': 0.0625, 'imb>50': 0.9167}; v4 {'flat': 0.0013, 'imb1-10': 0.019, 'imb10-50': 0.0626, 'imb>50': 0.9171}
- pair cost at COMPLETE (observed, n 581, median 0.9899): {'pc<0.90': 0.012, 'pc0.90-0.95': 0.0551, 'pc0.95-0.98': 0.0706, 'pc0.98-1.00': 0.6386, 'pc>=1.00': 0.2238}
- price aggressiveness: UNMEASURED -- the public activity fills carry no maker/taker flag (the collector marks every realtime row 'unknown'; 'maker' is asserted only inside the takerOnly feed coverage of the canonical history) and no book, so touch-join and taker share are not derivable here; neither lookup predicts them -- the aggressiveness policy comes from the fidelity artifact and is shared; observed price bands {'p<10c': 0.0232, 'p10-25c': 0.3011, 'p25-45c': 0.1927, 'p45-55c': 0.0976, 'p55-75c': 0.1993, 'p75-90c': 0.1679, 'p>90c': 0.0182}
- paired statistics (n 1209, 1000 resamples): agreement delta -0.01489 CI (-0.0306, 0.0); mean P delta -0.00025 CI (-0.00157, 0.00105); discordant {'candidate_only_right': 37, 'frozen_only_right': 55, 'both_right': 812, 'both_wrong': 305}; McNemar exact p 0.0757602358
- frozen fidelity spec view: v3 action_distribution_relation=PASS, post_fill_complete_gap=PASS, off_distribution_rate=PASS; v4 action_distribution_relation=PASS, post_fill_complete_gap=PASS, off_distribution_rate=PASS
- verdict checks: `{"holdout_rows": 1209, "train_rows": 101163, "holdout_windows": 15, "minimums": {"min_holdout_rows": 200, "min_train_rows": 200, "min_holdout_windows": 30}}`

| base state | n | agree v3 | agree v4 | mean P v3 | mean P v4 | rel L1 v3 | rel L1 v4 | post-fill n | P(complete) obs | exp v3 | exp v4 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| p10-25c|tte5-10m|imb>50 | 211 | 0.8199 | 0.8199 | 0.1902 | 0.19 | 0.054 | 0.0582 | 211 | 0.3507 | 0.3777 | 0.3798 |
| p55-75c|tte5-10m|imb>50 | 135 | 0.6815 | 0.6741 | 0.1615 | 0.1594 | 0.0862 | 0.0822 | 135 | 0.6 | 0.5569 | 0.5589 |
| p25-45c|tte5-10m|imb>50 | 118 | 0.7542 | 0.7542 | 0.1646 | 0.1626 | 0.0798 | 0.0998 | 118 | 0.6695 | 0.6296 | 0.6196 |
| p75-90c|tte5-10m|imb>50 | 109 | 0.7248 | 0.5688 | 0.1679 | 0.1557 | 0.0374 | 0.051 | 109 | 0.5596 | 0.5409 | 0.5341 |
| p10-25c|tte2-5m|imb>50 | 93 | 0.7419 | 0.7419 | 0.1573 | 0.1655 | 0.0198 | 0.036 | 93 | 0.3978 | 0.3879 | 0.3798 |
| p45-55c|tte>10m|imb>50 | 70 | 0.6 | 0.6 | 0.1531 | 0.148 | 0.21 | 0.1852 | 70 | 0.4571 | 0.5621 | 0.5497 |
| p25-45c|tte>10m|imb>50 | 67 | 0.806 | 0.806 | 0.1709 | 0.1678 | 0.1608 | 0.1616 | 67 | 0.2537 | 0.3341 | 0.3345 |
| p75-90c|tte2-5m|imb>50 | 63 | 0.6508 | 0.6508 | 0.1664 | 0.1713 | 0.2102 | 0.1786 | 63 | 0.6984 | 0.5933 | 0.6091 |
| p55-75c|tte>10m|imb>50 | 49 | 0.7551 | 0.7551 | 0.1649 | 0.1699 | 0.1006 | 0.1042 | 49 | 0.5306 | 0.5809 | 0.5827 |
| p45-55c|tte2-5m|imb>50 | 24 | 0.75 | 0.75 | 0.1892 | 0.1904 | 0.136 | 0.1214 | 24 | 0.5417 | 0.4737 | 0.481 |
| p25-45c|tte30-120s|imb>50 | 23 | 0.7826 | 0.7826 | 0.1358 | 0.1269 | 0.0334 | 0.1594 | 23 | 0.7826 | 0.7659 | 0.7029 |
| p55-75c|tte2-5m|imb>50 | 21 | 0.7143 | 0.7143 | 0.1919 | 0.2097 | 0.2674 | 0.1928 | 21 | 0.2857 | 0.4194 | 0.3821 |
| p10-25c|tte>10m|imb>50 | 21 | 0.9524 | 0.8095 | 0.1765 | 0.1764 | 0.3354 | 0.3258 | 21 | 0.1429 | 0.3106 | 0.3058 |
| p45-55c|tte30-120s|imb>50 | 16 | 0.8125 | 0.8125 | 0.2694 | 0.2667 | 0.0951 | 0.0618 | 16 | 0.3125 | 0.36 | 0.3434 |
| p10-25c|tte2-5m|imb10-50 | 13 | 0.6923 | 0.6923 | 0.1351 | 0.1409 | 0.0358 | 0.0652 | 13 | 0.4615 | 0.4436 | 0.4289 |
| p<10c|tte2-5m|imb>50 | 13 | 0.4615 | 0.4615 | 0.1658 | 0.1681 | 0.2244 | 0.192 | 13 | 0.4615 | 0.3493 | 0.3655 |
| p55-75c|tte5-10m|imb10-50 | 12 | 0.5833 | 0.75 | 0.1373 | 0.1417 | 0.1284 | 0.0784 | 12 | 0.5 | 0.5642 | 0.5392 |
| p55-75c|tte30-120s|imb>50 | 11 | 0.6364 | 0.6364 | 0.1605 | 0.1581 | 0.0058 | 0.0196 | 11 | 0.3636 | 0.3607 | 0.3734 |
| p>90c|tte2-5m|imb>50 | 11 | 0.4545 | 0.4545 | 0.1745 | 0.1761 | 0.2658 | 0.2176 | 11 | 0.4545 | 0.5874 | 0.5633 |
| p<10c|tte30-120s|imb>50 | 11 | 0.7273 | 0.7273 | 0.2 | 0.2059 | 0.0078 | 0.0276 | 11 | 0.2727 | 0.2766 | 0.2865 |
| p75-90c|tte5-10m|imb10-50 | 9 | 0.6667 | 0.7778 | 0.1778 | 0.1967 | 0.1164 | 0.1044 | 9 | 0.4444 | 0.5026 | 0.4966 |
| p25-45c|tte2-5m|imb>50 | 9 | 0.3333 | 0.3333 | 0.1369 | 0.1216 | 0.6882 | 0.6336 | 9 | 0.3333 | 0.6774 | 0.6501 |
| p10-25c|tte5-10m|imb10-50 | 8 | 0.375 | 0.875 | 0.1199 | 0.1228 | 0.631 | 0.66 | 8 | 0.75 | 0.4345 | 0.42 |
| p75-90c|tte2-5m|imb10-50 | 8 | 0.5 | 0.375 | 0.1423 | 0.1432 | 0.0888 | 0.1918 | 8 | 0.375 | 0.4194 | 0.4709 |
| p>90c|tte30-120s|imb>50 | 8 | 0.25 | 0.375 | 0.2179 | 0.2009 | 0.5134 | 0.5622 | 8 | 0.75 | 0.4933 | 0.4689 |

## Cut `strict` -- last 3 days of observed activity; window-level split

- split `window` at 2026-09-05T00:57:46Z: train 60792 rows / 1654 windows ['2026-09-01T01:12:06Z', '2026-09-05T00:53:55Z']; holdout 41356 rows / 1002 windows ['2026-09-05T01:01:22Z', '2026-09-08T00:57:46Z']; straddling rows dropped 224
- candidate v4 rebuilt on the training cohort only: n_transitions 130438, full states 1051, states touched 982, new 63, modal changes 277, count-table sha `dd8329b0a7fe9c54`
- **verdict `NOT_YET`** -- no material forward improvement: the gain is below the bar or its CI includes zero

| dimension | frozen v3 | candidate v4 | delta |
|---|---|---|---|
| relation top-1 agreement | 0.6936 | 0.6931 | -0.0005 |
| exact action agreement | 0.2721 | 0.2783 | 0.0062 |
| mean P(observed action) | 0.1731 | 0.1743 | 0.0012 |
| mean P(observed relation) | 0.5909 | 0.5917 | 0.0008 |
| relation L1 observed vs expected | 0.0299 | 0.0318 | 0.0019 |
| state-weighted relation L1 | 0.054 | 0.0521 | -0.0019 |
| off-distribution % | 0.11 | 0.04 | -0.07 |
| side: open cheaper-touch agreement (model-free) | 0.5346 | 0.5346 | 0.0 |
| side: post-fill side agreement | 0.6921 | 0.6916 | -0.0005 |
| size bucket L1 observed vs expected | 0.0106 | 0.0252 | 0.0146 |
| size bucket top-1 agreement | 0.3656 | 0.3707 | 0.0051 |
| timing: cadence_modal agreement | 0.4745 | 0.4745 | 0.0 |
| inventory: resid-after bucket L1 | 0.0066 | 0.005 | -0.0016 |
| inventory: imbalanced-after expected (obs 0.9944) | 0.9939 | 0.994 | 0.0001 |
| COMPLETE: P(complete|post-fill) expected (obs 0.4766, n 41139) | 0.4912 | 0.4923 | 0.0011 |
| COMPLETE: gap pp (observed - expected) | -1.46 | -1.57 | -0.11 |
| persistence P(extend|last=extend) expected (obs 0.7061, n 20940) | 0.6708 | 0.6667 | -0.0041 |
| persistence P(complete|last=complete) expected (obs 0.6878, n 19028) | 0.675 | 0.6737 | -0.0013 |

- observed relation freq {'open': 0.0052, 'extend': 0.5207, 'complete': 0.4741, 'sell': 0.0}; expected v3 {'open': 0.0043, 'extend': 0.5067, 'complete': 0.4891, 'sell': 0.0}; expected v4 {'open': 0.0047, 'extend': 0.5053, 'complete': 0.49, 'sell': 0.0}
- size: observed {'q<5': 0.1934, 'q5-15': 0.3286, 'q15-40': 0.2203, 'q>40': 0.2576}; expected v3 {'q<5': 0.1972, 'q5-15': 0.3302, 'q15-40': 0.2159, 'q>40': 0.2567}; v4 {'q<5': 0.192, 'q5-15': 0.327, 'q15-40': 0.2108, 'q>40': 0.2702}
- timing: observed gap freq {'gap<2s': 0.4985, 'gap2-10': 0.3246, 'gap10-30': 0.1208, 'gap>30': 0.0562} (cadence_modal is copied unchanged from frozen v3 into the candidate by the loop's builder, so timing is identical under both lookups)
- inventory: residual-after buckets observed {'flat': 0.0056, 'imb1-10': 0.0375, 'imb10-50': 0.1579, 'imb>50': 0.7991}; expected v3 {'flat': 0.0061, 'imb1-10': 0.0392, 'imb10-50': 0.159, 'imb>50': 0.7958}; v4 {'flat': 0.006, 'imb1-10': 0.0386, 'imb10-50': 0.1588, 'imb>50': 0.7966}
- pair cost at COMPLETE (observed, n 19605, median 0.9896): {'pc<0.90': 0.036, 'pc0.90-0.95': 0.1387, 'pc0.95-0.98': 0.2146, 'pc0.98-1.00': 0.218, 'pc>=1.00': 0.3927}
- price aggressiveness: UNMEASURED -- the public activity fills carry no maker/taker flag (the collector marks every realtime row 'unknown'; 'maker' is asserted only inside the takerOnly feed coverage of the canonical history) and no book, so touch-join and taker share are not derivable here; neither lookup predicts them -- the aggressiveness policy comes from the fidelity artifact and is shared; observed price bands {'p<10c': 0.1104, 'p10-25c': 0.1866, 'p25-45c': 0.2117, 'p45-55c': 0.1018, 'p55-75c': 0.2169, 'p75-90c': 0.1147, 'p>90c': 0.0579}
- paired statistics (n 41356, 1000 resamples): agreement delta -0.00051 CI (-0.003, 0.00184); mean P delta 0.00118 CI (0.00094, 0.00142); discordant {'candidate_only_right': 1342, 'frozen_only_right': 1363, 'both_right': 27321, 'both_wrong': 11330}; McNemar exact p 0.7005825651
- frozen fidelity spec view: v3 action_distribution_relation=PASS, post_fill_complete_gap=PASS, off_distribution_rate=PASS; v4 action_distribution_relation=PASS, post_fill_complete_gap=PASS, off_distribution_rate=PASS
- verdict checks: `{"agreement_delta_pp": -0.051, "agreement_delta_ci": [-0.003, 0.00184], "agreement_gain_material": false, "mean_p_delta": 0.00118, "mean_p_delta_ci": [0.00094, 0.00142], "mean_p_gain_material": false, "relation_l1_delta": 0.0019, "relation_l1_ok": true, "off_distribution_delta_pp": -0.07, "off_distribution_ok": true, "complete_gap_abs_delta_pp": 0.11, "complete_gap_ok": true, "frozen_spec_fail_candidate": [], "frozen_spec_fail_frozen": [], "spec_ok": true}`

| base state | n | agree v3 | agree v4 | mean P v3 | mean P v4 | rel L1 v3 | rel L1 v4 | post-fill n | P(complete) obs | exp v3 | exp v4 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| p10-25c|tte5-10m|imb>50 | 2781 | 0.8012 | 0.8012 | 0.2053 | 0.2027 | 0.0568 | 0.0684 | 2781 | 0.452 | 0.4804 | 0.4862 |
| p25-45c|tte5-10m|imb>50 | 2503 | 0.7343 | 0.7343 | 0.1806 | 0.1807 | 0.0098 | 0.0004 | 2503 | 0.4918 | 0.4869 | 0.4916 |
| p55-75c|tte>10m|imb>50 | 2113 | 0.6985 | 0.6985 | 0.166 | 0.167 | 0.0506 | 0.0482 | 2113 | 0.4297 | 0.455 | 0.4538 |
| p25-45c|tte>10m|imb>50 | 2049 | 0.7194 | 0.7145 | 0.1787 | 0.1782 | 0.014 | 0.016 | 2049 | 0.5364 | 0.5294 | 0.5284 |
| p55-75c|tte5-10m|imb>50 | 2021 | 0.6551 | 0.6507 | 0.1565 | 0.1574 | 0.022 | 0.0274 | 2021 | 0.5265 | 0.5375 | 0.5402 |
| p10-25c|tte2-5m|imb>50 | 1845 | 0.7648 | 0.7648 | 0.1715 | 0.1718 | 0.0514 | 0.0628 | 1845 | 0.432 | 0.4577 | 0.4634 |
| p55-75c|tte2-5m|imb>50 | 1810 | 0.7033 | 0.7033 | 0.177 | 0.1823 | 0.1164 | 0.0714 | 1810 | 0.4729 | 0.5311 | 0.5086 |
| p25-45c|tte2-5m|imb>50 | 1496 | 0.6852 | 0.6852 | 0.1673 | 0.1713 | 0.0478 | 0.026 | 1496 | 0.4813 | 0.5052 | 0.4943 |
| p<10c|tte30-120s|imb>50 | 1376 | 0.7776 | 0.7776 | 0.1935 | 0.1945 | 0.0294 | 0.0756 | 1376 | 0.3772 | 0.3919 | 0.415 |
| p<10c|tte2-5m|imb>50 | 1351 | 0.7461 | 0.7461 | 0.1832 | 0.1821 | 0.0032 | 0.028 | 1351 | 0.3679 | 0.3663 | 0.3819 |
| p75-90c|tte2-5m|imb>50 | 1312 | 0.6585 | 0.6585 | 0.1622 | 0.1645 | 0.0604 | 0.0676 | 1312 | 0.5366 | 0.5064 | 0.5028 |
| p75-90c|tte5-10m|imb>50 | 1302 | 0.6229 | 0.5568 | 0.1464 | 0.1448 | 0.0844 | 0.0892 | 1302 | 0.4654 | 0.5076 | 0.51 |
| p10-25c|tte30-120s|imb>50 | 1161 | 0.733 | 0.733 | 0.1983 | 0.2073 | 0.0 | 0.0252 | 1161 | 0.497 | 0.497 | 0.5096 |
| p45-55c|tte>10m|imb>50 | 1062 | 0.6798 | 0.6798 | 0.1696 | 0.1678 | 0.015 | 0.0074 | 1062 | 0.5508 | 0.5583 | 0.5545 |
| p55-75c|tte30-120s|imb>50 | 985 | 0.6772 | 0.6721 | 0.1811 | 0.1787 | 0.039 | 0.01 | 985 | 0.5198 | 0.5003 | 0.5148 |
| p45-55c|tte5-10m|imb>50 | 861 | 0.6655 | 0.6655 | 0.1555 | 0.1576 | 0.0144 | 0.006 | 861 | 0.5482 | 0.5554 | 0.5512 |
| p75-90c|tte30-120s|imb>50 | 801 | 0.6117 | 0.6117 | 0.159 | 0.1608 | 0.0071 | 0.0004 | 801 | 0.4707 | 0.4671 | 0.4705 |
| p45-55c|tte2-5m|imb>50 | 770 | 0.7052 | 0.7052 | 0.171 | 0.1711 | 0.026 | 0.0684 | 770 | 0.461 | 0.474 | 0.4952 |
| p>90c|tte2-5m|imb>50 | 767 | 0.5332 | 0.5854 | 0.152 | 0.1512 | 0.1158 | 0.0972 | 767 | 0.4785 | 0.5364 | 0.5271 |
| p55-75c|tte>10m|imb10-50 | 754 | 0.7175 | 0.7162 | 0.1688 | 0.1715 | 0.0339 | 0.0384 | 754 | 0.4244 | 0.4413 | 0.4436 |
| p25-45c|tte30-120s|imb>50 | 721 | 0.6976 | 0.6976 | 0.1962 | 0.1974 | 0.0324 | 0.0148 | 721 | 0.5076 | 0.5238 | 0.515 |
| p25-45c|tte>10m|imb10-50 | 706 | 0.7167 | 0.7096 | 0.1777 | 0.1789 | 0.0522 | 0.041 | 706 | 0.4603 | 0.4864 | 0.4808 |
| p<10c|tte5-10m|imb>50 | 698 | 0.7493 | 0.7493 | 0.1802 | 0.1786 | 0.0298 | 0.0046 | 698 | 0.5344 | 0.5195 | 0.5321 |
| p>90c|tte30-120s|imb>50 | 650 | 0.5462 | 0.5092 | 0.154 | 0.1512 | 0.0702 | 0.1106 | 650 | 0.4646 | 0.4997 | 0.5199 |
| p10-25c|tte>10m|imb>50 | 633 | 0.7299 | 0.7188 | 0.1773 | 0.176 | 0.0246 | 0.0194 | 633 | 0.5261 | 0.5384 | 0.5358 |

## Reading

- On 41356 clean forward decisions (1002 windows, 2026-09-05T01:01:22Z to 2026-09-08T00:57:46Z) that neither lookup saw, candidate v4 vs frozen v3: relation top-1 agreement 0.6931 vs 0.6936 (delta -0.00051, 95% CI (-0.003, 0.00184), McNemar p 0.7005825651); mean P(observed action) 0.1743 vs 0.1731 (delta 0.00118, CI (0.00094, 0.00142)); relation L1 0.0318 vs 0.0299; post-fill COMPLETE gap -1.57 pp vs -1.46 pp; both PASS every frozen-spec dimension.
- Verdict `NOT_YET`: no material forward improvement: the gain is below the bar or its CI includes zero. Against the material bar: agreement delta -0.051 pp vs the +2.0 pp bar; mean P delta 0.00118 vs the +0.01 bar.
- The shadow loop's own proposal verdict rests on a much thinner holdout than its name suggests: the collector's incremental call reaches back ~4,000 fills (a few hours at L5's current rate), so the loop's 'last 3 days' rule falls back to a 0.7 time quantile of that window (a holdout of roughly an hour, 12-15 windows). A CANDIDATE_VALIDATED_OOS from that path is not forward evidence of the kind the owner asked for; this strict validation is.
- Promotion recommendation: NOT YET. Re-run this validation as the clean cohort grows (the deployed loop's proposals should be judged by this module's strict cut, not by their own split); a v4 built on the same recipe is expected to stay a near-no-op until the drifted imb>50 states accumulate enough post-freeze transitions to move the shrunk masses.

## Assumptions about the loop's internals

- The candidate is `ShadowLoop.build_candidate` (imported): frozen v3 counts PLUS the training cohort's transitions on the same full key; shrinkage alpha=10 at lookup time; `cadence_modal` copied unchanged.
- The collector's incremental call reaches back at most 8 pages x 500 rows (~4,000 fills, a few hours of L5 activity now); the deeper cohort here comes from the existing windowed DESC fetch helper with the raw cache outside the repo. The loop's own 'last 3 days' holdout rule therefore falls back to its 0.7 time quantile on a window that short.
- Decision rows are the loop's own (`decision_for`), enriched with observable fields; lookup keys are unchanged. The strict cut splits at settlement-window level so no holdout window's state was built from training fills.
- No frozen artifact is written; the only outputs are this file and its JSON twin.
