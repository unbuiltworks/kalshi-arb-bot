# ARB Control Plane

Last verified: **2026-08-27 03:41 UTC / 2026-08-26 23:41 ET**

This is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

- `sports-kp-live-executor`: **SUCCESS/read-only census**. Fresh one-hour Railway metrics show 61/61 samples, average CPU ~0.0703, and sustained RX/TX traffic, so the process is genuinely alive. Last decision-grade taker sample remains valid but frozen; trigger-only intervals are unmeasurable and receive $0 capacity credit.
- `sports-generic-counterpart-shadow`: **SUCCESS/read-only maker + delayed-hedge markout shadow**. Fresh one-hour metrics show 61/61 samples, average CPU ~0.0338, and sustained network traffic; no order authority.
- `sports-gk-autonomous-supervisor`: **SUCCESS/read-only/disarmed hot shadow**. Fresh one-hour metrics show 61/61 samples, average CPU ~0.5962, and sustained network traffic. No fresh certifiable execution evidence was established in this checkpoint; no guaranteed capacity credit.
- `crypto-15m-sol-live-canary-10bp-50ms`: **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.

Railway's deeper runtime-log agent is quota-limited in this checkpoint, so no newer event-level census values are claimed beyond the last directly verified summaries below. Process metrics prove liveness only; they do not create capacity credit.

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

### Maker/fill shadow — larger sample, realistic hedge delay still fails the 3c requirement

Last directly verified production summary remains:

- **707** pessimistic simulated q1 maker fills;
- **595** trade-through fills / **112** exact-price fills;
- **121** fills were >=3c immediately after the verified maker-fee model;
- immediate maker shadow P&L was about **+$1.9077**;
- **0 missing Polymarket hedge books at the instant of fill**;
- `rules_complete=false`, `RULE_INCOMPLETE_NOT_EXECUTION_ELIGIBLE`, `read_only=true`, `order_entry=false`, `capacity_credit_usd_per_day=0`.

The delayed full-ladder hedge evidence is much weaker than the immediate-fill headline:

- **573 historical fills are currently unobservable** in the delayed-hedge tracker after the Polymarket WS tracking session was lost; those fills receive no capacity credit and must not be assumed hedgeable;
- **134** newer fills have completed observable delayed marks;
- at **80 ms**: **+$0.0408 total**, about **+$0.00030/fill**, with **7 >=1c / 4 >=2c / 2 >=3c**;
- at **250 ms**: **-$0.1014 total**, about **-$0.00076/fill**, with **7 / 4 / 2**;
- at **500 ms**: **-$0.4612 total**, about **-$0.00344/fill**, with **7 / 4 / 2**;
- `late_hedge_depth_miss=0` on the observable cohort, so the deterioration is not explained by missing q1 depth;
- current delayed tracker reports `ws_healthy=false` and **573 `unobservable_both`** fills, so the present interval is feed-degraded and cannot be promoted into deployable capacity.

Maker fill conversion is real, but **realistic delayed hedge economics are approximately flat at 80 ms and negative by 250/500 ms**, while the majority of accumulated fills are presently unobservable to the delayed tracker. Only **2/134** observable delayed marks retain >=3c at 80 ms, and the underlying K<->P moneyline mechanism still fails exact postponement/reschedule lifecycle equivalence. Therefore maker shadow remains **$0 guaranteed/deployable capacity and $0 REALIZED P&L**.

Do not use the +$1.9077 immediate shadow P&L, the 121 immediate >=3c fills, or the 573 unobservable fills to rescue the census verdict.

### Polymarket liquidity incentives

Visible MLB moneyline incentive programs remain descriptive only. Production still lacks complete time-weighted reward denominator/pickoff/adverse-selection accounting and receives **$0/day capacity credit**. Do not use headline reward-pool dollars as deployable or realized P&L.

### Strict MLB player props

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed-arb capacity** for this census window unless genuinely new authoritative rules resolve every mismatch. Do not turn this into open-ended research.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. Last verified comparable evidence is stale/unmeasurable while Gemini connectivity remains unreliable. Historical >=3c hot observations remain uncertified and receive $0 guaranteed capacity. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away. Do not re-arm to improve census coverage.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Evidence continues to favor concurrent websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

Fresh direct Neon queries at ~03:41 UTC / 23:41 ET confirm:

- **2026-08-26 ET: 0 `TRADE_REALIZED` / $0.0000 net**;
- **0 unresolved durable capital reservations**;
- Caleb Durbin remains **`SETTLED_ONE_LEG`, -$0.44 REALIZED** from the prior sports one-leg settlement;
- weather remains **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 since 2026-03-01**.

Historical/seasonal weather edge remains separate and receives no capacity credit without authoritative live records.

## Current verdict trajectory

Guaranteed/deployable evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- maker delayed hedge: only **2/134 observable marks >=3c at 80 ms**, aggregate ~flat at 80 ms and negative at 250/500 ms, with **573 older fills currently unobservable**;
- K<->P moneyline maker rules: lifecycle mismatch => **$0 guaranteed capacity** regardless of shadow fill count;
- Polymarket incentives: incomplete denominator/time-weighting/pickoff => **$0 capacity**;
- strict props: uncertified => **$0**;
- G<->K guaranteed execution: disarmed/unmeasurable => **$0**;
- weather verified realized since March: **$0**.

The larger maker sample proves that fills can occur, but it does **not** establish scalable deployable edge: realistic delayed hedging erodes the economics, most accumulated fills currently lack observable delayed marks, and the strongest cross-venue moneyline family still fails exact lifecycle equivalence.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker census alive; stale/unmeasurable intervals score $0 and are not mislabeled as fresh zero-edge samples.
3. Preserve the bounded delayed maker markout only as decision evidence. Restore/observe delayed Polymarket tracking if it recovers naturally, but do not disrupt unrelated live services or create a new engineering lane solely to rescue the maker result.
4. Keep G<->K guaranteed execution disarmed while rule lifecycle certification/connectivity is incomplete; Caleb's loss remains realized.
5. Do not manufacture same-venue ladder violations or force uncertified player props.
6. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
7. Keep weather historical/backtest edge separate from verified live realized P&L.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.