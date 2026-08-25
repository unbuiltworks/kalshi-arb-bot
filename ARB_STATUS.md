# ARB Control Plane

Last verified: **2026-08-25 18:38 UTC / 2026-08-25 14:38 ET**

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

- **2026-08-25 ET:** fresh direct Neon query at 18:38 UTC confirms **0 `TRADE_REALIZED` rows / $0.0000 realized net**.
- Caleb Durbin remains a real settled one-leg sports loss at **gross -$0.42 / net -$0.44**; never shadow it away.
- Weather since **2026-03-01:** last direct Neon verification remains **0 weather-tagged `TRADE_REALIZED` rows / $0.0000 verified realized weather P&L**. Seasonal/backtest edge remains excluded.
- Same-Gemini crypto prior snapshot remains roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Fresh production verification

### Kalshi<->Polymarket sports — `sports-kp-live-executor`

Railway deployment **`2108e5b0-84a5-48e0-9bd7-05a4e3e86f12`** is still **SUCCESS** as of the 18:38 UTC production status refresh. The bounded read-only Kalshi REST fallback remains the intended transport at a **100 ms minimum sampling floor** and preserves the source label `REST_SNAPSHOT_NOT_WS_LIFETIME`.

The prior async quote/coroutine crash is fixed. However, independent `EVENT_READY` / verdict-summary proof still has not been obtained in this run, so the **72-hour K<->P verdict clock remains INVALID**. Missing readiness is not scored as zero edge yet; if it remains unmeasurable after the one allowed +24h data-quality extension, the lane counts as $0 capacity.

Last fully verified counterpart state remains 45 Polymarket MLB total candidates, 172 Kalshi candidates, 42 exact cross-venue matches, and 42/42 Polymarket books seeded. Authenticated Kalshi `/account/limits` remains **advanced tier, 900 capacity / 300 refill for both reads and writes**. Evidence favors concurrent websocket handshake/account contention rather than low account tier or general API exhaustion. Do not weaken backoff or stop unrelated live services solely for census telemetry.

### Decision-grade K<->P telemetry — integration branch prepared

PR **#145** remains merged at **`99027f6e87add109f26fa29d075d1418b1f539aa`**. Its `kp_census_decision_stats` helper is read-only/network-free and implements the fixed >=1c/>=2c/>=3c bins, semantic-key lifetimes, q1 depth, quote skew, process latency, >=80ms realistic capture, and source-quality labels.

This run created isolated branch **`feature/kp-census-decision-telemetry-live`** from the active K/P production source branch and committed **`472145f61d83e85fdf88ccde88ad551416c9c554`**. The change wires the merged helper into the existing v5 gateway as a CPU-only post-evaluation measurement pass and extends the 10-second `EVENT_SUMMARY` with `decision_census`, `kalshi_transport`, `read_only=true`, and `order_entry=false`. The authoritative venue transport/matching/economics path remains unchanged and no order-entry capability is added.

Important implementation properties:

- each strict two-sided evaluation is measured after the existing v5 pricing pass;
- after-fee edge is normalized per contract before the >=1c/>=2c/>=3c bins;
- q1 capacity uses the minimum displayed executable depth across the two chosen legs;
- quote skew uses the existing receive timestamps;
- REST Kalshi snapshots remain labeled `REST_SNAPSHOT_NOT_WS_LIFETIME` rather than being represented as websocket lifetime;
- no venue request is added by the telemetry pass.

The GitHub connector safety layer blocked creation of the promotion PR for this integration branch, and the branch commit currently has no GitHub Actions workflow run. **Do not force-move the production branch or deploy this change without a verifiable test/PR path.** This is now the immediate instrumentation blocker.

K<->P production scope is also incomplete for the final verdict: current v5 is strict full-game MLB totals. Before the instrumentation deadline, add semantic-deduped MLB **moneylines, spreads and totals**, plus strict player props only where lifecycle/participation/result-source equivalence is proven. Participation-conditional or rule-mismatched rows never count as guaranteed arb.

### Gemini<->Kalshi sports — `sports-gk-autonomous-supervisor`

Production remains the read-only event-driven hot-shadow collector and execution remains disarmed. Last fully verified feed state had **272 Kalshi tickers / 272 initialized** while the separate K<->P websocket was rejected. Positive hot-shadow evaluations remain census observations only; the explicit blocker is `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`.

Caleb's **-$0.44** remains authoritative realized accounting.

### Old Gemini<->Kalshi 15m crypto

Fresh Railway status still shows `crypto-15m-sol-live-canary-10bp-50ms` **CRASHED** on deployment `03080d7a-1696-4c38-a86d-54fa41882892`. Its bounded-campaign deadline/`HALTED_FAIL_CLOSED` rollover bug is proven, but settlement-index non-equivalence remains the stronger safety blocker. Do not restart it as guaranteed arbitrage and do not spend census time reopening it.

## Weather side question

Authoritative central-ledger evidence remains **0 weather-tagged realized trades / $0 verified realized P&L since March 1, 2026**. Historical, seasonal, or backtest evidence may not be substituted for realized weather P&L without authoritative settlement records.

## Highest-value next actions

1. **Do not start the K<->P 72-hour clock yet.** Obtain direct runtime proof that both Polymarket and Kalshi REST-backed books are continuously ready and measurable.
2. Get commit `472145f…` through a verifiable PR/CI path; do not bypass the blocked PR mutation by force-moving production.
3. Once green, deploy the read-only decision telemetry and verify periodic >=1c/>=2c/>=3c, q1 depth, skew, lifetime/source-quality and >=80ms realistic-capture summaries.
4. Before Wednesday's instrumentation deadline, broaden K<->P to semantic-deduped MLB moneylines/spreads/totals plus strict rule-certified props.
5. Continue G<->K read-only census; do not re-arm it merely because hot-shadow signals exist.
6. Preserve the fixed verdict thresholds/deadline. If K<->P remains unmeasurable after the single allowed +24h feed-quality extension, count it as **$0 deployable capacity** rather than extending research.
7. Keep authoritative accounting direct: Aug 25 ET remains **0 realized trades / $0.00**, weather since March remains **$0 verified realized**, and Caleb remains **-$0.44 realized**.
