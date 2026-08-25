# ARB Control Plane

Last verified: **2026-08-25 13:48 UTC / 2026-08-25 09:48 ET**

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

- **2026-08-25 ET:** fresh direct Neon query at 09:38 ET confirms **0 `TRADE_REALIZED` rows / $0.0000 realized net**.
- Caleb Durbin remains a real settled one-leg sports loss at **gross -$0.42 / net -$0.44**; never shadow it away.
- Weather since **2026-03-01:** fresh direct Neon query confirms **0 weather-tagged `TRADE_REALIZED` rows / $0.0000 verified realized weather P&L**. Seasonal/backtest edge remains excluded.
- Same-Gemini crypto prior snapshot remains roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Fresh production verification

### Kalshi<->Polymarket sports — `sports-kp-live-executor`

Railway deployment `b7bb8eb6-53a8-4bc1-b74e-e25a135d0d58` remains **SUCCESS** and the intended read-only command is active:

`python -m src.polymarket_kalshi_shadow_v5 --max-quote-age-ms 100`

`restartPolicy=ALWAYS` remains in place for this census-only service. PR #140's bounded no-inventory retry continues to prevent the prior restart storm.

Fresh 13:26 UTC discovery is strong on inventory and Polymarket feed quality:

- **45 Polymarket MLB total candidates**
- **172 Kalshi candidates**
- **42 exact cross-venue matches**
- **42/42 Polymarket books seeded successfully** through `gateway.polymarket.us`
- Polymarket websocket subscribed to all 42 and connected

The current blocker is specifically the separate Kalshi websocket. It remains **0/42 initialized** and continues to receive **HTTP 429** on a long-lived process at the existing bounded cadence: initial 15s, then approximately **30s per retry**. Fresh logs show repeated retries through at least attempts 13-16 in this run. There is still no valid `EVENT_READY`, so the K<->P 72-hour verdict clock remains **INVALID** and no >=1c/>=2c/>=3c/lifetime/depth/skew/capture zeros from this interval may be interpreted as edge evidence.

This materially rules out semantic-counterpart scarcity and the earlier Polymarket seed failure as the current blocker. Both counterpart inventory and the Polymarket side are healthy when the slate is populated.

K<->P still needs event-driven coverage for full-game MLB **moneylines, spreads and totals**, plus strict player props only where lifecycle/participation/result-source equivalence can be proven. Semantic mirrors must be deduped. Participation-conditional or rule-mismatched rows never count as guaranteed arb.

### Kalshi account-limit / 429 diagnosis — concurrent-WS evidence is now decisive

Authenticated `/account/limits` remains:

- usage tier: **advanced**
- read bucket capacity/refill: **900 / 300**
- write bucket capacity/refill: **900 / 300**
- REST host: `external-api.kalshi.com`

At the exact same time the K<->P service is rejected on every fresh Kalshi websocket handshake, the independent read-only G<->K hot-shadow keeps its already-established Kalshi stream healthy with **272/272 tickers initialized** and continues receiving/evaluating book updates.

Fresh G<->K heartbeats during this run show roughly **28,900 comparable evaluations**, **54 hot evaluations**, and **36 completed hot signal lifecycles**, with recent modeled hot locks around **4-5c**. Those observations remain non-deployable because the full Gemini/Kalshi prop lifecycle/rule fingerprint is explicitly incomplete and `order_entry=false`.

The combined evidence strongly favors **concurrent websocket connection/handshake policy or account-level WS connection contention**, not weak account tier, general Kalshi REST exhaustion, or a market-data outage. Do not lower the K<->P backoff and do not stop unrelated live/read-only Kalshi services solely to improve census telemetry.

A read-only fallback has now been prepared as **draft PR #142** on isolated branch `feature/kp-kalshi-rest-census-fallback`. It keeps websocket as the default but can explicitly switch the K<->P book store to authenticated **batched Kalshi REST snapshots**, up to 100 tickers per request, with a hard **>=100ms interval floor**, bounded retry/backoff, and no order transport. At the current 42 matched tickers this is one batched read per sampling interval, conservatively below the verified 300 read-refill account limit. The intent is to measure a conservative >=100ms realistic-capture lower bound without disturbing the healthy G<->K websocket. **Do not merge/deploy until CI is green and the read-only isolation is verified.**

A true shared/multiplexed websocket remains architecturally preferable if it can be implemented without touching the healthy consumer, but the bounded REST fallback is the shortest safe path to prevent the instrumentation deadline from expiring solely because Kalshi refuses a second websocket handshake.

### Gemini<->Kalshi sports — `sports-gk-autonomous-supervisor`

Railway deployment `030a6f3d-f40c-48ba-8337-d53285cff801` remains **SUCCESS** and is genuinely active. Its production command is the read-only event-driven `src.gemini_kalshi_mlb_prop_hot_shadow` collector. It has **272 Kalshi tickers / 272 initialized** while K<->P's separate connection is being rejected.

Recent positive evaluations are census/shadow observations only. The explicit execution blocker remains `GEMINI_FULL_PROP_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION`; G<->K remains disarmed and Caleb's -$0.44 remains authoritative realized accounting.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED**. Its bounded-campaign deadline/`HALTED_FAIL_CLOSED` rollover bug is real, but settlement-index non-equivalence remains the stronger safety blocker. Do not restart it as guaranteed arbitrage and do not spend census time reopening it.

## Weather side question

Fresh authoritative central-ledger query confirms **0 weather-tagged realized trades / $0 verified realized P&L since March 1, 2026**. The separate weather project may have historical/seasonal or backtest evidence, but none of that may be substituted for realized P&L without an authoritative settlement record.

## Highest-value next actions

1. **Do not start the K<->P 72-hour clock yet.** The lane has 42 exact counterparts and healthy Polymarket books but zero usable Kalshi websocket books; that is unmeasurable, not zero-edge.
2. Get **draft PR #142** through CI and verify its bounded batched-REST path is read-only, account-budget-safe, and produces conservative ~100ms Kalshi snapshots without disturbing G<->K. Do not deploy it blind.
3. Once both K<->P books are healthy, persist >=1c/>=2c/>=3c after-fee bins, q1 depth, quote skew, OPEN/CLOSE lifetimes, and >=80ms realistic-capture capacity; REST-sampled observations must be labeled with their sampling resolution rather than presented as websocket lifetimes.
4. Before Wednesday, broaden event-driven K<->P to semantic-deduped MLB moneylines/spreads/totals plus strict rule-certified props. Participation-conditional/rule-mismatched rows remain excluded.
5. Continue G<->K read-only census. Its current hot observations remain **non-deployable** until full lifecycle/rule fingerprint certification is satisfied.
6. Preserve the fixed verdict thresholds/deadline. If K<->P remains unmeasurable after the single allowed +24h feed-quality extension, count it as **$0 deployable capacity** rather than extending research.
7. Continue querying authoritative ledger/weather directly; as of this checkpoint Aug 25 ET remains **0 realized trades / $0.00**, and weather since March remains **$0 verified realized**.
