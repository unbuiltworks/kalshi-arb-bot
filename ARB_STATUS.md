# ARB Control Plane

Last verified: **2026-08-27 19:41 UTC / 2026-08-27 15:41 ET**

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

- `sports-kp-live-executor`: Railway **SUCCESS/read-only census**. Fresh direct logs through ~15:40 ET prove it is alive. Raw triggers have reached **102,412,651**, but the comparable and decision samples remain frozen.
- `sports-generic-counterpart-shadow`: Railway **SUCCESS/read-only maker + delayed-hedge shadow**. Fresh direct logs show modeled maker fills continuing to increase; no order authority.
- `sports-gk-autonomous-supervisor`: Railway **SUCCESS/read-only/disarmed hot shadow**. Fresh runtime remains dominated by a broad Gemini reconnect storm. Kalshi REST reads continue returning HTTP 200. This interval is unmeasurable/$0, not fresh negative-edge evidence.
- `crypto-15m-sol-live-canary-10bp-50ms`: remains **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.
- `crypto-live-balance-reconcile-v2`: last classified state remains a read-only observability regression (`ModuleNotFoundError` for the removed Gemini/Kalshi transport module). This does not authorize restarting either crypto execution lane.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker

Fresh direct runtime summary at ~15:40 ET:

- raw triggers: **102,412,651**;
- comparable evaluations: **53,237**;
- decision observations: **1,661** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms;
- average closed positive-signal lifetime ~174 ms, max 567 ms;
- Kalshi transport remains explicitly labeled **REST_SNAPSHOT_NOT_WS_LIFETIME**;
- `read_only=true`, `order_entry=false`.

Raw activity increased by more than two million triggers since the prior checkpoint without adding a single comparable/decision observation. This interval therefore contributes **$0 measurable capacity** and is not treated as millions of additional zero-edge observations. The certified totals slice still supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After correcting scheduled-start normalization and excluding F5/partial-game contamination, clean full-game moneyline/spread/total evidence remains **0 >=1c / 0 >=2c / 0 >=3c** in the last certified snapshots. Displayed q1 depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow

Fresh direct maker telemetry now verifies:

- **1,374 simulated q1 maker fills**;
- **335** looked >=3c immediately under the maker model;
- only **134/1,374 (9.75%)** have decision-usable delayed hedge observations; **1,240** are unobservable and receive $0 executable-capacity credit;
- critically, the decision-usable delayed cohort did **not grow** while the modeled fill count increased;
- at **80 ms**: 134 observable hedges, **+$0.0408 total**, 22 >=1c / 9 >=2c / **2 >=3c**;
- at **250 ms**: **-$0.7158 total**, 14 / 8 / **2**;
- at **500 ms**: **-$1.1384 total**, 11 / 5 / **2**;
- no hedge-missing, full-ladder-missing, or q1 depth-insufficient failures in the 134-fill observable cohort;
- collector remains `rules_complete=false`, `read_only=true`, `order_entry=false`, `capacity_credit_usd_per_day=0`.

The K<->P full-game moneyline mechanism also fails exact postponement/reschedule lifecycle equivalence. Therefore all immediate shadow economics remain **$0 guaranteed/deployable capacity and $0 REALIZED P&L**. The observable 80ms cohort averages only about **$0.00030 per fill**, while 250/500ms are negative. Repairing maker telemetry is not a justified new engineering branch during the remaining census window because the mechanism independently fails exact rule equivalence.

### Strict MLB player props / incentives

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed capacity** for this census window. Incentive/rebate results remain $0 capacity until program-wide denominator, time-weighted realized yield and pickoff/adverse-selection costs are proven.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. Fresh runtime at this checkpoint shows:

- a continuing broad Gemini reconnect storm across many one-symbol shards;
- Kalshi market-data REST requests continue returning **HTTP 200**;
- the last fully decoded heartbeat remains **120,836 comparable evaluations**, historical **6 >=3c hot evaluations / 4 completed signal lifecycles**, Gemini **570 symbols / 0 connected**, Kalshi **570/570 initialized**;
- `read_only=true`, `order_entry=false`.

The current reconnect interval is **unmeasurable/$0 guaranteed capacity**, not fresh zero-edge evidence. Do not re-arm. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Current K<->P batched REST reads are returning HTTP 200. Prior evidence continues to favor websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

A fresh Neon SQL attempt at this checkpoint was blocked before execution by a connector argument-contract bug: the exposed schema requires `projectId`, while the connector backend translates/validates against `project_id`. Therefore accounting was **not advanced speculatively**.

Last successful direct Neon reconciliation remains **2026-08-27 11:46 ET**:

- **2026-08-27 ET: 0 ledger events, 0 `TRADE_REALIZED`, $0.0000 realized net, 0 capital reservations**;
- weather: **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 since 2026-03-01**;
- Caleb Durbin remains prior **`SETTLED_ONE_LEG`, -$0.44 REALIZED**.

No shadow/model result is credited to realized P&L.

## Current verdict trajectory

Guaranteed/deployable evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- K<->P maker: modeled fills continue to accumulate, but the decision-usable delayed cohort remains frozen at 134; only **2 >=3c at 80ms**, negligible aggregate 80ms P&L, negative 250/500ms P&L, ~90% unobservable, and exact moneyline lifecycle equivalence fails => **$0/day capacity**;
- strict props/incentives: uncertified/incomplete => **$0**;
- G<->K: disarmed and currently not decision-measurable because Gemini is disconnected => **$0 guaranteed capacity**;
- weather verified realized since March: **$0** at the last authoritative query.

Current evidence continues to track toward the pre-committed **<$50/day** bucket. Do not declare the final verdict before the fixed deadline.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker and maker/markout collectors alive; active-but-unmeasurable intervals score $0 and are not mislabeled as fresh zero-edge samples.
3. Keep G<->K guaranteed execution disarmed; do not count raw activity without two-sided Gemini connectivity and full lifecycle certification.
4. Do not spend the remaining census window manufacturing same-venue violations, repairing a maker mechanism already rule-ineligible, or building around uncertified props.
5. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
6. Keep weather historical/backtest edge separate from verified live realized P&L.
7. Repair read-only crypto reconciliation only if it can be done as a bounded observability fix without distracting from the census or reopening crypto execution.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.
