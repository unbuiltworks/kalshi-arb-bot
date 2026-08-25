# ARB Control Plane

Last verified: **2026-08-25 17:40 UTC / 2026-08-25 13:40 ET**

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

Railway deployment **`2108e5b0-84a5-48e0-9bd7-05a4e3e86f12`** remains **SUCCESS** and fresh two-hour service metrics show sustained CPU/memory activity plus nonzero network traffic, so the corrected K<->P process is alive rather than instant-exited.

The bounded read-only Kalshi REST fallback remains enabled at a **100 ms minimum sampling floor**, batching the 42 known matched Kalshi tickers in one request and preserving the source label `REST_SNAPSHOT_NOT_WS_LIFETIME`. The prior async quote/coroutine crash has not recurred in the verified window.

However, the verified runtime still does **not** provide independent `EVENT_READY` / decision-summary proof that both venues are continuously initialized. Therefore the **72-hour K<->P verdict clock remains INVALID**. Missing readiness is not scored as zero edge yet; if it remains unmeasurable after the one allowed +24h data-quality extension, the lane counts as $0 capacity.

Last fully verified counterpart state remains 45 Polymarket MLB total candidates, 172 Kalshi candidates, 42 exact cross-venue matches, and 42/42 Polymarket books seeded. The separate K<->P Kalshi websocket suffered repeated HTTP 429 handshakes; the REST fallback avoids opening another websocket and does not disturb the healthy G<->K feed.

Authenticated Kalshi `/account/limits` remains **advanced tier, 900 capacity / 300 refill for both reads and writes**. Evidence favors concurrent websocket handshake/account contention rather than low account tier or general API exhaustion. Do not weaken backoff or stop unrelated live services solely for census telemetry.

### Decision-grade K<->P telemetry — PR #145 merged

PR **#145** is now **merged** at merge commit **`99027f6e87add109f26fa29d075d1418b1f539aa`** after both GitHub Actions test runs passed. This is a measurement milestone, not a trading change.

The merged `kp_census_decision_stats` helper is network- and order-entry-free and provides the exact pre-committed decision fields:

- cumulative **>=1c / >=2c / >=3c after-fee bins**;
- semantic-key deduped signal lifecycles and lifetimes;
- q1 executable depth;
- quote skew and process latency;
- fixed **>=80ms realistic-capture bins** requiring q1 depth >=1;
- source-quality preservation so REST snapshots remain `REST_SNAPSHOT_NOT_WS_LIFETIME` and are not misrepresented as websocket lifetimes.

**Important:** the helper is merged but is **not yet wired into the production v5 collector**. The highest-value instrumentation task is to integrate it read-only into every fully rule-certified two-sided evaluation and emit periodic verdict summaries. Do not start a 72-hour clock that cannot answer the fixed capacity question.

K<->P production scope is also still incomplete for the full verdict: current v5 is strict full-game MLB totals. Before the instrumentation deadline, add semantic-deduped MLB **moneylines, spreads and totals**, plus strict player props only where lifecycle/participation/result-source equivalence is proven. Participation-conditional or rule-mismatched rows never count as guaranteed arb.

### Gemini<->Kalshi sports — `sports-gk-autonomous-supervisor`

Production remains the read-only event-driven hot-shadow collector and execution remains disarmed. Last fully verified feed state had **272 Kalshi tickers / 272 initialized** while the separate K<->P websocket was rejected. Positive hot-shadow evaluations remain census observations only; the explicit blocker is `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`.

Caleb's **-$0.44** remains authoritative realized accounting.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED**. Its bounded-campaign deadline/`HALTED_FAIL_CLOSED` rollover bug is proven, but settlement-index non-equivalence remains the stronger safety blocker. Do not restart it as guaranteed arbitrage and do not spend census time reopening it.

## Weather side question

Fresh authoritative central-ledger evidence this run remains **0 weather-tagged realized trades / $0 verified realized P&L since March 1, 2026**. Historical, seasonal, or backtest evidence may not be substituted for realized weather P&L without authoritative settlement records.

## Highest-value next actions

1. **Do not start the K<->P 72-hour clock yet.** Obtain direct runtime proof that both Polymarket and Kalshi REST-backed books are continuously ready and measurable.
2. Wire the now-merged PR #145 telemetry helper into v5 through a reviewed read-only change; emit explicit >=1c/>=2c/>=3c, q1 depth, skew, lifetime/source-quality and >=80ms realistic-capture summaries.
3. Before Wednesday's instrumentation deadline, broaden K<->P to semantic-deduped MLB moneylines/spreads/totals plus strict rule-certified props.
4. Continue G<->K read-only census; do not re-arm it merely because hot-shadow signals exist.
5. Preserve the fixed verdict thresholds/deadline. If K<->P remains unmeasurable after the single allowed +24h feed-quality extension, count it as **$0 deployable capacity** rather than extending research.
6. Keep authoritative accounting direct: Aug 25 ET remains **0 realized trades / $0.00**, weather since March remains **$0 verified realized**, and Caleb remains **-$0.44 realized**.
