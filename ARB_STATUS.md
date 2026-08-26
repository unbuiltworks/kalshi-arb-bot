# ARB Control Plane

Last verified: **2026-08-26 22:38 UTC / 2026-08-26 18:38 ET**

This is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

- `sports-kp-live-executor`: **SUCCESS/read-only census**. Fresh runtime is active, but the decision sample remains frozen while raw triggers continue rising.
- `sports-generic-counterpart-shadow`: **SUCCESS/read-only maker + delayed-hedge markout shadow**. Fresh runtime now measures full q1 Polymarket ladder markout at 80/250/500 ms after pessimistic simulated Kalshi maker fills.
- `sports-gk-autonomous-supervisor`: **SUCCESS/read-only/disarmed hot shadow**. Last verified comparable sample remains stale/unmeasurable; no guaranteed capacity credit.
- `crypto-15m-sol-live-canary-10bp-50ms`: **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker

Fresh 22:38 UTC runtime still reports the same valid decision-grade summary:

- **53,237 comparable evaluations**;
- **1,661 decision observations** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms;
- average closed positive-signal lifetime ~174 ms, max 567 ms;
- Kalshi transport remains the explicitly labeled **REST_SNAPSHOT_NOT_WS_LIFETIME** fallback.

Raw triggers have now exceeded **51 million** while comparable evaluations remain frozen at 53,237. This later interval is **unmeasurable**, not millions of additional zero-edge observations, and receives $0 capacity credit without resetting the wall clock. The valid certified totals slice still supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After fixing start-time normalization and excluding F5/partial-game contamination, clean full-game moneyline/spread/total evidence remains **0 >=1c / 0 >=2c / 0 >=3c** in the last certified snapshots. Displayed depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow — delayed hedge markout now materially weakens the apparent edge

Historical read-only maker telemetry established that pessimistic simulated maker fills are non-zero, including a prior sample of **197 q1 simulated fills / 40 >=3c immediate-fill observations / about +$4.2343 shadow P&L**. Those rows already had **$0 guaranteed capacity** because the strongest KXMLBGAME<->Polymarket US full-game moneyline mechanism fails exact postponement/reschedule lifecycle equivalence.

Fresh production now adds a more realistic **delayed full-ladder hedge markout** sample. In the current run:

- **29** pessimistic simulated Kalshi maker fills had occurred by 22:38 UTC;
- **28** had completed the 80 ms delayed Polymarket q1-ladder check at the latest complete summary;
- all 28 were q1 sweep-fillable with healthy Polymarket WS/full-ladder observations;
- immediate q1-sweep shadow P&L was **+$0.2601** across 29 fills;
- at **80 ms**, completed sample P&L was only **+$0.0142 total (~+$0.00051/fill)** with **5 >=1c / 3 >=2c / 0 >=3c**;
- at **250 ms**, P&L was **-$0.0026 total** with **4 >=1c / 3 >=2c / 0 >=3c**;
- at **500 ms**, P&L was **-$0.2498 total (~-$0.00961/fill)** with **3 >=1c / 2 >=2c / 0 >=3c**;
- observed examples include profitable immediate fills deteriorating by **4-5c** by 500 ms;
- `rules_complete=false`, `capacity_credit_usd_per_day=0`, `read_only=true`, `order_entry=false`.

This is materially stronger evidence than immediate-fill shadow P&L: even before the known rule-lifecycle mismatch, the fresh realistic delayed-hedge sample has **0 >=3c fills at 80/250/500 ms**, essentially flat total P&L at 80 ms, negative by 250 ms and materially negative by 500 ms. Do not use immediate maker shadow P&L to rescue the census verdict.

### Polymarket liquidity incentives

Visible MLB moneyline incentive programs remain descriptive only. Production still reports incomplete program denominator/time-weighted reward/pickoff accounting and **combined maker capacity credit = $0/day**. Do not use headline reward-pool dollars as deployable or realized P&L.

### Strict MLB player props

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed-arb capacity** for this census window unless genuinely new authoritative rules resolve every mismatch. Do not turn this into open-ended research.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. Last verified comparable evidence remains stale/unmeasurable while Gemini connectivity is impaired. Historical >=3c hot observations remain uncertified and receive $0 guaranteed capacity. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away. Do not re-arm to improve census coverage.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Evidence continues to favor concurrent websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

Fresh direct Neon query at ~22:38 UTC:

- **2026-08-26 ET: 0 `TRADE_REALIZED` / $0.0000 net**;
- **no ledger events of any type since ET midnight** at the time of the query;
- therefore no new capital reservation/order/fill/settlement is being counted;
- Caleb Durbin remains **-$0.44 REALIZED** from the prior sports one-leg settlement;
- weather remains **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 since 2026-03-01** from the last authoritative weather query.

Historical/seasonal weather edge remains separate and receives no capacity credit without authoritative live records.

## Current verdict trajectory

Guaranteed/deployable evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- delayed maker hedge markout: **0 >=3c at 80/250/500 ms**, ~flat at 80 ms and negative at 250/500 ms;
- K<->P moneyline maker rules: lifecycle mismatch => **$0 guaranteed capacity** regardless of shadow fill count;
- Polymarket incentives: incomplete denominator/time-weighting/pickoff => **$0 capacity**;
- strict props: uncertified => **$0**;
- G<->K guaranteed execution: disarmed/unmeasurable => **$0**;
- weather verified realized since March: **$0**.

The new delayed markout evidence weakens the only remaining maker-based rescue mechanism: immediate fill economics do not survive realistic hedge delay at the fixed 3c threshold. Do not lower the threshold or open a new research lane to rescue the outcome.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker census alive; stale/unmeasurable intervals score $0 and are not mislabeled as fresh zero-edge samples.
3. Continue the delayed maker hedge markout only as bounded decision evidence; accumulate enough 80/250/500 ms samples to test whether the current near-zero/negative result persists. Do not arm it and do not credit rule-mismatched rows.
4. Keep G<->K guaranteed execution disarmed while rule lifecycle certification/connectivity is incomplete; Caleb's loss remains realized.
5. Do not manufacture same-venue ladder violations or force uncertified player props.
6. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
7. Keep weather historical/backtest edge separate from verified live realized P&L.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.