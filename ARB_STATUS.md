# ARB Control Plane

Last verified: **2026-08-27 17:42 UTC / 2026-08-27 13:42 ET**

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

- `sports-kp-live-executor`: Railway **SUCCESS/read-only census**. Fresh direct logs through 13:42 ET confirm the process is alive and processing triggers. No new comparable/decision observations are being generated despite raw trigger growth.
- `sports-generic-counterpart-shadow`: Railway **SUCCESS/read-only maker + delayed-hedge shadow**. Fresh direct logs through 13:42 ET confirm the markout loop is alive, Polymarket is connected, and summaries continue. No order authority.
- `sports-gk-autonomous-supervisor`: Railway **SUCCESS/read-only/disarmed hot shadow**. Fresh direct heartbeat through 13:42 ET confirms the process is alive, but Gemini remains 570 symbols / 0 connected while Kalshi remains 570/570 initialized. Order entry remains false.
- `crypto-15m-sol-live-canary-10bp-50ms`: remains **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.
- `crypto-live-balance-reconcile-v2`: latest Railway deployment is now **SUCCESS** (2026-08-27 15:48 UTC), clearing the earlier deployment-level crash. This is reconciliation/observability only and does not authorize restarting either crypto execution lane.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker

Fresh direct runtime summaries through 13:42 ET show raw triggers rising past **97.4 million**, while the valid sample remains frozen at:

- **53,237 comparable evaluations**;
- **1,661 decision observations** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms;
- average closed positive-signal lifetime ~174 ms, max 567 ms;
- Kalshi transport explicitly labeled **REST_SNAPSHOT_NOT_WS_LIFETIME**.

The fresh trigger-only interval contributes **$0 measurable capacity** and is not treated as millions of additional zero-edge observations. The certified totals slice supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After correcting start-time normalization and excluding F5/partial-game contamination, clean full-game moneyline/spread/total evidence remains **0 >=1c / 0 >=2c / 0 >=3c** in the last certified snapshots. Displayed depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow

Fresh direct delayed-hedge summaries now verify:

- **1,223 simulated q1 maker fills** and **+$33.1731 immediate shadow P&L**;
- only **134/1,223 (10.96%)** have decision-usable delayed hedge observations; **1,089** are unobservable because the Polymarket WS state was unhealthy for those fills and receive $0 capacity credit;
- at **80 ms**: 134 executable, **+$0.0408 total**, 22 >=1c / 9 >=2c / **2 >=3c**;
- at **250 ms**: **-$0.7158 total**, 14 / 8 / **2**;
- at **500 ms**: **-$1.1384 total**, 11 / 5 / **2**;
- no hedge-missing, full-ladder-missing, or q1 depth-insufficient failures in the observable cohort;
- the collector itself reports **capacity_credit_usd_per_day=0**, `read_only=true`, `order_entry=false`.

The K<->P full-game moneyline mechanism also fails exact postponement/reschedule lifecycle equivalence. Immediate shadow P&L therefore remains **$0 guaranteed/deployable capacity and $0 REALIZED P&L**. The observable 80ms cohort averages only about **$0.00030 per fill**, while 250/500ms are negative; this is not scalable evidence toward the 3c floor.

### Strict MLB player props / incentives

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed capacity** for this census window. Fresh incentive snapshots show 28 active Polymarket MLB moneyline program rows, but observed books are already above raw target sizes and program-wide denominator/time-weighted yield/pickoff accounting remains incomplete; incentive capacity credit remains **$0/day**.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. Fresh direct heartbeats through 13:42 ET show:

- raw triggers/evaluations past **85.0 million**;
- comparable evaluations still frozen at **120,836**;
- historical positive/hot observations remain **6 >=3c evaluations / 4 completed signal lifecycles**;
- Gemini: **570 symbols / 0 connected**, book updates frozen at 76,955;
- Kalshi: **570/570 initialized**;
- `read_only=true`, `order_entry=false`, with full Gemini prop lifecycle fingerprint still required before execution.

This interval is unmeasurable/$0 guaranteed capacity, not fresh negative edge. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Evidence favors concurrent websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

Last successful direct Neon reconciliation at **2026-08-27 11:46 ET** confirmed:

- **2026-08-27 ET: 0 ledger events, 0 `TRADE_REALIZED`, $0.0000 realized net, 0 capital reservations**;
- weather: **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 since 2026-03-01**;
- Caleb Durbin remains prior **`SETTLED_ONE_LEG`, -$0.44 REALIZED**.

A fresh Neon connector call at 13:42 ET was blocked by a connector argument-schema mismatch before SQL executed, so these accounting values were not advanced speculatively. No shadow/model result is credited to realized P&L.

## Current verdict trajectory

Guaranteed/deployable evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- maker delayed-hedge evidence: only **2 >=3c** in the small observable 80ms cohort, negligible aggregate 80ms P&L, negative 250/500ms P&L, and ~89% of modeled fills unobservable; capacity credit remains **$0/day**;
- K<->P moneyline maker rules: lifecycle mismatch => **$0 guaranteed capacity**;
- strict props/incentives: uncertified/incomplete => **$0**;
- G<->K: disarmed and currently not decision-measurable => **$0 guaranteed capacity**;
- weather verified realized since March: **$0** at the last authoritative query.

Current evidence continues to track toward the pre-committed **<$50/day** bucket. Do not declare the final verdict before the fixed deadline.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker and maker/markout collectors alive; active-but-unmeasurable intervals score $0 and are not mislabeled as fresh zero-edge samples.
3. Keep G<->K guaranteed execution disarmed; do not count raw activity without two-sided Gemini connectivity and full lifecycle certification.
4. Do not spend the remaining census window manufacturing same-venue violations or building around uncertified props.
5. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
6. Keep weather historical/backtest edge separate from verified live realized P&L.
7. Treat the recovered `crypto-live-balance-reconcile-v2` as reconciliation/observability only; it must not reopen crypto execution.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.