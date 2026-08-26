# ARB Control Plane

Last verified: **2026-08-26 09:38 UTC / 2026-08-26 05:38 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Latest run delta

- **PR #156 passed both GitHub Actions test runs, was promoted from draft, merged, and deployed.** It is read-only and intended to reject explicit Polymarket partial-game/F5 MLB rows before full-game K<->P semantic matching.
- Railway exposed a production wiring miss after the merge: `sports-generic-counterpart-shadow` was still starting `src.sports_kp_generic_mlb_game_breadth`, so the new wrapper was not active. The effective start command was corrected to `python -m src.sports_kp_generic_mlb_game_breadth_fullgame --interval-seconds 60`; `restartPolicy=ALWAYS` is preserved. Replacement deployment `b0f1a02f-1fd9-435b-ac0f-61dc85c4bc74` is **SUCCESS**.
- Fresh runtime then proved **#156 itself is insufficient**. The deployed wrapper still admits F5 slugs such as `tsc-mlb-...-f5-6pt5`, and the contaminated descriptive spread/total bins remain visible. Root cause from source inspection: the base Polymarket normalizer returns a **list** of normalized sides, while #156 treated the return as one side; additionally `source_label` prefers question/title and can omit the `f5` scope marker even though `venue_market_id` retains it. Therefore all current positive spread/game-total breadth rows remain **$0 capacity**.
- Prepared **draft PR #157** on isolated branch `feature/kp-full-game-scope-slug-fix`. It filters the normalized list and fails closed when **either `source_label` or `venue_market_id`** identifies partial-game scope. Regression tests cover a generic human label with an F5 market slug and the normalizer list return shape. No order transport, pricing, start-time, settlement, rule, or risk gate changes. It remains draft pending normal CI/review.
- The last contaminated breadth snapshot was: moneyline **26 semantic groups, 0 >=1c / 0 >=2c / 0 >=3c**; spread **26 groups, 12 / 10 / 9**; game total **112 groups, 70 / 70 / 70**. **Only the moneyline zero is non-suspicious descriptive evidence.** Spread/game-total positive rows contain explicit F5/full-game mismatches and receive $0 capacity credit.
- The separate decision-grade certified K<->P MLB totals collector remains unchanged: **1,031 >=1c / 0 >=2c / 0 >=3c** after fees from **1,661 observations / 9 semantic keys**. At the fixed provisional **>=80ms** realistic-capture floor it remains **137 >=1c / 0 >=2c / 0 >=3c**. Average selected-side q1 depth remains **~2,072.66 contracts**. This certified totals slice remains **$0/day deployable capacity at the fixed 3c floor**.
- Fresh G<->K runtime is still `SUCCESS` but currently shows a **Gemini reconnect storm across many one-symbol shards**, followed by Railway log-rate limiting (`Messages dropped: 489`). This lane remains read-only/disarmed and is not decision-grade from this interval. No unrelated service was stopped to improve telemetry.
- Fresh authoritative Neon `arb-trade-ledger`: **2026-08-26 ET = 0 TRADE_REALIZED / $0.0000 net**, and **0 unresolved durable capital reservations**. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.
- Fresh authoritative weather query: **0 weather-tagged TRADE_REALIZED / $0 verified realized since 2026-03-01**. Seasonal/historical weather edge is not credited as current realized performance.
- Kalshi account diagnosis remains **advanced tier, 900 bucket / 300 refill** for both reads and writes. Do not weaken backoff or disrupt unrelated consumers solely for census telemetry.
- The 72-hour wall clock continues with **no reset or pause**. Invalid/unmeasurable or rule-mismatched breadth contributes $0 for the relevant interval/family.

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
- **Unresolved durable capital reservations:** **0**.
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
- average selected-side q1 depth **~2,072.66**, max **22,647.58**;
- average quote skew **~45.53ms**;
- REST-backed Kalshi transport is labeled **REST_SNAPSHOT_NOT_WS_LIFETIME**;
- `read_only=true`, `order_entry=false`.

Interpretation: liquidity is not the blocker. This certified totals slice has not produced a >=2c or >=3c after-fee observation and currently contributes **$0/day deployable capacity at the fixed 3c floor**.

### K<->P MLB moneyline/spread/game-total breadth

PR #155 fixed the four-hour Kalshi scheduled-start timezone bug. Exact semantic overlap reaches book pricing, but current spread/game-total results remain contaminated by Polymarket partial-game/F5 rows colliding with full-game Kalshi contracts.

PR #156 is merged and the Railway service now starts its wrapper, but production verification proved the wrapper fails to filter these rows because it checks the wrong return shape/field. Draft **PR #157** is the bounded correction: filter the normalized list and inspect both human label and physical market slug. Until #157 passes CI, is promoted, and a fresh clean snapshot is observed:

- moneyline descriptive evidence: **0 >=1c / 0 >=2c / 0 >=3c** in the latest snapshot;
- spread/game-total positive rows: **invalid / $0 capacity**;
- guaranteed MLB game breadth capacity: **$0**;
- census clock does not pause or reset.

### Gemini<->Kalshi sports

Guaranteed-arb order entry remains disarmed/research-only. Caleb is settled **-$0.44** and there are **0 unresolved durable reservations**. Fresh runtime currently shows repeated Gemini reconnect failures across many one-symbol shards and Railway log-rate limiting; this interval is not decision-grade evidence of executable capacity. Do not reopen guaranteed G<->K execution without complete lifecycle settlement equivalence.

### Weather

Authoritative central records show **0 weather-tagged realized trades / $0 realized since 2026-03-01**. Historical or seasonal weather edge remains a separate research statistic and is not credited toward current realized performance.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED** from the confirmed bounded-campaign deadline -> `HALTED_FAIL_CLOSED` path. Do not reopen as guaranteed arbitrage because Gemini/Kalshi settlement-index equivalence is not proven.

## Highest-value next actions

1. Keep the fixed 72-hour wall clock running with no reset. Certified K<->P totals remain **$0/day at >=3c**.
2. Get **draft PR #157** through normal CI/review. If green, merge and immediately verify that no `-f5-` Polymarket market reaches the full-game pricing output.
3. Once clean, report full-game moneyline/spread/game-total >=1c/>=2c/>=3c bins, q1 depth, and missing-to-3c. Rule-incomplete rows remain $0 even after scope cleanup until exact settlement rules are certified.
4. Keep G<->K disarmed and treat the current Gemini reconnect storm as a feed-quality interval, not executable capacity. Do not disrupt unrelated services solely to improve it.
5. Keep rule-incomplete player props at capacity $0 until settlement/postponement/cancellation/stat-correction semantics are certified.
6. Preserve Kalshi account headroom/backoff and the verified advanced **900 bucket / 300 refill** limits.
7. Preserve fixed **<$50 / $50-$149 / >=$150** verdict thresholds and the one-time +24h maximum data-quality allowance.
8. Keep AWS latency/maker-routing work isolated from this control-plane workstream.
