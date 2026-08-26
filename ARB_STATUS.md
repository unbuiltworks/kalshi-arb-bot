# ARB Control Plane

Last verified: **2026-08-26 17:32 UTC / 2026-08-26 13:32 ET**

This is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

- `sports-kp-live-executor`: Railway **SUCCESS** and actively receiving triggers. Read-only K<->P census gateway; no order authority.
- `sports-generic-counterpart-shadow`: Railway **SUCCESS** and actively collecting maker/fill evidence. Read-only only.
- `sports-gk-autonomous-supervisor`: Railway **SUCCESS**, guaranteed-arb execution remains disarmed/read-only.
- `crypto-15m-sol-live-canary-10bp-50ms`: **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.

## Decision-grade K<->P evidence

### Strict MLB totals

Fresh runtime at **17:31 UTC** remains alive, but the decision sample is still frozen while raw triggers have risen past **40.1M**:

- **53,237 comparable evaluations**;
- **1,661 decision observations** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms.

The trigger-only interval is **unmeasurable/stale-comparison**, not millions of additional zero-edge observations. It receives **$0 capacity credit for the interval** and does not reset the wall clock. The valid certified totals sample still supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After fixing start-time normalization and excluding F5/partial-game contamination, clean full-game moneyline/spread/total evidence remains **0 >=1c / 0 >=2c / 0 >=3c** in the last certified snapshots. Displayed depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow — material new signal, still $0 certified capacity

Fresh read-only moneyline maker telemetry changed materially during the **17:26-17:32 UTC** window:

- **92 matched moneyline pairs**;
- **860 quote opens / 195 reprices / 618 cancels** at the latest stable summary;
- **14,895 Kalshi public trades observed**;
- **520 qualifying opposite-side taker prints**;
- **47 pessimistic simulated q1 fills**;
- **31 trade-through fills / 16 exact-price fills**;
- median quote age at fill **~775 ms**;
- immediate-fill economics observed **24 >=1c / 18 >=2c / 10 >=3c** under the verified general-maker-fee model;
- zero-maker-fee upper-bound immediate bins **40 >=1c / 21 >=2c / 18 >=3c**;
- combined pure-trading **shadow** P&L **$0.9155**;
- `combined_maker_capacity_credit_usd_per_day=0`;
- `rules_complete=false`, `read_only=true`, `order_entry=false`.

Several modeled fills retained >=3c after 80/250/500 ms hedge checks, but the sample is concentrated in the TB-DET moneyline representation and the collector still explicitly classifies it as `RULE_INCOMPLETE_NOT_EXECUTION_ELIGIBLE`. Therefore **none of the 47 simulated fills or $0.9155 shadow P&L count as deployable or REALIZED capacity**.

This is the most important new evidence this run: maker fill probability is no longer empirically zero. The remaining blocker is now rule/lifecycle certification plus time-weighted adverse-selection and capital-bounded yield, not merely whether public prints can plausibly fill a maker quote. Do not arm from this shadow result alone.

### Strict MLB player props

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed-arb capacity** for this census window unless genuinely new authoritative rules resolve every mismatch. Do not turn this into open-ended research.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away. The latest verified interval had Gemini connectivity unusable for decision-grade sampling while Kalshi remained initialized, so that interval receives **$0 capacity credit** rather than being mislabeled as negative-edge evidence.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Evidence continues to favor concurrent websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

Fresh direct Neon query at **17:32 UTC**:

- **2026-08-26 ET: 0 `TRADE_REALIZED` / $0.0000 net**;
- **0 new `CAPITAL_RESERVED` events since ET midnight**;
- no ledger event of any type since ET midnight;
- Caleb Durbin remains **-$0.44 REALIZED** from the prior sports one-leg settlement.

Weather remains **0 verified weather-tagged REALIZED / $0 since 2026-03-01** from the last authoritative project-wide query. Historical/seasonal weather edge remains separate and receives no capacity credit without authoritative live records.

## Current verdict trajectory

Guaranteed/taker evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- strict props: uncertified => **$0**;
- G<->K current interval: unmeasurable/disarmed => **$0**;
- weather verified realized since March: **$0**.

The maker shadow is the only materially improved signal: 47 pessimistic simulated fills and 10 immediate >=3c observations now prove that maker fill conversion is not necessarily zero. But because rules remain incomplete and the rows are shadow-only, **certified deployable capacity remains $0/day**. Do not declare the final verdict before the fixed deadline, and do not move the thresholds.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker census alive; stale trigger-only intervals score $0 and are not treated as millions of zero-edge samples.
3. For the new maker signal, determine whether the >=3c simulated fills survive **strict full-game settlement/rule certification and adverse-selection/time-weighted yield accounting**. Do this as bounded evidence work; do not add live order authority during the census.
4. Keep G<->K guaranteed execution disarmed; Caleb's loss remains realized.
5. Do not manufacture same-venue ladder violations or force uncertified player props.
6. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
7. Keep weather historical/backtest edge separate from verified live realized P&L.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.
