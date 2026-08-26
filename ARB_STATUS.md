# ARB Control Plane

Last verified: **2026-08-26 19:40 UTC / 2026-08-26 15:40 ET**

This is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset or pause. Data/feed quality may add at most one +24-hour extension. Unmeasurable or uncertified lanes contribute **$0 deployable capacity** for the relevant interval. The provisional realistic-capture floor remains **>=80 ms** and may rise to measured production p95, but may not be lowered to rescue capacity.

## Fresh production state

Fresh Railway project status at 19:40 UTC:

- `sports-kp-live-executor`: **SUCCESS**. Last-hour metrics: 61/61 CPU/network samples, average CPU ~0.0904; process is alive.
- `sports-generic-counterpart-shadow`: **SUCCESS**. Last-hour average CPU ~0.0610 with active network traffic; read-only maker/fill census remains alive.
- `sports-gk-autonomous-supervisor`: **SUCCESS**. Last-hour average CPU ~0.563 with substantial network traffic; guaranteed-arb execution remains disarmed/read-only.
- `crypto-15m-sol-live-canary-10bp-50ms`: **CRASHED/contained**. Do not reopen as guaranteed arb while Gemini/Kalshi settlement-index equivalence remains unproven.

Railway's deep runtime/log agent is currently usage-limited, so this run does not claim newer event-level maker counts than the last verified summary.

## Decision-grade K<->P evidence

### Strict MLB totals

Last decision-grade totals summary remains:

- **53,237 comparable evaluations**;
- **1,661 decision observations** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth ~**2,073**, max ~22,648;
- average quote skew ~45.5 ms, max 100 ms.

The later trigger-only interval remains **unmeasurable/stale-comparison**, not millions of additional zero-edge observations. It receives **$0 capacity credit for the interval** and does not reset the wall clock. The valid certified totals sample still supports **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After fixing start-time normalization and excluding F5/partial-game contamination, clean full-game moneyline/spread/total evidence remains **0 >=1c / 0 >=2c / 0 >=3c** in the last certified snapshots. Displayed depth is substantial; edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill shadow — fill conversion real, guaranteed-arb certification fails

Last verified read-only maker telemetry at 18:38 UTC:

- **92 matched moneyline pairs**;
- roughly **1,290 quote opens / 420 reprices / 778 cancels**;
- **25,200+ Kalshi public trades observed**;
- roughly **1,280 qualifying opposite-side taker prints**;
- **92 pessimistic simulated q1 fills**;
- **58 trade-through / 34 exact-price fills**;
- median quote age at fill **~763 ms**;
- immediate-fill economics **37 >=1c / 23 >=2c / 11 >=3c** under the verified KXMLBGAME maker-fee model;
- **0 hedge-missing-at-fill** events;
- combined pure-trading **shadow** P&L about **$1.2768**;
- `combined_maker_capacity_credit_usd_per_day=0`;
- `read_only=true`, `order_entry=false`.

The >=3c shadow fills are heavily concentrated in the **TB-DET full-game moneyline** representation. The bounded certification question is now answered: **Kalshi and Polymarket US do not have equivalent postponement/reschedule lifecycles for this full-game moneyline mechanism.**

- Kalshi KXMLBGAME states that a postponed/delayed game remains live if completed **within two days**; if cancelled or rescheduled beyond two days it resolves to a fair price under venue rules.
- Polymarket US MLB moneyline states that delayed/postponed/suspended games remain valid if rescheduled **within two weeks**; otherwise the market settles to last fair market price.
- Both source the game outcome from MLB and include extra innings, but the **2-day vs 2-week lifecycle mismatch is sufficient to fail exact settlement/cancellation equivalence**.

Therefore the TB-DET maker shadow and analogous KXMLBGAME<->Polymarket full-game moneyline rows are **not guaranteed arbitrage** and receive **$0 guaranteed-arb deployable capacity**, regardless of modeled fill economics. Do not arm this representation as guaranteed arb. Shadow fills/P&L remain descriptive only.

### Strict MLB player props

No K<->P player-prop family currently has complete settlement/postponement/cancellation/participation/stat-correction equivalence. Strict props remain **$0 guaranteed-arb capacity** for this census window unless genuinely new authoritative rules resolve every mismatch. Do not turn this into open-ended research.

## G<->K sports

Guaranteed-arb execution remains **disarmed/read-only**. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

Last event-level heartbeat showed comparable evaluations frozen at 120,836, Gemini 570 symbols / 0 connected and Kalshi 570/570 initialized. Fresh Railway resource metrics confirm the service process itself remains active, but without current two-sided Gemini connectivity that interval remains **unmeasurable and gets $0 capacity credit**. Do not re-arm it.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for both reads and writes. Evidence continues to favor concurrent websocket handshake/connection contention rather than a weak account tier. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting

Fresh direct Neon queries at 19:40 UTC:

- **2026-08-26 ET: no ledger events at all since ET midnight; 0 `TRADE_REALIZED` / $0.0000 net**;
- therefore **0 new `CAPITAL_RESERVED` events since ET midnight**;
- Caleb Durbin remains **-$0.44 REALIZED** from the prior sports one-leg settlement;
- weather since **2026-03-01** remains **0 verified weather-tagged `TRADE_REALIZED` / $0.0000 net**.

Historical/seasonal weather edge remains separate and receives no capacity credit without authoritative live records.

## Current verdict trajectory

Guaranteed-arb evidence remains strongly negative at the fixed 3c floor:

- certified K<->P totals: **0 >=3c**;
- clean full-game taker/taker: **0 >=3c**;
- K<->P full-game moneyline maker: **rule lifecycle mismatch => $0 guaranteed capacity** despite simulated fills;
- strict props: uncertified => **$0**;
- G<->K current interval: unmeasurable/disarmed => **$0**;
- weather verified realized since March: **$0**.

The maker shadow remains useful execution research because fill conversion is demonstrably non-zero, but its strongest current representation fails guaranteed-arb lifecycle certification. That closes the only bounded maker question that could have rescued guaranteed K<->P capacity without changing the project definition. Do not replace the failed certification with open-ended strategy research during the census.

## Highest-value next actions

1. Keep the fixed census clock running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Keep K<->P taker census alive; stale/unmeasurable intervals score $0 and are not mislabeled as fresh zero-edge samples.
3. Continue collecting maker telemetry descriptively, but **do not credit or arm KXMLBGAME<->Polymarket full-game moneyline as guaranteed arb** under the current 2-day vs 2-week lifecycle mismatch.
4. Keep G<->K guaranteed execution disarmed; Caleb's loss remains realized.
5. Do not manufacture same-venue ladder violations or force uncertified player props.
6. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services solely for census telemetry.
7. Keep weather historical/backtest edge separate from verified live realized P&L.
8. At the deadline, apply the fixed **<$50 / $50-$149 / >=$150** verdict without moving thresholds.
