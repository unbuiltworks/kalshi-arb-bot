# ARB Control Plane

Last verified: **2026-08-25 10:52 UTC / 2026-08-25 06:52 ET**

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

- **2026-08-25 ET:** central Neon ledger re-queried this run: **0 `TRADE_REALIZED` rows / $0.00 realized net**.
- Caleb Durbin remains a real settled one-leg sports loss at **gross -$0.42 / net -$0.44**; never shadow it away.
- Weather since **2026-03-01:** central Neon ledger re-queried this run: **0 weather-tagged `TRADE_REALIZED` rows / $0 verified realized weather P&L**. Seasonal/backtest edge is not realized performance.
- Same-Gemini crypto prior snapshot remains roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Fresh production verification

### Kalshi<->Polymarket sports — `sports-kp-live-executor`

Railway production remains **SUCCESS** and one-hour CPU/memory/network metrics confirm the read-only process is alive. Current production config runs `src.polymarket_kalshi_shadow_v5`, `restartPolicy=ALWAYS`, with a 3600-second bounded readiness window and 100ms quote-age target. Direct detailed Railway log inspection is temporarily blocked by the runtime-agent usage quota, so no new EVENT_READY claim is made this run.

The semantic counterpart supply remains established from the last true v5 run: **45 Polymarket MLB total candidates, 154 Kalshi candidates, 42 exact cross-venue matches**. Polymarket previously seeded only roughly **6/42** usable books because many startup REST book reads returned HTTP 401; Kalshi dedicated `external-api-ws` handshakes were returning HTTP 429 under the existing 15s/30s backoff. Therefore the K<->P 72-hour verdict clock remains **INVALID**.

#### New root cause found this run: Polymarket startup seed endpoint

Code inspection found a concrete mismatch:

- production v5 `_fetch_poly_book()` calls `https://api.polymarket.us/v1/markets/<slug>/book` with no REST authentication;
- the older working read-only collector in the same repo uses the public `https://gateway.polymarket.us/v1/markets/<slug>/book` endpoint with `Origin: https://polymarket.us` plus a browser-style User-Agent.

This is the leading code-level explanation for the recent widespread 401 seed failures and low 6/42 usable-book coverage.

Draft **PR #140** (`feature/kp-poly-gateway-seed`) now isolates the repair without adding any order authority. It:

1. adds a read-only v5 entrypoint that overrides **only** startup Polymarket REST seeding to use `gateway.polymarket.us` with the established Origin/User-Agent headers;
2. leaves websocket matching, fee/economics math, depth/skew/lifetime telemetry and Kalshi backoff unchanged;
3. adds a network-free regression test locking the public gateway URL and Origin header;
4. updates the feature-branch Railway structural entrypoint/predeploy gate to the read-only wrapper.

PR #140 is intentionally **draft/unmerged** until CI appears and restored usable-book coverage is proven. Success is not candidate count; success is decision-grade Polymarket coverage with zero order authority.

### Kalshi account-limit / 429 diagnosis

Authenticated `/account/limits` evidence remains decisive:

- usage tier: **advanced**
- read bucket capacity/refill: **900 / 300**
- write bucket capacity/refill: **900 / 300**
- REST host: `external-api.kalshi.com`

This rules out a weak account tier as the simple cause. K<->P REST discovery works while its dedicated `external-api-ws` handshake is 429ing, and the separate G<->K collector has maintained hundreds of initialized Kalshi tickers. The remaining diagnosis is **websocket handshake/concurrent-connection contention or endpoint/account connection policy**, not generic REST exhaustion. Do not lower backoff or disrupt unrelated live services solely to improve census telemetry.

### Gemini<->Kalshi sports — `sports-gk-autonomous-supervisor`

Latest deployment remains **SUCCESS** and the event-driven read-only hot shadow is active. G<->K live mutation remains disarmed. Prior hot-shadow events are not deployable-capacity evidence until lifecycle/rule equivalence is certified; Caleb's -$0.44 remains authoritative realized accounting.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains the known **CRASHED** bounded-campaign case. Its benign deadline rollover bug is real, but settlement-index non-equivalence remains the stronger safety blocker. Do not restart it as guaranteed arbitrage and do not spend census time reopening it.

## Weather side question

The central authoritative ledger again shows **0 weather-tagged realized trades / $0 verified realized P&L since March 1**. The separate `kalshi-weather-bot` project exists, but no separate authoritative March-to-date settlement/P&L record has yet been exposed. Its current live probe has been fail-closed on a finalized target market with `order_entry=false`. Historical/backtest/seasonal edge remains categorically separate.

## Highest-value next actions

1. Get **PR #140** through CI, then verify whether public-gateway seeding restores Polymarket usable coverage toward the 42 strict total matches without adding order authority.
2. Keep the long-lived dedicated Kalshi WS process under its existing backoff and correlate handshake availability with **natural** account-wide websocket turnover; do not kill unrelated live consumers for telemetry.
3. Once both venue books are ready, persist decision-grade >=1c/>=2c/>=3c after-fee bins, q1 depth, quote skew, OPEN/CLOSE lifetimes, and >=80ms realistic-capture capacity.
4. Before Wednesday, broaden the event-driven K<->P census beyond full-game totals to semantic-deduped MLB moneylines/spreads and strict rule-certified props. Participation-conditional/rule-mismatched rows stay excluded.
5. Continue G<->K read-only census while disarmed; only lifecycle-rule-certified rows can contribute deployable-capacity estimates.
6. Continue weather record search only for authoritative settled P&L; until found, weather contributes **$0** to the census decision.
7. Preserve the fixed verdict thresholds/deadline. If K<->P remains unmeasurable after the single allowed +24h feed-quality extension, count it as **$0 deployable capacity** rather than extending research.
