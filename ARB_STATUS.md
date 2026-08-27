# ARB Control Plane

Last verified: **2026-08-27 13:47 UTC / 2026-08-27 09:47 ET**

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

- `sports-kp-live-executor`: Railway **SUCCESS/read-only census**. Fresh runtime summaries continue every ~10 seconds; raw triggers exceeded **87.6M** during this run. The process is alive, but `comparable_evaluations` remains frozen at **53,237** and the decision sample at **1,661**, so the current interval is unmeasurable rather than fresh zero-edge evidence.
- `sports-generic-counterpart-shadow`: Railway **SUCCESS/read-only maker + delayed-hedge shadow**. Fresh direct logs are continuously emitting markout summaries. Current in-process state remains **1,178 simulated fills / 134 delayed-hedge-observable fills**. No order authority.
- `sports-gk-autonomous-supervisor`: Railway **SUCCESS/read-only/disarmed hot shadow**. Fresh heartbeat advanced raw triggers from ~76.6M to **77.5M+**, but `comparable_evaluations` remains frozen at **120,836**. Gemini remains **570 symbols / 0 connected** while Kalshi remains **570/570 initialized**. Order entry is false.
- `crypto-15m-sol-live-canary-10bp-50ms`: remains **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.
- `crypto-live-balance-reconcile-v2`: remains an auxiliary read-only reconciliation blocker from its latest crash; this does not authorize restarting any crypto trading lane.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker

Fresh direct production summaries reconfirm the same cumulative sample:

- **53,237 comparable evaluations**;
- **1,661 decision observations** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms;
- average closed positive-signal lifetime ~174 ms, max 567 ms;
- Kalshi transport explicitly labeled **REST_SNAPSHOT_NOT_WS_LIFETIME**.

Raw trigger volume is still rising rapidly but no new comparable/decision observations are being created. The trigger-only interval therefore contributes **$0 measurable capacity** and is not treated as millions of additional zero-edge observations. The certified totals slice supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After correcting start-time normalization and excluding F5/partial-game contamination, clean full-game moneyline/spread/total evidence remains **0 >=1c / 0 >=2c / 0 >=3c** in the last certified snapshots. Displayed depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow

Fresh production logs directly reconfirm:

- **1,178** pessimistic simulated q1 maker fills;
- immediate shadow P&L about **+$32.67**;
- only **134/1,178** fills have delayed-hedge-observable marks; **1,044** are `ws_unhealthy`/unobservable and receive $0 capacity credit;
- **80 ms:** +$0.0408 total, **22 >=1c / 9 >=2c / 2 >=3c**;
- **250 ms:** -$0.7158 total, **14 / 8 / 2**;
- **500 ms:** -$1.1384 total, **11 / 5 / 2**;
- no hedge-depth misses in the observable cohort;
- the process explicitly reports `capacity_credit_usd_per_day=0`, `read_only=true`, `order_entry=false`.

The K<->P full-game moneyline mechanism also fails exact postponement/reschedule lifecycle equivalence. Maker shadow therefore remains **$0 guaranteed/deployable capacity and $0 REALIZED P&L**. The low 11.4% observable coverage is itself a decision-quality constraint; unobservable fills are never assumed hedgeable.

### Strict MLB player props / incentives

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed capacity** for this census window. Polymarket incentive denominator/time-weighted yield/pickoff accounting remains incomplete and receives **$0 capacity credit**.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. Fresh production heartbeat now exceeds **77.5M raw triggers** but remains frozen at **120,836 comparable evaluations**, **6 historical >=3c hot evaluations**, and 4 completed hot-signal lifecycles. Gemini is still **570 symbols / 0 connected**, while Kalshi is **570/570 initialized**. The service explicitly reports `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`, `read_only=true`, `order_entry=false`.

This interval is unmeasurable/$0 guaranteed capacity, not fresh negative edge. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Evidence favors concurrent websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

Fresh direct Neon queries at **2026-08-27 09:47 ET** confirm:

- **2026-08-27 ET: 0 ledger events, 0 `TRADE_REALIZED`, $0.0000 realized net, 0 capital reservations**;
- weather: **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 since 2026-03-01**;
- Caleb Durbin remains prior **`SETTLED_ONE_LEG`, -$0.44 REALIZED**.

No shadow/model result is credited to realized P&L.

## Current verdict trajectory

Guaranteed/deployable evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- maker delayed hedge: only **2/134** observable marks >=3c at 80 ms, aggregate only **+$0.0408** at 80 ms and negative at 250/500 ms, while **1,044/1,178** fills are unobservable;
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
7. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.
