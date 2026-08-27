# ARB Control Plane

Last verified: **2026-08-27 14:42 UTC / 2026-08-27 10:42 ET**

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

- `sports-kp-live-executor`: Railway **SUCCESS/read-only census**. Fresh 1-hour Railway metrics at 10:42 ET show 61/61 samples with average CPU ~0.070 and continuous RX/TX traffic, confirming the collector remains alive. Detailed runtime-log inspection is temporarily blocked by Railway agent usage quota, so no newer decision-census counts are claimed beyond the last directly verified sample.
- `sports-generic-counterpart-shadow`: Railway **SUCCESS/read-only maker + delayed-hedge shadow**. Fresh 1-hour metrics show 61/61 samples with average CPU ~0.035 and continuous network traffic. No order authority.
- `sports-gk-autonomous-supervisor`: Railway **SUCCESS/read-only/disarmed hot shadow**. Fresh 1-hour metrics show 61/61 samples with average CPU ~0.671 and sustained RX/TX traffic, confirming the process remains alive. Order entry remains false.
- `crypto-15m-sol-live-canary-10bp-50ms`: remains **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.
- `crypto-live-balance-reconcile-v2`: latest deployment is **CRASHED** (2026-08-27 13:49 UTC). It remains an auxiliary read-only reconciliation blocker and does not authorize restarting any crypto trading lane.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker

Last directly verified cumulative sample remains:

- **53,237 comparable evaluations**;
- **1,661 decision observations** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms;
- average closed positive-signal lifetime ~174 ms, max 567 ms;
- Kalshi transport explicitly labeled **REST_SNAPSHOT_NOT_WS_LIFETIME**.

Raw trigger volume was still rising at the last direct-log checkpoint while no new comparable/decision observations were being created. Until fresh runtime summaries prove otherwise, this interval contributes **$0 measurable capacity** and is not treated as millions of additional zero-edge observations. The certified totals slice supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After correcting start-time normalization and excluding F5/partial-game contamination, clean full-game moneyline/spread/total evidence remains **0 >=1c / 0 >=2c / 0 >=3c** in the last certified snapshots. Displayed depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow

Last directly verified delayed-hedge evidence remains decision-negative at realistic latency. Immediate modeled maker fills do not receive deployable-capacity credit. The most recent verified observable delayed-hedge cohort showed no scalable >=3c capture and adverse movement after the simulated fill; unobservable fills receive $0 capacity. The K<->P full-game moneyline mechanism also fails exact postponement/reschedule lifecycle equivalence. Maker shadow therefore remains **$0 guaranteed/deployable capacity and $0 REALIZED P&L**.

### Strict MLB player props / incentives

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed capacity** for this census window. Polymarket incentive denominator/time-weighted yield/pickoff accounting remains incomplete and receives **$0 capacity credit**.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. Fresh service metrics prove the process is alive, but no fresh decision-grade comparable sample can be claimed while detailed runtime logs are quota-blocked. The last directly verified state remained frozen at **120,836 comparable evaluations**, with Gemini connectivity as the measurement blocker and Kalshi books initialized. Historical hot-shadow observations remain uncertified and receive $0 guaranteed capacity.

This interval is unmeasurable/$0 guaranteed capacity, not fresh negative edge. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Evidence favors concurrent websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

Fresh direct Neon queries at **2026-08-27 10:42 ET** confirm:

- **2026-08-27 ET: 0 ledger events, 0 `TRADE_REALIZED`, $0.0000 realized net, 0 capital reservations**;
- weather: **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 since 2026-03-01**;
- Caleb Durbin remains prior **`SETTLED_ONE_LEG`, -$0.44 REALIZED**.

No shadow/model result is credited to realized P&L.

## Current verdict trajectory

Guaranteed/deployable evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- maker delayed-hedge evidence: no scalable certified >=3c realistic-capture capacity; unobservable fills receive $0;
- K<->P moneyline maker rules: lifecycle mismatch => **$0 guaranteed capacity**;
- strict props/incentives: uncertified/incomplete => **$0**;
- G<->K: disarmed and currently not decision-measurable => **$0 guaranteed capacity**;
- weather verified realized since March: **$0**.

Current evidence continues to track toward the pre-committed **<$50/day** bucket. Do not declare the final verdict before the fixed deadline.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker and maker/markout collectors alive; active-but-unmeasurable intervals score $0 and are not mislabeled as fresh zero-edge samples.
3. Keep G<->K guaranteed execution disarmed; do not count raw activity without two-sided Gemini connectivity and full lifecycle certification.
4. Do not spend the remaining census window manufacturing same-venue violations or building around uncertified props.
5. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
6. Keep weather historical/backtest edge separate from verified live realized P&L.
7. Repair/read the auxiliary `crypto-live-balance-reconcile-v2` crash only as a reconciliation/observability task; it must not reopen crypto execution.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.
