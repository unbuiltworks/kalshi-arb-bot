# ARB Control Plane

Last verified: **2026-08-26 04:42 UTC / 2026-08-26 00:42 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Latest run delta

- `sports-kp-live-executor` remains **SUCCESS**, read-only, and actively processing triggers. The decision-grade certified MLB totals result is still **1,031 >=1c / 0 >=2c / 0 >=3c** after fees from **1,661 observations / 9 semantic keys**. At the fixed provisional **>=80ms** realistic-capture floor it remains **137 >=1c / 0 >=2c / 0 >=3c**. Average selected-side q1 depth remains **~2,072.66 contracts**, max **22,647.58**; average quote skew **~45.53ms**; 612 closed signals average **~174.07ms** descriptive lifetime. Liquidity is not the blocker. The certified totals slice remains **$0/day deployable capacity at the fixed 3c floor**.
- The generic K<->P MLB game breadth service is also **SUCCESS** and both venue discovery feeds are returning HTTP 200. Fresh state is **55 Polymarket events / 70 Kalshi events**, with **124 expanded exact shared semantic keys**, but **0 book-comparable physical pairs** because none pass the existing <=5 minute scheduled-start gate. These families remain **$0 capacity** until the timestamp normalization issue is proven and repaired; the gate is not loosened.
- Source inspection now isolates the leading timestamp-normalization failure mode: `normalize_kalshi_mlb()` calls `_kalshi_start()`, which uses an event-ticker embedded `YYMONDDHHMM` timestamp when present, but otherwise falls back to `event.strike_date`, `market.expected_expiration_time`, then `market.close_time`. Current normal MLB event tickers are commonly date-only, so scheduled start can be replaced by strike/expiry/close metadata. This is a **leading diagnosis, not yet permission to patch**: raw venue timestamp fields still need authenticated runtime proof from merged diagnostic #153.
- I attempted to execute merged PR #153 in Railway without touching the active collectors. Railway's read-only agent remains quota-blocked, and the safe one-off service retained its older #152 snapshot rather than running the diagnostic. A no-op diagnostic-file trigger commit (`367ff3a...`) did not produce a verified #153 runtime output. Therefore no start-time gate or adapter behavior was changed this run.
- Fresh authoritative Neon `arb-trade-ledger`: **no events of any type after 2026-08-26 00:00 UTC**; **2026-08-26 ET = 0 TRADE_REALIZED / $0.0000 net**. There is no overnight G<->K order/fill/reservation hidden in the central ledger. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.
- Fresh authoritative weather query remains **0 weather-tagged TRADE_REALIZED / $0 verified realized since 2026-03-01**. Seasonal/historical weather edge is not credited as current realized performance.
- The bounded `sports-gk-multifamily-live-canary` has no `SUBMITTED` evidence in its current Railway deployment logs, while the guaranteed-arb G<->K path remains research-only/disarmed because complete lifecycle rule/settlement equivalence is not certified. Do not credit hot-shadow signals as deployable guaranteed capacity.
- Kalshi account diagnosis remains **advanced tier, 900 bucket / 300 refill** for both reads and writes. The active REST census calls to `external-api.kalshi.com` are healthy HTTP 200; do not weaken backoff or disrupt unrelated consumers.
- The old `crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED/contained**. Its bounded-campaign failure is proven, but Gemini/Kalshi settlement-index equivalence remains unproven, so it is not reopened as guaranteed arbitrage.

## Objective and accounting rule

Primary aspiration remains **$480/day REALIZED net**. Only authoritative settlement/reconciliation creates REALIZED P&L. Stale quotes, shadow/model P&L, rule-mismatched rows, unconfirmed fills and one-leg exposure never count.

## Edge-census protocol

The 72-hour K<->P wall clock started **2026-08-25 20:43 UTC / 16:43 ET** and is **not paused or reset** by instrumentation defects. Invalid/unmeasurable intervals score **$0 deployable capacity for that wall-clock interval**.

Fixed verdict thresholds remain unchanged:

- **<$50/day** realistic deployable capacity: end dedicated ARB engineering and archive.
- **$50-$149/day**: sidecar-only maintenance.
- **>= $150/day**: active scaling justified toward $480/day.

Data/feed quality may add at most one **+24 hour** extension. An unmeasurable lane after that contributes **$0 deployable capacity**. Thresholds do not move after seeing results.

Required evidence remains explicit >=1c/>=2c/>=3c after-fee bins, exact executable q1 depth, quote skew, source quality, valid signal lifetime, semantic dedupe, overlap-adjusted capacity, and a provisional **>=80ms** realistic-capture floor. The latency floor may rise to measured production p95 but may not be lowered to rescue capacity.

Same-venue Gemini/Polymarket/Kalshi zero results remain near-verdict evidence. Do not spend engineering time manufacturing ladder violations.

## Current authoritative scoreboard

- **2026-08-26 ET:** **0 TRADE_REALIZED / $0.0000 net**.
- **No central ledger event of any type after 2026-08-26 00:00 UTC** as of this checkpoint.
- Caleb Durbin: **-$0.44 REALIZED one-leg sports loss**.
- Weather since **2026-03-01:** **0 weather-tagged TRADE_REALIZED / $0.0000 verified realized**. Seasonal/historical edge is not substituted for realized performance.
- Same-Gemini crypto prior snapshot: roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Cross-venue census

### Kalshi<->Polymarket strict MLB totals

` sports-kp-live-executor` remains the decision-grade read-only totals collector. Latest cumulative verified totals evidence:

- **53,237 comparable evaluations**;
- **1,661 decision-census observations** across **9 semantic keys**;
- after-fee bins: **1,031 >=1c / 0 >=2c / 0 >=3c**;
- provisional >=80ms capture bins: **137 >=1c / 0 >=2c / 0 >=3c**;
- average selected-side q1 depth **~2,072.66**, max **22,647.58**;
- average quote skew **~45.53ms**, max **100ms**;
- average local process latency **~0.234ms**;
- **612 closed signals**, descriptive average lifetime **~174.07ms**, max **567ms**;
- Kalshi transport is bounded `rest_batch`, source quality **REST_SNAPSHOT_NOT_WS_LIFETIME**;
- `read_only=true`, `order_entry=false`.

Interpretation: liquidity is deep enough, but this certified totals slice has **zero >=2c and zero >=3c after-fee observations**, including zero >=2c/>=3c at the provisional realistic-capture floor. At the fixed 3c taker/taker floor this slice currently contributes **$0/day deployable capacity**.

### K<->P MLB moneyline/spread/game-total breadth

Fresh production has **55 Polymarket events / 70 Kalshi events** and **124 exact shared semantic keys after physical-representation expansion**, but no key reaches book pricing because the existing <=5 minute scheduled-start identity gate rejects the venue-normalized starts.

The leading source-level cause is `_kalshi_start()` fallback behavior when event tickers omit HHMM: `strike_date`, `expected_expiration_time`, and `close_time` are used as start candidates. Merged PR #153 exists specifically to expose raw timestamp fields and the skew distribution. Its first GitHub-hosted run received Kalshi HTTP 403; the attempted Railway one-off in this run did not obtain a current diagnostic snapshot. Therefore:

- this is **not** proof of zero MLB game-market overlap;
- the <=5 minute identity gate remains unchanged;
- moneyline/spread/game-total capacity remains **$0** until authenticated raw timestamp evidence proves the normalization mismatch and a narrow adapter correction is tested;
- the census clock does not pause or reset.

### Gemini<->Kalshi sports

The guaranteed-arb path remains deliberately disarmed/research-only. Prior hot-shadow state had 570 Gemini symbols / 570 initialized Kalshi tickers and a handful of >=3c modeled signals, but complete Gemini prop lifecycle settlement equivalence remains uncertified. Those signals receive **$0 guaranteed-arb capacity credit**.

Central ledger reconciliation is clean: Caleb is settled -$0.44 and there is no new overnight ledger event. Keep guaranteed G<->K execution disarmed unless exact rule/settlement equivalence is independently certified.

Kalshi secondary websocket 429s remain consistent with concurrent-handshake/contention, not a weak account tier. `/account/limits` remains **advanced**, **900 bucket / 300 refill** for both reads and writes.

### Weather

Fresh authoritative central records show **0 weather-tagged realized trades / $0 realized since 2026-03-01**. Historical or seasonal weather edge remains a separate research statistic and is not credited toward current realized performance.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED** from the confirmed bounded-campaign deadline -> `HALTED_FAIL_CLOSED` path. Do not reopen as guaranteed arbitrage because Gemini/Kalshi settlement-index equivalence is not proven.

## Highest-value next actions

1. Keep the K<->P 72-hour wall clock running with no resets. Current certified totals evidence remains **$0/day at >=3c**.
2. Obtain one authenticated, read-only #153 timestamp report from Railway/source runtime. Do not loosen the <=5 minute gate. If it proves Kalshi is substituting strike/expiry/close for scheduled first pitch, patch only that adapter normalization and test it before production.
3. If timestamps align after a proven correction, immediately price moneyline/spread/game-total physical pairs and report >=1c/>=2c/>=3c, q1 depth, skew, and missing-to-3c. Until then those families remain $0 capacity.
4. Treat G<->K hot >=3c observations as **research-only capacity $0** until complete lifecycle settlement equivalence is certified. Keep guaranteed order entry disarmed.
5. Keep rule-incomplete player props at capacity $0 until settlement/postponement/cancellation/stat-correction semantics are certified. Participation proof alone is insufficient.
6. Preserve Kalshi account headroom and websocket backoff; do not stop unrelated services for census convenience.
7. Preserve fixed **<$50 / $50-$149 / >=$150** verdict thresholds and the one-time +24h maximum data-quality allowance.
8. Keep AWS latency/maker-routing work isolated from this control-plane workstream.
