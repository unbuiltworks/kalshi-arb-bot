# ARB Control Plane

Last verified: **2026-08-25 21:12 UTC / 2026-08-25 17:12 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Objective and accounting rule

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

## Edge-census protocol

The 72-hour K<->P wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and continues through imperfect data. Invalid/unmeasurable intervals are scored as **$0 deployable capacity for that wall-clock interval** rather than pausing the clock.

Fixed verdict thresholds remain unchanged:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

Required evidence remains >=1c/>=2c/>=3c after fees, exact executable q1 depth, quote skew, source quality, signal lifetime where valid, overlap-adjusted capacity, and a provisional **>=80ms** realistic-capture floor. The latency floor may rise to measured production p95 but may not be lowered to rescue capacity.

Same-venue Gemini/Polymarket/Kalshi zero results remain near-verdict evidence; do not spend engineering time manufacturing ladder violations.

## Current authoritative scoreboard

- **2026-08-25 ET:** last fresh Neon verification = **0 TRADE_REALIZED / $0.0000 net**.
- Caleb Durbin remains a real settled one-leg sports loss at **net -$0.44** and is not reclassified away.
- Weather since **2026-03-01:** last fresh Neon verification = **0 weather-tagged TRADE_REALIZED / $0.0000 verified realized weather P&L**.
- Same-Gemini crypto prior snapshot remains roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Fresh production verification

### Kalshi<->Polymarket sports — `sports-kp-live-executor`

Production remains healthy/read-only. Deployment **`11e53e98-d318-4455-8341-2cfadc5d275b`** is SUCCESS and reached `EVENT_READY` with **72 strict discovered / 72 matched live books / 72 seeded Polymarket books / 72 initialized Kalshi books**.

The two census telemetry defects are now corrected in production:

1. `ShadowPlan.as_dict()` emits `quantity` rather than `qty`, and selected-side depth must be derived from the already-loaded quotes.
2. Sports-total plans use semantic side labels **OVER/UNDER**, while quote objects expose binary YES/NO depth. The production mapping is now **OVER -> YES depth** and **UNDER -> NO depth** on both venues. Regression coverage was added. Production branch commits: `c1b0aaf4e8d54199fd33d7634ea7e1ae0c3ccded` plus test-only `16ff4bf894fa75a4acedcbd7443f6302628df16f`.

First corrected decision telemetry proves the earlier zero-depth result was mechanical, not market liquidity:

- first ~50s: **10 >=1c observations**, **0 >=2c**, **0 >=3c**;
- **avg q1 selected-side depth ~626.58 contracts**, **max ~811.03**;
- observed positive signals closed at **~170.4ms average / 225ms max**;
- receive skew averaged **~56.4ms**, max **99ms**;
- two observed semantic keys included Boston-Miami 8.5 and Texas-Chicago WS 8.5 at a 1c q1 taker/taker gap.

Kalshi transport for this census remains **`REST_SNAPSHOT_NOT_WS_LIFETIME` at 100ms**. Therefore REST-derived lifetime/capture is not promoted to authoritative sub-100ms executable evidence. The internal realistic-capture counter may register observations after 80ms, but under the fixed source-quality policy unsupported REST intervals still score **$0 realistic deployable capacity**. Descriptive price/depth evidence remains useful.

Current v5 strict matcher is still **full-game MLB totals only** (`KXMLBTOTAL`). Required MLB moneyline/spread and strict rule-certified prop breadth remains incomplete. Missing breadth does not pause the 72-hour clock.

### Gemini<->Kalshi sports

Bounded q=1 canary deployment **`e029636d-5342-478e-a733-7f8c0dadf669`** remains SUCCESS and armed as `LIVE_RULE_RISK_PROBE_NOT_GUARANTEED_ARB`.

The prior Kalshi authenticated websocket HTTP 429 transport blocker is **CLEARED**. Root cause was consistent with authenticated read-budget pressure from aggressive background REST fallback polling. `sports-gk-autonomous-supervisor` was reduced from 100ms to **1000ms** Kalshi REST batches, cutting its background request rate by roughly 10x. Its replacement deployment **`6979fce9-3b6c-4659-aef0-13d36dd893e9`** is SUCCESS.

At **2026-08-25 21:00:01 UTC / 17:00:01 ET**, the live canary successfully logged:

`Kalshi WS subscribed to 139 matched ticker(s)`

The repeated 429 loop stopped. Fresh checks after websocket recovery found **no verified SUBMITTED, FILLED, RESULT, settlement or realized event**. The canary remains taker/taker and retains q=1, >=3c modeled net, <=50ms skew, <=100ms books, 50ms exact revalidation, dual-FOK, $5 hard budget, one attempt, ambiguity reconciliation and restart NEVER.

### Maker lane relationship

The fixed **3c net threshold is not being lowered**. Maker-first is the mechanism being tested in the separate AWS/low-latency lane to improve entry economics enough that sub-3c taker/taker markets may become >=3c net after maker fill price, hedge price, all fees, latency/adverse-selection reserve and actual fill behavior.

The first corrected K<->P sample is economically relevant input to that lane: 1c taker/taker gaps had hundreds of contracts of selected-side depth and ~170-225ms descriptive lifetimes, but no >=2c or >=3c taker/taker signal yet. That does **not** prove maker profitability; it identifies the class of opportunities maker routing must test.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains crashed from the previously confirmed bounded-campaign deadline -> `HALTED_FAIL_CLOSED` path. It is not being reopened here because settlement-index non-equivalence remains the stronger safety/economic blocker. AWS/low-latency maker-routing work remains isolated in the separate chat.

## Highest-value next actions

1. Keep the K<->P 72-hour wall clock running without resets; use the corrected q1 depth telemetry and preserve REST source-quality exclusions.
2. Rank actual >=1c/>=2c/>=3c q1 opportunity supply and identify deep sub-3c rows suitable as **inputs** to the isolated maker lane; do not count maker-model economics as realized.
3. Add K<->P MLB moneyline/spread breadth and only strict props with provable rule/settlement equivalence; semantic mirrors must be deduped.
4. Leave the now-healthy G<->K q1 taker/taker canary armed under its existing safety controls and record only authoritative submissions/fills/settlements.
5. Preserve Kalshi read-budget headroom for execution websocket traffic; background read-only collectors should not crowd out the live WS again.
6. Preserve the fixed <$50 / $50-$149 / >=$150 verdict thresholds and do not extend the census because results are interesting.
7. Keep AWS latency/maker-routing work isolated from this main control-plane chat.
