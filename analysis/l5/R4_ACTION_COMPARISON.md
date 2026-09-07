# R4 action-by-action comparison

Markets: 2 (2 settled) | decisions 144 | submitted 45 | filled 9

## Campaign

- sampled relation: {'complete': 0.3681, 'extend': 0.3819, 'open': 0.25}
- acted (excl. hold): {'complete': 0.7111, 'extend': 0.0222, 'open': 0.2667}
- expected L5: {'open': 0.2278, 'extend': 0.3964, 'complete': 0.3758}
- L1 sampled vs L5: 0.0444 | acted vs L5: 0.7484
- hold share 0.6875; holds by refusal: {'unmatched_usd_cap': 44, 'off_distribution_hold': 24, 'complement_not_priceable': 21, 'entry_band_left': 8, 'complete_without_residual': 1, 'spend_cap_reached': 1}
- cap refusals: 44 | off-distribution decisions: 24
- completions filled 5: unlocked by the 1.00 bound 1; over par on the engine basis 0, on open lots 1, marginal lots over par 1
- venue realized sum: 1.9955 | derived matched-pair sum: 1.0361

## aec-atpcq-aleher-tundur-2026-09-07

- decisions 79 idx [0, 78]; states {'p10-25c|tte>10m|imb1-10': 36, 'p45-55c|tte>10m|flat': 30, 'p45-55c|tte>10m|imb10-50': 12, 'p45-55c|tte>10m|imb1-10': 1}
- sampled {'complete': 0.4051, 'extend': 0.2278, 'open': 0.3671} | acted excl. hold {'complete': 0.625, 'open': 0.375} | expected L5 {'open': 0.3271, 'extend': 0.2672, 'complete': 0.4057}
- L1 sampled vs L5 0.08 | acted vs L5 0.5344
- hold share 0.7975; holds by refusal {'off_distribution_hold': 24, 'complement_not_priceable': 21, 'unmatched_usd_cap': 9, 'entry_band_left': 8, 'complete_without_residual': 1}; off-distribution 24
- orders 16 submitted / 5 filled (all maker); unfilled by reason {'rejected': 7, 'deadline': 2, 'repriced': 2}
- fills: open YES 5.31@0.47 (idx 2); complete NO 5.2@0.51 (idx 5); complete NO 0.11@0.50 (idx 6); open YES 5.1@0.49 (idx 10); complete NO 3.24@0.22 (idx 42)
- complement decisions: {'with_counterfactual': 31, 'within_old_bound_0_98': 31, 'unlocked_by_rung_bound': 0, 'priced_down_to_par_from_above': 27}
- completions: {'filled': 3, 'unlocked_by_rung_bound': 0, 'engine_over_par': 0, 'open_lots_over_par': 0, 'marginal_lots_over_par': 0}
  - spring_complete_6 NO 5.2@0.51: engine 0.9800, open-lots 0.98, marginal 5.2x0.47->0.98; unlocked_by_rung_bound False
  - spring_complete_1 NO 0.11@0.50: engine 0.97, open-lots 0.97, marginal 0.11x0.47->0.97; unlocked_by_rung_bound False
  - spring_complete_5 NO 3.24@0.22: engine 0.6997982708933717579250720461, open-lots 0.71, marginal 3.24x0.49->0.71; unlocked_by_rung_bound False
- size executed/L5 p50: {'n': 79, 'mean': 0.4648, 'p50': 0.462, 'p90': 0.52, 'max': 0.555}; cadence s: {'n': 78, 'mean': 37.1, 'p50': 10.1, 'p90': 31.2, 'max': 1803.3}
- economics: settled True (REALIZED, position_resolution_activity); venue realized 1.9955; derived {'bought_yes': '10.41', 'bought_no': '8.55', 'avg_yes': '0.4797982708933717579250720461', 'avg_no': '0.3999766081871345029239766082', 'cost_basis_usd': '8.4145', 'matched_pairs': '8.55', 'matched_pair_cost': '0.8797748790805062608490486543', 'matched_contribution_usd': '1.027924783861671469740634006', 'directional_residual': '1.86', 'residual_contribution_usd': '0.967575216138328530259365994', 'fees_usd': 'venue_unreported'}

## aec-atpcq-norgom-ollwal-2026-09-07

- decisions 65 idx [0, 64]; states {'p45-55c|tte>10m|imb1-10': 55, 'p25-45c|tte>10m|flat': 4, 'p45-55c|tte>10m|flat': 3, 'p45-55c|tte>10m|imb10-50': 3}
- sampled {'complete': 0.3231, 'extend': 0.5692, 'open': 0.1077} | acted excl. hold {'complete': 0.7586, 'extend': 0.0345, 'open': 0.2069} | expected L5 {'open': 0.1071, 'extend': 0.5534, 'complete': 0.3395}
- L1 sampled vs L5 0.0328 | acted vs L5 1.0378
- hold share 0.5538; holds by refusal {'unmatched_usd_cap': 35, 'spend_cap_reached': 1}; off-distribution 0
- orders 29 submitted / 4 filled (all maker); unfilled by reason {'deadline': 21, 'rejected': 3, 'repriced': 1}
- fills: open YES 5.31@0.47 (idx 5); complete NO 5.2@0.51 (idx 8); extend YES 4.9@0.51 (idx 21); complete NO 5.01@0.51 (idx 63)
- complement decisions: {'with_counterfactual': 22, 'within_old_bound_0_98': 5, 'unlocked_by_rung_bound': 6, 'priced_down_to_par_from_above': 12}
- completions: {'filled': 2, 'unlocked_by_rung_bound': 1, 'engine_over_par': 0, 'open_lots_over_par': 1, 'marginal_lots_over_par': 1}
  - spring_complete_3 NO 5.2@0.51: engine 0.98, open-lots 0.98, marginal 5.2x0.47->0.98; unlocked_by_rung_bound False
  - spring_complete_4 NO 5.01@0.51: engine 0.9991968658178256611165523996, open-lots 1.019121756487025948103792415, marginal 0.11x0.47->0.98, 4.90x0.51->1.02; unlocked_by_rung_bound True
- size executed/L5 p50: {'n': 65, 'mean': 0.5382, 'p50': 0.541, 'p90': 0.568, 'max': 0.57}; cadence s: {'n': 64, 'mean': 20.0, 'p50': 30.1, 'p90': 32.9, 'max': 61.4}
- economics: settled True (SETTLED_REALIZED_UNCONFIRMED, venue_market_resolved_account_flat); venue realized None; derived {'bought_yes': '10.21', 'bought_no': '10.21', 'avg_yes': '0.4891968658178256611165523996', 'avg_no': '0.51', 'cost_basis_usd': '10.2018', 'matched_pairs': '10.21', 'matched_pair_cost': '0.9991968658178256611165523996', 'matched_contribution_usd': '0.008200000000000000000000000084', 'directional_residual': '0.00', 'residual_contribution_usd': None, 'fees_usd': 'venue_unreported'}

Labels: realized = venue number only; derived = arithmetic on ledger fills; engine / open_lots / marginal pair bases as defined in the module.
