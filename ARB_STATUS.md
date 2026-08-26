# ARB Control Plane

Last verified: **2026-08-26 05:39 UTC / 2026-08-26 01:39 ET**

This file is the current production checkpoint. Historical detail belongs in git history / `ARB_STATUS_HISTORY_2026-08-23.md`.

## Latest run delta

- `sports-kp-live-executor` remains **SUCCESS**, read-only, and actively processing triggers. The decision-grade certified MLB totals result is still **1,031 >=1c / 0 >=2c / 0 >=3c** after fees from **1,661 observations / 9 semantic keys**. At the fixed provisional **>=80ms** realistic-capture floor it remains **137 >=1c / 0 >=2c / 0 >=3c**. Average selected-side q1 depth remains **~2,072.66 contracts**, max **22,647.58**; average quote skew **~45.53ms**; 612 closed signals average **~174.07ms** descriptive lifetime. Liquidity is not the blocker. The certified totals slice remains **$0/day deployable capacity at the fixed 3c floor**.
- The generic K<->P MLB game breadth service was freshly re-verified as healthy before the diagnostic intervention: both venue discovery feeds returned HTTP 200 and the loop continued finding **>100 exact shared semantic keys** with zero reaching pricing because of the existing <=5 minute scheduled-start gate.
- Merged timestamp diagnostic #153 was executed directly in the same Railway service/runtime as the working breadth loop. It still failed immediately on Kalshi `403 Forbidden` from `external-api.kalshi.com`. Because the normal breadth loop in that same service obtains Kalshi HTTP 200, **region/IP is no longer the leading explanation**. The narrower root cause is that #153 reuses an unauthenticated/raw `_fetch_inputs()` discovery path whose auth/header behavior differs from the working breadth loop. Therefore the 403 is a **diagnostic plumbing defect, not market evidence** and not permission to alter the <=5 minute identity gate.
- The brief read-only diagnostic deployment was immediately removed and the production service was restored to `python -m src.sports_kp_mlb_generic_breadth_loop`. Replacement Railway deployment `48daaa89-21bd-4b19-9685-c7fef1b223f0` is **SUCCESS**. No order authority or trading gate changed and no unrelated service was stopped.
- The next bounded repair is to make #153 reuse the same authenticated Kalshi discovery/request helper as the working breadth loop, then rerun it read-only. Only a successful raw timestamp report can justify a narrow scheduled-start normalization correction; the <=5 minute gate remains fixed.
- Fresh authoritative Neon `arb-trade-ledger` from the prior checkpoint remains: **no events of any type after 2026-08-26 00:00 UTC**; **2026-08-26 ET = 0 TRADE_REALIZED / $0.0000 net**. Caleb Durbin remains **-$0.44 REALIZED** and is not reclassified away.
- Weather remains **0 weather-tagged TRADE_REALIZED / $0 verified realized since 2026-03-01** from the latest authoritative query. Seasonal/historical weather edge is not credited as current realized performance.
- Kalshi account diagnosis remains **advanced tier, 900 bucket / 300 refill** for both reads and writes. The working breadth loop's active REST census calls to `external-api.kalshi.com` return HTTP 200; do not weaken backoff or disrupt unrelated consumers.
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

- **2026-08-26 ET:** **0 TRADE_REALIZED / $0.0000 net** at the latest authoritative ledger checkpoint.
- **No central ledger event of any type after 2026-08-26 00:00 UTC** at that checkpoint.
- Caleb Durbin: **-$0.44 REALIZED one-leg sports loss**.
- Weather since **2026-03-01:** **0 weather-tagged TRADE_REALIZED / $0.0000 verified realized**. Seasonal/historical edge is not substituted for realized performance.
- Same-Gemini crypto prior snapshot: roughly **2,647 certified q1 pairs / 275 books / 0 immediate >=3c FOK/FOK locks**; near-verdict evidence only.

## Cross-venue census

### Kalshi<->Polymarket strict MLB totals

`sports-kp-live-executor` remains the decision-grade read-only totals collector. Latest cumulative verified totals evidence:

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

Fresh production continues to find **>100 exact shared semantic keys after physical-representation expansion**, but no key reaches book pricing because the existing <=5 minute scheduled-start identity gate rejects the venue-normalized starts.

The leading source-level hypothesis remains `_kalshi_start()` fallback behavior when event tickers omit HHMM: `strike_date`, `expected_expiration_time`, and `close_time` are used as start candidates. However, merged diagnostic #153 cannot currently prove this because its discovery path receives Kalshi HTTP 403 even inside the same Railway service where the normal breadth loop gets HTTP 200.

The new diagnostic root cause is therefore **request/auth plumbing**: #153 must reuse the working authenticated Kalshi discovery helper before its raw timestamp output can be trusted. Therefore:

- this is **not** proof of zero MLB game-market overlap;
- the <=5 minute identity gate remains unchanged;
- moneyline/spread/game-total capacity remains **$0** until authenticated raw timestamp evidence proves the normalization mismatch and a narrow adapter correction is tested;
- the census clock does not pause or reset.

### Gemini<->Kalshi sports

The guaranteed-arb path remains deliberately disarmed/research-only. Prior hot-shadow state had 570 Gemini symbols / 570 initialized Kalshi tickers and a handful of >=3c modeled signals, but complete Gemini prop lifecycle settlement equivalence remains uncertified. Those signals receive **$0 guaranteed-arb capacity credit**.

Central ledger reconciliation is clean at the latest authoritative checkpoint: Caleb is settled -$0.44 and there is no new overnight ledger event. Keep guaranteed G<->K execution disarmed unless exact rule/settlement equivalence is independently certified.

Kalshi secondary websocket 429s remain consistent with concurrent-handshake/contention, not a weak account tier. `/account/limits` remains **advanced**, **900 bucket / 300 refill** for both reads and writes.

### Weather

Latest authoritative central records show **0 weather-tagged realized trades / $0 realized since 2026-03-01**. Historical or seasonal weather edge remains a separate research statistic and is not credited toward current realized performance.

### Old Gemini<->Kalshi 15m crypto

`crypto-15m-sol-live-canary-10bp-50ms` remains **CRASHED** from the confirmed bounded-campaign deadline -> `HALTED_FAIL_CLOSED` path. Do not reopen as guaranteed arbitrage because Gemini/Kalshi settlement-index equivalence is not proven.

## Highest-value next actions

1. Keep the K<->P 72-hour wall clock running with no resets. Current certified totals evidence remains **$0/day at >=3c**.
2. Patch #153's read-only discovery path to reuse the exact authenticated Kalshi request/helper used by the working breadth loop. Do not loosen the <=5 minute gate.
3. Rerun #153 in the working Railway service. If authenticated raw timestamps prove Kalshi is substituting strike/expiry/close for scheduled first pitch, patch only that adapter normalization and test it before production.
4. If timestamps align after a proven correction, immediately price moneyline/spread/game-total physical pairs and report >=1c/>=2c/>=3c, q1 depth, skew, and missing-to-3c. Until then those families remain $0 capacity.
5. Treat G<->K hot >=3c observations as **research-only capacity $0** until complete lifecycle settlement equivalence is certified. Keep guaranteed order entry disarmed.
6. Keep rule-incomplete player props at capacity $0 until settlement/postponement/cancellation/stat-correction semantics are certified. Participation proof alone is insufficient.
7. Preserve Kalshi account headroom and websocket backoff; do not stop unrelated services for census convenience.
8. Preserve fixed **<$50 / $50-$149 / >=$150** verdict thresholds and the one-time +24h maximum data-quality allowance.
9. Keep AWS latency/maker-routing work isolated from this control-plane workstream.
