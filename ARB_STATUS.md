# ARB Control Plane

Last verified: **2026-08-25 12:53 UTC / 2026-08-25 08:53 ET**

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

- **2026-08-25 ET:** fresh direct Neon query at 08:53 ET confirms **0 `TRADE_REALIZED` rows / $0.0000 realized net**.
- Caleb Durbin remains a real settled one-leg sports loss at **gross -$0.42 / net -$0.44**; never shadow it away.
- Weather since **2026-03-01:** fresh direct Neon query confirms **0 weather-tagged `TRADE_REALIZED` rows / $0.0000 verified realized weather P&L**. Seasonal/backtest edge remains excluded.
- Same-Gemini crypto prior snapshot remains roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Fresh production verification

### Kalshi<->Polymarket sports — `sports-kp-live-executor`

The true event-driven K<->P collector remains **alive** on Railway deployment `b7bb8eb6-53a8-4bc1-b74e-e25a135d0d58` with `SUCCESS` status. The prior empty-slate crash storm fixed by PR #140 has not returned; the process stays alive under its in-process retry/backoff behavior.

However, the census is still **not decision-grade and the 72-hour K<->P verdict clock remains INVALID** because the Kalshi order-book websocket has not initialized. Direct Railway logs show the dedicated Kalshi websocket handshake returning **HTTP 429 continuously from at least 11:50 UTC through 12:52 UTC**. The first retry uses 15s and then the collector persists at a 30s backoff while remaining alive. No `EVENT_READY` state can be credited while Kalshi books remain unavailable, and therefore no >=1c/>=2c/>=3c edge, lifetime, depth, skew, or >=80ms capture observation from this interval counts toward deployable capacity.

The previously established semantic-counterpart evidence remains valid for inventory breadth: genuine v5 discovery previously found **45 Polymarket MLB total candidates, 154 Kalshi candidates, and 42 exact cross-venue total matches** when the slate was populated. PR #140 remains the merged read-only repair for public Polymarket book seeding plus bounded no-inventory retry. It adds no order transport.

K<->P still needs event-driven coverage for full-game MLB **moneylines, spreads and totals**, plus strict player props only where lifecycle/participation/result-source equivalence can be proven. Semantic mirrors must be deduped. Participation-conditional or rule-mismatched rows never count as guaranteed arb.

### Kalshi account-limit / 429 diagnosis — stronger evidence

Authenticated `/account/limits` remains:

- usage tier: **advanced**
- read bucket capacity/refill: **900 / 300**
- write bucket capacity/refill: **900 / 300**
- REST host: `external-api.kalshi.com`

Fresh concurrent-consumer evidence now narrows the 429 diagnosis materially. The read-only G<->K hot-shadow process has maintained **272/272 Kalshi tickers initialized** continuously while the separate K<->P Kalshi websocket handshake is rejected every ~30 seconds with HTTP 429. G<->K is processing books normally rather than showing a general Kalshi data outage.

This strongly favors **concurrent websocket connection/handshake policy or account-level connection contention** over a weak account tier or generic REST bucket exhaustion. Do not lower the existing K<->P backoff and do not stop unrelated live/read-only Kalshi services solely to improve census telemetry. The next engineering option should favor **sharing/multiplexing an already-established Kalshi market-data connection** or otherwise reducing redundant account handshakes, provided this can be done read-only and without disturbing existing live services.

### Gemini<->Kalshi sports — `sports-gk-autonomous-supervisor`

Fresh Railway heartbeat at 12:48 UTC proves this service is genuinely active and read-only:

- uptime roughly **34,070s**
- **272 Gemini symbols / 272 connected**
- **272 Kalshi tickers / 272 initialized**
- 4,400 Gemini book updates
- 27,002 comparable evaluations
- 51 positive evaluations
- 51 `hot_3c_evaluations`
- 33 opens / 33 closes / 0 active positive keys
- trigger->evaluation avg **0.039ms**, p95 **0.079ms**
- `read_only=true`, `order_entry=false`
- execution rule gate remains `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`

The 51 hot >=3c observations are **not deployable-capacity evidence yet** because the full lifecycle/rule fingerprint is still an explicit blocker; they remain shadow/census observations only. G<->K stays disarmed. Caleb's -$0.44 remains authoritative realized accounting.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED**. Its bounded-campaign deadline/`HALTED_FAIL_CLOSED` rollover bug is real, but settlement-index non-equivalence remains the stronger safety blocker. Do not restart it as guaranteed arbitrage and do not spend census time reopening it.

## Weather side question

Fresh authoritative central-ledger query confirms **0 weather-tagged realized trades / $0 verified realized P&L since March 1, 2026**. The separate weather project may have historical/seasonal or backtest evidence, but none of that may be substituted for realized P&L without an authoritative settlement record.

## Highest-value next actions

1. **Do not start the K<->P 72-hour clock yet.** A lane with zero Kalshi books is unmeasurable, not a zero-edge result.
2. Diagnose/implement a **read-only shared or multiplexed Kalshi market-data path** so K<->P can consume the existing healthy account connection without creating another repeatedly rejected websocket handshake. Do not disturb unrelated live services to do this.
3. Once both K<->P books are healthy, persist >=1c/>=2c/>=3c after-fee bins, q1 depth, quote skew, OPEN/CLOSE lifetimes, and >=80ms realistic-capture capacity.
4. Before Wednesday, broaden event-driven K<->P to semantic-deduped MLB moneylines/spreads/totals plus strict rule-certified props. Participation-conditional/rule-mismatched rows remain excluded.
5. Continue G<->K read-only census. Its current >=3c hot observations remain **non-deployable** until full lifecycle/rule fingerprint certification is satisfied.
6. Preserve the fixed verdict thresholds/deadline. If K<->P remains unmeasurable after the single allowed +24h feed-quality extension, count it as **$0 deployable capacity** rather than extending research.
7. Continue querying authoritative ledger/weather directly; as of this checkpoint Aug 25 ET remains **0 realized trades / $0.00**, and weather since March remains **$0 verified realized**.
