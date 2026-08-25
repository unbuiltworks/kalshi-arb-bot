# ARB Control Plane

Last verified: **2026-08-25 11:44 UTC / 2026-08-25 07:44 ET**

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

- **2026-08-25 ET:** last successful authoritative central-ledger query this morning showed **0 `TRADE_REALIZED` rows / $0.00 realized net**. A fresh re-query at 07:44 ET was attempted but Railway's read-only agent quota blocked database inspection before any SQL ran, so no newer realized claim is made.
- Caleb Durbin remains a real settled one-leg sports loss at **gross -$0.42 / net -$0.44**; never shadow it away.
- Weather since **2026-03-01:** last successful central-ledger query showed **0 weather-tagged `TRADE_REALIZED` rows / $0 verified realized weather P&L**. The fresh re-query hit the same Railway agent quota; seasonal/backtest edge remains excluded.
- Same-Gemini crypto prior snapshot remains roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Fresh production verification

### Kalshi<->Polymarket sports — `sports-kp-live-executor`

This run found and fixed a new census-invalidating runtime failure.

Immediately before the fix, Railway showed the service **CRASHED** despite `restartPolicy=ALWAYS`. Fresh runtime logs proved the process was not waiting on Kalshi websocket 429 backoff at that moment; instead it was restarting roughly once per second because discovery returned:

`STRICT_DISCOVERY {"poly_candidates":0,"kalshi_candidates":154,"matched":0,...}`

followed by:

`RuntimeError: No strict Polymarket/Kalshi matches discovered`

This tight restart loop repeatedly re-hit both venue discovery APIs and could itself worsen account/feed contention. An empty morning Polymarket MLB-total slate therefore cannot be treated as an edge result, and the 72-hour K<->P clock remains **INVALID**.

#### PR #140 merged: public seed + safe no-inventory retry

PR **#140** is now merged into `feature/sports-kp-live-executor-impl` at commit **0d48e6f37be4e4a46032b4ed8f1bf4f15f8e08e1**. It remains strictly read-only and adds no order transport.

It now does two things:

1. startup Polymarket REST book seeding uses the established public `gateway.polymarket.us` endpoint with the expected Origin/User-Agent headers instead of unauthenticated `api.polymarket.us` book reads;
2. only the two known temporary inventory-gap states (`No strict ... matches` and `No strict pairs have a current two-sided Polymarket book`) retry **in-process every 60 seconds** rather than crashing Railway. All other runtime failures still propagate fail-closed.

Repository CI is green after correcting one stale MLB pitcher-rule test. The production policy intentionally requires an official **batter-faced** record, not merely one pitch, before strikeout props become settlement-equivalent after participation. The stricter production rule was preserved; only the stale test was updated.

Railway auto-deployed the merge as deployment `b7bb8eb6-53a8-4bc1-b74e-e25a135d0d58`, which is **SUCCESS**. Its scoped predeploy tests passed **2/2**. Fresh runtime confirms the new behavior:

`CENSUS_NO_INVENTORY_RETRY wait_s=60 reason=No strict Polymarket/Kalshi matches discovered`

The process now remains alive instead of entering the prior restart storm. This is infrastructure/data-quality repair only; zero current matches do **not** count toward edge-capacity verdicts.

The previously established counterpart evidence still matters when Polymarket has the relevant slate loaded: earlier true-v5 discovery found **45 Polymarket MLB total candidates, 154 Kalshi candidates, and 42 exact cross-venue matches**. Once matches return, verify public-gateway seeding restores usable two-sided books and then observe whether the long-lived Kalshi dedicated websocket escapes its 429 backoff.

### Kalshi account-limit / 429 diagnosis

Authenticated `/account/limits` evidence remains decisive:

- usage tier: **advanced**
- read bucket capacity/refill: **900 / 300**
- write bucket capacity/refill: **900 / 300**
- REST host: `external-api.kalshi.com`

This rules out a weak account tier as the simple cause. Do not lower backoff or disrupt unrelated live services solely to improve census telemetry. The prior K<->P dedicated `external-api-ws` 429 evidence still points toward websocket handshake/concurrent-connection contention or endpoint/account connection policy; today's empty-slate restart storm was an additional self-inflicted source of churn and is now removed.

### Gemini<->Kalshi sports — `sports-gk-autonomous-supervisor`

Fresh Railway config confirms this service is the event-driven **read-only hot shadow**, not the live mutation executor:

`python -m src.gemini_kalshi_mlb_prop_hot_shadow --max-book-age-ms 1000 --max-quote-skew-ms 50 --hot-book-age-ms 100 --hot-profit 0.01 ...`

`restartPolicy=ALWAYS`; latest deployment remains **SUCCESS**. G<->K stays disarmed for census purposes. Caleb's -$0.44 one-leg loss remains authoritative realized accounting and must not be shadowed away.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED**. Its bounded-campaign deadline/`HALTED_FAIL_CLOSED` rollover bug is real, but settlement-index non-equivalence remains the stronger safety blocker. Do not restart it as guaranteed arbitrage and do not spend census time reopening it.

## Weather side question

The last successful central-ledger query shows **0 weather-tagged realized trades / $0 verified realized P&L since March 1**. The separate `kalshi-weather-bot` project exists, but no separate authoritative March-to-date settlement/P&L record has yet been exposed. Historical/backtest/seasonal edge remains categorically separate.

## Highest-value next actions

1. Keep the newly stabilized K<->P process alive and verify the next non-empty Polymarket MLB-total slate reaches strict matches without another restart storm.
2. When matches return, prove the public gateway restores decision-grade Polymarket book coverage, then measure whether the long-lived dedicated Kalshi WS connection can initialize without disrupting unrelated Kalshi consumers.
3. Persist >=1c/>=2c/>=3c after-fee bins, q1 depth, quote skew, OPEN/CLOSE lifetimes, and >=80ms realistic-capture capacity only after both venue books are healthy.
4. Before Wednesday, broaden event-driven K<->P beyond full-game totals to semantic-deduped MLB moneylines/spreads and strict rule-certified props. Participation-conditional/rule-mismatched rows remain excluded.
5. Continue G<->K read-only census while disarmed; only lifecycle/rule-certified rows can contribute deployable-capacity estimates.
6. Retry authoritative central-ledger/weather queries when Railway's read-only agent quota recovers; until then do not manufacture new P&L claims.
7. Preserve the fixed verdict thresholds/deadline. If K<->P remains unmeasurable after the single allowed +24h feed-quality extension, count it as **$0 deployable capacity** rather than extending research.
