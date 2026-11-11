# L5 RESTRICTION AUDIT

_Generated 2026-09-07T18:00:52.562553+00:00. Program: L5 is the strategy; pairing, rebates, residual caps and sports selection are implementation/economic considerations, not replacements for his behavior; any safety override is recorded as OUR deviation from L5; a hard catastrophic-loss shell around the account, maximum L5 fidelity inside it._

## The question

What restrictions are preventing us from executing what L5 would actually do, and how far can we safely remove them with this bankroll?

## The shell (hard catastrophic-loss bound, dollars of this account)

- Bankroll (venue, last read): **$111.1422**
- Per-market cap $7.5 x concurrency 2 = worst case **$15.0** (13.5% of bankroll)
- Daily loss stop **-$15.0** (13.5%)
- Preflight deployed cash required (3x worst case): $45.0 -> OK

## L5's own numbers (canonical history, DERIVED from executed fills)

- Fills 71984, windows 2216; taker share of known-side fills 0.1659
- Shares per fill p25/p50/p75: 5.0/12.5/40.0; dollars per fill p50 $4.5
- Pair unit cost at completion p10/p25/p50/p75/p90: 0.9318/0.9605/0.9876/1.0151/1.0553; above 1.00: 0.3723, above 1.02: 0.2209
- Residual dollars per window p50/p90/p99: $18.5701/$121.159/$354.4883; spend per window p50 $92.9151
- Simultaneous windows: median 2, share of active time at 2+: 0.507929
- Reprice cadence: multi-print cluster p50 2 s, p90 15 s
- Share of his fills priced inside our 0.35-0.65 band: 0.2909

## What bound in our live decisions

- Decisions audited: 427 (deduplicated by decision_id)
- L5 said / we did (top rows): pre-R2 extend->complete x77; R4 extend->hold x54; pre-R2 complete->complete x51; pre-R2 extend->extend x36; R4 complete->hold x30; R4 extend->complete x26; R4 open->hold x25; R2C open->open x22
- Would act without the cap / acted: R4 extend->hold x62; R4 complete->hold x22; pre-R2 extend->complete x20; R3L complete->hold x7; pre-R2 complete->hold x3; R2C complete->hold x3
- COMPLETE counterfactuals: 211; L5's touch pair above 1.00 in 101 (0.4787); pair at touch p50 0.9992, p90 1.1475

## Restrictions, ranked

| # | Restriction | Class | Ours | L5 does | Bound live | Removal | Worst case under shell | R5 |
|---|---|---|---|---|---|---|---|---|
| 1 | Completion pair-cost ceiling | safety cap (owner) | COMPLETE only at pair <= 1.00 (fee-safe bound price) | pair unit cost at completion p50 0.9876, p75 1.0151, p90 1.0553; 0.3723 of his completions above 1.00, 0.2209 above 1.02, 0.1127 above 1.05 | {"R4_complete_held": 22, "R4_complement_not_priceable": 21, "complete_counterfactuals_touch_above_1_00": 101} | price the complement at L5's touch (deficit-side touch join), whatever the pair; the loss on an above-par pair is (pair - 1) x matched shares and is bounded by the per-market cap | $0.83 (DERIVED: at L5's p90 pair 1.0553 on the whole per-market cap the above-par cost is this many dollars; the shell already bounds it) | **REMOVE in R5 (record pair > 1.00 as L5-consistent, not as a deviation)** |
| 2 | Per-market unmatched-dollar cap | safety cap (owner) | 2.50 USD unmatched per market | residual per window p50 $18.5701, p90 $121.159 on spend p50 $92.9151 (residual/spend p50 0.1999); 0.9836 of his EXTENDs would be refused by a 1-action-unit cap | {"R4_extend_held": 62, "R4_refusals": 54} | remove the separate unmatched cap; the per-market cap ($7.5) is the only inventory bound, exactly as L5's residual is bounded only by what he committed | $7.5 (already the per-market cap; no new dollars at risk) | **REMOVE in R5** |
| 3 | Maker rest floor before cancel/replace | (b) owner execution policy, not a venue requirement | 30 s rest, then cancel; reprice cadence >= 30 s | multi-print cluster duration p50 2 s, p90 15 s; clusters longer than our 30 s deadline 0.0389 | {"ours": 23, "fill_share": 0.2} | reprice at the model's own reevaluation cadence (2 s / 30 s per state) with cancel/replace; class (b): an owner execution policy, not a venue requirement | $0.0 (no new dollars at risk; more cancel/replace messages) | **REMOVE in R5** |
| 4 | Concurrency (markets worked at once) | safety cap (owner) | 1 market at a time; a held residual blocks every other candidate | median 2 simultaneous windows, 0.507929 of active time at 2+, max 4 | {"R4_open_held": 1, "note": "every candidate cycle while a residual was held was skipped (CAMPAIGN_HOLD:POSITION_HELD)"} | concurrency 2 (L5's median), worst case 2 x $7.5 = $15.0 | $15.0 (the shell's worst case) | **REMOVE to 2 in R5** |
| 5 | Campaign cohort stop | approval ladder (owner) | 3 markets then stop and report | continuous; 2,216 windows over the history | {"R4": "campaign stops at 3 settled; the 6 h horizon stopped it once at 14:48Z"} | continuous operation under the shell: daily loss stop and the 7-day horizon are the stops; report per settlement instead of per cohort | $15.0 (the daily stop) | **REMOVE in R5 (report cadence unchanged)** |
| 6 | Entry price band | observation scope (owner) | enter only with YES bid in 0.35-0.65 | share of L5 fills priced inside 0.35-0.65: 0.2909 | {"R4_entry_band_left": 8} | widen entry to every state the frozen model covers with evidence (the off-distribution hold still protects unknown states); size unchanged so the worst case is unchanged. This is the largest fidelity gap by share of his activity (71% of his fills are outside the band) | $7.5 (unchanged per market) | **STAGE (R5b) after the band-inside sample is compared out of sample** |
| 7 | Maker-only (post-only), taker disabled | fee/risk policy (owner) | 0% taker | taker share of known-side fills 0.1659; model p_taker 2-3% of actions by tte bucket | {"live_policy": "taker disabled on every row"} | allow taker only when the model's p_taker draw says so for that state (2-3% of actions); fee 0.06 x p(1-p) per share, bounded by the per-market cap | $0.22 (DERIVED: max taker fee if every share of the per-market cap were taken at p=0.5) | **STAGE (R5b) after the fee is measured on the first taker fills** |
| 8 | Off-distribution hold | fidelity rule (frozen spec) | hold when the (state, relation) key is unknown | n/a (states without L5 evidence) | {"R4_refusals": 24} | none; fidelity protection. Shrinks as the versioned lookup refreshes from his newest activity | $0.0 (n/a) | **KEEP (refresh the lookup)** |
| 9 | Per-action size / size cap | bankroll sizing (owner) | ~5 shares / $2.50 per action | shares per fill p25/p50/p75 5.0/12.5/40.0; dollars per fill p50 $4.5 | {"every_row": "size_divergence=venue_minimum_vs_l5_target", "R4_spend_cap_reached": 1} | keep the action size where it is; size scales with the bankroll after profitability is measured (owner: profitability tells how far to scale, not how to rewrite) | $7.5 (unchanged) | **KEEP (scale later)** |
| 10 | Strict evidence gate (prints in window, two-sided flow, q1 touch depth) | evidence requirement (owner 5526683554) | admit only markets with recent prints and served two-sided books | trades where flow exists | {"R4_refusals": "thin_flow dominated while the feed was dead; honest refusals"} | none; this is the evidence requirement, not a deviation | $0.0 (n/a) | **KEEP** |

## R5 rung that follows (PROPOSAL, owner writes the id)

- Shell: {"per_market_cap_usd": 7.5, "concurrency": 2, "worst_case_simultaneous_usd": 15.0, "daily_loss_stop_usd": 15.0, "preflight_deployed_required_usd": 45.0, "horizon_s": 604800, "maker_only": true, "per_action_target_usd": 2.5}
- Removed: pair_cost_ceiling, unmatched_usd_cap, maker_rest_30s, concurrency_1, cohort_3_markets
- Kept: off_distribution_hold, action_size, strict_evidence_gate
- Staged for R5b: observation_band, maker_only
- Deviation recording: every live decision keeps would_act_without_safety_cap, divergence_class and counterfactual_l5_price; under R5 a pair above 1.00 at L5's touch is L5-consistent (no deviation); the per-market cap and the daily stop are the only deviations and are labelled shell_cap
- Stop: daily loss stop, the 7-day horizon, or the owner; no cohort stop; report per settlement

## Labels

- Realized dollars are never in this document; every dollar above is a cap, a bound or a DERIVED counterfactual.
- Nothing here mutates the live path; the R4 shell runs unchanged until the owner writes the R5 identity.
