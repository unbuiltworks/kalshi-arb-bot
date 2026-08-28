# ARB Control Plane

Last verified: **2026-08-28 05:44 UTC / 2026-08-28 01:44 ET**

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

Fresh Railway verification at ~01:44 ET:

- `sports-kp-live-executor`: **SUCCESS** with sustained CPU/network over the last hour. Read-only census remains alive.
- `sports-generic-counterpart-shadow`: **SUCCESS** with sustained CPU/network and fresh delayed-markout summaries. Read-only; no order entry.
- `sports-gk-autonomous-supervisor`: **SUCCESS** and actively emitting heartbeats. Guaranteed execution remains read-only/disarmed.
- `crypto-15m-sol-live-canary-10bp-50ms`: **CRASHED/contained**. Do not reopen while Gemini/Kalshi settlement-index equivalence remains unproven.
- `crypto-live-balance-reconcile-v2`: latest deployment **SUCCESS**; treat as observability/reconciliation only until actual venue reconciliation output is independently proven.

Railway's higher-level agent remains usage-limited, but direct deployment logs are available and are the source of the fresh counters below.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker

The valid sample remains frozen despite continued raw trigger activity:

- comparable evaluations: **53,237**;
- decision observations: **1,661** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80 ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms;
- average closed positive-signal lifetime ~174 ms, max 567 ms;
- Kalshi transport remains `REST_SNAPSHOT_NOT_WS_LIFETIME`;
- `read_only=true`, `order_entry=false`.

Raw triggers are now roughly **127M+**, but the comparable/decision sample has not advanced. Trigger-only intervals are therefore **unmeasurable/$0 capacity**, not millions of additional zero-edge observations.

The certified totals slice still supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After scheduled-start normalization and F5/partial-game exclusion, clean full-game moneyline/spread/total snapshots remain **0 >=1c / 0 >=2c / 0 >=3c**. Available q1 depth was substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill/delayed-hedge shadow — fresh 01:44 ET

Fresh production summaries materially increased the simulated-fill headline without increasing the usable delayed-hedge cohort:

- **2,166 simulated q1 maker fills**;
- immediate q1-sweep shadow P&L **+$128.6480** — descriptive only, never realized/capacity credit;
- immediate q1 sweep executable: **2,166 / 2,166**, depth-unfillable **0**;
- only **134 / 2,166 (6.19%)** have decision-usable delayed hedge observations;
- **2,032 / 2,166 (93.81%)** are `ws_unhealthy`/unobservable and receive **$0 executable-capacity credit**;
- at **80 ms**: **+$0.0408 total**, avg +$0.000304/fill, 22 >=1c / 9 >=2c / **2 >=3c**;
- at **250 ms**: **-$0.7158 total**, 14 / 8 / **2**;
- at **500 ms**: **-$1.1384 total**, 11 / 5 / **2**;
- delayed cohort has **0 hedge-missing, 0 full-ladder-missing, 0 depth-insufficient** failures;
- worst-case full-sample lower bounds are approximately **-$999.9954 / -$1000.7520 / -$1001.1746** at 80/250/500 ms under the explicit unobservable-fill policy;
- `capacity_credit_usd_per_day=0`, `read_only=true`, `order_entry=false`.

This is a stronger negative measurement, not a positive one: immediate shadow P&L increased while usable realistic-hedge coverage fell to 6.19%. The usable cohort remains nearly flat at 80 ms and negative at 250/500 ms. Exact K<->P full-game moneyline postponement/reschedule lifecycle equivalence also fails independently. Therefore all maker shadow economics remain **$0 guaranteed/deployable capacity and $0 REALIZED P&L**.

### Strict props and incentives

No K<->P player-prop family has complete settlement/postponement/cancellation/participation/stat-correction equivalence for this census window. Strict props and incomplete incentive telemetry remain **$0 guaranteed capacity**.

## G<->K sports — fresh 01:44 ET

Direct production heartbeats prove the process is alive but the two-sided sample remains frozen:

- raw triggers/evaluations: **108,332,190+** and continuing;
- comparable evaluations: **120,836** unchanged;
- historical **6 >=3c hot evaluations / 4 completed signal lifecycles**;
- Gemini **570 symbols / 0 connected**;
- Gemini book updates frozen at **76,955**;
- Kalshi **570/570 initialized**;
- `read_only=true`, `order_entry=false`;
- rule gate remains `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`.

The latest one-minute metric sample being idle was only a sample-boundary artifact; direct heartbeats continued through 01:44 ET. Economically the lane is still **unmeasurable/$0 guaranteed capacity** because Gemini remains 0/570 connected and comparables are frozen. Do not re-arm. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Prior evidence favors websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting and weather

Fresh SQL reconciliation remains unavailable because the Neon connector's direct SQL path is not currently exposed in a usable form for this project. Accounting is therefore **not advanced speculatively**.

Last successful direct reconciliation remains:

- **2026-08-27 ET: 0 ledger events, 0 TRADE_REALIZED, $0.0000 realized net, 0 capital reservations**;
- weather: **0 verified weather-tagged TRADE_REALIZED / $0.0000 since 2026-03-01**;
- Caleb Durbin: prior `SETTLED_ONE_LEG`, **-$0.44 REALIZED**.

Canonical weather `weather-live-probe` was last verified **CRASHED**. Seasonal historical/backtest weather edge remains separate from verified realized performance and gets no census credit.

## Current verdict trajectory

Guaranteed/deployable evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- K<->P maker: 2,166-fill immediate shadow headline is non-deployable; only 134 delayed hedges are decision-usable, 250/500 ms aggregate economics are negative, and lifecycle equivalence fails => **$0/day capacity**;
- strict props/incentives: uncertified/incomplete => **$0**;
- G<->K: disarmed, Gemini 0/570 connected and comparables frozen => **$0 guaranteed capacity**;
- weather verified realized since March: **$0** at the last authoritative query.

Current evidence continues to track strongly toward the pre-committed **<$50/day** bucket. Do not declare the final verdict before the fixed deadline.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker and maker/markout collectors alive; active-but-unmeasurable intervals score $0 and are not mislabeled as fresh zero-edge samples.
3. Keep G<->K guaranteed execution disarmed; do not count raw activity without two-sided Gemini connectivity and full lifecycle certification.
4. Do not spend the remaining census window manufacturing same-venue violations, repairing rule-ineligible maker mechanics, or building around uncertified props.
5. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
6. Keep weather historical/backtest edge separate from verified live realized P&L.
7. Treat `crypto-live-balance-reconcile-v2` as observability-only until its actual reconciliation output is independently verified.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.
