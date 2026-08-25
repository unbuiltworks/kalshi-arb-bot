# ARB Control Plane

Last verified: **2026-08-25 19:35 UTC / 2026-08-25 15:35 ET**

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

- **2026-08-25 ET:** last direct Neon verification remains **0 `TRADE_REALIZED` rows / $0.0000 realized net**.
- Caleb Durbin remains a real settled one-leg sports loss at **gross -$0.42 / net -$0.44**; never shadow it away.
- Weather since **2026-03-01:** last direct Neon verification remains **0 weather-tagged `TRADE_REALIZED` rows / $0.0000 verified realized weather P&L**. Seasonal/backtest edge remains excluded.
- Same-Gemini crypto prior snapshot remains roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Fresh production verification

### Kalshi<->Polymarket sports — `sports-kp-live-executor`

A material production wiring problem was fixed this run. Railway's effective service-level override was still launching raw `src.polymarket_kalshi_shadow_v5` even though `railway.structural.json` specified the gateway. That meant the decision-grade telemetry integration would not have reached the live process even after merge.

PR **#147** (`feature/kp-census-decision-telemetry-live` -> `feature/sports-kp-live-executor-impl`) was created through the normal review path, marked ready, and GitHub Actions **Tests run #4686 passed**. PR #147 then merged as commit **`c3074a292aba4a0d089a507045811f1eba3fe72c`**.

The merged integration is read-only. It wires `kp_census_decision_stats` into every rule-certified two-sided v5 evaluation and emits explicit:

- >=1c / >=2c / >=3c after-fee bins;
- semantic-deduped signal lifetimes;
- q1 executable depth;
- quote skew;
- process latency;
- >=80ms realistic-capture bins requiring q1 depth >=1;
- source-quality labeling so REST snapshots remain `REST_SNAPSHOT_NOT_WS_LIFETIME`.

The second pricing pass is CPU-only against already-loaded books. It adds no venue requests and no order-entry authority.

Railway service configuration was then corrected so the **effective** production start command is now:

`python -m src.polymarket_kalshi_shadow_v5_gateway --max-quote-age-ms 100 --startup-timeout-s 3600`

with the gateway scoped predeploy gate and `restartPolicy=ALWAYS` preserved.

Replacement Railway deployment **`2cb8e91c-5587-4e92-8e51-07dfd13e8ffe`** on merge commit `c3074a2...` is **SUCCESS**. Fresh service config independently confirms the gateway command is now effective, not merely present in the repo config.

The previous hour also showed sustained production activity before the replacement deploy: CPU current ~0.053 / avg ~0.092, memory ~0.0405 GB, and nonzero RX/TX traffic. Railway's deeper runtime-log agent is currently usage-limited, so direct post-deploy `EVENT_READY` / `EVENT_SUMMARY` proof is still missing. Therefore the **72-hour K<->P verdict clock remains INVALID** until two-sided readiness and the new decision summaries are directly observed.

Last fully verified counterpart/feed state remains roughly 45 Polymarket MLB total candidates, 172 Kalshi candidates, 42 exact counterparts, and 42/42 Polymarket books seeded. The Kalshi REST fallback is intended to avoid separate websocket-handshake contention at a hard >=100ms sampling floor. Authenticated Kalshi `/account/limits` remains **advanced tier, 900 capacity / 300 refill for both reads and writes**. Do not weaken backoff or disrupt unrelated Kalshi consumers solely to improve telemetry.

K<->P production breadth is still incomplete for the final verdict: current v5 is strict full-game MLB totals. Before the instrumentation deadline, add semantic-deduped MLB moneylines, spreads and totals, plus strict player props only where lifecycle/participation/result-source equivalence is proven. Participation-conditional or rule-mismatched rows never count as guaranteed arbitrage.

### Gemini<->Kalshi sports — `sports-gk-autonomous-supervisor`

Production remains the read-only event-driven hot-shadow collector and execution remains disarmed. Last fully verified feed state had **272 Kalshi tickers / 272 initialized**. Positive hot-shadow evaluations remain census observations only; the explicit blocker is `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`.

Caleb's **-$0.44** remains authoritative realized accounting.

### Old Gemini<->Kalshi 15m crypto

Fresh Railway project status still shows `crypto-15m-sol-live-canary-10bp-50ms` **CRASHED** on deployment `03080d7a-1696-4c38-a86d-54fa41882892`. Its bounded-campaign deadline/`HALTED_FAIL_CLOSED` rollover bug is proven, but settlement-index non-equivalence remains the stronger safety blocker. Do not restart it as guaranteed arbitrage and do not spend census time reopening it.

## Weather side question

Authoritative central-ledger evidence remains **0 weather-tagged realized trades / $0 verified realized P&L since March 1, 2026**. Historical, seasonal, or backtest evidence may not be substituted for realized weather P&L without authoritative settlement records.

## Highest-value next actions

1. **Do not start the K<->P 72-hour clock yet.** Obtain direct post-deploy runtime proof that both Polymarket and Kalshi REST-backed books are continuously ready and that `EVENT_SUMMARY.decision_census` is being emitted.
2. Once summaries are visible, begin a valid clock and preserve >=1c/>=2c/>=3c, depth, skew, lifetime/source-quality and >=80ms realistic-capture evidence without threshold drift.
3. Before Wednesday's instrumentation deadline, broaden K<->P to semantic-deduped MLB moneylines/spreads/totals plus strict rule-certified props.
4. Continue G<->K read-only census; do not re-arm it merely because hot-shadow signals exist.
5. Preserve the fixed verdict thresholds/deadline. If K<->P remains unmeasurable after the single allowed +24h feed-quality extension, count it as **$0 deployable capacity** rather than extending research.
6. Keep authoritative accounting direct: Aug 25 ET remains **0 realized trades / $0.00** at last direct query, weather since March remains **$0 verified realized**, and Caleb remains **-$0.44 realized**.
