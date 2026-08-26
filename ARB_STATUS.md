# ARB Control Plane

Last verified: **2026-08-26 15:36 UTC / 2026-08-26 11:36 ET**

This is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

- `sports-kp-live-executor`: Railway **SUCCESS** and actively receiving triggers. It remains the read-only K<->P census gateway with no order authority.
- `sports-generic-counterpart-shadow`: Railway **SUCCESS** and actively refreshing current Polymarket MLB books. It remains read-only maker-feasibility/incentive telemetry.
- `sports-gk-autonomous-supervisor`: Railway **SUCCESS**, but guaranteed-arb order entry remains disarmed/read-only.
- `crypto-15m-sol-live-canary-10bp-50ms`: Railway **CRASHED/contained**. The bounded-campaign deadline failure is proven, but Gemini/Kalshi settlement-index equivalence remains unproven, so it must not be reopened as guaranteed arbitrage.

## Decision-grade K<->P evidence

### Strict MLB totals

The latest cumulative verified result remains:

- **53,237 comparable evaluations**;
- **1,661 decision observations** across **9 semantic keys**;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth **~2,073**, max **~22,648**;
- average quote skew **~45.5 ms**, max **100 ms**;
- average process latency **~0.234 ms**;
- descriptive positive-signal lifetime **~174 ms avg / ~567 ms max**;
- Kalshi REST fallback remains labeled `REST_SNAPSHOT_NOT_WS_LIFETIME`.

Interpretation: displayed liquidity is not the blocker. The certified totals slice has produced **zero >=2c and zero >=3c after-fee observations**, so current realistic deployable capacity at the fixed 3c taker/taker floor is **$0/day**.

### Fresh data-quality classification

At **15:35 UTC**, K<->P trigger count had increased to **36,229,603**, but `comparable_evaluations` remained frozen at **53,237**, `decision_census.observations` remained **1,661**, and `active_signals=0`. The process is alive, drops are zero, and trigger-to-calc latency remains low, but this interval is **not creating new comparable two-sided samples**.

Do **not** count this trigger churn as additional zero-edge evidence. It is an unmeasurable/stale-comparison interval and contributes **$0 deployable capacity for the interval** under the pre-committed data-quality rule. The 72-hour wall clock does not reset.

### Clean MLB full-game breadth and maker feasibility

After fixing the Kalshi start-time normalization bug and removing F5/partial-game contamination, clean full-game taker/taker evidence remains **0 >=1c / 0 >=2c / 0 >=3c** across moneylines, spreads and totals.

Fresh read-only maker/fill telemetry at **15:34-15:35 UTC** is materially negative:

- **92 matched moneyline pairs** in the fill shadow;
- **329 quote opens / 28 reprices / 301 cancels**;
- **3,721 Kalshi public trades observed**;
- **1 qualifying opposite taker print**;
- **0 simulated q1 fills**, including **0 trade-through fills** and **0 exact-price fills**;
- current conservative/general-maker bins: **0 >=1c / 0 >=2c / 0 >=3c**;
- current zero-maker-fee bins: **0 >=1c / 0 >=2c / 0 >=3c**;
- combined pure-trading shadow P&L: **$0**;
- `combined_maker_capacity_credit_usd_per_day=0`.

The fill model is deliberately pessimistic: a touch alone never counts as a fill; it requires a trade-through or exact-price print after displayed queue exhaustion. No live order authority is present.

The program-wide incentive denominator shadow also remains **$0 capacity credit**. It currently sees **54 Polymarket MLB moneyline markets**, including a $500 day-of program and a $225 early program, but the runtime explicitly marks time-weighted realized yield and pickoff cost as unresolved. The displayed program-share dollar equivalents are theoretical shadow allocations, not venue credits or realized P&L.

This closes the current maker/incentive path as **evidence**, not as a reason to open another live-maker build during the census window.

### Strict MLB player props

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Kalshi and Polymarket lifecycle rules differ materially in the existing registry, so strict props receive **$0 guaranteed-arb capacity** for this census window unless genuinely new authoritative rule text resolves every mismatch. Do not turn this into open-ended research.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

Fresh heartbeat at **15:35 UTC**:

- **33.59M+ triggers/evaluations** and still rising;
- `comparable_evaluations` frozen at **120,836**;
- historical `hot_3c_evaluations=6`, opens **4**, closes **4**, active positive keys **0**;
- **570 Gemini symbols but 0 connected Gemini symbols**;
- **570/570 Kalshi books initialized**;
- rule gate remains `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`;
- `read_only=true`, `order_entry=false`.

This current interval is not decision-grade cross-venue evidence because Gemini is disconnected and the comparable sample is not advancing. It receives **$0 deployable-capacity credit**, and historical hot-shadow signals remain uncertified rather than executable.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Evidence continues to favor concurrent websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

Fresh direct Neon queries at this checkpoint:

- **2026-08-26 ET: 0 `TRADE_REALIZED` / $0.0000 net**.
- **0 unresolved durable reservations** when unresolved is correctly defined as a reservation with neither `CAPITAL_RELEASED` nor `TRADE_REALIZED` for that trade ID.
- Caleb Durbin remains **-$0.44 REALIZED** from the prior sports one-leg settlement.

Weather:

- **0 weather-tagged `TRADE_REALIZED` / $0.0000 verified realized since 2026-03-01** in the authoritative central ledger.
- Seasonal/backtest weather edge remains separate from current verified realized performance and contributes $0 to the decision unless authoritative project-local live records prove otherwise.

## Current verdict trajectory

The evidence is increasingly consistent across mechanisms:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- current maker feasibility: **0 >=3c**;
- pessimistic public-trade fill shadow: **0 fills** after 329 quote opens and 3,721 observed Kalshi public trades;
- incentive/reward shadow: **$0 capacity credit** pending actual time-weighted venue economics;
- strict props: uncertified, therefore **$0**;
- current G<->K interval: unmeasurable due Gemini disconnect, therefore **$0**;
- weather verified realized since March: **$0**.

Do not declare the final verdict before the fixed wall-clock deadline, but do not create new research projects to rescue these results. On present evidence, the project is tracking toward the **<$50/day** bucket unless the remaining valid cross-venue sampling produces materially different certified >=3c realistic capacity.

## Highest-value next actions

1. Keep the fixed 72-hour clock running through **2026-08-28 20:43 UTC / 16:43 ET**. Do not reset it.
2. Keep K<->P alive and distinguish valid new comparable samples from trigger-only stale intervals; unsupported intervals score $0 rather than being mislabeled as edge zeros.
3. Keep the maker/incentive sidecar read-only. Current evidence is 0 fills / 0 current >=3c / $0 capacity credit; do not turn it into another live-maker build before the verdict.
4. Keep G<->K guaranteed execution disarmed. A 0-connected-symbol Gemini interval is not executable evidence; Caleb's loss remains realized.
5. Do not spend engineering time manufacturing same-venue ladder violations or forcing uncertified player props.
6. Preserve Kalshi account headroom/backoff; do not stop unrelated services solely for census telemetry.
7. Keep weather historical/backtest edge separate from verified live realized P&L.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.
