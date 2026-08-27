# ARB Control Plane

Last verified: **2026-08-27 21:40 UTC / 2026-08-27 17:40 ET**

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

Fresh Railway runtime/log verification at ~17:40 ET:

- `sports-kp-live-executor`: latest deployment **SUCCESS** and actively emitting census summaries.
- `sports-generic-counterpart-shadow`: latest deployment **SUCCESS** and actively emitting fill/markout/incentive summaries. It remains strictly read-only.
- `sports-gk-autonomous-supervisor`: latest deployment **SUCCESS** and actively emitting heartbeats. It remains explicitly `read_only=true`, `order_entry=false`.
- `crypto-15m-sol-live-canary-10bp-50ms`: latest deployment remains **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.
- `crypto-live-balance-reconcile-v2`: newest deployment currently shows **SUCCESS**, but it remains observability-only and grants no crypto execution authority.

Railway's database AI agent is currently quota-blocked (`Agent usage limit reached`), so this run could not advance authoritative ledger totals. Accounting below therefore remains pinned to the last successful direct reconciliation rather than inferred from shadows or service logs.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker

Fresh production output remains decision-stable despite rapidly increasing raw triggers:

- comparable evaluations: **53,237**;
- decision observations: **1,661** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max ~99 ms;
- average closed positive-signal lifetime ~174 ms, max 567 ms;
- Kalshi transport explicitly labeled **REST_SNAPSHOT_NOT_WS_LIFETIME**;
- `read_only=true`, `order_entry=false`.

The collector is alive, but the comparable/decision counters are still frozen while raw triggers continue rising. The fresh trigger-only interval is therefore **unmeasurable/$0 capacity**, not millions of additional zero-edge observations. The certified totals slice still supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After correcting scheduled-start normalization and excluding F5/partial-game contamination, the last certified clean full-game moneyline/spread/total snapshots remain **0 >=1c / 0 >=2c / 0 >=3c**. Displayed q1 depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow

Fresh maker telemetry has grown materially, but the decision-usable hedge sample has **not** grown:

- **1,511 simulated q1 maker fills**;
- immediate shadow P&L **+$55.9718** — descriptive only, never realized/capacity credit;
- immediate after-fee maker bins: **864 >=1c / 608 >=2c / 447 >=3c**;
- trade-through fills **1,018**, exact-price fills **493**, median quote age at fill ~**779 ms**;
- only **134/1,511 (8.87%)** have decision-usable delayed hedge observations;
- **1,377** are `ws_unhealthy`/unobservable and receive **$0 executable-capacity credit**;
- at **80 ms**: 134 observable hedges, **+$0.0408 total**, 22 >=1c / 9 >=2c / **2 >=3c**;
- at **250 ms**: **-$0.7158 total**, 14 / 8 / **2**;
- at **500 ms**: **-$1.1384 total**, 11 / 5 / **2**;
- no hedge-missing, full-ladder-missing, or q1 depth-insufficient failures in the 134-fill observable cohort;
- worst-case full-sample lower bound is roughly **-$756.92 at 80 ms** when unobservable fills are treated under the configured conservative policy;
- collector remains `rules_complete=false`, `read_only=true`, `order_entry=false`, `capacity_credit_usd_per_day=0`.

The K<->P full-game moneyline mechanism also fails exact postponement/reschedule lifecycle equivalence. Therefore the larger immediate shadow headline remains **$0 guaranteed/deployable capacity and $0 REALIZED P&L**. Do not spend the remaining census window repairing a maker mechanism already rule-ineligible.

### Incentives

Fresh incentive telemetry currently sees **0 active Kalshi programs** and **28 Polymarket programs**, but the Polymarket program denominator remains incomplete. Estimated incentive run rate receives **$0 capacity credit**. Incentive telemetry is occasionally throwing read-only endpoint errors; this is auxiliary and does not justify disrupting the core census or extending the deadline.

### Strict MLB player props

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed capacity** for this census window.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. Fresh heartbeat at ~17:40 ET shows:

- raw evaluations: **~92.68 million and rising**;
- comparable evaluations: still **120,836**;
- historical **6 >=3c hot evaluations / 4 completed signal lifecycles**;
- Gemini **570 symbols / 0 connected**;
- Gemini book updates frozen at **76,955**;
- Kalshi **570/570 initialized**;
- `read_only=true`, `order_entry=false`;
- rule gate remains `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`.

The process is alive, but the comparable sample is not advancing because Gemini has not recovered. This interval is therefore **unmeasurable/$0 guaranteed capacity**, not fresh zero-edge evidence. Do not re-arm. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Prior evidence continues to favor websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

A fresh authoritative SQL reconciliation could not be completed in this run because Railway's read-only database agent is quota-blocked before SQL execution. Accounting is therefore **not advanced speculatively**.

Last successful direct reconciliation remains **2026-08-27 11:46 ET**:

- **2026-08-27 ET: 0 ledger events, 0 `TRADE_REALIZED`, $0.0000 realized net, 0 capital reservations**;
- weather: **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 since 2026-03-01**;
- Caleb Durbin remains prior **`SETTLED_ONE_LEG`, -$0.44 REALIZED**.

No shadow/model/incentive result is credited to realized P&L.

## Current verdict trajectory

Guaranteed/deployable evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- K<->P maker: **1,511** modeled fills and **447** immediate >=3c headlines, but only **134** delayed hedges are observable; just **2** remain >=3c at 80 ms, aggregate 80 ms P&L is only **+$0.0408**, 250/500 ms are negative, and exact moneyline lifecycle equivalence fails => **$0/day capacity**;
- strict props/incentives: uncertified/incomplete => **$0**;
- G<->K: disarmed and not decision-measurable with Gemini **0/570 connected** => **$0 guaranteed capacity**;
- weather verified realized since March: **$0** at the last authoritative query.

Current evidence continues to track toward the pre-committed **<$50/day** bucket. Do not declare the final verdict before the fixed deadline.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker and maker/markout collectors alive; active-but-unmeasurable intervals score $0 and are not mislabeled as fresh zero-edge samples.
3. Keep G<->K guaranteed execution disarmed; do not count raw activity without two-sided Gemini connectivity and full lifecycle certification.
4. Do not spend the remaining census window manufacturing same-venue violations, repairing rule-ineligible maker mechanics, or building around uncertified props.
5. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
6. Keep weather historical/backtest edge separate from verified live realized P&L.
7. Treat `crypto-live-balance-reconcile-v2` as observability-only until its actual output is independently verified; its green deployment badge is not execution authority.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.