# ARB Control Plane

Last verified: **2026-08-27 20:42 UTC / 2026-08-27 16:42 ET**

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

Fresh Railway control-plane and one-hour metrics at ~16:42 ET:

- `sports-kp-live-executor`: latest deployment **SUCCESS**. Last-hour metrics: 61 samples, average CPU ~**0.0698**, average RX ~**0.00429 GB/min-sample**, average TX ~**0.00213 GB/min-sample**. This verifies the process had sustained activity during the hour even though the current sample is zero. Detailed runtime-log inspection is quota-blocked, so the decision counters are **not advanced speculatively**.
- `sports-generic-counterpart-shadow`: latest deployment **SUCCESS**. Last-hour metrics: 61 samples, current CPU ~**0.0179**, average CPU ~**0.0349**, current RX/TX nonzero. The maker/markout shadow is actively running; no order authority.
- `sports-gk-autonomous-supervisor`: latest deployment **SUCCESS**. Last-hour metrics: 61 samples, current CPU ~**0.379**, average CPU ~**0.668**, current RX/TX nonzero. This proves the disarmed hot-shadow process is alive; it does **not** prove two-sided Gemini comparability.
- `crypto-15m-sol-live-canary-10bp-50ms`: latest deployment remains **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.
- `crypto-live-balance-reconcile-v2`: newest Railway deployment is **SUCCESS** at commit `733ca8da...`, with start command `python -m src.gemini_kalshi_balance_reconcile_readonly` and `restartPolicy=NEVER`. However, current `main` still shows that module importing the removed `src.gemini_kalshi_probe_transport`; therefore a green deployment badge alone is **not sufficient proof that authoritative balance reconciliation succeeded**. Treat this as observability-only and do not use it to authorize crypto execution.

Railway's detailed runtime agent is currently quota-blocked (`Agent usage limit reached`), so this checkpoint deliberately does not invent newer edge-bin, maker-markout, or G<->K connectivity counters.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker

Last fully decoded decision-grade summary remains:

- raw triggers: **102,412,651**;
- comparable evaluations: **53,237**;
- decision observations: **1,661** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms;
- average closed positive-signal lifetime ~174 ms, max 567 ms;
- Kalshi transport explicitly labeled **REST_SNAPSHOT_NOT_WS_LIFETIME**;
- `read_only=true`, `order_entry=false`.

The collector is alive, but without fresh comparable/decision counters the current interval remains **unmeasurable/$0 capacity**, not fresh negative-edge evidence. The certified totals slice still supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After correcting scheduled-start normalization and excluding F5/partial-game contamination, the last certified clean full-game moneyline/spread/total snapshots remain **0 >=1c / 0 >=2c / 0 >=3c**. Displayed q1 depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow

Last fully decoded maker telemetry remains:

- **1,374 simulated q1 maker fills**;
- **335** looked >=3c immediately under the maker model;
- only **134/1,374 (9.75%)** had decision-usable delayed hedge observations; **1,240** were unobservable and receive $0 executable-capacity credit;
- at **80 ms**: 134 observable hedges, **+$0.0408 total**, 22 >=1c / 9 >=2c / **2 >=3c**;
- at **250 ms**: **-$0.7158 total**, 14 / 8 / **2**;
- at **500 ms**: **-$1.1384 total**, 11 / 5 / **2**;
- no hedge-missing, full-ladder-missing, or q1 depth-insufficient failures in the 134-fill observable cohort;
- collector remains `rules_complete=false`, `read_only=true`, `order_entry=false`, `capacity_credit_usd_per_day=0`.

The K<->P full-game moneyline mechanism also fails exact postponement/reschedule lifecycle equivalence. Therefore immediate shadow economics remain **$0 guaranteed/deployable capacity and $0 REALIZED P&L**. Do not spend the remaining census window repairing a maker mechanism already rule-ineligible.

### Strict MLB player props / incentives

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed capacity** for this census window. Incentive/rebate results remain $0 capacity until program-wide denominator, time-weighted realized yield and pickoff/adverse-selection costs are proven.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. Last fully decoded state remains:

- **120,836 comparable evaluations**;
- historical **6 >=3c hot evaluations / 4 completed signal lifecycles**;
- Gemini **570 symbols / 0 connected**;
- Kalshi **570/570 initialized**;
- `read_only=true`, `order_entry=false`.

Fresh Railway CPU/network metrics prove the process is alive, but do not prove Gemini connectivity recovered. Therefore the current interval remains **unmeasurable/$0 guaranteed capacity**. Do not re-arm. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Prior evidence continues to favor websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

A fresh authoritative SQL reconciliation could not be completed in this run because the Neon project connector still cannot be reached through a valid project context. Accounting is therefore **not advanced speculatively**.

Last successful direct reconciliation remains **2026-08-27 11:46 ET**:

- **2026-08-27 ET: 0 ledger events, 0 `TRADE_REALIZED`, $0.0000 realized net, 0 capital reservations**;
- weather: **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 since 2026-03-01**;
- Caleb Durbin remains prior **`SETTLED_ONE_LEG`, -$0.44 REALIZED**.

No shadow/model result is credited to realized P&L.

## Current verdict trajectory

Guaranteed/deployable evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- K<->P maker: last usable delayed cohort had only **2 >=3c at 80ms**, negligible aggregate 80ms P&L, negative 250/500ms P&L, ~90% unobservable, and exact moneyline lifecycle equivalence fails => **$0/day capacity**;
- strict props/incentives: uncertified/incomplete => **$0**;
- G<->K: disarmed and not decision-measurable without two-sided Gemini connectivity => **$0 guaranteed capacity**;
- weather verified realized since March: **$0** at the last authoritative query.

Current evidence continues to track toward the pre-committed **<$50/day** bucket. Do not declare the final verdict before the fixed deadline.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker and maker/markout collectors alive; active-but-unmeasurable intervals score $0 and are not mislabeled as fresh zero-edge samples.
3. Keep G<->K guaranteed execution disarmed; do not count raw activity without two-sided Gemini connectivity and full lifecycle certification.
4. Do not spend the remaining census window manufacturing same-venue violations, repairing a maker mechanism already rule-ineligible, or building around uncertified props.
5. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
6. Keep weather historical/backtest edge separate from verified live realized P&L.
7. Treat `crypto-live-balance-reconcile-v2` as observability-only until its actual output is independently verified; its green deployment badge is not execution authority.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.
