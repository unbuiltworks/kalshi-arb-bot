# ARB Control Plane

Last verified: **2026-08-26 14:48 UTC / 2026-08-26 10:48 ET**

This is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

- `sports-kp-live-executor`: Railway **SUCCESS**. Fresh runtime logs are available again. The process is the read-only K<->P census gateway; no order authority is present.
- `sports-generic-counterpart-shadow`: Railway **SUCCESS**. It remains read-only maker-feasibility/incentive telemetry with explicit $0 deployable-capacity credit until fill/adverse-selection/capital-bounded yield is proven.
- `sports-gk-autonomous-supervisor`: Railway **SUCCESS** at the service-control-plane level; guaranteed-arb order entry remains disarmed/read-only.
- `crypto-15m-sol-live-canary-10bp-50ms`: Railway **CRASHED/contained**. The bounded-campaign deadline failure is proven, but Gemini/Kalshi settlement-index equivalence remains unproven, so it must not be reopened as guaranteed arbitrage.

## Decision-grade K<->P evidence

### Strict MLB totals

Latest cumulative verified result remains:

- **53,237 comparable evaluations**;
- **1,661 decision observations** across **9 semantic keys**;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth **~2,073**, max **~22,648**;
- average quote skew **~45.5 ms**;
- descriptive positive-signal lifetime **~174 ms avg / ~567 ms max**;
- Kalshi REST fallback remains labeled `REST_SNAPSHOT_NOT_WS_LIFETIME`.

Interpretation: displayed liquidity is not the blocker. The certified totals slice has produced **zero >=2c and zero >=3c after-fee observations**, so current realistic deployable capacity at the fixed 3c taker/taker floor is **$0/day**.

### Clean MLB full-game breadth

After fixing the four-hour Kalshi start-time normalization bug and removing F5/partial-game contamination, the latest clean full-game taker/taker result remains **0 >=1c / 0 >=2c / 0 >=3c** across moneylines, spreads and totals. The earlier large apparent spread/total edges were scope mismatches and remain assigned $0 capacity.

Fresh maker-feasibility runtime at **2026-08-26 14:48 UTC** is also unfavorable under the conservative model. The collector reports **98 semantic groups** and **277 priced representations**. Conservative maker bins are **1 >=1c / 0 >=2c / 0 >=3c**; the best conservative row is a moneyline at about **+1.5c**. The optimistic zero-Kalshi-maker-fee upper bound reports **2 >=1c / 1 >=2c / 1 >=3c**, with a best row around **+3.5c**, but the runtime itself labels `deployable_capacity_credit_usd_per_day=0.0` and states that fill probability/adverse selection/capital-bounded yield remain unproven. That optimistic row therefore remains **$0 deployable capacity** and is not a reason to arm live maker execution.

### Strict MLB player props

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Kalshi and Polymarket lifecycle rules differ materially in the existing registry, so strict props receive **$0 guaranteed-arb capacity** for this census window unless genuinely new authoritative rule text resolves every mismatch. Do not turn this into open-ended research.

## G<->K sports

Guaranteed-arb execution remains disarmed/read-only. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away. The hot-shadow lane may observe positive quote gaps, but the Gemini full prop lifecycle fingerprint remains uncertified; shadow signals receive **$0 guaranteed-arb capacity**.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Evidence continues to favor concurrent websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

Last direct Neon checkpoint remains:

- **2026-08-26 ET: 0 `TRADE_REALIZED` / $0.0000 net**.
- Caleb Durbin remains **-$0.44 REALIZED** from the prior sports one-leg settlement.

Weather:

- Last direct central-ledger query: **0 weather-tagged `TRADE_REALIZED` / $0.0000 verified realized since 2026-03-01**.
- Seasonal/backtest weather edge remains separate from current verified realized performance and contributes $0 to the decision unless authoritative project-local live records prove otherwise.

## Highest-value next actions

1. Keep the fixed 72-hour clock running. Do not reset it for interesting but non-executable observations.
2. Continue collecting clean time-weighted K<->P decision telemetry and compute overlap-adjusted realistic $/day capacity. Do not lower the 3c floor.
3. Treat the current maker census as evidence, not a new build project: conservative maker has **0 >=3c**, while the only optimistic >=3c row has no proven fill/adverse-selection yield and remains $0 capacity.
4. Do not spend engineering time manufacturing same-venue ladder violations; same-venue zeros remain near-verdict evidence.
5. Keep strict props at $0 unless complete lifecycle equivalence is proven inside the current window.
6. Keep G<->K guaranteed execution disarmed and Caleb's loss realized.
7. Preserve Kalshi account headroom/backoff; do not stop unrelated services solely for census telemetry.
8. Keep weather historical/backtest edge separate from verified live realized P&L.
9. At the census deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.
