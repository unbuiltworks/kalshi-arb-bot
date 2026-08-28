# ARB Control Plane

Last verified: **2026-08-28 06:53 UTC / 2026-08-28 02:53 ET**

## Objective and fixed verdict

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

Pre-committed census verdict thresholds remain fixed:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

The 72-hour wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and ends **2026-08-28 20:43 UTC / 16:43 ET**. It continues with no reset. Data/feed quality may add at most one +24-hour allowance; a lane that remains unmeasurable after that allowance contributes **$0 deployable capacity**. The realistic-capture floor remains **>=80 ms** and may not be lowered to rescue the result.

## Fresh production state

Fresh Railway verification at ~02:53 ET:

- `sports-kp-live-executor`: **SUCCESS**, still actively polling books and emitting summaries; read-only.
- `sports-generic-counterpart-shadow`: **SUCCESS**, read-only; normal Polymarket books are HTTP 200, while the separate program-wide incentive endpoint is returning HTTP 403.
- `sports-gk-autonomous-supervisor`: **SUCCESS** but in a broad Gemini reconnect storm across one-symbol shards; guaranteed execution remains read-only/disarmed.
- `crypto-15m-sol-live-canary-10bp-50ms`: **CRASHED/contained**. Do not reopen while Gemini/Kalshi settlement-index equivalence remains unproven.
- `crypto-live-balance-reconcile-v2`: latest deployment remains **SUCCESS**; observability/reconciliation only.

## Decision-grade K<->P evidence

### Strict MLB totals taker/taker — fresh 02:52 ET summary

The process is alive, but the decision sample remains frozen:

- raw triggers: **130,105,795**;
- comparable evaluations: **53,237**;
- decision observations: **1,661** across 9 semantic keys;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- >=80 ms realistic-capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth: **2,072.66**, max **22,647.58**;
- average quote skew: **45.53 ms**, max **100 ms**;
- average process latency: **0.234 ms**;
- closed positive signals: **612**;
- average closed lifetime: **174.07 ms**, max **567 ms**;
- source quality: `REST_SNAPSHOT_NOT_WS_LIFETIME`;
- Kalshi transport: `rest_batch`;
- `read_only=true`, `order_entry=false`.

The existing collector is still receiving Kalshi HTTP 200 responses through `api.elections.kalshi.com`; this is not the current blocker. New census code should continue to prefer Kalshi's dedicated external-api/external-api-ws hosts, but there is no justification to rewrite a functioning near-deadline collector solely for host elegance.

The comparable sample has failed to advance for well beyond the allowed data-quality grace while raw triggers continue to climb. Under the pre-committed rule, the stale/unmeasurable tail is now **locked at $0 deployable-capacity credit**. It does not earn a census extension and is not treated as millions of fresh zero-edge observations.

The valid certified totals slice itself remains **0 >=3c**, hence **$0/day deployable capacity at the fixed 3c taker/taker floor**.

### Clean MLB full-game taker/taker

After scheduled-start normalization and F5/partial-game exclusion, clean full-game moneyline/spread/total snapshots remain **0 >=1c / 0 >=2c / 0 >=3c** with substantial q1 depth. Edge magnitude, not liquidity, remains the limiting factor.

### Maker/fill/delayed-hedge shadow

The large immediate maker headline remains non-decision-grade. Last verified delayed-hedge cohort:

- **2,166 simulated q1 maker fills** and **+$128.6480 immediate shadow P&L** — descriptive only;
- only **134 / 2,166 (6.19%)** had decision-usable delayed hedge observations;
- at **80 ms**: **+$0.0408 total**, 22 >=1c / 9 >=2c / **2 >=3c**;
- at **250 ms**: **-$0.7158 total**, 14 / 8 / **2**;
- at **500 ms**: **-$1.1384 total**, 11 / 5 / **2**;
- delayed cohort had **0 depth-insufficient** failures;
- exact K<->P full-game moneyline postponement/reschedule lifecycle equivalence fails independently;
- `capacity_credit_usd_per_day=0`, `read_only=true`, `order_entry=false`.

Do not spend the remaining census window repairing this maker telemetry: even perfect observability would not convert a rule-ineligible mechanism into guaranteed arb. Maker shadow remains **$0 guaranteed/deployable capacity and $0 REALIZED P&L**.

### Strict props and incentives

No K<->P player-prop family has complete settlement/postponement/cancellation/participation/stat-correction equivalence for this census window. Strict props remain **$0 guaranteed capacity**.

Fresh Polymarket incentive telemetry sees **22 active MLB moneyline program rows** and **0 under-target live-pool candidates**. The program-wide incentive endpoint currently returns HTTP 403, so the denominator remains incomplete. `combined_capacity_credit_usd_per_day=0`; do not count headline reward pools as deployable or realized profit.

## G<->K sports — fresh 02:53 ET

The supervisor is alive but the feed remains unusable for decision evidence. Fresh logs show a broad `MLB_GK_PROP_GEMINI_RECONNECT ... RuntimeError` storm across many one-symbol shards; Railway dropped additional messages because of log volume. Prior decision state remains:

- comparable evaluations: **120,836** frozen;
- historical **6 >=3c hot evaluations / 4 completed lifecycles**, uncertified;
- Gemini **570 symbols / 0 connected** at the last decoded heartbeat;
- Kalshi **570/570 initialized**;
- `read_only=true`, `order_entry=false`;
- rule gate remains `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`.

This lane has remained two-sided-unmeasurable beyond the allowed data-quality grace. Under the fixed census rule, its decision contribution is now **locked at $0 deployable capacity**. Do not re-arm it or extend the census to rescue it. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.

## Kalshi account / 429 diagnosis

Authenticated account limits remain **advanced tier, 900 bucket / 300 refill** for reads and writes. Prior evidence favors websocket handshake/connection contention rather than a weak account tier. Fresh K<->P Kalshi REST traffic is HTTP 200. Do not weaken backoff or disrupt unrelated live Kalshi services solely to improve census telemetry.

## Authoritative accounting and weather

Fresh SQL reconciliation remains unavailable through the current Neon connector path, so accounting is **not advanced speculatively**.

Last successful direct reconciliation remains:

- **2026-08-27 ET: 0 ledger events, 0 TRADE_REALIZED, $0.0000 realized net, 0 capital reservations**;
- weather: **0 verified weather-tagged TRADE_REALIZED / $0.0000 since 2026-03-01**;
- Caleb Durbin: prior `SETTLED_ONE_LEG`, **-$0.44 REALIZED**.

Seasonal/backtest weather edge remains separate from verified current realized performance and receives no census credit.

## Current verdict trajectory

Guaranteed/deployable evidence at the fixed 3c floor:

- certified K<->P totals: **0 >=3c => $0/day**;
- clean full-game taker/taker: **0 >=3c => $0/day**;
- stale K<->P comparable tail: grace exhausted => **$0 capacity credit**;
- K<->P maker: realistic delayed economics weak/negative beyond 80 ms and lifecycle mismatch => **$0/day guaranteed capacity**;
- strict props/incentives: uncertified/incomplete => **$0**;
- G<->K: two-sided unmeasurable beyond grace and disarmed => **$0 guaranteed capacity**;
- weather verified realized since March: **$0** at last authoritative reconciliation.

Current certified evidence is therefore strongly in the pre-committed **<$50/day** bucket. Do not move the thresholds or declare the final verdict before **16:43 ET today**.

## Highest-value next actions

1. Keep the fixed census running through **2026-08-28 20:43 UTC / 16:43 ET** with no reset.
2. Do **not** repair stale K<->P comparables, Gemini reconnects, maker delayed-WS coverage, or incentive denominator plumbing merely to extend the census; their zero-credit treatment is already determined by the pre-committed rules or rule-certification failures.
3. Keep G<->K guaranteed execution disarmed and preserve Caleb's -$0.44 realized loss.
4. Preserve Kalshi account headroom/backoff; do not disrupt unrelated live services.
5. Keep weather historical/backtest edge separate from verified live realized P&L.
6. At the fixed deadline, aggregate only rule-certified, realistically executable capacity and apply **<$50 / $50-$149 / >=$150** exactly as committed.
