# ARB Control Plane

Last verified: **2026-08-28 00:39 UTC / 2026-08-27 20:39 ET**

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

Fresh Railway control-plane verification at ~20:39 ET:

- `sports-kp-live-executor`: latest deployment **SUCCESS**. No newer decision-grade counters are available in this run, so the last valid census statistics remain authoritative.
- `sports-generic-counterpart-shadow`: latest deployment **SUCCESS** and remains read-only.
- `sports-gk-autonomous-supervisor`: latest deployment **SUCCESS** and remains read-only/disarmed.
- `crypto-15m-sol-live-canary-10bp-50ms`: latest deployment remains **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.
- `crypto-live-balance-reconcile-v2`: a newer deployment created **2026-08-27 23:47 UTC / 19:47 ET** is now **SUCCESS**. This is observability/reconciliation only and grants no execution authority; a green deployment badge alone is not proof of reconciled venue state.
- canonical separate `kalshi-weather-bot` project: core bot **SUCCESS**, `weather-shadow-arb` **SUCCESS**, backtest **SUCCESS**, and canonical `weather-live-probe` remains **CRASHED**. A different same-named service inside `sports-arb-autonomous` is green but does not supersede canonical weather state or create realized weather P&L.

No fresh decision-grade economics were recovered in this run. Tooling/runtime visibility limitations therefore do not change any edge bin, capacity estimate, realized P&L, or verdict threshold.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker

Last decision-valid production output remains:

- comparable evaluations: **53,237**;
- decision observations: **1,661** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max ~99 ms;
- average closed positive-signal lifetime ~174 ms, max 567 ms;
- Kalshi transport explicitly labeled **REST_SNAPSHOT_NOT_WS_LIFETIME**;
- `read_only=true`, `order_entry=false`.

No newer decision summary was independently verified this run. Active-but-unmeasurable intervals receive no additional capacity credit. The certified totals slice still supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After correcting scheduled-start normalization and excluding F5/partial-game contamination, the last certified clean full-game moneyline/spread/total snapshots remain **0 >=1c / 0 >=2c / 0 >=3c**. Displayed q1 depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow

Last decision-valid maker telemetry remains:

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
- collector remains `rules_complete=false`, `read_only=true`, `order_entry=false`, `capacity_credit_usd_per_day=0`.

No newer decision summary was independently verified this run. The K<->P full-game moneyline mechanism also fails exact postponement/reschedule lifecycle equivalence. Therefore the immediate shadow headline remains **$0 guaranteed/deployable capacity and $0 REALIZED P&L**. Do not spend the remaining census window repairing a maker mechanism already rule-ineligible.

### Incentives

Last valid incentive telemetry saw **0 active Kalshi programs** and **28 Polymarket programs**, but the Polymarket program denominator remained incomplete. Estimated incentive run rate receives **$0 capacity credit**. Incentive telemetry is auxiliary and does not justify disrupting the core census or extending the deadline.

### Strict MLB player props

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed capacity** for this census window.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. The last decision-valid heartbeat remains:

- raw evaluations: **~92.68 million and rising**;
- comparable evaluations: still **120,836**;
- historical **6 >=3c hot evaluations / 4 completed signal lifecycles**;
- Gemini **570 symbols / 0 connected**;
- Gemini book updates frozen at **76,955**;
- Kalshi **570/570 initialized**;
- `read_only=true`, `order_entry=false`;
- rule gate remains `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`.

No newer two-sided Gemini proof was obtained this run. Until connectivity and lifecycle certification are directly re-proven, this interval remains **unmeasurable/$0 guaranteed capacity**. Do not re-arm. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Prior evidence continues to favor websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

No fresh authoritative SQL reconciliation was completed in this run, so accounting is **not advanced speculatively**.

Last successful direct reconciliation remains **2026-08-27 11:46 ET**:

- **2026-08-27 ET: 0 ledger events, 0 `TRADE_REALIZED`, $0.0000 realized net, 0 capital reservations**;
- weather: **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 since 2026-03-01**;
- Caleb Durbin remains prior **`SETTLED_ONE_LEG`, -$0.44 REALIZED**.

Canonical `kalshi-weather-bot` project state at 20:39 ET remains core bot/shadow/backtest green with canonical `weather-live-probe` **CRASHED**. A separate similarly named service in the sports-arb project is green but does not override canonical weather-project state and is not evidence of realized weather P&L. Seasonal historical/backtest edge remains explicitly separate from verified realized performance and gets no census credit.

No shadow/model/incentive result is credited to realized P&L.

## Current verdict trajectory

Guaranteed/deployable evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- K<->P maker: large immediate shadow headline, but only **134** delayed hedges are decision-usable; just **2** remain >=3c at 80 ms, aggregate 80 ms P&L is only **+$0.0408**, 250/500 ms are negative, and exact moneyline lifecycle equivalence fails => **$0/day capacity**;
- strict props/incentives: uncertified/incomplete => **$0**;
- G<->K: disarmed and not decision-measurable at the last valid heartbeat => **$0 guaranteed capacity**;
- weather verified realized since March: **$0** at the last authoritative query.

Current evidence continues to track toward the pre-committed **<$50/day** bucket. Do not declare the final verdict before the fixed deadline.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker and maker/markout collectors alive; active-but-unmeasurable intervals score $0 and are not mislabeled as fresh zero-edge samples.
3. Keep G<->K guaranteed execution disarmed; do not count raw activity without two-sided Gemini connectivity and full lifecycle certification.
4. Do not spend the remaining census window manufacturing same-venue violations, repairing rule-ineligible maker mechanics, or building around uncertified props.
5. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
6. Keep weather historical/backtest edge separate from verified live realized P&L; canonical weather-live-probe crash is not proof of realized loss or profit.
7. Treat `crypto-live-balance-reconcile-v2` as observability-only until its actual reconciliation output is independently verified; its newer green deployment is not execution authority.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.