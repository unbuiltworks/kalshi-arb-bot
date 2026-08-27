# ARB Control Plane

Last verified: **2026-08-27 11:31 UTC / 2026-08-27 07:31 ET**

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

- `sports-kp-live-executor`: Railway **SUCCESS/read-only census**. Fresh last-hour Railway metrics show **61/61 samples**, current CPU ~0.0397, average CPU ~0.0703, with continuous RX/TX. This is an active process, not a green-but-dead deployment.
- `sports-generic-counterpart-shadow`: Railway **SUCCESS/read-only maker + delayed-hedge shadow**. Fresh last-hour metrics show **61/61 samples**, current CPU ~0.0112, average ~0.0231, with continuous RX/TX. The process is active; no new decision-usable markout summary could be extracted because the Railway detailed-log agent is quota-blocked.
- `sports-gk-autonomous-supervisor`: Railway **SUCCESS/read-only/disarmed hot shadow**. Fresh last-hour metrics show **61/61 samples**, current CPU ~0.351, average ~0.629, with heavy continuous RX/TX. It is active, but prior decision evidence remains blocked by Gemini connectivity/rule certification; order entry stays disarmed.
- `crypto-15m-sol-live-canary-10bp-50ms`: remains **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.
- `crypto-live-balance-reconcile-v2`: new auxiliary blocker — latest deployment **CRASHED** at ~10:40 UTC. Effective command is the read-only `python -m src.gemini_kalshi_balance_reconcile_readonly`, `restartPolicy=NEVER`. Railway detailed logs are quota-blocked, so the exact exception is not yet classified. This crash does not authorize restarting any crypto trading lane.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker

Latest directly verified cumulative sample remains:

- **53,237 comparable evaluations**;
- **1,661 decision observations** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms;
- average closed positive-signal lifetime ~174 ms, max 567 ms;
- Kalshi transport explicitly labeled **REST_SNAPSHOT_NOT_WS_LIFETIME**.

The live process is active, but a newer cumulative event summary could not be retrieved this run because Railway's detailed runtime agent hit its usage limit. Missing telemetry is not converted into fresh zero-edge evidence. The currently certified totals slice supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After correcting start-time normalization and excluding F5/partial-game contamination, clean full-game moneyline/spread/total evidence remains **0 >=1c / 0 >=2c / 0 >=3c** in the last certified snapshots. Displayed depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow

Latest decision-usable maker evidence remains:

- **1,178** pessimistic simulated q1 maker fills;
- **291** immediate fills >=3c after the verified maker-fee model;
- immediate shadow P&L about **+$32.67**;
- only **134/1,178** fills have delayed-hedge-observable marks; **1,044** are unobservable and receive $0 capacity credit;
- **80 ms:** +$0.0408 total, **22 >=1c / 9 >=2c / 2 >=3c**;
- **250 ms:** -$0.7158 total, **14 / 8 / 2**;
- **500 ms:** -$1.1384 total, **11 / 5 / 2**;
- no hedge-depth misses in the observable cohort.

The underlying K<->P full-game moneyline mechanism also fails exact postponement/reschedule lifecycle equivalence. Maker shadow therefore remains **$0 guaranteed/deployable capacity and $0 REALIZED P&L**. Fresh service metrics prove the process is alive, but this run produced no independently retrievable newer decision summary.

### Strict MLB player props / incentives

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed capacity** for this census window. Polymarket incentive denominator/time-weighted yield/pickoff accounting remains incomplete and receives **$0 capacity credit**.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. The service is strongly active at the process/network level, but the last directly verified decision state had **570 Gemini symbols / 0 connected**, Kalshi **570/570 initialized**, frozen comparable evaluations, and only historical uncertified >=3c hot observations. This interval remains unmeasurable/$0 guaranteed capacity until two-sided connectivity and full lifecycle certification are restored. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Evidence favors concurrent websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

Last directly verified ledger state remains:

- **2026-08-27 ET: 0 `TRADE_REALIZED` / $0.0000 net**;
- weather: **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 since 2026-03-01**;
- Caleb Durbin remains prior **`SETTLED_ONE_LEG`, -$0.44 REALIZED**.

No shadow/model result is credited to realized P&L.

## Current verdict trajectory

Guaranteed/deployable evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- maker delayed hedge: only **2/134** observable marks >=3c at 80 ms, aggregate about **+$0.0408** at 80 ms and negative at 250/500 ms, while **1,044/1,178** fills are unobservable;
- K<->P moneyline maker rules: lifecycle mismatch => **$0 guaranteed capacity**;
- strict props/incentives: uncertified/incomplete => **$0**;
- G<->K: disarmed and currently not decision-measurable => **$0 guaranteed capacity**;
- weather verified realized since March: **$0**.

Current evidence continues to track toward the pre-committed **<$50/day** bucket. Do not declare the final verdict before the fixed deadline.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker and maker/markout collectors alive; active-but-unmeasurable intervals score $0 and are not mislabeled as fresh zero-edge samples.
3. Keep G<->K guaranteed execution disarmed; do not count raw activity without two-sided connectivity and rule certification.
4. Diagnose `crypto-live-balance-reconcile-v2` only as a read-only reconciliation utility; do not let its crash divert the census or reopen unsafe crypto execution.
5. Do not manufacture same-venue ladder violations, loosen 3c economics, or force uncertified player props.
6. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
7. Keep weather historical/backtest edge separate from verified live realized P&L.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.
