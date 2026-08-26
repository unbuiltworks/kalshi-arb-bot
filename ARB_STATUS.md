# ARB Control Plane

Last verified: **2026-08-26 13:38 UTC / 2026-08-26 09:38 ET**

This is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

- `sports-kp-live-executor`: Railway **SUCCESS**. Fresh 1-hour metrics show **61/61 samples**, average CPU ~**0.0916**, average RX ~**0.02225 GB**, average TX ~**0.00176 GB**. The process is genuinely active, not merely carrying a green badge.
- `sports-generic-counterpart-shadow`: Railway **SUCCESS** on a fresh 2026-08-26 13:17 UTC deployment. Fresh 1-hour metrics show **61/61 samples**, average CPU ~**0.0482**, with continuous network activity. Its effective entrypoint is the read-only `sports_kp_generic_mlb_moneyline_maker_fill_shadow`; it records maker-feasibility/incentive telemetry only, has **no order authority**, and explicitly assigns **$0/day capacity credit** until fill/adverse-selection/capital-bounded yield is proven.
- `sports-gk-autonomous-supervisor`: Railway **SUCCESS**. Fresh 1-hour metrics show **61/61 samples**, average CPU ~**0.515** with sustained network traffic. Effective process remains the read-only `gemini_kalshi_mlb_prop_hot_shadow`; guaranteed-arb order entry remains disarmed.
- `crypto-15m-sol-live-canary-10bp-50ms`: Railway **CRASHED/contained**. The bounded-campaign deadline failure is proven, but Gemini/Kalshi settlement-index equivalence remains unproven, so it must not be reopened as guaranteed arbitrage.
- Railway detailed runtime/log inspection is temporarily blocked by the Railway agent usage limit. Missing fresh log summaries are **not** converted into zero edge or used to reset the census clock.

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

After fixing the four-hour Kalshi start-time normalization bug and removing F5/partial-game contamination, the latest clean full-game result remains **0 >=1c / 0 >=2c / 0 >=3c** across moneylines, spreads and totals. The earlier large apparent spread/total edges were scope mismatches and remain assigned $0 capacity.

The current conservative maker-feasibility shadow has **zero >=3c rows** across the measured full-game universe. One deliberately optimistic zero-Kalshi-maker-fee upper-bound row can exceed 3c, but it has no fill model and incomplete rule certification; it receives **$0 deployable-capacity credit** and is not a reason to build or arm a live maker engine in this control-plane workstream.

### Strict MLB player props

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Kalshi and Polymarket lifecycle rules differ materially in the existing registry, so strict props receive **$0 guaranteed-arb capacity** for this census window unless genuinely new authoritative rule text resolves every mismatch. Do not turn this into open-ended research.

## G<->K sports

Guaranteed-arb execution remains disarmed/read-only. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away. The hot-shadow lane may observe positive quote gaps, but the Gemini full prop lifecycle fingerprint remains uncertified; shadow signals receive **$0 guaranteed-arb capacity**.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Evidence continues to favor concurrent websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

Fresh Neon `arb-trade-ledger` query:

- **2026-08-26 ET: 0 `TRADE_REALIZED` / $0.0000 net**.
- No event of any type has landed in the ET-day window at this checkpoint.
- Caleb Durbin remains **-$0.44 REALIZED** from the prior sports one-leg settlement.

Weather:

- Fresh central-ledger query: **0 weather-tagged `TRADE_REALIZED` / $0.0000 verified realized since 2026-03-01**.
- Separate `kalshi-weather-bot` project is still present; current services include the original weather bot, a shadow service, a backtest service, and a crashed live probe. Railway's read-only runtime agent is quota-blocked, so project-local historical records could not be independently extracted this run.
- Therefore **seasonal/backtest weather edge remains separate from current verified realized performance** and contributes $0 to the decision unless authoritative project-local live records prove otherwise.

## Highest-value next actions

1. Keep the fixed 72-hour clock running. Do not reset it for interesting but non-executable observations.
2. Continue collecting clean time-weighted K<->P decision telemetry and compute overlap-adjusted realistic $/day capacity. Do not lower the 3c floor.
3. Do not spend engineering time manufacturing same-venue ladder violations; same-venue zeros remain near-verdict evidence.
4. Keep strict props at $0 unless complete lifecycle equivalence is proven inside the current window.
5. Keep G<->K guaranteed execution disarmed and Caleb's loss realized.
6. Preserve Kalshi account headroom/backoff; do not stop unrelated services solely for census telemetry.
7. Keep weather historical/backtest edge separate from verified live realized P&L.
8. At the census deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.
