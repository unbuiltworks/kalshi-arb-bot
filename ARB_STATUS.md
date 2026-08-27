# ARB Control Plane

Last verified: **2026-08-27 08:50 UTC / 2026-08-27 04:50 ET**

This is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

- `sports-kp-live-executor`: latest Railway deployment remains **SUCCESS/read-only census**. Fresh 1-hour metrics at ~08:50 UTC show 61/61 samples with average CPU ~0.067 and sustained RX/TX over the window, so the process is alive. The latest valid decision sample remains frozen at 53,237 comparable evaluations / 1,661 observations; trigger-only intervals are unmeasurable and receive $0 capacity credit rather than being counted as fresh zero-edge evidence.
- `sports-generic-counterpart-shadow`: latest deployment remains **SUCCESS/read-only maker + delayed-hedge markout shadow**, configured with `restartPolicy=ALWAYS`. Fresh 1-hour metrics still show prior activity (average CPU ~0.017 with RX/TX), while the current CPU/RX/TX sample is zero. Railway's detailed log agent is usage-limited, so this cannot yet be distinguished between overnight no-slate idle behavior and a successful process exit. This interval receives **no new maker-capacity credit** and no newer markout counts are invented.
- `sports-gk-autonomous-supervisor`: **SUCCESS/read-only/disarmed hot shadow**. Fresh 1-hour metrics at ~08:50 UTC show 61/61 samples with average CPU ~0.634 and sustained RX/TX over the window; the process remains strongly active. The comparable sample remains frozen at 120,836 and Gemini connectivity remains the governing measurement blocker from the last detailed runtime checkpoint.
- `crypto-15m-sol-live-canary-10bp-50ms`: remains **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.
- Railway's detailed runtime/log agent was retried at ~08:50 UTC and remains usage-limited, so no newer decision-census/maker/ledger summary is being invented from process metrics alone.

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

Trigger-only growth without increases in comparable evaluations or decision observations remains **unmeasurable**, not additional zero-edge evidence, and receives $0 capacity credit without resetting the wall clock. The valid certified totals slice still supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After fixing start-time normalization and excluding F5/partial-game contamination, clean full-game moneyline/spread/total evidence remains **0 >=1c / 0 >=2c / 0 >=3c** in the last certified snapshots. Displayed depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow — larger fill sample, delayed hedge evidence still fails the 3c capacity requirement

Last verified production summary:

- **1,178** pessimistic simulated q1 maker fills;
- **786** trade-through fills / **392** exact-price fills;
- **291** fills were >=3c immediately after the verified general maker-fee model;
- immediate/q1-sweep maker shadow P&L was about **+$32.6697**;
- **0** missing Polymarket hedge books at the instant of fill and **0** immediate q1 sweep unfillable;
- median displayed quote age at fill ~**687 ms**;
- `rules_complete=false`, `RULE_INCOMPLETE_NOT_EXECUTION_ELIGIBLE`, `read_only=true`, `order_entry=false`, `capacity_credit_usd_per_day=0`.

The delayed full-ladder hedge evidence remains the governing measurement:

- only **134 / 1,178** fills are currently executable/observable in the delayed tracker; **1,044** are `ws_unhealthy` and receive no executable capacity credit;
- at **80 ms**: **+$0.0408 total**, about **+$0.00030 per executable fill**, **22 >=1c / 9 >=2c / 2 >=3c**;
- at **250 ms**: **-$0.7158 total**, about **-$0.00534/fill**, **14 / 8 / 2**;
- at **500 ms**: **-$1.1384 total**, about **-$0.00850/fill**, **11 / 5 / 2**;
- `hedge_missing=0`, `full_ladder_missing=0`, and `depth_insufficient=0` on the observable cohort, so deterioration is adverse price movement/feed observability rather than missing q1 depth;
- the conservative full-sample lower-bound accounting remains deeply negative because unobservable fills are not assumed safely hedgeable.

Additional immediate shadow fills are not translating into additional realistic-capture capacity. The underlying K<->P full-game moneyline mechanism also still fails exact postponement/reschedule lifecycle equivalence. Therefore maker shadow remains **$0 guaranteed/deployable capacity and $0 REALIZED P&L**. Do not use immediate shadow P&L or immediate >=3c fills to rescue the census verdict.

### Polymarket liquidity incentives

Last verified runtime saw **28 active Polymarket MLB moneyline incentive programs/markets**. Program-wide denominator/time-weighted yield/pickoff accounting remains incomplete and production explicitly reports `capacity_credit_usd_per_day=0`. Do not use headline reward-pool dollars as deployable or realized P&L.

### Strict MLB player props

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed-arb capacity** for this census window unless genuinely new authoritative rules resolve every mismatch. Do not turn this into open-ended research.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. The last detailed runtime checkpoint showed the comparable sample frozen at **120,836**, with Gemini connectivity degraded while Kalshi remained initialized. Fresh metrics at ~08:50 UTC confirm the process remains strongly active over the last hour. This interval remains operationally unmeasurable and receives **$0 guaranteed capacity**, not a synthetic zero-edge count. Historical >=3c hot observations remain uncertified. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Evidence continues to favor concurrent websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

Last successful direct Neon queries at ~07:33 UTC confirm:

- **2026-08-27 ET: no ledger events yet; 0 `TRADE_REALIZED` / $0.0000 net**;
- weather remains **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 since 2026-03-01**;
- Caleb Durbin remains the prior **`SETTLED_ONE_LEG`, -$0.44 REALIZED** sports loss.

Historical/seasonal weather edge remains separate and receives no capacity credit without authoritative live records.

## Current verdict trajectory

Guaranteed/deployable evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- maker delayed hedge: only **2/134 observable marks >=3c at 80 ms**, aggregate about **+$0.0408** at 80 ms and negative at 250/500 ms, while **1,044/1,178 fills are currently unobservable** to the delayed tracker;
- K<->P moneyline maker rules: lifecycle mismatch => **$0 guaranteed capacity** regardless of shadow fill count;
- Polymarket incentives: incomplete denominator/time-weighting/pickoff => **$0 capacity**;
- strict props: uncertified => **$0**;
- G<->K guaranteed execution: disarmed/feed-degraded => **$0**;
- weather verified realized since March: **$0**.

This run adds no evidence of scalable deployable edge. The cross-venue K<->P and G<->K collectors remain alive, while the maker-markout shadow's current idle/exited state is unverified and receives no new capacity credit. Current evidence therefore continues to track toward the pre-committed **<$50/day** bucket.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker census alive; stale/unmeasurable intervals score $0 and are not mislabeled as fresh zero-edge samples.
3. Do not restart or redeploy the maker-markout shadow solely to improve telemetry while the log reader is quota-blocked and the overnight MLB slate is inactive; missing evidence receives $0 capacity.
4. Keep G<->K guaranteed execution disarmed while Gemini connectivity and rule lifecycle certification remain incomplete; Caleb's loss remains realized.
5. Do not manufacture same-venue ladder violations or force uncertified player props.
6. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
7. Keep weather historical/backtest edge separate from verified live realized P&L.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.