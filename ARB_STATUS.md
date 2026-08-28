# ARB Control Plane

Last verified: **2026-08-28 03:37 UTC / 2026-08-27 23:37 ET**

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

Fresh Railway verification at ~23:37 ET:

- `sports-kp-live-executor`: deployment **SUCCESS**. Last-hour metrics are fully active: CPU current ~0.0768 / avg ~0.0712 across 60 samples; RX avg ~0.00437 GB/min; TX avg ~0.00217 GB/min. The collector is alive, not a green-but-dead deployment.
- `sports-generic-counterpart-shadow`: deployment **SUCCESS**. Last-hour metrics are active across 61 samples: CPU current ~0.0172 / avg ~0.0354; RX avg ~0.00186 GB/min; TX avg ~0.00045 GB/min. Maker/fill/markout shadow remains read-only; no new decision-valid maker result is promoted without fresh delayed-hedge evidence.
- `sports-gk-autonomous-supervisor`: deployment **SUCCESS**. Last-hour trace is active despite an idle instantaneous sample: CPU avg ~0.697, RX avg ~0.130 GB/min, TX avg ~0.070 GB/min across 61 samples. Guaranteed execution remains read-only/disarmed.
- `crypto-15m-sol-live-canary-10bp-50ms`: remains **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.
- `crypto-live-balance-reconcile-v2`: latest deployment remains **SUCCESS**. This is observability/reconciliation only and grants no execution authority; a green badge alone is not proof of reconciled venue state.
- Railway's detailed runtime/log inspector returned `Agent usage limit reached` again this run, so no stale counter is being promoted as fresh decision evidence.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker

Last decision-valid production output remains:

- raw triggers: **119,932,267 and rising at the last decoded heartbeat**;
- comparable evaluations: **53,237**;
- decision observations: **1,661** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms;
- average closed positive-signal lifetime ~174 ms, max 567 ms;
- Kalshi transport remains **REST_SNAPSHOT_NOT_WS_LIFETIME**;
- `read_only=true`, `order_entry=false`.

Fresh Railway resource metrics prove the process remains alive, but because the detailed log reader is quota-blocked, this run adds **no new decoded comparable/decision observation**. The fresh interval therefore receives **no new positive capacity credit** and is not mislabeled as fresh zero-edge evidence. The certified totals slice still supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After correcting scheduled-start normalization and excluding F5/partial-game contamination, the last certified clean full-game moneyline/spread/total snapshots remain **0 >=1c / 0 >=2c / 0 >=3c**. Displayed q1 depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow

Last decision-valid maker summary remains:

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
- collector remains `rules_complete=false`, `read_only=true`, `order_entry=false`, `capacity_credit_usd_per_day=0`.

The immediate shadow headline remains non-decision-valid. The usable 80 ms cohort is tiny, delayed hedging becomes negative at 250/500 ms, and the K<->P full-game moneyline mechanism fails exact postponement/reschedule lifecycle equivalence. Therefore all maker shadow P&L remains **$0 guaranteed/deployable capacity and $0 REALIZED P&L**.

### Incentives and strict props

Last valid incentive telemetry remains incomplete and receives **$0 capacity credit**. No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence; strict props remain **$0 guaranteed capacity** for this census window.

## G<->K sports

Fresh Railway resource metrics prove the supervisor remains active, but no new decoded two-sided evidence is available because the detailed log reader is quota-blocked. Last decision-valid heartbeat remains:

- raw evaluations: **102,481,837 and rising at the last decoded heartbeat**;
- comparable evaluations: **120,836**;
- historical **6 >=3c hot evaluations / 4 completed signal lifecycles**;
- Gemini **570 symbols / 0 connected**;
- Gemini book updates frozen at **76,955**;
- Kalshi **570/570 initialized**;
- `read_only=true`, `order_entry=false`;
- rule gate remains `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`.

The lane remains **unmeasurable/$0 guaranteed capacity** until two-sided Gemini connectivity and complete lifecycle certification are re-proven. Do not re-arm. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Prior evidence continues to favor websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting and weather

A fresh Neon SQL reconciliation remains blocked by the connector's internal `projectId`/`project_id` argument-contract mismatch, so accounting is **not advanced speculatively**.

Last successful direct reconciliation remains **2026-08-27 11:46 ET**:

- **2026-08-27 ET: 0 ledger events, 0 `TRADE_REALIZED`, $0.0000 realized net, 0 capital reservations**;
- weather: **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 since 2026-03-01**;
- Caleb Durbin remains prior **`SETTLED_ONE_LEG`, -$0.44 REALIZED**.

Fresh canonical Railway weather-project state at ~23:37 ET:

- `kalshi-weather-bot`: **SUCCESS**;
- `weather-shadow-arb`: **SUCCESS**;
- `kalshi-weather-backtest`: **SUCCESS**;
- canonical `weather-live-probe`: **CRASHED** (deployment from 2026-08-25 03:11 UTC).

A separate green `weather-live-probe` exists inside `sports-arb-autonomous`; it does not supersede the canonical weather project state and does not create realized weather P&L. Seasonal historical/backtest weather edge remains explicitly separate from verified realized performance and gets no census credit.

## Current verdict trajectory

Guaranteed/deployable evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- K<->P maker: **2,002** modeled fills and +$125.78 immediate shadow P&L, but only **134** delayed hedges are decision-usable; just **2** remain >=3c at 80 ms, aggregate 80 ms P&L is only **+$0.0408**, 250/500 ms are negative, and exact moneyline lifecycle equivalence fails => **$0/day capacity**;
- strict props/incentives: uncertified/incomplete => **$0**;
- G<->K: disarmed, last decoded Gemini state 0/570 connected, comparable sample frozen => **$0 guaranteed capacity**;
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