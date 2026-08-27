# ARB Control Plane

Last verified: **2026-08-27 10:38 UTC / 2026-08-27 06:38 ET**

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

- `sports-kp-live-executor`: Railway **SUCCESS/read-only census**. Fresh direct `EVENT_SUMMARY` logs through ~10:38 UTC prove the process is alive and emitting every 10 seconds. Raw triggers advanced to ~**79.90M**, while `comparable_evaluations` remains frozen at **53,237** and `decision_census.observations` at **1,661**. This interval is unmeasurable and receives $0 capacity credit rather than being counted as fresh zero-edge evidence.
- `sports-generic-counterpart-shadow`: Railway **SUCCESS/read-only maker + delayed-hedge shadow**. Fresh direct runtime proves the process is alive, but the modeled sample remains frozen at **1,178 fills / 291 immediate >=3c fills**, with only **134 delayed-hedge-observable fills** and **1,044 ws_unhealthy**. No new maker capacity is credited.
- `sports-gk-autonomous-supervisor`: Railway **SUCCESS/read-only/disarmed hot shadow**. Fresh heartbeat through ~10:37 UTC shows raw triggers advanced above **71.27M** while `comparable_evaluations` remains frozen at **120,836**. Gemini is still **570 symbols / 0 connected**; Kalshi remains **570/570 initialized**. Historical 6 >=3c hot evaluations remain uncertified and no order entry is enabled. This interval receives $0 capacity credit.
- `crypto-15m-sol-live-canary-10bp-50ms`: remains **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker

Fresh direct logs through ~10:38 UTC confirm the valid cumulative sample is unchanged:

- **53,237 comparable evaluations**;
- **1,661 decision observations** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms;
- average closed positive-signal lifetime ~174 ms, max 567 ms;
- Kalshi transport remains explicitly labeled **REST_SNAPSHOT_NOT_WS_LIFETIME**.

Raw trigger growth without comparable-evaluation growth is unmeasurable, not additional zero-edge evidence. The valid certified totals slice supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After correcting start-time normalization and excluding F5/partial-game contamination, clean full-game moneyline/spread/total evidence remains **0 >=1c / 0 >=2c / 0 >=3c** in the last certified snapshots. Displayed depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow

Fresh direct logs prove the process is still alive but the fill/markout sample has not advanced:

- **1,178** pessimistic simulated q1 maker fills;
- **786** trade-through / **392** exact-price fills;
- **291** immediate fills >=3c after the verified maker-fee model;
- immediate shadow P&L about **+$32.6697**;
- `rules_complete=false`, `RULE_INCOMPLETE_NOT_EXECUTION_ELIGIBLE`, `read_only=true`, `order_entry=false`, `capacity_credit_usd_per_day=0`.

Delayed full-ladder hedge evidence remains governing:

- only **134/1,178** fills are delayed-hedge observable; **1,044** are `ws_unhealthy` and receive no executable capacity credit;
- delayed-observation coverage is only **11.38%**;
- **80 ms:** +$0.0408 total, ~+$0.00030/executable fill, **22 >=1c / 9 >=2c / 2 >=3c**;
- **250 ms:** -$0.7158 total, **14 / 8 / 2**;
- **500 ms:** -$1.1384 total, **11 / 5 / 2**;
- no hedge-depth misses on the observable cohort; deterioration is adverse movement/feed observability rather than q1 depth.

The underlying K<->P full-game moneyline mechanism also fails exact postponement/reschedule lifecycle equivalence. Maker shadow therefore remains **$0 guaranteed/deployable capacity and $0 REALIZED P&L**.

### Polymarket liquidity incentives

Incentive denominator/time-weighted yield/pickoff accounting remains incomplete and `combined_maker_capacity_credit_usd_per_day=0`; no incentive dollars are credited.

### Strict MLB player props

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed-arb capacity** for this census window unless genuinely new authoritative rules resolve every mismatch. Do not turn this into open-ended research.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. Fresh heartbeat shows **>71.27M raw triggers but only 120,836 comparable evaluations**, with Gemini **570 symbols / 0 connected** and Kalshi **570/570 initialized**. Historical **6 >=3c hot evaluations / 4 completed signal lifecycles** remain uncertified under `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`. This is an unmeasurable feed interval and receives $0 guaranteed capacity rather than synthetic zero-edge counts. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Evidence favors concurrent websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

Fresh direct Neon queries in this run confirm:

- **2026-08-27 ET: no ledger events of any type; 0 `TRADE_REALIZED` / $0.0000 net**;
- weather: **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 since 2026-03-01**;
- Caleb Durbin remains the prior **`SETTLED_ONE_LEG`, -$0.44 REALIZED** sports loss.

Historical/seasonal weather edge remains separate and receives no capacity credit without authoritative live records.

## Current verdict trajectory

Guaranteed/deployable evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- maker delayed hedge: only **2/134** observable marks >=3c at 80 ms, aggregate about **+$0.0408** at 80 ms and negative at 250/500 ms, while **1,044/1,178** fills are unobservable;
- K<->P moneyline maker rules: lifecycle mismatch => **$0 guaranteed capacity**;
- Polymarket incentives: incomplete => **$0 capacity**;
- strict props: uncertified => **$0**;
- G<->K guaranteed execution: disarmed and currently **0/570 Gemini connected**, comparable sample frozen => **$0**;
- weather verified realized since March: **$0**.

Current evidence continues to track toward the pre-committed **<$50/day** bucket. Do not declare the final verdict before the fixed deadline.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker census alive; stale/unmeasurable intervals score $0 and are not mislabeled as fresh zero-edge samples. Do not restart/redeploy solely to inflate trigger counts.
3. Keep the maker/markout shadow read-only. The process is alive but its sample is frozen and 88.6% of delayed marks remain unobservable; no capacity is credited until fresh decision-usable observations appear.
4. Keep G<->K guaranteed execution disarmed. Do not treat raw-trigger growth with **0 Gemini-connected symbols** as new executable evidence; Caleb's loss remains realized.
5. Do not manufacture same-venue ladder violations or force uncertified player props.
6. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
7. Keep weather historical/backtest edge separate from verified live realized P&L.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.
