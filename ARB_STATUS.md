# ARB Control Plane

Last verified: **2026-08-27 04:50 UTC / 2026-08-27 00:50 ET**

This is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

- `sports-kp-live-executor`: latest successful Railway deployment remains **SUCCESS/read-only census**. Later commits are intentionally SKIPPED for this service because the delayed-maker work is routed through the generic shadow. Last decision-grade taker sample remains valid but frozen; trigger-only intervals are unmeasurable and receive $0 capacity credit.
- `sports-generic-counterpart-shadow`: **SUCCESS/read-only maker + delayed-hedge markout shadow** on commit `044204d3…`. Fresh runtime at ~04:49 UTC is producing new maker/fill/markout summaries; no order authority.
- `sports-gk-autonomous-supervisor`: **SUCCESS/read-only/disarmed hot shadow**, but fresh runtime shows a broad Gemini reconnect storm across one-symbol shards and Railway log-rate drops. This interval is unmeasurable and receives $0 guaranteed-capacity credit.
- `crypto-15m-sol-live-canary-10bp-50ms`: remains **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker

Last valid decision-grade summary remains:

- **53,237 comparable evaluations**;
- **1,661 decision observations** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms;
- average closed positive-signal lifetime ~174 ms, max 567 ms;
- Kalshi transport remains explicitly labeled **REST_SNAPSHOT_NOT_WS_LIFETIME**.

Later raw-trigger-only intervals remain **unmeasurable**, not additional zero-edge observations, and receive $0 capacity credit without resetting the wall clock. The valid certified totals slice still supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After fixing start-time normalization and excluding F5/partial-game contamination, clean full-game moneyline/spread/total evidence remains **0 >=1c / 0 >=2c / 0 >=3c** in the last certified snapshots. Displayed depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow — much larger fill sample, delayed hedge evidence still fails the 3c capacity requirement

Fresh production summary at ~04:49 UTC:

- **1,176** pessimistic simulated q1 maker fills;
- **784** trade-through fills / **392** exact-price fills;
- **291** fills were >=3c immediately after the verified general maker-fee model;
- immediate/q1-sweep maker shadow P&L was about **+$32.6533**;
- **0** missing Polymarket hedge books at the instant of fill and **0** immediate q1 sweep unfillable;
- median displayed quote age at fill ~**686 ms**;
- `rules_complete=false`, `RULE_INCOMPLETE_NOT_EXECUTION_ELIGIBLE`, `read_only=true`, `order_entry=false`, `capacity_credit_usd_per_day=0`.

The delayed full-ladder hedge evidence remains the governing measurement:

- only **134 / 1,176** fills are currently executable/observable in the delayed tracker; **1,042** are `ws_unhealthy` and receive no executable capacity credit;
- at **80 ms**: **+$0.0408 total**, about **+$0.00030 per executable fill**, **22 >=1c / 9 >=2c / 2 >=3c**;
- at **250 ms**: **-$0.7158 total**, about **-$0.00534/fill**, **14 / 8 / 2**;
- at **500 ms**: **-$1.1384 total**, about **-$0.00850/fill**, **11 / 5 / 2**;
- `hedge_missing=0`, `full_ladder_missing=0`, and `depth_insufficient=0` on the observable cohort, so deterioration is adverse price movement/feed observability rather than missing q1 depth;
- the conservative full-sample lower-bound accounting is deeply negative because unobservable fills are not assumed safely hedgeable.

This is the strongest fresh result of the run: simulated maker fills increased from 707 to 1,176 and immediate >=3c fills increased to 291, but **the realistic 80 ms aggregate P&L did not improve beyond +$0.0408 because the observable delayed cohort is still only 134 fills, with only 2 retaining >=3c**. By 250/500 ms the observable cohort is negative.

The underlying K<->P full-game moneyline mechanism also still fails exact postponement/reschedule lifecycle equivalence. Therefore maker shadow remains **$0 guaranteed/deployable capacity and $0 REALIZED P&L**. Do not use the +$32.6533 immediate shadow P&L or 291 immediate >=3c fills to rescue the census verdict.

### Polymarket liquidity incentives

Fresh runtime sees **28 active Polymarket MLB moneyline incentive programs/markets in the current snapshot**, but all sampled raw resting sizes already exceed target thresholds and the program-wide denominator/time-weighted yield/pickoff accounting remains incomplete. Production explicitly reports `capacity_credit_usd_per_day=0`. Do not use headline reward-pool dollars as deployable or realized P&L.

### Strict MLB player props

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed-arb capacity** for this census window unless genuinely new authoritative rules resolve every mismatch. Do not turn this into open-ended research.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. Fresh runtime at ~04:49 UTC shows a broad Gemini reconnect storm across many one-symbol shards, severe enough for Railway to drop hundreds of log messages due to log rate. This interval is operationally unmeasurable and receives **$0 guaranteed capacity**, not a synthetic zero-edge count. Historical >=3c hot observations remain uncertified. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Evidence continues to favor concurrent websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

Fresh direct Neon query after ET midnight confirms:

- **2026-08-27 ET: no ledger events yet; 0 `TRADE_REALIZED` / $0.0000 net**;
- weather remains **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 since 2026-03-01**;
- Caleb Durbin remains the prior **`SETTLED_ONE_LEG`, -$0.44 REALIZED** sports loss.

Historical/seasonal weather edge remains separate and receives no capacity credit without authoritative live records.

## Current verdict trajectory

Guaranteed/deployable evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- maker delayed hedge: only **2/134 observable marks >=3c at 80 ms**, aggregate about **+$0.0408** at 80 ms and negative at 250/500 ms, while **1,042/1,176 fills are currently unobservable** to the delayed tracker;
- K<->P moneyline maker rules: lifecycle mismatch => **$0 guaranteed capacity** regardless of shadow fill count;
- Polymarket incentives: incomplete denominator/time-weighting/pickoff and currently over target => **$0 capacity**;
- strict props: uncertified => **$0**;
- G<->K guaranteed execution: disarmed/feed-degraded => **$0**;
- weather verified realized since March: **$0**.

The larger maker sample proves that fills occur, but it does **not** establish scalable deployable edge. Immediate shadow P&L grew sharply while delayed realistic-capture economics stayed essentially flat at 80 ms and negative beyond it. Current evidence therefore continues to track toward the pre-committed **<$50/day** bucket.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker census alive; stale/unmeasurable intervals score $0 and are not mislabeled as fresh zero-edge samples.
3. Preserve the delayed maker markout only as decision evidence. Do not create a new engineering lane solely to rescue its observability; unobservable fills remain $0 capacity.
4. Keep G<->K guaranteed execution disarmed while Gemini connectivity and rule lifecycle certification remain incomplete; Caleb's loss remains realized.
5. Do not manufacture same-venue ladder violations or force uncertified player props.
6. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
7. Keep weather historical/backtest edge separate from verified live realized P&L.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.