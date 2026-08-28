# ARB Control Plane

Last verified: **2026-08-28 01:49 UTC / 2026-08-27 21:49 ET**

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

Fresh Railway verification at ~21:49 ET:

- `sports-kp-live-executor`: deployment **SUCCESS** and continuously emitting `EVENT_SUMMARY`.
- `sports-generic-counterpart-shadow`: deployment **SUCCESS**, actively emitting maker/fill/markout summaries, read-only.
- `sports-gk-autonomous-supervisor`: deployment **SUCCESS**, heartbeat active, guaranteed execution remains read-only/disarmed.
- `crypto-15m-sol-live-canary-10bp-50ms`: remains **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.
- `crypto-live-balance-reconcile-v2`: latest deployment remains **SUCCESS**. This is observability/reconciliation only and grants no execution authority; a green badge alone is not proof of reconciled venue state.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker

Fresh production output at 21:49 ET:

- raw triggers: **117,704,443 and rising**;
- comparable evaluations: still **53,237**;
- decision observations: **1,661** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms;
- average closed positive-signal lifetime ~174 ms, max 567 ms;
- Kalshi transport remains **REST_SNAPSHOT_NOT_WS_LIFETIME**;
- `read_only=true`, `order_entry=false`.

Raw trigger volume has increased sharply while decision-usable comparisons remain frozen. The fresh trigger-only interval is therefore **unmeasurable/$0 capacity**, not millions of additional zero-edge observations. The certified totals slice still supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After correcting scheduled-start normalization and excluding F5/partial-game contamination, the last certified clean full-game moneyline/spread/total snapshots remain **0 >=1c / 0 >=2c / 0 >=3c**. Displayed q1 depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow

Fresh 21:49 ET production summary:

- **2,002 simulated q1 maker fills**;
- immediate shadow P&L **+$125.7791** — descriptive only, never realized/capacity credit;
- historical immediate after-fee maker bins: **1,297 >=1c / 1,023 >=2c / 838 >=3c**;
- trade-through fills **1,322**, exact-price fills **680**;
- median quote age at fill ~**991 ms**;
- only **134/2,002 (6.69%)** have decision-usable delayed hedge observations;
- **1,868** are `ws_unhealthy`/unobservable and receive **$0 executable-capacity credit**;
- at **80 ms**: 134 observable hedges, **+$0.0408 total**, 22 >=1c / 9 >=2c / **2 >=3c**;
- at **250 ms**: **-$0.7158 total**, 14 / 8 / **2**;
- at **500 ms**: **-$1.1384 total**, 11 / 5 / **2**;
- no hedge-missing, full-ladder-missing, or q1 depth-insufficient failures in the 134-fill observable cohort;
- current maker snapshot itself has **0 >=1c / 0 >=2c / 0 >=3c** under the verified general maker-fee model;
- current delayed hedge telemetry reports `poly_global_ws_healthy_1000ms=false`; recent fills are explicitly `delayed_hedge_executable=false` rather than credited from stale REST ladders;
- collector remains `rules_complete=false`, `read_only=true`, `order_entry=false`, `capacity_credit_usd_per_day=0`.

The immediate shadow headline more than doubled, but decision-valid coverage fell to **6.69%** and the usable 80 ms cohort did not improve. The K<->P full-game moneyline mechanism also fails exact postponement/reschedule lifecycle equivalence. Therefore all maker shadow P&L remains **$0 guaranteed/deployable capacity and $0 REALIZED P&L**.

### Incentives and strict props

Last valid incentive telemetry remains incomplete and receives **$0 capacity credit**. No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence; strict props remain **$0 guaranteed capacity** for this census window.

## G<->K sports

Fresh heartbeat at 21:49 ET confirms the process is alive but still unusable for decision capacity:

- raw evaluations: **100,747,807 and rising**;
- comparable evaluations: still **120,836**;
- historical **6 >=3c hot evaluations / 4 completed signal lifecycles**;
- Gemini **570 symbols / 0 connected**;
- Gemini book updates frozen at **76,955**;
- Kalshi **570/570 initialized**;
- `read_only=true`, `order_entry=false`;
- rule gate remains `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`.

The fresh interval remains **unmeasurable/$0 guaranteed capacity**. Do not re-arm. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Prior evidence continues to favor websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting and weather

A fresh Neon SQL reconciliation was attempted in this run, but the connector currently has an internal `projectId`/`project_id` argument-contract mismatch: camelCase is rejected by the backend while snake_case is rejected by the exposed schema. SQL therefore did not execute and accounting is **not advanced speculatively**.

Last successful direct reconciliation remains **2026-08-27 11:46 ET**:

- **2026-08-27 ET: 0 ledger events, 0 `TRADE_REALIZED`, $0.0000 realized net, 0 capital reservations**;
- weather: **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 since 2026-03-01**;
- Caleb Durbin remains prior **`SETTLED_ONE_LEG`, -$0.44 REALIZED**.

Seasonal historical/backtest weather edge remains explicitly separate from verified realized performance and gets no census credit. No shadow/model/incentive result is credited to realized P&L.

## Current verdict trajectory

Guaranteed/deployable evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- K<->P maker: **2,002** modeled fills and +$125.78 immediate shadow P&L, but only **134** delayed hedges are decision-usable; just **2** remain >=3c at 80 ms, aggregate 80 ms P&L is only **+$0.0408**, 250/500 ms are negative, current delayed hedge WS is unhealthy, and exact moneyline lifecycle equivalence fails => **$0/day capacity**;
- strict props/incentives: uncertified/incomplete => **$0**;
- G<->K: disarmed, Gemini 0/570 connected, comparable sample frozen => **$0 guaranteed capacity**;
- weather verified realized since March: **$0** at the last authoritative query.

Current evidence continues to track toward the pre-committed **<$50/day** bucket. Do not declare the final verdict before the fixed deadline.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker and maker/markout collectors alive; active-but-unmeasurable intervals score $0 and are not mislabeled as fresh zero-edge samples.
3. Keep G<->K guaranteed execution disarmed; do not count raw activity without two-sided Gemini connectivity and full lifecycle certification.
4. Do not spend the remaining census window manufacturing same-venue violations, repairing rule-ineligible maker mechanics, or building around uncertified props.
5. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
6. Keep weather historical/backtest edge separate from verified live realized P&L.
7. Treat `crypto-live-balance-reconcile-v2` as observability-only until its actual reconciliation output is independently verified.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.