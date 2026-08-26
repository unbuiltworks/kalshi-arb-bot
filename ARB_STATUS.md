# ARB Control Plane

Last verified: **2026-08-26 18:38 UTC / 2026-08-26 14:38 ET**

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

Fresh runtime at **18:38 UTC** remains alive, but the decision sample is still frozen while raw triggers have risen to roughly **42.35M**:

- **53,237 comparable evaluations**;
- **1,661 decision observations** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms.

The trigger-only interval is **unmeasurable/stale-comparison**, not millions of additional zero-edge observations. It receives **$0 capacity credit for the interval** and does not reset the wall clock. The valid certified totals sample still supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After fixing start-time normalization and excluding F5/partial-game contamination, clean full-game moneyline/spread/total evidence remains **0 >=1c / 0 >=2c / 0 >=3c** in the last certified snapshots. Displayed depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow — stronger material signal, still $0 certified capacity

Fresh read-only moneyline maker telemetry strengthened materially during the **18:16-18:37 UTC** window:

- **92 matched moneyline pairs**;
- roughly **1,290 quote opens / 420 reprices / 778 cancels** at the latest summaries;
- **25,200+ Kalshi public trades observed**;
- roughly **1,280 qualifying opposite-side taker prints**;
- **92 pessimistic simulated q1 fills**;
- **58 trade-through fills / 34 exact-price fills**;
- median quote age at fill **~763 ms**;
- immediate-fill economics **37 >=1c / 23 >=2c / 11 >=3c** under the verified general-maker-fee model;
- zero-maker-fee upper-bound immediate bins **59 >=1c / 29 >=2c / 20 >=3c**;
- **0 hedge-missing-at-fill** events;
- combined pure-trading **shadow** P&L approximately **$1.2768**;
- `combined_maker_capacity_credit_usd_per_day=0`;
- `rules_complete=false`, `read_only=true`, `order_entry=false`.

The new >=3c modeled fills remain heavily concentrated in the TB-DET full-game moneyline representation. Several modeled fills have retained >=3c through subsequent hedge checks, but the collector still explicitly classifies them as `PESSIMISTIC_PUBLIC_TRADE_FILL_SHADOW_RULE_INCOMPLETE_NOT_EXECUTION_ELIGIBLE`. Therefore **none of the 92 simulated fills, 11 >=3c observations, or ~$1.28 shadow P&L count as deployable or REALIZED capacity**.

This materially rules out the earlier hypothesis that maker fill conversion is simply zero. The remaining bounded question is whether this moneyline representation can pass exact full-game settlement/cancellation/lifecycle certification and time-weighted adverse-selection/capital-bounded yield accounting before the fixed census deadline. Do not arm from shadow evidence alone.

### Strict MLB player props

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed-arb capacity** for this census window unless genuinely new authoritative rules resolve every mismatch. Do not turn this into open-ended research.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

Fresh heartbeat at **18:38 UTC** shows:

- raw triggers/evaluations ~**39.6M**;
- comparable evaluations remain frozen at **120,836**;
- historical positive / >=3c hot evaluations remain **6 / 6**, with 4 complete lifecycles;
- Gemini symbols **570**, connected symbols **0**;
- Kalshi tickers **570/570 initialized**;
- `rule_gate=GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`;
- `read_only=true`, `order_entry=false`.

Thus the current G<->K interval is **unmeasurable and receives $0 capacity credit**, not fresh zero-edge evidence. Do not re-arm it.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Evidence continues to favor concurrent websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

Fresh direct Neon query at **18:38 UTC**:

- **2026-08-26 ET: 0 `TRADE_REALIZED` / $0.0000 net**;
- **0 `CAPITAL_RESERVED` events since ET midnight**;
- no ledger event of any type since ET midnight;
- Caleb Durbin remains **-$0.44 REALIZED** from the prior sports one-leg settlement.

Fresh project-wide query also confirms weather remains **0 verified weather-tagged REALIZED / $0 since 2026-03-01**. Historical/seasonal weather edge remains separate and receives no capacity credit without authoritative live records.

## Current verdict trajectory

Guaranteed/taker evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- strict props: uncertified => **$0**;
- G<->K current interval: unmeasurable/disarmed => **$0**;
- weather verified realized since March: **$0**.

The maker shadow is the only materially improved signal: **92 pessimistic simulated fills and 11 immediate >=3c observations** now show plausible fill conversion under the verified fee model. But because exact rules remain incomplete and the rows are shadow-only, **certified deployable capacity remains $0/day**. Do not declare the final verdict before the fixed deadline, and do not move the thresholds.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker census alive; stale trigger-only intervals score $0 and are not treated as millions of zero-edge samples.
3. Treat the TB-DET/full-game moneyline maker representation as the one bounded maker question worth resolving: verify exact settlement/cancellation/lifecycle equivalence and calculate time-weighted adverse-selection plus capital-bounded $/day yield. If certification fails or remains incomplete, it stays $0; do not branch into open-ended maker research.
4. Keep G<->K guaranteed execution disarmed; Caleb's loss remains realized.
5. Do not manufacture same-venue ladder violations or force uncertified player props.
6. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
7. Keep weather historical/backtest edge separate from verified live realized P&L.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.
