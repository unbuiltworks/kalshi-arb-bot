# ARB Control Plane

Last verified: **2026-08-27 12:44 UTC / 2026-08-27 08:44 ET**

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

- `sports-kp-live-executor`: Railway **SUCCESS/read-only census**. Fresh last-hour metrics show activity across all 61 one-minute samples with continuous CPU/RX/TX. Direct runtime summaries are still emitting normally.
- `sports-generic-counterpart-shadow`: Railway **SUCCESS/read-only maker + delayed-hedge shadow**. Direct runtime logs prove the process is alive and continuously refreshing books/markouts despite a brief zero-current-metrics snapshot.
- `sports-gk-autonomous-supervisor`: Railway **SUCCESS/read-only/disarmed hot shadow**. Fresh heartbeat at ~08:44 ET shows ~**75.45M raw triggers**, but comparable evaluations remain frozen at **120,836**; Gemini remains **570 symbols / 0 connected**, while Kalshi remains **570/570 initialized**. Order entry remains disabled.
- `crypto-15m-sol-live-canary-10bp-50ms`: remains **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker

Fresh direct production summaries through ~08:43 ET show raw triggers continuing to advance past **85.0M**, while the valid comparable/decision sample remains frozen:

- **53,237 comparable evaluations**;
- **1,661 decision observations** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms;
- average process latency ~0.234 ms;
- average closed positive-signal lifetime ~174 ms, max 567 ms;
- Kalshi transport explicitly labeled **REST_SNAPSHOT_NOT_WS_LIFETIME**.

The service is alive, but the additional raw-trigger interval is not generating fresh comparable observations. That interval is therefore **unmeasurable/$0 capacity**, not millions of extra zero-edge samples. The currently certified totals slice remains **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After correcting start-time normalization and excluding F5/partial-game contamination, clean full-game moneyline/spread/total evidence remains **0 >=1c / 0 >=2c / 0 >=3c** in the last certified snapshots. Displayed depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill + delayed-hedge shadow

Fresh decision-usable maker evidence materially worsened versus the prior checkpoint:

- **1,186** pessimistic simulated q1 maker fills;
- **291** immediate fills >=3c after the verified maker-fee model;
- immediate shadow P&L about **+$32.92**;
- **208/1,186** fills have delayed-hedge observations; **978** are unobservable and receive $0 capacity credit;
- **80 ms:** 206 completed/sweepable, **18 >=1c / 9 >=2c / 0 >=3c**, aggregate **-$0.5758**;
- **250 ms:** 206 completed/sweepable, **10 / 3 / 0**, aggregate **-$1.3531**;
- **500 ms:** 202 completed/sweepable, **9 / 2 / 0**, aggregate **-$1.7449**;
- **0 hedge-depth misses** in the observable cohort;
- markout websocket currently reports `ws_healthy=true`, 90 tracked markets, 8 reconnects / 8 errors.

This is the strongest new economic evidence this run: the previously observed two >=3c 80ms captures have fallen to **zero** on a larger observable cohort, and aggregate shadow P&L is now negative even at 80 ms. The weakness is adverse movement/observability, not q1 hedge depth.

The underlying K<->P full-game moneyline mechanism also fails exact postponement/reschedule lifecycle equivalence. Maker shadow therefore remains **$0 guaranteed/deployable capacity and $0 REALIZED P&L**.

### Strict MLB player props / incentives

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed capacity** for this census window. Current incentive telemetry still reports no decision-grade time-weighted reward/pickoff yield and **$0 capacity credit**.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. Fresh direct heartbeat confirms the process is alive, but it is still not producing fresh decision evidence:

- raw triggers/evaluations ~**75.45M** and increasing;
- comparable evaluations frozen at **120,836**;
- historical positive/hot >=3c evaluations remain **6**, with **4** complete open/close lifecycles;
- Gemini **570 symbols / 0 connected**, `gemini_book_updates=76,955` frozen;
- Kalshi **570/570 initialized**;
- rule gate remains `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`;
- `read_only=true`, `order_entry=false`.

This interval remains **unmeasurable/$0 guaranteed capacity**. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Evidence favors concurrent websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

Fresh direct Neon reconciliation at ~08:44 ET confirms:

- **2026-08-27 ET: 0 `TRADE_REALIZED` / $0.0000 net**;
- **0 `CAPITAL_RESERVED` events today**;
- **no ledger events at all in the Aug 27 ET window**;
- weather: **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 since 2026-03-01**;
- Caleb Durbin remains prior **`SETTLED_ONE_LEG`, -$0.44 REALIZED**.

No shadow/model result is credited to realized P&L.

## Current verdict trajectory

Guaranteed/deployable evidence is now even more strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- maker delayed hedge: **0/206** observable completed marks >=3c at 80 ms, aggregate **-$0.5758** at 80 ms and more negative at 250/500 ms, while **978/1,186** fills remain unobservable;
- K<->P moneyline maker rules: lifecycle mismatch => **$0 guaranteed capacity**;
- strict props/incentives: uncertified/incomplete => **$0**;
- G<->K: disarmed and currently not decision-measurable => **$0 guaranteed capacity**;
- weather verified realized since March: **$0**.

Current evidence continues to track toward the pre-committed **<$50/day** bucket. Do not declare the final verdict before the fixed deadline.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker and maker/markout collectors alive; active-but-unmeasurable intervals score $0 and are not mislabeled as fresh zero-edge samples.
3. Keep G<->K guaranteed execution disarmed; do not count raw activity without two-sided connectivity and rule certification.
4. Do not build another same-venue or uncertified-prop research lane to rescue the verdict; the current economic evidence should be allowed to stand.
5. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
6. Keep weather historical/backtest edge separate from verified live realized P&L.
7. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.
