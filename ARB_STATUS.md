# ARB Control Plane

Last verified: **2026-08-25 16:45 UTC / 2026-08-25 12:45 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and accounting rule

Primary aspiration remains **$480/day REALIZED net**, but the pre-committed census verdict is based on measured realistically deployable capacity, not that aspiration.

Only authoritative settlement/reconciliation can create `REALIZED` P&L. Stale quotes, maker-indicated economics, shadow/model P&L, unmatched rule-risk rows, unconfirmed fills and unsettled positions never count as realized arbitrage profit.

## Fixed edge-census protocol

Instrumentation deadline: **Wednesday 2026-08-26**. Then collect **72 hours** and publish verdict **Sunday 2026-08-30**. Data-quality/feed failure may add at most 24 hours; an unmeasurable lane after that counts as **$0 deployable capacity**. Do not extend because results are interesting or disappointing.

Pre-committed project verdict thresholds, which must not move after seeing results:

- **<$50/day** realistically deployable locked-edge capacity: end dedicated arb engineering and archive the project.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward the $480/day aspiration.

Required measurements: >=1c/>=2c/>=3c after fees, exact executable depth, p50/p90/max lifetime, quote skew, overlap-adjusted capacity, and latency-floor-filtered realistic capture. Provisional latency floor is 80ms and may rise to measured production p95 but may not be lowered to rescue capacity. Maker adverse-selection evidence uses public trade-print proxy, not live census orders.

Same-venue Gemini/Polymarket/Kalshi zero results are near-verdict evidence; do not spend engineering time manufacturing same-venue ladder violations.

## Current authoritative scoreboard

- **2026-08-25 ET:** fresh direct Neon query this run confirms **0 `TRADE_REALIZED` rows / $0.0000 realized net**.
- Caleb Durbin remains a real settled one-leg sports loss at **gross -$0.42 / net -$0.44**; never shadow it away.
- Weather since **2026-03-01:** fresh direct Neon query this run confirms **0 weather-tagged `TRADE_REALIZED` rows / $0.0000 verified realized weather P&L**. Seasonal/backtest edge remains excluded.
- Same-Gemini crypto prior snapshot remains roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Fresh production verification

### Kalshi<->Polymarket sports — `sports-kp-live-executor`

Railway deployment **`2108e5b0-84a5-48e0-9bd7-05a4e3e86f12`** remains **SUCCESS** and fresh one-hour metrics show sustained CPU plus nonzero network traffic, so the corrected K<->P process is alive rather than instant-exited.

The active command remains:

`python -m src.polymarket_kalshi_shadow_v5 --max-quote-age-ms 100`

The bounded read-only Kalshi REST fallback remains enabled:

- `KALSHI_REST_BOOKS_FALLBACK=1`
- `KALSHI_REST_BOOKS_INTERVAL_MS=100`
- 42 matched Kalshi tickers in one <=100-ticker batch at startup
- transport label `REST_SNAPSHOT_NOT_WS_LIFETIME`
- `read_only=true`
- `order_entry=false`

Fresh deployment logs show the REST fallback starting and **no recurrence** of the prior async quote/coroutine traceback. However, the verified log window still contains **no visible `EVENT_READY` and no `EVENT_SUMMARY`**, and Railway's deeper read-only runtime agent hit its usage quota. Therefore exact initialized Kalshi book count is still not independently proven and the **72-hour K<->P verdict clock remains INVALID**.

Last fully verified healthy counterpart state before the fallback transition remains:

- 45 Polymarket MLB total candidates
- 172 Kalshi candidates
- 42 exact cross-venue matches
- 42/42 Polymarket books seeded successfully through `gateway.polymarket.us`
- Polymarket websocket connected
- the separate K<->P Kalshi websocket could not initialize because repeated HTTP 429 handshakes

Authenticated Kalshi `/account/limits` remains advanced tier, **900 capacity / 300 refill** for both reads and writes. The evidence still favors concurrent websocket handshake/account contention rather than low account tier or general API exhaustion. Do not disrupt the healthy G<->K websocket solely to improve K<->P telemetry.

### New decision-telemetry blocker and repair — draft PR #145

Source inspection found that even after K<->P reaches two-sided readiness, deployed v5 does **not yet emit the pre-committed decision summary needed for the verdict**. It logs event-level positive-edge signal lifecycle data, but not explicit cumulative >=1c/>=2c/>=3c bins plus a clearly labeled >=80ms realistic-capture summary. That is a measurement blocker, not a request to build another strategy.

Draft **PR #145** (`feature/kp-census-decision-telemetry`) is now CI-green in **both GitHub Actions test runs**. It is intentionally isolated from live transport and adds only a network/order-entry-free telemetry helper with tests. It records:

- explicit cumulative **>=1c / >=2c / >=3c after-fee bins**;
- semantic-key deduped signal open/close lifecycles and lifetimes;
- q1 executable depth;
- quote skew;
- processing latency;
- fixed **>=80ms realistic-capture** bins that additionally require q1 depth >=1;
- source-quality labels so REST snapshots remain `REST_SNAPSHOT_NOT_WS_LIFETIME` and are never misrepresented as websocket lifetimes.

PR #145 remains **draft/unmerged**. No live service or order authority was changed. Next step is a reviewed integration into v5 only after the current readiness path is proven, then persist decision-grade summaries without altering economics/rule gates.

K<->P still needs event-driven coverage for full-game MLB **moneylines, spreads and totals**, plus strict player props only where lifecycle/participation/result-source equivalence can be proven. Semantic mirrors must be deduped. Participation-conditional or rule-mismatched rows never count as guaranteed arb.

### Gemini<->Kalshi sports — `sports-gk-autonomous-supervisor`

Production remains the read-only event-driven hot-shadow collector. Last verified feed state had **272 Kalshi tickers / 272 initialized** while K<->P's separate websocket was rejected. Positive evaluations remain census/shadow observations only; the explicit execution blocker is `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`.

G<->K remains disarmed. Caleb's **-$0.44** remains authoritative realized accounting.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED**. Its bounded-campaign deadline/`HALTED_FAIL_CLOSED` rollover bug is proven, but settlement-index non-equivalence remains the stronger safety blocker. Do not restart it as guaranteed arbitrage and do not spend census time reopening it.

## Weather side question

Fresh authoritative central-ledger evidence this run remains **0 weather-tagged realized trades / $0 verified realized P&L since March 1, 2026**. Historical, seasonal, or backtest evidence may not be substituted for realized weather P&L without authoritative settlement records.

## Highest-value next actions

1. **Do not start the K<->P 72-hour clock yet.** Obtain direct runtime proof that deployment `2108e5b0-...` reaches `EVENT_READY`, initializes the Kalshi REST snapshots, and remains free of the prior coroutine crash.
2. Integrate the now-CI-green PR #145 decision telemetry into v5 only through a reviewed read-only change, preserving REST source-quality labeling and all existing rule/economics gates.
3. Persist explicit >=1c/>=2c/>=3c after-fee bins, q1 depth, quote skew, lifetimes and >=80ms realistic-capture capacity once both books are healthy.
4. Before Wednesday's instrumentation deadline, broaden K<->P to semantic-deduped MLB moneylines/spreads/totals plus strict rule-certified props. Participation-conditional/rule-mismatched rows remain excluded.
5. Continue G<->K read-only census; do not re-arm it merely because hot shadow signals exist.
6. Preserve the fixed verdict thresholds/deadline. If K<->P remains unmeasurable after the single allowed +24h feed-quality extension, count it as **$0 deployable capacity** rather than extending research.
7. Keep authoritative accounting direct: Aug 25 ET remains **0 realized trades / $0.00**, and weather since March remains **$0 verified realized**.
