# ARB Control Plane

Last verified: **2026-08-28 04:39 UTC / 2026-08-28 00:39 ET**

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

Fresh Railway production status at ~00:39 ET:

- `sports-kp-live-executor`: **SUCCESS**. Keep census running read-only.
- `sports-generic-counterpart-shadow`: **SUCCESS**. Maker/fill/markout remains shadow-only; no immediate shadow P&L counts as capacity or realized profit.
- `sports-gk-autonomous-supervisor`: **SUCCESS**. Guaranteed execution remains read-only/disarmed.
- `sports-gk-multifamily-live-canary`: **SUCCESS**, but this does not override the standing rule that G<->K guaranteed execution stays disarmed unless two-sided connectivity, lifecycle certification and zero unresolved exposure are all proven.
- `crypto-15m-sol-live-canary-10bp-50ms`: still **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.
- `crypto-live-balance-reconcile-v2`: newest deployment **SUCCESS** at 2026-08-28 03:39 UTC. Treat as observability/reconciliation only; a green badge is not proof of successful venue reconciliation and grants no execution authority.
- canonical `kalshi-weather-bot/weather-live-probe`: still **CRASHED**. Other weather services (`kalshi-weather-bot`, `weather-shadow-arb`, `kalshi-weather-backtest`) are **SUCCESS**.

No fresh decoded event/log counters were independently recovered this run, so prior decision-valid economics remain the governing evidence. Do not promote deployment status alone into edge evidence.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker

Last decision-valid production output remains:

- comparable evaluations: **53,237**;
- decision observations: **1,661** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80 ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms;
- average closed positive-signal lifetime ~174 ms, max 567 ms;
- Kalshi transport remains `REST_SNAPSHOT_NOT_WS_LIFETIME`;
- `read_only=true`, `order_entry=false`.

The certified totals slice supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After scheduled-start normalization and F5/partial-game exclusion, clean full-game moneyline/spread/total snapshots remain **0 >=1c / 0 >=2c / 0 >=3c**. Available q1 depth was substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow

Last decision-valid maker summary remains:

- **2,002 simulated q1 maker fills**;
- immediate shadow P&L **+$125.7791** — descriptive only, never realized/capacity credit;
- historical immediate after-fee maker bins: **1,297 >=1c / 1,023 >=2c / 838 >=3c**;
- only **134/2,002 (6.69%)** have decision-usable delayed hedge observations;
- **1,868** are `ws_unhealthy`/unobservable and receive **$0 executable-capacity credit**;
- at **80 ms**: **+$0.0408 total**, 22 >=1c / 9 >=2c / **2 >=3c**;
- at **250 ms**: **-$0.7158 total**, 14 / 8 / **2**;
- at **500 ms**: **-$1.1384 total**, 11 / 5 / **2**;
- no q1 depth-insufficient failures in the 134-fill observable cohort;
- `rules_complete=false`, `read_only=true`, `order_entry=false`, `capacity_credit_usd_per_day=0`.

The usable 80 ms cohort is tiny, delayed hedging is negative at 250/500 ms, and the K<->P full-game moneyline mechanism fails exact postponement/reschedule lifecycle equivalence. Therefore all maker shadow P&L remains **$0 guaranteed/deployable capacity and $0 REALIZED P&L**.

### Strict props and incentives

No K<->P player-prop family has complete settlement/postponement/cancellation/participation/stat-correction equivalence for this census window. Strict props and incomplete incentive telemetry remain **$0 guaranteed capacity**.

## G<->K sports

Last decision-valid heartbeat remains:

- comparable evaluations: **120,836**;
- historical **6 >=3c hot evaluations / 4 completed signal lifecycles**;
- Gemini **570 symbols / 0 connected**;
- Kalshi **570/570 initialized**;
- `read_only=true`, `order_entry=false`;
- rule gate remains `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`.

The lane remains **unmeasurable/$0 guaranteed capacity** until two-sided Gemini connectivity and complete lifecycle certification are re-proven. Do not re-arm. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Prior evidence favors websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting and weather

Fresh SQL reconciliation remains unavailable because the Neon connector has an internal `projectId`/`project_id` argument-contract mismatch. Accounting is therefore **not advanced speculatively**.

Last successful direct reconciliation remains:

- **2026-08-27 ET: 0 ledger events, 0 TRADE_REALIZED, $0.0000 realized net, 0 capital reservations**;
- weather: **0 verified weather-tagged TRADE_REALIZED / $0.0000 since 2026-03-01**;
- Caleb Durbin: prior `SETTLED_ONE_LEG`, **-$0.44 REALIZED**.

Fresh canonical weather project state at ~00:39 ET:

- `kalshi-weather-bot`: **SUCCESS**;
- `weather-shadow-arb`: **SUCCESS**;
- `kalshi-weather-backtest`: **SUCCESS**;
- canonical `weather-live-probe`: **CRASHED**.

Seasonal historical/backtest weather edge remains separate from verified realized performance and gets no census credit.

## Current verdict trajectory

Guaranteed/deployable evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- K<->P maker: immediate shadow headline is large, but realistic delayed-hedge evidence is tiny/weak, 250/500 ms aggregate economics are negative, exact lifecycle equivalence fails => **$0/day capacity**;
- strict props/incentives: uncertified/incomplete => **$0**;
- G<->K: disarmed and last decoded Gemini state 0/570 connected => **$0 guaranteed capacity**;
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
