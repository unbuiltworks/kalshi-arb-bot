# ARB Control Plane

Last verified: **2026-08-26 10:40 UTC / 2026-08-26 06:40 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Latest run delta

- **K<->P production is alive and both relevant collectors are SUCCESS.** `sports-kp-live-executor` continues receiving Polymarket WS traffic and Kalshi REST snapshots; `sports-generic-counterpart-shadow` is also healthy on the clean full-game wrapper. No live order authority was added.
- The certified event-driven MLB-total census sample has **stopped growing overnight even though transport triggers continue to rise**: cumulative comparable evaluations remain **53,237**, decision observations **1,661 / 9 semantic keys**, after-fee bins **1,031 >=1c / 0 >=2c / 0 >=3c**, and provisional >=80ms capture bins **137 / 0 / 0**. Average selected-side q1 depth remains **~2,073**, average quote skew **~45.5ms**, descriptive positive-signal lifetime **~174ms avg / ~567ms max**. This is not treated as a new zero-edge interval; the process is alive, but the strict semantic set is not rotating into fresh comparable totals overnight.
- Fresh clean full-game K<->P breadth at ~10:39 UTC is stronger negative evidence on the current Aug. 26/27/28 slate: **54 Polymarket events / 67 Kalshi events / 258 physical comparable pairs / 97 semantic groups**, with no ambiguous semantic keys and no F5 contamination.
  - moneyline: **26 semantic groups, 0 >=1c / 0 >=2c / 0 >=3c / 0 positive**, avg q1 depth **~6,459**, p50 ~279, max ~58,081; best current after-fee row about **-2c** and median gap to the +3c target ~**6.5c**;
  - spread: **26 groups, 0 / 0 / 0 / 0 positive**, avg q1 depth **~1,124**, p50 ~740, max ~4,699; best about **-2c**, median gap to target ~**6.5c**;
  - game total: **45 groups, 0 / 0 / 0 / 0 positive**, avg q1 depth **~977**, p50 ~208, max ~11,172; best about **-2.5c**, median gap to target ~**6.5c**.
- These game-market rows remain `RULE_INCOMPLETE_NORMAL_EVENT_SHADOW_NOT_EXECUTION_ELIGIBLE`, so guaranteed capacity is **$0** regardless; importantly, even before full rule certification there is currently **not one positive after-fee row**. The evidence continues to point to **edge magnitude, not liquidity**, as the limiting factor.
- Existing rule-fingerprint evidence also remains restrictive rather than permissive. The prior exact K<->P MLB-total audit found settlement differences in official source / postponement / interrupted-game / cancellation semantics and therefore `exact_settlement_equivalent=false`. No strict K<->P player-prop family produced new complete settlement/cancellation/stat-correction certification in this run. Under the fixed protocol, uncertified props remain **$0 capacity** rather than earning an open-ended integration project.
- Fresh authoritative Neon `arb-trade-ledger`: **2026-08-26 ET = 0 TRADE_REALIZED / $0.0000 net**. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.
- Fresh authoritative weather query: **0 weather-tagged TRADE_REALIZED / $0 verified realized since 2026-03-01**. Seasonal/historical weather edge is not credited as current realized performance.
- The old Gemini<->Kalshi 15m crypto executor remains **CRASHED/contained**. Do not reopen it as guaranteed arbitrage; its bounded-campaign failure is proven but settlement-index equivalence remains unproven.
- Kalshi account diagnosis remains **advanced tier, 900 bucket / 300 refill** for both reads and writes. No unrelated Kalshi service was stopped and no backoff was weakened.
- The 72-hour wall clock continues with **no reset or pause**. Invalid/unmeasurable or rule-mismatched breadth contributes $0 for the relevant interval/family. The fixed verdict thresholds remain unchanged.

## Objective and accounting rule

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

## Edge-census protocol

The 72-hour K<->P wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and is **not paused or reset** by instrumentation defects. Invalid/unmeasurable intervals score **$0 deployable capacity for that wall-clock interval**.

Fixed verdict thresholds remain unchanged:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

Data/feed quality may add at most one **+24 hour** extension. An unmeasurable lane after that contributes **$0 deployable capacity**. Thresholds do not move after seeing results.

Required evidence remains explicit >=1c/>=2c/>=3c after-fee bins, exact executable q1 depth, quote skew, source quality, valid signal lifetime, semantic dedupe, overlap-adjusted capacity, and a provisional **>=80ms** realistic-capture floor. The latency floor may rise to measured production p95 but may not be lowered to rescue capacity.

Same-venue Gemini/Polymarket/Kalshi zero results remain near-verdict evidence. Do not spend engineering time manufacturing ladder violations.

## Current authoritative scoreboard

- **2026-08-26 ET:** **0 TRADE_REALIZED / $0.0000 net**.
- Caleb Durbin: **-$0.44 REALIZED one-leg sports loss**.
- Weather since **2026-03-01:** **0 weather-tagged TRADE_REALIZED / $0.0000 verified realized**.
- Same-Gemini crypto prior snapshot: roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Cross-venue census

### Kalshi<->Polymarket strict MLB totals

Latest cumulative verified decision-grade totals evidence:

- **53,237 comparable evaluations**;
- **1,661 decision-census observations** across **9 semantic keys**;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth **~2,073**, max **~22,648**;
- average quote skew **~45.5ms**;
- descriptive positive-signal lifetime **~174ms avg / ~567ms max**;
- REST-backed Kalshi transport is labeled **REST_SNAPSHOT_NOT_WS_LIFETIME**;
- `read_only=true`, `order_entry=false`.

Interpretation: liquidity is not the blocker. This certified totals slice has not produced a >=2c or >=3c after-fee observation and currently contributes **$0/day deployable capacity at the fixed 3c floor**. The transport is still alive, but the strict semantic sample has not rotated overnight; that does not reset the wall clock or justify an extension beyond the single allowed data-quality allowance.

### K<->P MLB moneyline/spread/game-total breadth

The scheduled-start timezone bug and partial-game/F5 contamination are removed from the active read-only breadth path. Fresh production at ~10:39 UTC is clean and current:

- **54 Polymarket events / 67 Kalshi events / 258 physical comparable pairs / 97 semantic groups**;
- moneyline: **26 semantic groups, 0 >=1c / 0 >=2c / 0 >=3c**; avg q1 depth ~6,459; best edge about -2c;
- spread: **26 groups, 0 / 0 / 0**; avg q1 depth ~1,124; best edge about -2c;
- game total: **45 groups, 0 / 0 / 0**; avg q1 depth ~977; best edge about -2.5c.

The generic breadth lane remains explicitly rule-incomplete/read-only, so these descriptive zeros are not settlement certification. They are nevertheless strong economic evidence: the current full-game K<->P books are generally about **5-7c short of the +3c target** after fees despite substantial depth. Guaranteed capacity remains **$0** unless both economics and exact settlement rules pass.

### Strict K<->P player props

Only families with complete settlement/postponement/cancellation/participation/stat-correction equivalence may enter guaranteed-arb capacity. No new player-prop family reached that certification in this run. Existing read-only prop probes remain research evidence only; participation proof alone is insufficient. Under the fixed 72-hour protocol, uncertified prop families contribute **$0 capacity** rather than triggering open-ended engineering.

### Gemini<->Kalshi sports

Guaranteed-arb order entry remains disarmed/research-only. Caleb is settled **-$0.44 REALIZED**. The recent Gemini reconnect-storm interval remains feed-quality evidence only; no unrelated service is to be disrupted solely to improve census telemetry. Do not reopen guaranteed G<->K execution without complete lifecycle settlement equivalence.

### Weather

Authoritative central records show **0 weather-tagged realized trades / $0 realized since 2026-03-01**. Historical or seasonal weather edge remains a separate research statistic and is not credited toward current realized performance.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED** from the confirmed bounded-campaign deadline -> `HALTED_FAIL_CLOSED` path. Do not reopen as guaranteed arbitrage because Gemini/Kalshi settlement-index equivalence is not proven.

## Highest-value next actions

1. Keep the fixed 72-hour wall clock running with no reset. The certified K<->P totals slice remains **$0/day at >=3c**, and the fresh clean full-game ML/spread/total snapshot is also **0/0/0 across all three families**.
2. Continue current clean snapshots through the committed window and compute time-weighted/overlap-adjusted capacity; do **not** lower the 3c floor or build another same-venue strategy.
3. Give strict K<->P player props only a bounded certification check using existing rule probes. If complete lifecycle equivalence cannot be proven within the existing census window, score props at **$0 capacity** rather than extend research.
4. Keep G<->K guaranteed execution disarmed and Caleb's -$0.44 loss realized.
5. Preserve Kalshi account headroom/backoff and the verified advanced **900 bucket / 300 refill** limits.
6. Preserve fixed **<$50 / $50-$149 / >=$150** verdict thresholds and the one-time +24h maximum data-quality allowance.
7. Keep AWS latency/maker-routing work isolated from this control-plane workstream.
