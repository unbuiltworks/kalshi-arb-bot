# ARB Control Plane

Last verified: **2026-08-26 09:40 UTC / 2026-08-26 05:40 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Latest run delta

- **PR #156 passed CI, merged, and exposed two separate correctness problems in production rather than a real spread/total edge.** First Railway was still starting the unwrapped breadth module; that override was corrected to `python -m src.sports_kp_generic_mlb_game_breadth_fullgame --interval-seconds 60`. Second, the wrapper itself treated the base normalizer's list return as one side and inspected only `source_label`, while Polymarket human labels can omit the F5 marker even when `venue_market_id` retains `-f5-`.
- Built **PR #157** to fix that exact defect: filter the normalized list and reject a Polymarket row when either its human label or physical market ID identifies partial-game scope. Regression tests cover a generic label paired with an `-f5-` slug and the real list return shape. No order transport, pricing, start-time, settlement, rule, or risk gate changed.
- **PR #157 CI passed, it was promoted, merged at `9e11a4344e47b89bef67c37e257e01c2782a7f28`, and Railway deployment `f4de216f-0ba5-4706-aae7-d6f7e223319d` is SUCCESS.** The service retains `restartPolicy=ALWAYS` and the full-game wrapper entrypoint.
- The first fresh post-#157 production snapshot is decisive: **630 Polymarket normalized sides / 536 Kalshi sides / 258 physical pairs / 97 semantic unique groups**, with **no F5 rows in the priced output**. Clean full-game family bins are now:
  - moneyline: **26 semantic groups, 0 >=1c / 0 >=2c / 0 >=3c**;
  - spread: **26 groups, 0 / 0 / 0**;
  - game total: **45 groups, 0 / 0 / 0**.
- The clean full-game sample is liquid but economically negative. Average q1 depth is about **4,948.50 moneyline / 953.23 spread / 891.55 game total**. The best rows are still negative after fees: roughly **-2c** at best; median missing-to-3c is about **6.5c moneyline / 6c spread / 6.5c game total**. These rows remain `RULE_INCOMPLETE_NORMAL_EVENT_SHADOW_NOT_EXECUTION_ELIGIBLE`, so guaranteed capacity is still $0 even before rule certification; importantly, the prior 30-40c F5 mirage is gone.
- The separate decision-grade certified K<->P MLB totals collector remains unchanged: **1,031 >=1c / 0 >=2c / 0 >=3c** after fees from **1,661 observations / 9 semantic keys**. At the fixed provisional **>=80ms** realistic-capture floor it remains **137 >=1c / 0 >=2c / 0 >=3c**. Average selected-side q1 depth remains **~2,072.66 contracts**. This certified totals slice remains **$0/day deployable capacity at the fixed 3c floor**.
- Fresh G<->K runtime remains `SUCCESS` but is currently producing a **Gemini reconnect storm across many one-symbol shards**, followed by Railway log-rate limiting (`Messages dropped: 489`). Guaranteed G<->K order entry remains disarmed/research-only; this interval is not decision-grade and no unrelated service was stopped to improve telemetry.
- Fresh authoritative Neon `arb-trade-ledger`: **2026-08-26 ET = 0 TRADE_REALIZED / $0.0000 net**, with **0 unresolved durable capital reservations**. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.
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

The scheduled-start timezone bug and partial-game/F5 contamination are now both removed from the active read-only breadth path. PR #157 is merged and fresh production output contains only full-game physical market IDs.

Latest clean descriptive snapshot:

- moneyline: **26 semantic groups, 0 >=1c / 0 >=2c / 0 >=3c**; avg q1 depth ~4,948.50; best edge about -2c;
- spread: **26 groups, 0 / 0 / 0**; avg q1 depth ~953.23; best edge about -2c;
- game total: **45 groups, 0 / 0 / 0**; avg q1 depth ~891.55; best edge about -3c.

The generic breadth lane remains explicitly rule-incomplete/read-only, so these descriptive zeros are not settlement certification. They do, however, remove the earlier false-positive F5 rows and add strong evidence that current full-game taker/taker K<->P pricing is not approaching the fixed 3c floor. Guaranteed capacity remains **$0** unless both economics and exact settlement rules later pass.

### Gemini<->Kalshi sports

Guaranteed-arb order entry remains disarmed/research-only. Caleb is settled **-$0.44** and there are **0 unresolved durable reservations**. Fresh runtime currently shows repeated Gemini reconnect failures across many one-symbol shards and Railway log-rate limiting; this interval is not decision-grade evidence of executable capacity. Do not reopen guaranteed G<->K execution without complete lifecycle settlement equivalence.

### Weather

Authoritative central records show **0 weather-tagged realized trades / $0 realized since 2026-03-01**. Historical or seasonal weather edge remains a separate research statistic and is not credited toward current realized performance.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED** from the confirmed bounded-campaign deadline -> `HALTED_FAIL_CLOSED` path. Do not reopen as guaranteed arbitrage because Gemini/Kalshi settlement-index equivalence is not proven.

## Highest-value next actions

1. Keep the fixed 72-hour wall clock running with no reset. Certified K<->P totals remain **$0/day at >=3c**, and the first clean full-game ML/spread/total snapshot is also **0/0/0 across all three families**.
2. Continue collecting clean full-game snapshots to establish realistic time-weighted/overlap-adjusted capacity; do not treat one snapshot as a final verdict, and do not lower the 3c floor.
3. Add only **strict player props whose settlement/postponement/cancellation/stat-correction semantics can be proven equivalent**. Participation proof alone is insufficient; uncertified props remain $0 capacity.
4. Keep G<->K disarmed and treat the current Gemini reconnect storm as a feed-quality interval, not executable capacity. Do not disrupt unrelated services solely to improve it.
5. Preserve Kalshi account headroom/backoff and the verified advanced **900 bucket / 300 refill** limits.
6. Preserve fixed **<$50 / $50-$149 / >=$150** verdict thresholds and the one-time +24h maximum data-quality allowance.
7. Keep AWS latency/maker-routing work isolated from this control-plane workstream.
