# ARB Control Plane

Last verified: **2026-08-26 21:35 UTC / 2026-08-26 17:35 ET**

This is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

- `sports-kp-live-executor`: **SUCCESS/read-only census**. Last decision-grade totals sample remains the authoritative taker evidence below.
- `sports-generic-counterpart-shadow`: **SUCCESS/read-only maker + incentive shadow**. Fresh runtime at 21:34 UTC confirms active Polymarket book/incentive collection and explicitly keeps incentive capacity at $0 pending denominator/time-weighted/pickoff proof.
- `sports-gk-autonomous-supervisor`: **SUCCESS/read-only/disarmed hot shadow**. At 21:35 UTC it had ~45.45M raw triggers but still only 120,836 comparable evaluations; Gemini 570 symbols / **0 connected**, Kalshi 570/570 initialized. No new comparable evidence is being credited.
- `crypto-15m-sol-live-canary-10bp-50ms`: **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker

Last valid decision-grade summary remains:

- **53,237 comparable evaluations**;
- **1,661 decision observations** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms.

Later trigger-only/stale-comparison intervals remain **unmeasurable**, not additional zero-edge observations. They receive $0 capacity credit for those intervals and do not reset the wall clock. The valid certified totals slice still supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After fixing start-time normalization and excluding F5/partial-game contamination, clean full-game moneyline/spread/total evidence remains **0 >=1c / 0 >=2c / 0 >=3c** in the last certified snapshots. Displayed depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow — larger sample, still $0 guaranteed capacity

Last fully verified read-only maker telemetry remains:

- **92 matched moneyline pairs**;
- **2,447 quote opens / 1,018 reprices / 1,230 cancels**;
- **42,077 Kalshi public trades observed**;
- **2,600 qualifying opposite-side taker prints**;
- **197 pessimistic simulated q1 fills**;
- **121 trade-through / 76 exact-price fills**;
- **0 hedge-missing-at-fill** events;
- immediate-fill economics under the verified KXMLBGAME maker-fee model: **102 >=1c / 68 >=2c / 40 >=3c**;
- zero-maker-fee upper bound: **141 >=1c / 84 >=2c / 62 >=3c**;
- median quote age at fill **~1,004 ms**;
- pure-trading **shadow** P&L about **+$4.2343**;
- `rules_complete=false`, `capacity_credit_usd_per_day=0`, `read_only=true`, `order_entry=false`.

This is real evidence that maker fill conversion is non-zero, but it does **not** rescue guaranteed-arb capacity. The strongest mechanism is KXMLBGAME<->Polymarket US full-game MLB moneyline, whose postponement/reschedule lifecycles are not equivalent (Kalshi two-day treatment versus Polymarket US two-week treatment). Therefore these modeled fills and shadow P&L remain **$0 guaranteed-arb deployable capacity** and must not be armed or counted as realized.

### Polymarket liquidity incentives — fresh bounded evidence, still $0 capacity

Fresh 21:34 UTC read-only telemetry confirms **31 active Polymarket MLB moneyline incentive programs/market-program rows are visible** across live/day-of/early phases. The currently observed qualified books are already above their raw target-size thresholds; the coverage census reported **0 under-target live-pool candidates**. Production also reports:

- `program_denominator_complete=false`;
- `daily_yield_status=PENDING_TIME_WEIGHTING_AND_PICKOFF_COST`;
- `reward_accounting=SHADOW_ESTIMATE_NOT_REALIZED`;
- `combined_capacity_credit_usd_per_day=0`;
- `read_only=true`, `order_entry=false`.

The visible reward pools do **not** become deployable or realized P&L without program-wide denominator, time-weighting, queue/fill, capital and adverse-selection/pickoff accounting. Do not use incentive headline dollars to rescue the census verdict.

### Strict MLB player props

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed-arb capacity** for this census window unless genuinely new authoritative rules resolve every mismatch. Do not turn this into open-ended research.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. Fresh 21:35 UTC heartbeat:

- ~**45.45M** raw triggers/evaluations;
- **120,836 comparable evaluations** (unchanged);
- **6 >=3c hot evaluations / 4 complete signal lifecycles** (historical, uncertified);
- Gemini: **570 symbols / 0 connected symbols**;
- Kalshi: **570/570 initialized**;
- `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`;
- `read_only=true`, `order_entry=false`.

This interval remains unmeasurable and receives $0 capacity credit. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away. Do not re-arm to improve census coverage.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Evidence continues to favor concurrent websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

Fresh direct Neon queries at 21:31 UTC:

- **2026-08-26 ET: 0 `TRADE_REALIZED` / $0.0000 net**;
- **0 `CAPITAL_RESERVED` events since ET midnight**;
- **no ledger events of any type since ET midnight** at the time of the query;
- Caleb Durbin remains **-$0.44 REALIZED** from the prior sports one-leg settlement;
- weather since **2026-03-01** remains **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 net**.

Historical/seasonal weather edge remains separate and receives no capacity credit without authoritative live records.

## Current verdict trajectory

Guaranteed-arb evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- K<->P full-game moneyline maker: **197 modeled fills / 40 >=3c immediate fills, but rule lifecycle mismatch => $0 guaranteed capacity**;
- Polymarket incentives: visible, but denominator/time-weighting/pickoff incomplete and current books not under raw target => **$0 capacity**;
- strict props: uncertified => **$0**;
- G<->K guaranteed execution: disarmed/unmeasurable => **$0**;
- weather verified realized since March: **$0**.

The maker sample proves fill mechanics can work, but exact lifecycle mismatch blocks guaranteed-arb credit. The incentive sample does not currently provide a certified alternative revenue stream. Do not replace failed certification with open-ended maker/incentive research during the census.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker census alive; stale/unmeasurable intervals score $0 and are not mislabeled as fresh zero-edge samples.
3. Continue maker/incentive telemetry only descriptively; do **not** credit or arm rule-mismatched full-game moneyline or incomplete incentive estimates.
4. Keep G<->K guaranteed execution disarmed while Gemini remains 0 connected and rule lifecycle certification is incomplete; Caleb's loss remains realized.
5. Do not manufacture same-venue ladder violations or force uncertified player props.
6. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
7. Keep weather historical/backtest edge separate from verified live realized P&L.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.